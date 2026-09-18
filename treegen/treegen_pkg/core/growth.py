"""Space colonization growth with light competition and age.

The simulation runs from year 0 to the requested age. The envelope, apical
dominance and shed threshold are evaluated at the *current simulated year*, not
at the target age, so a tree at 20 years is exactly the first 20 years of the
same tree at 80 years (same seed). That is what makes "three ages of the same
tree" and an age scrub possible.

Node states:
    ALIVE  – part of the tree, may still spawn growth
    DEAD   – dead stub or dieback: rendered, never grows, casts no foliage
    SHED   – culled by light; kept for the "shed" display mode, not rendered
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree

from ..schema import Scene
from .age import age_state
from .envelope import Envelope
from .light import LightField, _inside, neighbour_bounds
from .util import UP, depth_levels, hash01, normalize, rng

ALIVE, DEAD, SHED = 0, 1, 2
A_WAITING, A_ACTIVE, A_CONSUMED, A_DORMANT = 0, 1, 2, 3


@dataclass
class GrowthResult:
    pos: np.ndarray
    parent: np.ndarray
    birth: np.ndarray
    state: np.ndarray
    depth: np.ndarray
    shed_year: np.ndarray
    shed_tips: np.ndarray            # tip count removed below this node (keeps the pipe model honest)
    exposure: np.ndarray             # final per-node exposure
    light_dir: np.ndarray
    attractors: np.ndarray
    attractor_state: np.ndarray
    age: float
    stats: dict = field(default_factory=dict)

    def to_npz(self, path):
        np.savez_compressed(path, **{k: v for k, v in self.__dict__.items() if isinstance(v, np.ndarray)},
                            age=self.age)

    @classmethod
    def from_npz(cls, path):
        z = np.load(path)
        kw = {k: z[k] for k in z.files if k != "age"}
        return cls(age=float(z["age"]), **kw)


class _Nodes:
    """Growable struct-of-arrays."""

    def __init__(self, cap=4096):
        self.n = 0
        self.pos = np.zeros((cap, 3))
        self.parent = np.full(cap, -1, np.int64)
        self.birth = np.zeros(cap, np.float32)
        self.state = np.zeros(cap, np.int8)
        self.depth = np.zeros(cap, np.int32)
        self.first_child = np.full(cap, -1, np.int64)
        self.nchild = np.zeros(cap, np.int32)
        self.shed_year = np.full(cap, -1.0, np.float32)
        self.shed_tips = np.zeros(cap, np.float32)
        self.vigor = np.ones(cap, np.float32)

    def _grow(self, need):
        cap = len(self.parent)
        if need <= cap:
            return
        new = max(need, cap * 2)
        for k in ("pos", "parent", "birth", "state", "depth", "first_child", "nchild", "shed_year", "shed_tips", "vigor"):
            a = getattr(self, k)
            fill = -1 if k in ("parent", "first_child", "shed_year") else (1 if k == "vigor" else 0)
            b = np.full((new,) + a.shape[1:], fill, a.dtype)
            b[: self.n] = a[: self.n]
            setattr(self, k, b)

    def add(self, pos, parent, year):
        m = len(pos)
        self._grow(self.n + m)
        s = slice(self.n, self.n + m)
        self.pos[s] = pos
        self.parent[s] = parent
        self.birth[s] = year
        ids = np.arange(self.n, self.n + m)
        valid = parent >= 0
        self.depth[s] = np.where(valid, self.depth[np.maximum(parent, 0)] + 1, 0)
        self.vigor[s] = np.where(valid, self.vigor[np.maximum(parent, 0)], 1.0)
        pv = parent[valid]
        if len(pv):
            np.add.at(self.nchild, pv, 1)
            no_first = self.first_child[pv] < 0
            self.first_child[pv[no_first]] = ids[valid][no_first]
        self.n += m
        return ids


# ---------------------------------------------------------------------------------------------
def _max_extent(params, max_age):
    h = r = 0.0
    for y in np.linspace(0, max_age, 101):
        e = Envelope.at_age(params, age_state(params, y))
        h, r = max(h, e.height), max(r, e.radius)
    return h, r


def seed_attractors(params: dict, seed: int):
    """Seed attractors once in the largest envelope the tree will ever have.

    Each attractor gets an activation year — the first year the growing envelope
    contains it. Density is constant, so the attractor field of a young tree is a
    strict subset of the old tree's. That subset property is what keeps ages coherent.
    """
    meta = params["meta"]
    H, R = _max_extent(params, meta["max_age"])
    ref_env = Envelope.at_age(params, age_state(params, meta["reference_age"]))
    density = params["growth"]["attractor_count"] / max(ref_env.volume(), 1e-9)
    cyl_vol = math.pi * R * R * H
    count = int(round(density * cyl_vol))
    g = rng(seed, 1)
    rad = R * np.sqrt(g.random(count))
    th = 2 * np.pi * g.random(count)
    y = H * g.random(count)
    pts = np.stack([rad * np.cos(th), y, rad * np.sin(th)], 1)

    years = np.arange(0, int(math.ceil(meta["max_age"])) + 1)
    act = np.full(count, np.inf)
    for yr in years:
        pending = ~np.isfinite(act)
        if not pending.any():
            break
        e = Envelope.at_age(params, age_state(params, float(yr)))
        hit = pending.copy()
        hit[pending] = e.inside(pts[pending])
        act[hit] = yr
    keep = np.isfinite(act)
    return pts[keep], act[keep], (H, R)


# ---------------------------------------------------------------------------------------------
def grow(params: dict, scene: Scene, seed: int, age: float, progress=None) -> GrowthResult:
    G, L, A = params["growth"], params["light"], params["architecture"]
    step = G["step_length"]
    infl, kill = G["influence_radius"] * step, G["kill_radius"] * step
    ipy = G["iterations_per_year"]

    att, act_year, (Hmax, Rmax) = seed_attractors(params, seed)
    a_state = np.zeros(len(att), np.int8)
    a_weight = np.ones(len(att), np.float32)

    nodes = _Nodes()
    nodes.add(np.zeros((1, 3)), np.array([-1]), 0.0)

    # light grid bounds: tree's lifetime extent plus neighbours, clipped to a sane margin
    lo = np.array([-Rmax * 1.3, 0.0, -Rmax * 1.3])
    hi = np.array([Rmax * 1.3, Hmax * 1.15, Rmax * 1.3])
    if scene.neighbours:
        nlo, nhi = neighbour_bounds(scene.neighbours, 1.0)
        clip_lo = np.array([-Rmax * 2.5, 0.0, -Rmax * 2.5])
        clip_hi = np.array([Rmax * 2.5, Hmax * 1.6, Rmax * 2.5])
        lo = np.maximum(np.minimum(lo, nlo), clip_lo)
        hi = np.minimum(np.maximum(hi, nhi), clip_hi)
        lo[1] = 0.0

    total_iters = int(round(age * ipy))
    epoch_iters = max(1, int(round(L["epoch_years"] * ipy)))
    ang_mean, ang_var = A["branch_angle"]["mean"], A["branch_angle"]["var"]
    stats = {"iterations": total_iters, "epochs": 0, "shed_nodes": 0, "bridged": 0, "reiterations": 0}
    exposure_tip = None

    for it in range(1, total_iters + 1):
        year = it / ipy
        st = age_state(params, year)
        a_state[(a_state == A_WAITING) & (act_year <= year)] = A_ACTIVE

        # ---- light epoch: rebuild field, weight attractors, shed, reiterate ------------------
        if it % epoch_iters == 1 % epoch_iters:
            stats["epochs"] += 1
            # space already occupied by a neighbour's crown is not available to this tree
            occupied = np.zeros(len(att), bool)
            for nb in scene.neighbours:
                occupied |= _inside(nb, att, st.height_mult if nb.age_scaled else 1.0)
            field_ = _build_field(params, scene, nodes, lo, hi, st.height_mult)
            a_state[occupied & (a_state != A_CONSUMED)] = A_DORMANT
            live = np.flatnonzero(((a_state == A_ACTIVE) | (a_state == A_DORMANT)) & ~occupied)
            if len(live):
                e, _ = field_.sample(att[live])
                # light weights attractors; it does not switch them off (that made limbs trace
                # the lit shell of the envelope). Hollowing is the shed pass's job.
                a_weight[live] = np.maximum(e, 0.02)
                a_state[live] = A_ACTIVE
            nodes.vigor[: nodes.n] = _vigor(nodes, field_, st.apical_dominance)
            stats["shed_nodes"] += _shed(nodes, field_, st.shed_threshold, year, L["epoch_years"], L["stub_nodes"])
            if st.reiteration > 0 and hash01(it, seed, 11) < st.reiteration * 0.5:
                if _dieback(nodes, step):
                    stats["reiterations"] += 1

        active = np.flatnonzero(a_state == A_ACTIVE)
        if len(active) == 0:
            continue
        growing = np.flatnonzero(nodes.state[: nodes.n] == ALIVE)
        if len(growing) == 0:
            break
        tree = cKDTree(nodes.pos[growing])
        d, j = tree.query(att[active], k=1, distance_upper_bound=infl)

        killed = d <= kill
        a_state[active[killed]] = A_CONSUMED
        infl_mask = np.isfinite(d) & ~killed

        if not infl_mask.any():
            # Nothing within reach: bridge the nearest node toward the nearest lit attractor.
            dd, jj = tree.query(att[active], k=1)
            a = int(np.argmin(dd))
            _bridge(nodes, growing[jj[a]], att[active[a]], infl, step, st.apical_dominance, year)
            stats["bridged"] += 1
            continue

        ai, ni = active[infl_mask], j[infl_mask]
        vec = normalize(att[ai] - nodes.pos[growing[ni]]) * a_weight[ai, None]
        m = len(growing)
        acc = np.stack([np.bincount(ni, vec[:, k], minlength=m) for k in range(3)], 1)
        cnt = np.bincount(ni, a_weight[ai], minlength=m)
        which = np.flatnonzero(cnt > 0)
        src = growing[which]
        mean = acc[which] / cnt[which, None]
        # resource allocation gate: low-vigor buds grow only some iterations
        go = hash01(src * 7919 + it, seed, 13) < nodes.vigor[src]
        src, mean = src[go], mean[go]
        coherent = np.linalg.norm(mean, axis=1) > 0.05
        src, mean = src[coherent], mean[coherent]
        if len(src) == 0:
            continue

        dirs = normalize(mean) + st.apical_dominance * 0.5 * UP
        # tip extension keeps some memory of its heading (laterals are free to diverge)
        ext = (nodes.first_child[src] < 0) & (nodes.parent[src] >= 0)
        if G["straightness"] > 0 and ext.any():
            heading = normalize(nodes.pos[src[ext]] - nodes.pos[nodes.parent[src[ext]]])
            dirs[ext] += G["straightness"] * 2.0 * heading
        if G["trunk_bias"] > 0:
            lead = _leader_tip(nodes)
            dirs[src == lead] += G["trunk_bias"] * 2.0 * UP
        dirs = normalize(dirs)
        dirs[:, 1] = np.maximum(dirs[:, 1], -0.2)       # gravitropism: wood does not grow downhill
        dirs = normalize(dirs)
        dirs = _enforce_branch_angle(nodes, src, dirs, ang_mean, ang_var, seed)

        new = nodes.pos[src] + dirs * step
        dn, _ = tree.query(new, k=1)
        ok = dn > 0.3 * step            # no near-duplicate nodes
        if ok.any():
            nodes.add(new[ok], src[ok], year)
        if progress and it % 20 == 0:
            progress(it / max(total_iters, 1), f"year {year:.0f}: {nodes.n} nodes")

    # ---- final light sample for primvars ------------------------------------------------------
    st = age_state(params, age)
    field_ = _build_field(params, scene, nodes, lo, hi, st.height_mult)
    n = nodes.n
    expo, ldir = field_.sample(nodes.pos[:n])
    stats.update(nodes=n, alive=int((nodes.state[:n] == ALIVE).sum()), attractors=len(att),
                 consumed=int((a_state == A_CONSUMED).sum()), grid=list(field_.density.shape))
    return GrowthResult(
        pos=nodes.pos[:n].copy(), parent=nodes.parent[:n].copy(), birth=nodes.birth[:n].copy(),
        state=nodes.state[:n].copy(), depth=nodes.depth[:n].copy(), shed_year=nodes.shed_year[:n].copy(),
        shed_tips=nodes.shed_tips[:n].copy(), exposure=expo, light_dir=ldir,
        attractors=att, attractor_state=a_state, age=float(age), stats=stats)


# ---------------------------------------------------------------------------------------------
def _tips(nodes: _Nodes):
    n = nodes.n
    alive = nodes.state[:n] == ALIVE
    # a tip is an alive node with no alive children
    alive_child = np.zeros(n, np.int32)
    ch = np.flatnonzero(alive[1:]) + 1
    np.add.at(alive_child, nodes.parent[ch], 1)
    return np.flatnonzero(alive & (alive_child == 0))


def _build_field(params, scene, nodes, lo, hi, hscale):
    L = params["light"]
    tips = _tips(nodes)
    return LightField.build(lo, hi, L["grid_resolution"], scene.neighbours, nodes.pos[tips],
                            L["foliage_density"], L["ray_count"], L["sky_bias"], hscale)


def _shed(nodes: _Nodes, field_: LightField, thr: float, year: float, grace: float, stub_nodes: int) -> int:
    n = nodes.n
    tips = _tips(nodes)
    if len(tips) < 2:
        return 0
    e, _ = field_.sample(nodes.pos[tips])
    best = np.full(n, -1.0, np.float32)
    best[tips] = e
    tipcount = np.zeros(n, np.float32)
    tipcount[tips] = 1.0
    alive = nodes.state[:n] != SHED
    levels = depth_levels(nodes.depth[:n])
    for lvl in reversed(levels[1:]):
        lvl = lvl[alive[lvl]]
        np.maximum.at(best, nodes.parent[lvl], best[lvl])
        np.add.at(tipcount, nodes.parent[lvl], tipcount[lvl])

    # protect the path to the best-lit tip so the tree always survives
    protected = np.zeros(n, bool)
    k = int(tips[np.argmax(best[tips])])
    while k >= 0:
        protected[k] = True
        k = int(nodes.parent[k])

    par = nodes.parent[:n]
    cand = alive & (nodes.state[:n] == ALIVE) & (best < thr) & ~protected
    cand[0] = False
    # a limb must have lived one epoch before it can be shed (base age, not tip age:
    # otherwise a limb that keeps creeping into the shade is immortal)
    base = cand & ~cand[np.maximum(par, 0)] & (nodes.birth[:n] <= year - grace)
    mark = base.copy()
    for lvl in levels[1:]:
        mark[lvl] |= mark[par[lvl]] & cand[lvl]
    bases = np.flatnonzero(base)
    if len(bases) == 0:
        return 0

    # children lists
    order = np.argsort(par[1:], kind="stable") + 1
    starts = np.searchsorted(par[order], np.arange(n))
    ends = np.searchsorted(par[order], np.arange(n), side="right")

    stub = np.zeros(n, bool)
    for b in bases:
        nodes.shed_tips[par[b]] += tipcount[b]
        if stub_nodes > 0 and tipcount[b] >= 8:        # only real limbs leave stubs
            k = b
            for _ in range(stub_nodes):
                stub[k] = True
                kids = order[starts[k]:ends[k]]
                kids = kids[mark[kids]]
                if len(kids) == 0:
                    break
                k = int(kids[np.argmax(tipcount[kids])])
    nodes.state[:n][mark & stub] = DEAD
    shed = mark & ~stub
    nodes.state[:n][shed] = SHED
    nodes.shed_year[:n][shed] = year
    shed_count = int(shed.sum())
    return shed_count


def _vigor(nodes: _Nodes, field_: LightField, apical: float) -> np.ndarray:
    """Borchert-Honda style allocation (after Palubicki et al. 2009), simplified.

    Light collected at tips flows to the root; the root's resource is handed back
    out, with the continuation child favoured by lambda (apical control). A bud's
    vigor is its share relative to the average tip; it becomes its per-iteration
    growth probability. Shaded laterals therefore grow slowly, get overtaken,
    fall into deeper shade and are shed — which is what makes a forest bole.
    """
    n = nodes.n
    tips = _tips(nodes)
    out = np.ones(n, np.float32)
    if len(tips) < 2:
        return out
    alive = nodes.state[:n] == ALIVE
    e, _ = field_.sample(nodes.pos[tips])
    Q = np.zeros(n)
    Q[tips] = e + 1e-4
    ntip = np.zeros(n)
    ntip[tips] = 1
    levels = depth_levels(nodes.depth[:n])
    par = nodes.parent[:n]
    for lvl in reversed(levels[1:]):
        lvl = lvl[alive[lvl]]
        np.add.at(Q, par[lvl], Q[lvl])
        np.add.at(ntip, par[lvl], ntip[lvl])
    lam = 0.5 + 0.35 * min(max(apical, 0.0), 1.0)
    idx = np.arange(n)
    main = np.zeros(n, bool)
    main[1:] = nodes.first_child[par[1:]] == idx[1:]
    main &= alive
    s_main = np.zeros(n)
    s_lat = np.zeros(n)
    ch = np.flatnonzero(alive[1:]) + 1
    np.add.at(s_main, par[ch], np.where(main[ch], Q[ch], 0.0))
    np.add.at(s_lat, par[ch], np.where(main[ch], 0.0, Q[ch]))
    V = np.zeros(n)
    V[0] = 1.0
    for lvl in levels[1:]:
        lvl = lvl[alive[lvl]]
        p = par[lvl]
        denom = lam * s_main[p] + (1 - lam) * s_lat[p]
        w = np.where(main[lvl], lam, 1 - lam) * Q[lvl] / np.maximum(denom, 1e-12)
        V[lvl] = V[p] * w
    per_tip = V / np.maximum(ntip, 1) * len(tips)
    out = np.clip(per_tip, 0.2, 1.0).astype(np.float32)
    out[~alive] = 0
    return out


def _leader_tip(nodes: _Nodes) -> int:
    k = 0
    while nodes.first_child[k] >= 0 and nodes.state[nodes.first_child[k]] != SHED:
        k = int(nodes.first_child[k])
    return k


def _dieback(nodes: _Nodes, step: float) -> bool:
    """Reiteration: the highest growing tip dies back ~1 m, co-dominant stems take over."""
    tips = _tips(nodes)
    if len(tips) < 4:
        return False
    k = int(tips[np.argmax(nodes.pos[tips, 1])])
    for _ in range(max(1, int(round(1.0 / step)))):
        if k <= 0:
            break
        nodes.state[k] = DEAD
        k = int(nodes.parent[k])
    return True


def _bridge(nodes, start, target, infl, step, apical, year):
    k = int(start)
    for _ in range(400):
        p = nodes.pos[k]
        to = target - p
        dist = np.linalg.norm(to)
        if dist < infl * 0.8:
            break
        d = normalize(to / dist + apical * 0.5 * UP)
        k = int(nodes.add((p + d * step)[None], np.array([k]), year)[0])


def _enforce_branch_angle(nodes, src, dirs, mean_deg, var_deg, seed):
    """Laterals diverge from their sibling continuation by at least a sampled branch angle."""
    has = nodes.first_child[src] >= 0
    if not has.any():
        return dirs
    idx = np.flatnonzero(has)
    c = nodes.first_child[src[idx]]
    cdir = normalize(nodes.pos[c] - nodes.pos[src[idx]])
    d = dirs[idx]
    u = hash01(src[idx], seed, 5) * 2 - 1
    target = np.radians(np.clip(mean_deg + var_deg * u, 5, 175))
    cosang = np.clip((d * cdir).sum(1), -1, 1)
    need = np.arccos(cosang) < target
    if not need.any():
        return dirs
    perp = d - cosang[:, None] * cdir
    pn = np.linalg.norm(perp, axis=1)
    # degenerate (same direction): pick a deterministic perpendicular
    alt = np.cross(cdir, np.array([0.0, 0.0, 1.0]))
    alt = np.where(np.linalg.norm(alt, axis=1, keepdims=True) < 1e-3, np.cross(cdir, [1.0, 0, 0]), alt)
    perp = np.where(pn[:, None] < 1e-6, alt, perp)
    perp = normalize(perp)
    newd = np.cos(target)[:, None] * cdir + np.sin(target)[:, None] * perp
    out = dirs.copy()
    out[idx[need]] = newd[need]
    return out
