"""Bud-based growth: self-organising tree development after Palubicki et al. 2009.

One step is one growing season. Every node carries a lateral bud and every
axis ends in a terminal bud. Each season:

    1. light      exposure Q and the best light direction V at every bud
    2. gather     Q summed from the tips down to the root (basipetal)
    3. allocate   resource v = alpha * Q_root shared back out (acropetal); at each
                  node the main axis and the lateral compete as
                  lambda * Q_main : (1 - lambda) * Q_lateral   (extended Borchert-Honda)
    4. bud fate   a bud with resource v grows floor(v) internodes of length
                  shoot_length * v / floor(v); below 1 it stays dormant
    5. direction  each internode = default orientation + xi * V + eta * up

Hydraulic limitation (Ryan and Yoder 1997; Koch et al. 2004): lifting water gets
harder with height, and leaf productivity falls roughly linearly toward zero at a
species' maximum height. With max_height > 0 a bud's light counts for
(1 - height / max_height) of itself, so height growth slows and stops near it.

Acrotony (an architectural trait of the species, like phyllotaxis): with
lateral_buds = k > 0, only the last k nodes of each season's shoot carry a lateral
bud, so branching clusters at the end of each year's growth and the rest of the
shoot stays bare. Oaks are strongly acrotonic. k = 0 puts a bud on every node.

Between gathering and allocating, a branch whose gathered light per internode is
below shed_threshold is shed with its buds (Takenaka 1994, as in the paper): it
costs the tree more than it earns. Apical control lambda moves from its young to
its old value over the tree's life, which is how the paper takes a tree from a
leader-dominated youth to a spreading old crown.

Nothing here knows about an envelope or about attractor points: where a branch
starts, how long it runs and where the crown ends all come out of the light and
the resource budget. See docs/BUD_BACKEND_DESIGN.md for the model, the check
against the paper and the places treegen departs from it (the light model above
all: the existing occlusion field stands in for the paper's shadow propagation).

"""
from __future__ import annotations

import math

import numpy as np

from ..schema import Scene
from .age import age_state
from .growth import ALIVE, SHED, GrowthResult
from .light import LightField, neighbour_bounds
from .util import UP, depth_levels, hash01, normalize

MAX_LIVE = 400_000           # a runaway budget is a calibration error, not a tree
MAX_SHOOT = 12               # internodes one bud may add in a season
MAX_GRID = 128               # light grid cells along the longest axis
MIN_CELL = 0.4               # m; light cells no finer than this (the paper: about one internode)


class _Grow:
    """Struct of growable arrays; `fields` maps name -> (dtype, trailing shape, fill)."""

    def __init__(self, fields: dict, cap: int = 4096):
        self.n = 0
        self._fields = fields
        for k, (dt, shape, fill) in fields.items():
            setattr(self, k, np.full((cap,) + shape, fill, dt))

    def reserve(self, m: int) -> slice:
        need = self.n + m
        cap = len(getattr(self, next(iter(self._fields))))
        if need > cap:
            new = max(need, cap * 2)
            for k, (dt, shape, fill) in self._fields.items():
                a = np.full((new,) + shape, fill, dt)
                a[: self.n] = getattr(self, k)[: self.n]
                setattr(self, k, a)
        s = slice(self.n, need)
        for k, (dt, shape, fill) in self._fields.items():   # rows may be reused after keep()
            getattr(self, k)[s] = fill
        self.n = need
        return s

    def keep(self, mask: np.ndarray):
        """Drop the rows where mask is False."""
        m = int(mask.sum())
        for k in self._fields:
            a = getattr(self, k)
            a[:m] = a[: self.n][mask]
        self.n = m


def _basis(d: np.ndarray):
    """Two unit vectors perpendicular to each direction in d (N, 3)."""
    ref = np.where(np.abs(d[:, 1:2]) > 0.95, np.array([1.0, 0.0, 0.0]), UP)
    u = normalize(np.cross(d, ref))
    return u, np.cross(d, u)


def _lateral_dir(d, phi, angle):
    """A lateral bud's direction: the internode direction d tipped out by `angle`, at azimuth phi."""
    u, w = _basis(d)
    return normalize(math.cos(angle) * d + math.sin(angle) * (np.cos(phi)[:, None] * u + np.sin(phi)[:, None] * w))


def _field(params, scene, pos, leafy, hscale, cell):
    """Occlusion field around the tree as it stands, cells about one internode across."""
    L = params["light"]
    lo, hi = pos.min(0), pos.max(0)
    margin = max(3.0, 0.25 * float((hi - lo).max()))
    lo, hi = lo - margin, hi + margin
    lo[1] = 0.0
    if scene.neighbours:                       # as much of the stand as can shade the tree
        nlo, nhi = neighbour_bounds(scene.neighbours, hscale)
        reach = 10.0 + 2.0 * margin
        lo = np.maximum(np.minimum(lo, nlo), lo - reach)
        hi = np.minimum(np.maximum(hi, nhi), hi + reach)
        lo[1] = 0.0
    ext = float((hi - lo).max())
    res = int(min(MAX_GRID, max(8, math.ceil(ext / cell))))
    # optical depth of leaves: half their area is what a ray meets, per cubic metre of cell
    return LightField.build(lo, hi, res, scene.neighbours, pos[leafy], 0.5 * params["buds"]["leaf_area"],
                            L["ray_count"], L["sky_bias"], hscale)


class _Shadow:
    """The paper's shadow propagation (Palubicki et al. 2009, section 4.1).

    Space is a grid of cells. A bud in cell (I, J, K) adds a * b**-q of shadow to the
    cells (I +- p, J - q, K +- p) for q = 0..q_max and p = 0..q: a square pyramid of
    penumbra below it. A bud's exposure is max(C - s + a, 0) with C = 1, the + a
    cancelling its own shadow, and its best direction of growth is down the shadow
    gradient. The pyramids are summed layer by layer with box sums, so the cost is
    proportional to the grid, not to buds times pyramid size.
    """

    def __init__(self, bud_pos, cell, a, b, q_max):
        self.cell, self.a = cell, a
        lo = bud_pos.min(0) - (q_max + 2) * cell
        hi = bud_pos.max(0) + (q_max + 2) * cell
        lo[1] = min(lo[1], 0.0)
        self.origin = lo
        shape = np.maximum(np.ceil((hi - lo) / cell).astype(int), 1)
        idx = self._index(bud_pos, shape)
        count = np.zeros(shape, np.float64)
        np.add.at(count, (idx[:, 0], idx[:, 1], idx[:, 2]), 1.0)
        # integral image over x and z of every layer, for (2q+1)^2 box sums
        integ = np.zeros((shape[0] + 1, shape[1], shape[2] + 1))
        integ[1:, :, 1:] = count.cumsum(0).cumsum(2)
        s = np.zeros(shape, np.float64)
        nx, ny, nz = shape
        for q in range(q_max + 1):
            i0 = np.clip(np.arange(nx) - q, 0, nx)
            i1 = np.clip(np.arange(nx) + q + 1, 0, nx)
            k0 = np.clip(np.arange(nz) - q, 0, nz)
            k1 = np.clip(np.arange(nz) + q + 1, 0, nz)
            box = (integ[i1][:, :, k1] - integ[i0][:, :, k1] - integ[i1][:, :, k0] + integ[i0][:, :, k0])
            if q == 0:
                s += a * box
            elif q < ny:
                s[:, : ny - q, :] += a * b ** (-q) * box[:, q:, :]   # cast onto the layer q below
        self.s, self.shape = s, shape

    def _index(self, pts, shape=None):
        shape = self.shape if shape is None else shape
        i = np.floor((pts - self.origin) / self.cell).astype(np.int64)
        return np.clip(i, 0, np.asarray(shape) - 1)

    def sample(self, pts):
        """(exposure, direction) at pts: max(1 - s + a, 0) and the downhill shadow gradient."""
        i = self._index(pts)
        s = self.s
        q = np.clip(1.0 - s[i[:, 0], i[:, 1], i[:, 2]] + self.a, 0.0, 1.0)
        g = np.zeros((len(pts), 3))
        for ax in range(3):
            up, dn = i.copy(), i.copy()
            up[:, ax] = np.minimum(up[:, ax] + 1, self.shape[ax] - 1)
            dn[:, ax] = np.maximum(dn[:, ax] - 1, 0)
            g[:, ax] = s[up[:, 0], up[:, 1], up[:, 2]] - s[dn[:, 0], dn[:, 1], dn[:, 2]]
        v = -g
        n = np.linalg.norm(v, axis=1)
        v = np.where(n[:, None] > 1e-9, v / np.maximum(n, 1e-9)[:, None], UP)
        return q, v


def _light(params, scene, pos, leafy, bud_nodes, hscale, model):
    """(exposure, direction) at the nodes bud_nodes: the paper's shadows, or the occlusion field.

    With shadow propagation, neighbour proxies still shade through the occlusion field
    (proxies only, no foliage), multiplied in, so stands keep working.
    """
    B = params["buds"]
    pts = pos[bud_nodes]
    if model == "shadow":
        sh = _Shadow(pts, B["shadow_cell"], B["shadow_a"], B["shadow_b"], int(B["shadow_depth"]))
        q, v = sh.sample(pts)
        if scene.neighbours:
            nb = _field(params, scene, pos, np.zeros(len(pos), bool), hscale, max(MIN_CELL, B["shoot_length"] * 1.5))
            qn, vn = _sample_by_cell(nb, pts)
            q = q * qn
            v = normalize(v + vn * (1.0 - qn)[:, None])
        return q, v, sh.shape
    field_ = _field(params, scene, pos, leafy, hscale, max(MIN_CELL, B["shoot_length"] * 1.5))
    q, v = _sample_by_cell(field_, pts)
    return q, v, field_.density.shape


def _sample_by_cell(field_, pts):
    """Exposure and light direction at pts, sampled once per occupied grid cell.

    Light sampling is nearly all of the model's cost, buds crowd into the same cells,
    and the field cannot tell points inside one cell apart anyway.
    """
    idx = np.floor((pts - field_.origin) / field_.voxel).astype(np.int64)
    idx = np.clip(idx, 0, field_.shape - 1)
    key = (idx[:, 0] * field_.shape[1] + idx[:, 1]) * field_.shape[2] + idx[:, 2]
    cells, inverse = np.unique(key, return_inverse=True)
    first = np.zeros(len(cells), np.int64)
    first[inverse] = np.arange(len(pts))
    centre = field_.origin + (idx[first] + 0.5) * field_.voxel
    q, v = field_.sample(centre)
    return q[inverse], v[inverse]


def _shed(nodes, N, levels, Q, threshold, year, max_ring=np.inf):
    """Mark branches whose light per internode is below threshold; returns the shed mask or None.

    A branch is a lateral axis with everything it carries. Its size is its internode
    count. Branch autonomy (Sprugel et al. 1991): the decision is made by small units,
    so only branches whose base is thinner than max_ring (in ring units) are judged on
    their light; thicker wood goes only once it carries no live buds at all. Judging
    big limbs as a whole fails, because a limb carries far more wood per leaf than a
    twig and so always looks poorer than the shaded twigs it should outlive.
    The main stem is never shed. The tips a shed branch carried are remembered on the
    node it left, so the wood below keeps the thickness it grew to support.
    """
    par = nodes.parent[:N]
    live = nodes.state[:N] == ALIVE
    size = live.astype(np.float64)
    kids = np.zeros(N, np.int64)
    np.add.at(kids, par[1:][live[1:]], 1)
    tips = (live & (kids == 0)).astype(np.float64)
    for lvl in reversed(levels[1:]):
        np.add.at(size, par[lvl], size[lvl])
        np.add.at(tips, par[lvl], tips[lvl])
    small = nodes.ring[:N] < max_ring
    base = live & ~nodes.main[:N] & np.where(small, Q < threshold * size, Q <= 0.0)
    base[0] = False
    if not base.any():
        return None
    mark = base.copy()
    for lvl in levels[1:]:
        mark[lvl] |= mark[par[lvl]]
    mark &= live
    top = np.flatnonzero(base & ~mark[np.maximum(par, 0)])          # outermost shed branches only
    np.add.at(nodes.shed_tips, par[top], tips[top])
    nodes.state[:N][mark] = SHED
    nodes.shed_year[:N][mark] = year
    return mark


_ARCHIVED = ("uid", "pos", "birth", "depth", "state", "shed_year", "shed_tips", "ring")
KEEP_SHED_YEARS = 10         # shed wood that lived this long is kept for the shed overlay and age scrub


def _archive_shed(nodes, buds, archive):
    """Move shed wood out of the working arrays; it is only needed again at the end.

    Every season walks the working arrays several times, and shed wood soon
    outnumbers live wood many times over (2.5 million to 0.3 million by 120 years).
    A shed branch takes its whole subtree, so no live node or bud refers to it.
    Only wood that lived KEEP_SHED_YEARS is kept in the output: half of all shed
    wood is twig turnover of two years or less, its rings are already counted in
    the wood below, and keeping it made a 200-year tree a 580 MB cache file. A
    branch's base is never younger than what it carried, so what is kept stays a
    connected tree.
    """
    N, M = nodes.n, buds.n
    gone = nodes.state[:N] == SHED
    if not gone.any():
        return
    par = nodes.parent[:N]
    puid = np.where(par >= 0, nodes.uid[np.maximum(par, 0)], -1)
    lived = gone & (nodes.shed_year[:N] - nodes.birth[:N] >= KEEP_SHED_YEARS)
    if lived.any():
        rec = {k: getattr(nodes, k)[:N][lived].copy() for k in _ARCHIVED}
        rec["parent_uid"] = puid[lived]
        archive.append(rec)
    remap = np.full(N, -1, np.int64)
    remap[~gone] = np.arange(int((~gone).sum()))
    nodes.keep(~gone)
    n = nodes.n
    nodes.parent[:n] = np.where(nodes.parent[:n] >= 0, remap[np.maximum(nodes.parent[:n], 0)], -1)
    buds.node[:M] = remap[buds.node[:M]]


def _assemble(nodes, archive):
    """Live and archived nodes as one tree, in creation order (parents before children)."""
    n = nodes.n
    par = nodes.parent[:n]
    live = {k: getattr(nodes, k)[:n] for k in _ARCHIVED}
    live["parent_uid"] = np.where(par >= 0, nodes.uid[np.maximum(par, 0)], -1)
    parts = [live] + archive
    cat = {k: np.concatenate([p[k] for p in parts]) for k in live}
    order = np.argsort(cat["uid"], kind="stable")
    cat = {k: v[order] for k, v in cat.items()}
    index = np.full(int(cat["uid"].max()) + 1, -1, np.int64)
    index[cat["uid"]] = np.arange(len(order))
    cat["parent"] = np.where(cat["parent_uid"] >= 0, index[np.maximum(cat["parent_uid"], 0)], -1)
    return cat


def grow_buds(params: dict, scene: Scene, seed: int, age: float, progress=None) -> GrowthResult:
    B = params["buds"]
    alpha = B["resource_per_light"]
    lam_young, lam_old = B["apical_control"], B["apical_control_old"]
    shed_thr, max_age = B["shed_threshold"], params["meta"]["max_age"]
    shoot, xi, eta = B["shoot_length"], B["light_weight"], B["tropism"]
    angle, phyllo = math.radians(B["branching_angle"]), math.radians(B["phyllotaxis"])
    acro = int(B["lateral_buds"])
    h_max = B["max_height"]
    max_ring = np.inf                         # the shed diameter limit, in ring units (tip-years)
    if B["shed_max_diameter"] > 0:
        R_ = params["radii"]
        max_ring = (B["shed_max_diameter"] / (2 * R_["tip_radius"])) ** R_["pipe_exponent"]
    seasons = int(math.floor(age))

    nodes = _Grow({
        "pos": (np.float64, (3,), 0.0), "parent": (np.int64, (), -1), "birth": (np.float32, (), 0.0),
        "depth": (np.int32, (), 0), "main": (bool, (), True),      # main: continues its parent's axis
        "phi": (np.float64, (), 0.0),                               # phyllotactic azimuth along the axis
        "state": (np.int8, (), ALIVE), "shed_year": (np.float32, (), -1.0), "shed_tips": (np.float32, (), 0.0),
        "ring": (np.float64, (), 0.0),                              # wood cross-section, tip-years
        "uid": (np.int64, (), 0),                                   # creation order, never changes
    })
    archive: list = []                                              # shed wood, see _archive_shed
    next_uid = 1
    buds = _Grow({
        "node": (np.int64, (), 0), "dir": (np.float64, (3,), 0.0), "terminal": (bool, (), False),
        "birth": (np.float32, (), 0.0),
    }, cap=4096)

    nodes.reserve(1)                                                # the seedling: one node, one bud
    s = buds.reserve(1)
    buds.dir[s], buds.terminal[s], buds.birth[s] = UP, True, -1.0
    stats = {"seasons": seasons, "shoots": 0, "shed_nodes": 0, "grid": [0, 0, 0]}
    model = B["light_model"]
    lit = False

    for year in range(1, seasons + 1):
        N, M = nodes.n, buds.n
        pos, par = nodes.pos[:N], nodes.parent[:N]
        bnode = buds.node[:M]
        hscale = age_state(params, float(year)).height_mult
        lam = lam_young + (lam_old - lam_young) * min(year / max_age, 1.0)
        live = nodes.state[:N] == ALIVE

        # ---- 1. light: leaves sit on last season's shoots and at the axis ends ----------------
        leafy = np.zeros(N, bool)
        leafy[nodes.birth[:N] >= year - 1] = True
        leafy[bnode[buds.terminal[:M]]] = True
        leafy &= live
        with_bud = np.unique(bnode)
        q_s, v_s, grid = _light(params, scene, pos, leafy, with_bud, hscale, model)
        lit = True
        q_node = np.zeros(N)
        q_node[with_bud] = q_s
        vdir = np.tile(UP, (N, 1))
        vdir[with_bud] = v_s
        qb = q_node[bnode]

        # ---- 2. gather light, tips to root ------------------------------------------------------
        Q = np.zeros(N)
        np.add.at(Q, bnode, qb)
        levels = depth_levels(nodes.depth[:N])
        for lvl in reversed(levels[1:]):
            np.add.at(Q, par[lvl], Q[lvl])

        # ---- shed branches that gather too little light for their size --------------------------
        if shed_thr > 0:
            shed = _shed(nodes, N, levels, Q, shed_thr, year, max_ring)
            if shed is not None:
                stats["shed_nodes"] += int(shed.sum())
                buds.keep(~shed[bnode])
                M = buds.n
                bnode, qb = buds.node[:M], q_node[buds.node[:M]]
                Q = np.zeros(N)                                     # gather again without them
                np.add.at(Q, bnode, qb)
                for lvl in reversed(levels[1:]):
                    np.add.at(Q, par[lvl], Q[lvl])

        # ---- annual ring: each node adds wood in proportion to the tips it carries this season --
        live = nodes.state[:N] == ALIVE
        kids = np.zeros(N, np.int64)
        np.add.at(kids, par[1:][live[1:]], 1)
        carried = (live & (kids == 0)).astype(np.float64)
        for lvl in reversed(levels[1:]):
            lvl = lvl[live[lvl]]
            np.add.at(carried, par[lvl], carried[lvl])
        nodes.ring[:N] += carried

        # ---- hydraulic limitation: high leaves turn their light into less resource -------------
        # (shedding above used the light itself; only the resource budget pays for height)
        if h_max > 0:
            q_node = q_node * np.clip(1.0 - pos[:, 1] / h_max, 0.0, 1.0)
            qb = q_node[bnode]
            Q = np.zeros(N)
            np.add.at(Q, bnode, qb)
            for lvl in reversed(levels[1:]):
                np.add.at(Q, par[lvl], Q[lvl])

        # ---- 3. allocate resource, root to tips (extended Borchert-Honda) -----------------------
        w_node = np.where(nodes.main[:N], lam, 1.0 - lam)
        w_bud = np.where(buds.terminal[:M], lam, 1.0 - lam)
        denom = np.zeros(N)
        np.add.at(denom, par[1:], (w_node * Q)[1:])
        np.add.at(denom, bnode, w_bud * qb)
        denom = np.maximum(denom, 1e-12)
        v = np.zeros(N)
        v[0] = alpha * Q[0]
        for lvl in levels[1:]:
            p = par[lvl]
            v[lvl] = v[p] * w_node[lvl] * Q[lvl] / denom[p]
        vb = v[bnode] * w_bud * qb / denom[bnode]

        # ---- 4. bud fate: floor(v) internodes; prolepsis keeps this season's buds waiting -------
        count = np.minimum(np.floor(vb).astype(np.int64), MAX_SHOOT)
        go = np.flatnonzero((count >= 1) & (buds.birth[:M] < year))
        if len(go) == 0:
            continue
        if int((nodes.state[:N] == ALIVE).sum()) + int(count[go].sum()) > MAX_LIVE:
            raise RuntimeError(f"bud model passed {MAX_LIVE} live nodes at year {year}: "
                               "lower buds.resource_per_light or raise buds.shoot_length")
        count_g = count[go]
        length = shoot * vb[go] / count_g
        cur = bnode[go].copy()
        cur_dir = buds.dir[:M][go].copy()
        light = vdir[cur]
        lateral_start = ~buds.terminal[:M][go]
        stats["shoots"] += len(go)

        # ---- 5. extend every growing shoot one internode at a time ------------------------------
        for k in range(int(count_g.max())):
            act = np.flatnonzero(count_g > k)
            d = normalize(cur_dir[act] + xi * light[act] + eta * UP)
            s = nodes.reserve(len(act))
            ids = np.arange(s.start, s.stop)
            uids = np.arange(next_uid, next_uid + len(act))
            next_uid += len(act)
            nodes.uid[s] = uids
            p = cur[act]
            nodes.pos[s] = nodes.pos[p] + d * length[act, None]
            nodes.parent[s] = p
            nodes.birth[s] = year
            nodes.depth[s] = nodes.depth[p] + 1
            starts_axis = lateral_start[act] & (k == 0)
            nodes.main[s] = ~starts_axis
            # phyllotaxis runs along an axis; a new axis starts at its own (seeded) azimuth
            nodes.phi[s] = np.where(starts_axis, 2 * math.pi * hash01(uids, seed, 23), nodes.phi[p] + phyllo)
            # one lateral bud per new node, or (acrotony) only on the shoot's last nodes
            budded = np.ones(len(act), bool) if acro == 0 else (count_g[act] - k <= acro)
            if budded.any():
                sb = buds.reserve(int(budded.sum()))
                buds.node[sb] = ids[budded]
                buds.dir[sb] = _lateral_dir(d[budded], nodes.phi[s][budded], angle)
                buds.terminal[sb] = False
                buds.birth[sb] = year
            cur[act], cur_dir[act] = ids, d
        # the bud that grew now sits at the end of its shoot as that axis's terminal bud
        buds.node[go], buds.dir[go], buds.terminal[go] = cur, cur_dir, True

        _archive_shed(nodes, buds, archive)
        if progress:
            progress(year / max(seasons, 1), f"year {year}: {nodes.n} nodes, {buds.n} buds")

    live_bud_pos = nodes.pos[buds.node[: buds.n]].copy()
    term_bud_nodes = nodes.uid[buds.node[: buds.n][buds.terminal[: buds.n]]]
    tree = _assemble(nodes, archive)
    N = len(tree["uid"])
    pos = tree["pos"]
    if not lit or seasons == 0:
        expo, ldir = np.ones(N, np.float32), np.tile(np.array([0, 1, 0], np.float32), (N, 1))
    else:
        leafy = tree["birth"] >= seasons - 1
        leafy[np.isin(tree["uid"], term_bud_nodes)] = True
        leafy &= tree["state"] == ALIVE
        hs = age_state(params, float(age)).height_mult
        if model == "shadow":                     # shadow from the live buds, read at every node
            sh = _Shadow(live_bud_pos, B["shadow_cell"], B["shadow_a"], B["shadow_b"],
                         int(B["shadow_depth"]))
            expo, ldir = sh.sample(pos)
            stats["grid"] = list(sh.shape)
        else:
            field_ = _field(params, scene, pos, leafy, hs, max(MIN_CELL, shoot * 1.5))
            expo, ldir = _sample_by_cell(field_, pos)
            stats["grid"] = list(field_.density.shape)
    stats.update(nodes=N, alive=int((tree["state"] == ALIVE).sum()), buds=int(buds.n))
    return GrowthResult(
        pos=pos, parent=tree["parent"], birth=tree["birth"], state=tree["state"], depth=tree["depth"],
        shed_year=tree["shed_year"], shed_tips=tree["shed_tips"],
        exposure=expo, light_dir=ldir, attractors=np.zeros((0, 3)), attractor_state=np.zeros(0, np.int8),
        age=float(age), stats=stats, ring_area=tree["ring"])
