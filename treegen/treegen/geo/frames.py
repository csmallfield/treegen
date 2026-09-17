"""Parallel-transport frames over the skeleton (Hanson & Ma), never Frenet.

THE FRAME CONVENTION IS PART OF THE USD CONTRACT. A downstream rebind must
rebuild frames exactly like this from the (possibly deformed) skeleton:

  1. Tangent T[i]: central difference normalize(P[i+1] - P[i-1]); forward / backward
     difference at the ends. Curves are processed parents-first.
  2. Initial normal of the root curve: world +X projected onto the plane normal to
     T[0] (world +Z if T[0] is within ~8 degrees of X).
  3. Initial normal of any other curve: the parent curve's normal at
     primvars:parentPointIndex, projected onto the plane normal to the child's T[0]
     (same world-axis fallback if degenerate).
  4. Transport: N[i] = R(T[i-1] -> T[i]) N[i-1], R the minimal rotation; then
     re-orthogonalise against T[i]. B[i] = T[i] x N[i].

A geometry point stores skelPointIndex = k and skelLocalOffset = (o_T, o_N, o_B),
and is reconstructed as  P[k] + o_T T[k] + o_N N[k] + o_B B[k].
"""
from __future__ import annotations

import numpy as np


def _tangents(points, offsets):
    T = np.zeros_like(points)
    for c in range(len(offsets) - 1):
        a, b = offsets[c], offsets[c + 1]
        p = points[a:b]
        if b - a < 2:
            T[a] = (0, 1, 0)
            continue
        t = np.empty_like(p)
        t[1:-1] = p[2:] - p[:-2]
        t[0] = p[1] - p[0]
        t[-1] = p[-1] - p[-2]
        T[a:b] = t
    n = np.linalg.norm(T, axis=1, keepdims=True)
    return np.where(n > 1e-12, T / np.maximum(n, 1e-12), np.array([0.0, 1.0, 0.0]))


def _project_normal(seed_n, t):
    n = seed_n - (seed_n * t).sum(-1, keepdims=True) * t
    ln = np.linalg.norm(n, axis=-1, keepdims=True)
    bad = ln[..., 0] < 0.14
    if np.any(bad):
        fx = np.array([1.0, 0, 0])
        fz = np.array([0, 0, 1.0])
        alt_seed = np.where(np.abs(t[..., :1]) > 0.99, fz, fx)
        alt = alt_seed - (alt_seed * t).sum(-1, keepdims=True) * t
        alt2 = fz - (fz * t).sum(-1, keepdims=True) * t
        alt = np.where(np.linalg.norm(alt, axis=-1, keepdims=True) < 0.14, alt2, alt)
        n = np.where(bad[..., None], alt, n)
        ln = np.linalg.norm(n, axis=-1, keepdims=True)
    return n / np.maximum(ln, 1e-12)


def _min_rotate(v, t0, t1):
    """Rotate vectors v by the minimal rotation taking unit t0 to unit t1 (row-wise)."""
    axis = np.cross(t0, t1)
    s = np.linalg.norm(axis, axis=1, keepdims=True)
    c = (t0 * t1).sum(1, keepdims=True)
    k = axis / np.maximum(s, 1e-12)
    rot = v * c + np.cross(k, v) * s + k * (k * v).sum(1, keepdims=True) * (1 - c)
    return np.where(s > 1e-9, rot, v)


def parallel_transport(points, offsets, parent_curve, parent_point):
    """Return (T, N, B) for every skeleton point, following the documented convention."""
    offsets = np.asarray(offsets)
    C = len(offsets) - 1
    T = _tangents(points, offsets)
    N = np.zeros_like(points)
    counts = np.diff(offsets)

    depth = np.zeros(C, np.int64)
    for c in range(C):                      # parents precede children in curve order
        if parent_curve[c] >= 0:
            depth[c] = depth[parent_curve[c]] + 1

    for d in range(int(depth.max()) + 1 if C else 0):
        curves = np.flatnonzero(depth == d)
        starts = offsets[curves]
        t0 = T[starts]
        if d == 0:
            seed = np.tile([1.0, 0.0, 0.0], (len(curves), 1))
        else:
            seed = N[parent_point[curves]]
        N[starts] = _project_normal(seed, t0)
        maxlen = counts[curves].max()
        for i in range(1, maxlen):
            live = counts[curves] > i
            cur = starts[live] + i
            prev = cur - 1
            n = _min_rotate(N[prev], T[prev], T[cur])
            N[cur] = _project_normal(n, T[cur])
    B = np.cross(T, N)
    return T, N, B
