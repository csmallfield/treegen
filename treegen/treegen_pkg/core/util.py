"""Small deterministic helpers shared by the core."""
from __future__ import annotations

import numpy as np

UP = np.array([0.0, 1.0, 0.0])
_M1 = np.uint64(0xBF58476D1CE4E5B9)
_M2 = np.uint64(0x94D049BB133111EB)


def rng(seed: int, stream: int) -> np.random.Generator:
    """Independent, stable random stream per (seed, purpose)."""
    return np.random.default_rng(np.random.SeedSequence([int(seed) & 0xFFFFFFFF, stream]))


def hash01(idx, seed: int, stream: int) -> np.ndarray:
    """Stateless integer hash -> [0, 1). Same index, same value, on every platform."""
    with np.errstate(over="ignore"):
        z = np.asarray(idx, dtype=np.uint64) + np.uint64((int(seed) * 0x9E3779B1 + stream * 0x85EBCA77) & 0xFFFFFFFFFFFFFFFF)
        z = (z ^ (z >> np.uint64(30))) * _M1
        z = (z ^ (z >> np.uint64(27))) * _M2
        z = z ^ (z >> np.uint64(31))
    return (z >> np.uint64(11)).astype(np.float64) / float(1 << 53)


def curve_eval(curve, x):
    """Piecewise-linear evaluation, clamped at the ends."""
    c = np.asarray(curve, dtype=float)
    return np.interp(x, c[:, 0], c[:, 1])


def normalize(v, eps=1e-12):
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(n, eps)


def depth_levels(depth: np.ndarray):
    """Group node indices by depth (ascending). Used for vectorised tree passes."""
    order = np.argsort(depth, kind="stable")
    d = depth[order]
    cuts = np.flatnonzero(np.diff(d)) + 1
    return np.split(order, cuts)
