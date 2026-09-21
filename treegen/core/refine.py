"""Refinement passes, run in order: gravity droop, phototropism, noise.

(Trunk flare and junction swell live in radii.shape_radii — same cache group.)
All passes edit per-segment vectors and rebuild positions root-first, so a bend
carries its whole subtree with it.
"""
from __future__ import annotations

import numpy as np

from .radii import WOOD_DENSITY
from .skeleton import NodeGraph
from .util import normalize, rng

DOWN = np.array([0.0, -1.0, 0.0])


def _segments(ng):
    v = np.zeros_like(ng.pos)
    v[1:] = ng.pos[1:] - ng.pos[ng.parent[1:]]
    return v


def _rebuild(ng, v):
    p = ng.pos.copy()
    for lvl in ng.levels()[1:]:
        p[lvl] = p[ng.parent[lvl]] + v[lvl]
    ng.pos[:] = p


def _rotate_toward(v, target, angle):
    """Rotate each v toward target direction by angle (Rodrigues), never past it."""
    ln = np.linalg.norm(v, axis=1, keepdims=True)
    u = v / np.maximum(ln, 1e-12)
    cos_to = np.clip(u @ target, -1, 1)
    max_ang = np.arccos(cos_to) * 0.85
    a = np.minimum(angle, max_ang)
    axis = np.cross(u, target)
    an = np.linalg.norm(axis, axis=1, keepdims=True)
    ok = an[:, 0] > 1e-8
    axis = axis / np.maximum(an, 1e-12)
    ca, sa = np.cos(a)[:, None], np.sin(a)[:, None]
    rot = u * ca + np.cross(axis, u) * sa + axis * (axis * u).sum(1, keepdims=True) * (1 - ca)
    out = np.where(ok[:, None], rot, u)
    return out * ln


def gravity_droop(ng: NodeGraph, r: np.ndarray, amount: float, iterations: int = 2):
    if amount <= 0:
        return
    for _ in range(iterations):
        v = _segments(ng)
        seg = np.linalg.norm(v, axis=1)
        m = np.pi * r * r * seg * WOOD_DENSITY
        M = ng.subtree_sum(m)
        com = ng.subtree_sum(m[:, None] * ng.pos) / np.maximum(M[:, None], 1e-12)
        base = np.zeros_like(ng.pos)
        base[1:] = ng.pos[ng.parent[1:]]
        lever = np.hypot(com[:, 0] - base[:, 0], com[:, 2] - base[:, 2])
        # beam bending: curvature ~ torque / (E * I), I ~ r^4; normalised so the parameter is scale free
        k = M * lever / np.maximum(r, 1e-5) ** 4 * seg
        ref = np.percentile(k[1:], 90) if ng.n > 10 else 1.0
        theta = amount * 0.04 * np.clip(k / max(ref, 1e-12), 0, 2.5)
        theta[0] = 0.0
        acc = theta.copy()
        for lvl in ng.levels()[1:]:
            acc[lvl] += acc[ng.parent[lvl]] * 0.9
        acc = np.minimum(acc, 1.2)
        v2 = _rotate_toward(v, DOWN, acc)
        v2[0] = 0
        _rebuild(ng, v2)


def phototropism(ng: NodeGraph, r: np.ndarray, amount: float):
    if amount <= 0:
        return
    v = _segments(ng)
    ln = np.linalg.norm(v, axis=1, keepdims=True)
    flex = (1.0 - r / r.max())[:, None]
    u = normalize(v + amount * 0.15 * flex * ln * ng.light_dir)
    v2 = u * ln
    v2[0] = 0
    _rebuild(ng, v2)


def noise(ng: NodeGraph, amplitude: float, frequency: float, seed: int):
    if amplitude <= 0:
        return
    g = rng(seed, 9)
    waves = 5
    k = normalize(g.normal(size=(3, waves, 3))) * frequency * (0.5 + g.random((3, waves, 1)))
    ph = g.random((3, waves)) * 2 * np.pi
    p = ng.pos
    field = np.stack([np.sin(p @ k[c].T + ph[c]).sum(1) / waves ** 0.5 for c in range(3)], 1)
    v = _segments(ng)
    ln = np.linalg.norm(v, axis=1, keepdims=True)
    v2 = normalize(v + amplitude * ln * field) * ln
    v2[0] = 0
    _rebuild(ng, v2)


def smooth_chains(ng: NodeGraph, amount: float, iterations: int = 4):
    """Laplacian smoothing of in-chain nodes (exactly one child). Forks, tips and root stay put.

    Raw space colonization zig-zags between attractors; every published
    implementation smooths. Forks are pinned, so laterals stay attached.
    """
    if amount <= 0 or ng.n < 3:
        return
    order, starts, ends = ng.children()
    single = (ends - starts) == 1
    single[0] = False
    idx = np.flatnonzero(single)
    child = order[starts[idx]]
    par = ng.parent[idx]
    for _ in range(iterations):
        mid = 0.5 * (ng.pos[par] + ng.pos[child])
        ng.pos[idx] += amount * (mid - ng.pos[idx])


def refine(ng: NodeGraph, r: np.ndarray, params: dict, seed: int):
    R = params["refinement"]
    smooth_chains(ng, R["smoothing"])
    gravity_droop(ng, r, R["gravity_droop"])
    phototropism(ng, r, R["phototropism"])
    noise(ng, R["noise"]["amplitude"], R["noise"]["frequency"], seed)
