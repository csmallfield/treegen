"""Stage pipeline with narrow cache invalidation (design doc §8).

    growth    <- meta, envelope, growth, light, architecture*, age_response, seed, age, scene
    skeleton  <- growth + radii, refinement
    geometry  <- skeleton + geometry, materials, architecture.min_radius_for_mesh, lod

(*architecture.branch_angle affects growth; max_order affects extraction. Both
are in the growth key, which is conservative.)

Growth results are also cached on disk as .npz, so re-running the CLI with only
a geometry change skips the simulation entirely.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .core import radii as radii_mod
from .core.age import age_state
from .core.growth import GrowthResult, grow
from .core.refine import refine
from .core.skeleton import Branches, build_graph, extract_branches
from .schema import STAGE_GROUPS, Scene, group_hash, scene_key

CACHE_VERSION = 1


@dataclass
class Skeleton:
    """The authoritative skeleton. One linear curve per branch chain."""
    points: np.ndarray          # (P, 3)
    counts: np.ndarray          # (C,)
    offsets: np.ndarray         # (C+1,)
    order: np.ndarray           # (C,)
    parent_curve: np.ndarray    # (C,)
    parent_point: np.ndarray    # (C,)
    path: list                  # (C,)
    radius: np.ndarray          # (P,)
    arc_length: np.ndarray      # (P,)
    downstream_mass: np.ndarray # (P,)
    light_exposure: np.ndarray  # (P,)
    birth_age: np.ndarray       # (P,)
    dead: np.ndarray            # (P,) bool
    node: np.ndarray            # (P,) node-graph index
    height: float
    age: float

    @property
    def curve_count(self):
        return len(self.counts)


def assemble_skeleton(g: GrowthResult, params: dict, seed: int) -> Skeleton:
    st = age_state(params, g.age)
    ng = build_graph(g)
    br: Branches = extract_branches(ng, params["architecture"]["max_order"], st.reiteration)
    r = radii_mod.pipe_radii(ng, params)
    r = radii_mod.shape_radii(ng, br, r, params, st)
    refine(ng, r, params, seed)                       # moves ng.pos
    mass = radii_mod.downstream_mass(ng, r)

    pts = ng.pos[br.nodes]
    counts = br.counts()
    arc = np.zeros(len(pts))
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    for c in range(br.count):
        a, b = br.offsets[c], br.offsets[c + 1]
        start = 0.0
        if br.parent_point[c] >= 0:
            start = arc[br.parent_point[c]]          # arc length continues from the attach point
        arc[a] = start
        arc[a + 1:b] = start + np.cumsum(seg[a:b - 1])

    return Skeleton(
        points=pts, counts=counts, offsets=br.offsets, order=br.order, parent_curve=br.parent_curve,
        parent_point=br.parent_point, path=br.path, radius=r[br.nodes], arc_length=arc,
        downstream_mass=mass[br.nodes], light_exposure=ng.exposure[br.nodes],
        birth_age=ng.birth[br.nodes].astype(np.float64), dead=ng.dead[br.nodes], node=br.nodes,
        height=float(ng.pos[:, 1].max()), age=g.age)


class Pipeline:
    def __init__(self, cache_dir: str | Path | None = None, verbose: bool = False):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.verbose = verbose
        self._mem: dict = {}

    def _log(self, msg):
        if self.verbose:
            print(f"\r\033[K  {msg}", flush=True)

    def growth(self, params: dict, scene: Scene, seed: int, age: float, progress=None) -> GrowthResult:
        key = ("growth", CACHE_VERSION, group_hash(params, STAGE_GROUPS["skeleton"],
                                                    [seed, age, scene_key(scene)]))
        if key in self._mem:
            self._log("growth: memory cache hit")
            return self._mem[key]
        path = self.cache_dir / f"growth-{key[2]}.npz" if self.cache_dir else None
        if path and path.exists():
            self._log(f"growth: disk cache hit ({path.name})")
            res = GrowthResult.from_npz(path)
        else:
            t = time.perf_counter()
            res = grow(params, scene, seed, age, progress=progress)
            self._log(f"growth: {time.perf_counter() - t:.1f}s  {res.stats}")
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                res.to_npz(path)
        self._mem = {k: v for k, v in self._mem.items() if k[0] != "growth"}
        self._mem[key] = res
        return res

    def skeleton(self, params, scene, seed, age, progress=None) -> tuple[GrowthResult, Skeleton]:
        g = self.growth(params, scene, seed, age, progress)
        key = ("skeleton", id(g), group_hash(params, STAGE_GROUPS["radii"] + ["architecture"], seed))
        if key not in self._mem:
            t = time.perf_counter()
            self._mem = {k: v for k, v in self._mem.items() if k[0] != "skeleton"}
            self._mem[key] = assemble_skeleton(g, params, seed)
            self._log(f"skeleton: {time.perf_counter() - t:.1f}s  {self._mem[key].curve_count} curves")
        return g, self._mem[key]

    def geometry(self, params, scene, seed, age, lod=1.0, progress=None):
        from .geo.sweep import build_geometry
        g, sk = self.skeleton(params, scene, seed, age, progress)
        key = ("geometry", id(sk), group_hash(params, STAGE_GROUPS["geometry"],
                                              [lod, params["architecture"]["min_radius_for_mesh"]]))
        if key not in self._mem:
            t = time.perf_counter()
            self._mem = {k: v for k, v in self._mem.items() if k[0] != "geometry"}
            self._mem[key] = build_geometry(sk, params, lod)
            self._log(f"geometry: {time.perf_counter() - t:.1f}s")
        return g, sk, self._mem[key]
