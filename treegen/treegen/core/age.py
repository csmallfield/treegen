"""Age model.

Normalised age n = age / max_age (clamped to [0, 1]) indexes every age_response
curve. Base values in the species file hold at reference_age, so curves that
modify a base value are applied as a *ratio* against the curve's value at the
reference age: multiplier(age) = curve(n) / curve(n_ref). That way the species
file is literally true at reference_age, and the curve only describes departure.

crown_flatten and reiteration have no base value; they are used as deltas /
absolutes respectively.
"""
from __future__ import annotations

from dataclasses import dataclass

from .util import curve_eval

_RATIO = ("height", "trunk_radius", "apical_dominance", "shed_threshold")


@dataclass(frozen=True)
class AgeState:
    age: float
    n: float
    height_mult: float
    trunk_radius_mult: float
    apical_dominance: float
    shed_threshold: float
    crown_flatten_delta: float
    reiteration: float


def age_state(params: dict, age: float) -> AgeState:
    meta, ar = params["meta"], params["age_response"]
    n = min(max(age / meta["max_age"], 0.0), 1.0)
    n_ref = min(max(meta["reference_age"] / meta["max_age"], 0.0), 1.0)

    def ratio(key):
        ref = float(curve_eval(ar[key], n_ref))
        return float(curve_eval(ar[key], n)) / ref if abs(ref) > 1e-9 else float(curve_eval(ar[key], n))

    r = {k: ratio(k) for k in _RATIO}
    return AgeState(
        age=float(age),
        n=n,
        height_mult=r["height"],
        trunk_radius_mult=r["trunk_radius"],
        apical_dominance=min(params["growth"]["apical_dominance"] * r["apical_dominance"], 2.0),
        shed_threshold=min(params["light"]["shed_threshold"] * r["shed_threshold"], 0.95),
        crown_flatten_delta=float(curve_eval(ar["crown_flatten"], n) - curve_eval(ar["crown_flatten"], n_ref)),
        reiteration=float(curve_eval(ar["reiteration"], n)),
    )
