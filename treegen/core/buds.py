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


def _shed(nodes, N, levels, Q, threshold, year):
    """Mark branches whose light per internode is below threshold; returns the shed mask or None.

    A branch is a lateral axis with everything it carries. Its size is its internode
    count. The main stem is never shed. The tips a shed branch carried are remembered
    on the node it left, so the wood below keeps the thickness it grew to support.
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
    base = live & ~nodes.main[:N] & (Q < threshold * size)
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


def grow_buds(params: dict, scene: Scene, seed: int, age: float, progress=None) -> GrowthResult:
    B = params["buds"]
    alpha = B["resource_per_light"]
    lam_young, lam_old = B["apical_control"], B["apical_control_old"]
    shed_thr, max_age = B["shed_threshold"], params["meta"]["max_age"]
    shoot, xi, eta = B["shoot_length"], B["light_weight"], B["tropism"]
    angle, phyllo = math.radians(B["branching_angle"]), math.radians(B["phyllotaxis"])
    acro = int(B["lateral_buds"])
    seasons = int(math.floor(age))

    nodes = _Grow({
        "pos": (np.float64, (3,), 0.0), "parent": (np.int64, (), -1), "birth": (np.float32, (), 0.0),
        "depth": (np.int32, (), 0), "main": (bool, (), True),      # main: continues its parent's axis
        "phi": (np.float64, (), 0.0),                               # phyllotactic azimuth along the axis
        "state": (np.int8, (), ALIVE), "shed_year": (np.float32, (), -1.0), "shed_tips": (np.float32, (), 0.0),
    })
    buds = _Grow({
        "node": (np.int64, (), 0), "dir": (np.float64, (3,), 0.0), "terminal": (bool, (), False),
        "birth": (np.float32, (), 0.0),
    }, cap=4096)

    nodes.reserve(1)                                                # the seedling: one node, one bud
    s = buds.reserve(1)
    buds.dir[s], buds.terminal[s], buds.birth[s] = UP, True, -1.0
    stats = {"seasons": seasons, "shoots": 0, "shed_nodes": 0, "grid": [0, 0, 0]}
    field_ = None

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
        field_ = _field(params, scene, pos, leafy, hscale, max(MIN_CELL, shoot * 1.5))
        with_bud = np.unique(bnode)
        q_s, v_s = _sample_by_cell(field_, pos[with_bud])
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
            shed = _shed(nodes, N, levels, Q, shed_thr, year)
            if shed is not None:
                stats["shed_nodes"] += int(shed.sum())
                buds.keep(~shed[bnode])
                M = buds.n
                bnode, qb = buds.node[:M], q_node[buds.node[:M]]
                Q = np.zeros(N)                                     # gather again without them
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
            p = cur[act]
            nodes.pos[s] = nodes.pos[p] + d * length[act, None]
            nodes.parent[s] = p
            nodes.birth[s] = year
            nodes.depth[s] = nodes.depth[p] + 1
            starts_axis = lateral_start[act] & (k == 0)
            nodes.main[s] = ~starts_axis
            # phyllotaxis runs along an axis; a new axis starts at its own (seeded) azimuth
            nodes.phi[s] = np.where(starts_axis, 2 * math.pi * hash01(ids, seed, 23), nodes.phi[p] + phyllo)
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

        if progress:
            progress(year / max(seasons, 1), f"year {year}: {nodes.n} nodes, {buds.n} buds")

    N = nodes.n
    pos = nodes.pos[:N].copy()
    if field_ is None or seasons == 0:
        expo, ldir = np.ones(N, np.float32), np.tile(np.array([0, 1, 0], np.float32), (N, 1))
    else:
        leafy = nodes.birth[:N] >= seasons - 1
        leafy[buds.node[: buds.n][buds.terminal[: buds.n]]] = True
        leafy &= nodes.state[:N] == ALIVE
        field_ = _field(params, scene, pos, leafy, age_state(params, float(age)).height_mult,
                        max(MIN_CELL, shoot * 1.5))
        expo, ldir = _sample_by_cell(field_, pos)
        stats["grid"] = list(field_.density.shape)
    stats.update(nodes=N, alive=int((nodes.state[:N] == ALIVE).sum()), buds=int(buds.n))
    return GrowthResult(
        pos=pos, parent=nodes.parent[:N].copy(), birth=nodes.birth[:N].copy(),
        state=nodes.state[:N].copy(), depth=nodes.depth[:N].copy(),
        shed_year=nodes.shed_year[:N].copy(), shed_tips=nodes.shed_tips[:N].copy(),
        exposure=expo, light_dir=ldir, attractors=np.zeros((0, 3)), attractor_state=np.zeros(0, np.int8),
        age=float(age), stats=stats)
