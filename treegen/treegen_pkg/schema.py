"""Parameter schema — the single source of truth.

Every species parameter is declared once here with its default, range, unit,
group and a one-line doc. The TOML loader validates against it, and the Phase-1
viewer panel (M6) is meant to be generated from it, so adding a parameter means
editing this file and nothing else.

Curves (``age_response``, ``envelope.profile``) are lists of [x, y] pairs.
"""
from __future__ import annotations

import copy
import hashlib
import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Param:
    group: str
    name: str
    default: Any
    kind: str = "float"          # float | int | str | curve | range | dict | list
    lo: float | None = None
    hi: float | None = None
    unit: str = ""
    doc: str = ""
    choices: tuple = ()


P = Param
SCHEMA: list[Param] = [
    # meta ------------------------------------------------------------------
    P("meta", "name", "unnamed", "str", doc="Species identifier"),
    P("meta", "reference_age", 80.0, lo=1, hi=500, unit="years",
      doc="Age at which the base values in this file hold"),
    P("meta", "max_age", 200.0, lo=1, hi=1000, unit="years",
      doc="Age that maps to normalised age 1.0 on every age_response curve"),
    # envelope ----------------------------------------------------------------
    P("envelope", "shape", "profile", "str", choices=("profile",),
      doc="Envelope type (mesh / implicit are Phase-2)"),
    P("envelope", "profile", [[0, 0], [0.3, 0.9], [0.7, 1.0], [1, 0.2]], "curve",
      doc="Radius (0-1) as a function of normalised height (0-1)"),
    P("envelope", "height", 22.0, lo=0.5, hi=120, unit="m", doc="Crown top height at reference age"),
    P("envelope", "spread_ratio", 1.15, lo=0.05, hi=4, doc="Crown diameter / height"),
    P("envelope", "clear_height", 0.0, lo=0, hi=0.9,
      doc="Fraction of height below which no attractors are seeded (0 lets light decide)"),
    # growth ----------------------------------------------------------------
    P("growth", "attractor_count", 8000, "int", lo=100, hi=500_000,
      doc="Attractors inside the envelope at reference age (density is held constant across ages)"),
    P("growth", "influence_radius", 10.0, lo=1.0, hi=100, unit="x step",
      doc="Attractor influence radius, relative to step_length"),
    P("growth", "kill_radius", 2.0, lo=0.5, hi=20, unit="x step",
      doc="Attractor kill radius, relative to step_length"),
    P("growth", "step_length", 0.35, lo=0.02, hi=5, unit="m", doc="Node spacing"),
    P("growth", "apical_dominance", 0.75, lo=0, hi=2, doc="Vertical bias on growth direction"),
    P("growth", "iterations_per_year", 2, "int", lo=1, hi=50,
      doc="Colonization iterations run per simulated year"),
    P("growth", "straightness", 0.3, lo=0, hi=3, doc="Heading memory on tip extension; stops limbs tracing the lit shell"),
    P("growth", "trunk_bias", 0.0, lo=0, hi=1,
      doc="Extra vertical bias on the leader only (conifers)"),
    # architecture ----------------------------------------------------------
    P("architecture", "branch_angle", {"mean": 48.0, "var": 12.0}, "dict", unit="deg",
      doc="Minimum divergence between a lateral and its sibling continuation"),
    P("architecture", "max_order", 4, "int", lo=0, hi=12, doc="Branches above this order are pruned at extraction"),
    P("architecture", "min_radius_for_mesh", 0.04, lo=0, hi=2, unit="m",
      doc="Mesh-order branches thinner than this at their base become curves"),
    # radii -----------------------------------------------------------------
    P("radii", "pipe_exponent", 2.3, lo=1.5, hi=3.0, doc="r_parent^n = sum r_child^n"),
    P("radii", "tip_radius", 0.01, lo=0.0005, hi=0.1, unit="m", doc="Radius of every terminal node"),
    P("radii", "trunk_flare", {"amount": 0.4, "height": 0.08}, "dict",
      doc="Base flare: amount (x radius) over height (fraction of tree height)"),
    P("radii", "junction_swell", 1.15, lo=1.0, hi=2.0, doc="Radius multiplier at bifurcations"),
    # refinement ------------------------------------------------------------
    P("refinement", "smoothing", 0.5, lo=0, hi=1, doc="Chain smoothing before bending (removes colonization zig-zag)"),
    P("refinement", "gravity_droop", 0.45, lo=0, hi=3, doc="Sag scaled by downstreamMass"),
    P("refinement", "phototropism", 0.2, lo=0, hi=2, doc="Bend toward the local light direction"),
    P("refinement", "noise", {"amplitude": 0.08, "frequency": 2.0}, "dict",
      doc="Low-amplitude per-node direction noise"),
    # light -----------------------------------------------------------------
    P("light", "shed_threshold", 0.15, lo=0, hi=1, doc="Branches whose best tip exposure falls below this are shed"),
    P("light", "grid_resolution", 64, "int", lo=8, hi=256, doc="Voxels along the longest axis"),
    P("light", "foliage_density", 2.0, lo=0, hi=20,
      doc="Optical depth contributed per growing tip, per cubic metre of voxel"),
    P("light", "sky_bias", 1.5, lo=0, hi=8, doc="Exponent on cos(zenith): higher = overhead light dominates"),
    P("light", "ray_count", 24, "int", lo=4, hi=128, doc="Hemisphere directions per exposure sample"),
    P("light", "epoch_years", 5.0, lo=0.5, hi=50, unit="years", doc="Light field rebuild + shed interval"),
    P("light", "stub_nodes", 2, "int", lo=0, hi=10, doc="Nodes kept as dead stubs when a branch is shed"),
    # geometry --------------------------------------------------------------
    P("geometry", "mesh_orders", [0, 1], "list", doc="Branch orders swept as meshes; others become BasisCurves"),
    P("geometry", "radial_segments", {"min": 6, "max": 24}, "dict", doc="Adaptive ring resolution"),
    # materials -------------------------------------------------------------
    P("materials", "order_ranges", [[0, 1, "bark_mature"], [2, 12, "bark_young"]], "list",
      doc="[min_order, max_order, material] triples"),
    # age_response ----------------------------------------------------------
    P("age_response", "height", [[0, 0.1], [0.3, 0.7], [1, 1.0]], "curve"),
    P("age_response", "trunk_radius", [[0, 0.05], [1, 1.0]], "curve"),
    P("age_response", "crown_flatten", [[0, 0.0], [1, 0.6]], "curve"),
    P("age_response", "apical_dominance", [[0, 1.0], [1, 0.3]], "curve"),
    P("age_response", "shed_threshold", [[0, 0.05], [1, 0.3]], "curve"),
    P("age_response", "reiteration", [[0, 0.0], [0.8, 0.4]], "curve"),
]

GROUPS = list(dict.fromkeys(p.group for p in SCHEMA))

# Which stage each group invalidates (see design doc §8 Caching).
STAGE_GROUPS = {
    "skeleton": ["meta", "envelope", "growth", "light", "architecture", "age_response"],
    "radii": ["radii", "refinement"],
    "geometry": ["geometry", "materials"],
}


class SchemaError(ValueError):
    pass


def defaults() -> dict:
    out: dict = {g: {} for g in GROUPS}
    for p in SCHEMA:
        out[p.group][p.name] = copy.deepcopy(p.default)
    return out


def _check(p: Param, v: Any, where: str) -> Any:
    if p.kind in ("float", "int"):
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise SchemaError(f"{where}: expected number, got {v!r}")
        v = int(v) if p.kind == "int" else float(v)
        if p.lo is not None and v < p.lo or p.hi is not None and v > p.hi:
            raise SchemaError(f"{where}: {v} outside [{p.lo}, {p.hi}]")
    elif p.kind == "str":
        if not isinstance(v, str):
            raise SchemaError(f"{where}: expected string")
        if p.choices and v not in p.choices:
            raise SchemaError(f"{where}: {v!r} not in {p.choices}")
    elif p.kind == "curve":
        try:
            pts = [(float(a), float(b)) for a, b in v]
        except Exception as e:  # noqa: BLE001
            raise SchemaError(f"{where}: curve must be [[x, y], ...]") from e
        if len(pts) < 1 or any(pts[i + 1][0] < pts[i][0] for i in range(len(pts) - 1)):
            raise SchemaError(f"{where}: curve x values must be ascending")
        v = [list(q) for q in pts]
    elif p.kind == "dict":
        if not isinstance(v, dict):
            raise SchemaError(f"{where}: expected table")
        merged = copy.deepcopy(p.default)
        unknown = set(v) - set(merged)
        if unknown:
            raise SchemaError(f"{where}: unknown keys {sorted(unknown)}")
        merged.update({k: float(x) for k, x in v.items()})
        v = merged
    return v


def validate(raw: dict, source: str = "<dict>") -> dict:
    by_key = {(p.group, p.name): p for p in SCHEMA}
    out = defaults()
    for group, table in raw.items():
        if group not in out:
            raise SchemaError(f"{source}: unknown group [{group}]")
        if not isinstance(table, dict):
            raise SchemaError(f"{source}: [{group}] must be a table")
        for name, v in table.items():
            p = by_key.get((group, name))
            if p is None:
                raise SchemaError(f"{source}: unknown parameter {group}.{name}")
            out[group][name] = _check(p, v, f"{source}: {group}.{name}")
    return out


def load_species(path: str | Path) -> dict:
    path = Path(path)
    with path.open("rb") as f:
        return validate(tomllib.load(f), str(path))


def group_hash(params: dict, groups: list[str], extra: Any = None) -> str:
    blob = json.dumps({"p": {g: params[g] for g in groups}, "x": extra}, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- scene
@dataclass
class Neighbour:
    kind: str                      # capsule | box | sphere
    a: tuple[float, float, float]  # capsule p0 / box centre / sphere centre
    b: tuple[float, float, float] = (0.0, 0.0, 0.0)  # capsule p1 / box half-size
    radius: float = 1.0
    density: float = 1.5           # optical depth per metre
    age_scaled: bool = False       # scale height with the tree's height curve (same-age stand)


@dataclass
class Scene:
    name: str = "open_field"
    neighbours: list[Neighbour] = field(default_factory=list)


def load_scene(path: str | Path | None) -> Scene:
    if path is None:
        return Scene()
    path = Path(path)
    with path.open("rb") as f:
        raw = tomllib.load(f)
    sc = Scene(name=raw.get("name", path.stem))
    for i, n in enumerate(raw.get("neighbour", [])):
        kind = n.get("type")
        where = f"{path}: neighbour[{i}]"
        common = dict(density=float(n.get("density", 1.5)), age_scaled=bool(n.get("age_scaled", False)))
        if kind == "capsule":
            sc.neighbours.append(Neighbour("capsule", tuple(n["p0"]), tuple(n["p1"]), float(n["radius"]), **common))
        elif kind == "sphere":
            sc.neighbours.append(Neighbour("sphere", tuple(n["center"]), radius=float(n["radius"]), **common))
        elif kind == "box":
            sc.neighbours.append(Neighbour("box", tuple(n["center"]), tuple(v / 2 for v in n["size"]), **common))
        else:
            raise SchemaError(f"{where}: type must be capsule | box | sphere")
    return sc


def scene_key(scene: Scene) -> Any:
    return [(n.kind, n.a, n.b, n.radius, n.density, n.age_scaled) for n in scene.neighbours]
