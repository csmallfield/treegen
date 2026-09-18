"""Geometry derived from the skeleton.

- Mesh orders (default 0-1): swept tubes on parallel-transport frames, radial
  segment count adaptive to base radius, tip caps, flat base cap on the trunk.
- Everything else: linear BasisCurves (renderers ray-trace curves natively).
- UVs in metres: U around the sweep (circumference of the branch base), V = arc length.
- Every point carries skelPointIndex + skelLocalOffset for FX rebinding.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .frames import parallel_transport


@dataclass
class MeshData:
    points: np.ndarray
    face_counts: np.ndarray
    face_indices: np.ndarray
    st: np.ndarray                # (P, 2) vertex-interpolated, seam duplicated
    skel_index: np.ndarray        # (P,)
    skel_offset: np.ndarray       # (P, 3)
    face_order: np.ndarray        # (F,) branch order per face
    face_curve: np.ndarray        # (F,) skeleton curve per face

    @property
    def empty(self):
        return len(self.face_counts) == 0


@dataclass
class CurveData:
    points: np.ndarray
    counts: np.ndarray
    widths: np.ndarray
    skel_index: np.ndarray
    order: np.ndarray            # (C,)
    arc_length: np.ndarray       # (P,)


@dataclass
class Geometry:
    trunk: MeshData
    branches: MeshData
    twigs: CurveData
    proxy: MeshData
    stats: dict = field(default_factory=dict)


def _ring_segments(r_base, edge, lo, hi):
    return int(np.clip(round(2 * np.pi * r_base / max(edge, 1e-6)), lo, hi))


def _sweep(sk, frames, curves, edge, seg_lo, seg_hi, stride=1, base_cap_root=True) -> MeshData:
    T, N, B = frames
    pts, stv, sidx, soff = [], [], [], []
    fcounts, fidx, forder, fcurve = [], [], [], []
    vbase = 0
    for c in curves:
        a, b = int(sk.offsets[c]), int(sk.offsets[c + 1])
        ids = np.arange(a, b)
        if stride > 1 and len(ids) > 2:
            ids = np.unique(np.r_[ids[::stride], ids[-1]])
        m = len(ids)
        if m < 2:
            continue
        r = sk.radius[ids]
        s = _ring_segments(r.max(), edge, seg_lo, seg_hi)
        th = np.linspace(0, 2 * np.pi, s + 1)                 # seam duplicated
        cs, sn = np.cos(th), np.sin(th)
        off_n = r[:, None] * cs[None]                         # (m, s+1)
        off_b = r[:, None] * sn[None]
        P = (sk.points[ids][:, None] + off_n[..., None] * N[ids][:, None] + off_b[..., None] * B[ids][:, None])
        pts.append(P.reshape(-1, 3))
        u = (th / (2 * np.pi))[None].repeat(m, 0) * (2 * np.pi * r.max())
        v = sk.arc_length[ids][:, None].repeat(s + 1, 1)
        stv.append(np.stack([u, v], -1).reshape(-1, 2))
        sidx.append(np.repeat(ids, s + 1))
        soff.append(np.stack([np.zeros_like(off_n), off_n, off_b], -1).reshape(-1, 3))

        ring = np.arange(m - 1)[:, None] * (s + 1) + np.arange(s)[None]
        q = np.stack([ring, ring + 1, ring + s + 2, ring + s + 1], -1).reshape(-1, 4) + vbase
        fidx.append(q.reshape(-1))
        fcounts.append(np.full(len(q), 4))
        nverts = m * (s + 1)

        # tip cap: fan to a point slightly beyond the last ring
        k = ids[-1]
        tip_off = 0.3 * r[-1]
        pts.append((sk.points[k] + T[k] * tip_off)[None])
        stv.append(np.array([[np.pi * r.max(), sk.arc_length[k] + tip_off]]))
        sidx.append(np.array([k]))
        soff.append(np.array([[tip_off, 0.0, 0.0]]))
        last = (m - 1) * (s + 1) + np.arange(s) + vbase
        center = vbase + nverts
        fidx.append(np.stack([last, np.full(s, center), last + 1], -1).reshape(-1))
        fcounts.append(np.full(s, 3))
        nverts += 1
        nf = len(q) + s

        if base_cap_root and sk.parent_curve[c] < 0:
            k0 = ids[0]
            pts.append(sk.points[k0][None])
            stv.append(np.array([[np.pi * r.max(), sk.arc_length[k0]]]))
            sidx.append(np.array([k0]))
            soff.append(np.zeros((1, 3)))
            first = np.arange(s) + vbase
            fidx.append(np.stack([first + 1, np.full(s, vbase + nverts), first], -1).reshape(-1))
            fcounts.append(np.full(s, 3))
            nverts += 1
            nf += s

        forder.append(np.full(nf, sk.order[c]))
        fcurve.append(np.full(nf, c))
        vbase += nverts

    if not pts:
        z3, z = np.zeros((0, 3)), np.zeros(0, np.int64)
        return MeshData(z3, z, z, np.zeros((0, 2)), z, z3, z, z)
    return MeshData(np.concatenate(pts), np.concatenate(fcounts), np.concatenate(fidx), np.concatenate(stv),
                    np.concatenate(sidx), np.concatenate(soff), np.concatenate(forder), np.concatenate(fcurve))


def build_geometry(sk, params: dict, lod: float = 1.0) -> Geometry:
    G = params["geometry"]
    mesh_orders = set(int(o) for o in G["mesh_orders"])
    min_r = params["architecture"]["min_radius_for_mesh"]
    lo, hi = int(G["radial_segments"]["min"]), int(G["radial_segments"]["max"])
    lo_l, hi_l = max(3, int(round(lo * lod))), max(3, int(round(hi * lod)))
    edge = params["growth"]["step_length"] * 0.6 / max(lod, 1e-3)

    frames = parallel_transport(sk.points, sk.offsets, sk.parent_curve, sk.parent_point)
    base_r = np.array([sk.radius[sk.offsets[c]:sk.offsets[c + 1]].max() for c in range(sk.curve_count)])
    is_mesh = np.array([(int(o) in mesh_orders) and (base_r[i] >= min_r or o == 0)
                        for i, o in enumerate(sk.order)], bool)
    trunk_c = np.flatnonzero(is_mesh & (sk.order == 0))
    branch_c = np.flatnonzero(is_mesh & (sk.order != 0))
    twig_c = np.flatnonzero(~is_mesh)

    trunk = _sweep(sk, frames, trunk_c, edge, lo_l, hi_l)
    branches = _sweep(sk, frames, branch_c, edge, lo_l, hi_l, base_cap_root=False)
    proxy = _sweep(sk, frames, np.flatnonzero(is_mesh), edge * 4, 4, 6, stride=3)

    if len(twig_c):
        ids = np.concatenate([np.arange(sk.offsets[c], sk.offsets[c + 1]) for c in twig_c])
        counts = sk.counts[twig_c]
    else:
        ids, counts = np.zeros(0, np.int64), np.zeros(0, np.int64)
    twigs = CurveData(sk.points[ids], counts, 2.0 * sk.radius[ids], ids, sk.order[twig_c], sk.arc_length[ids])

    stats = {"trunk_faces": len(trunk.face_counts), "branch_faces": len(branches.face_counts),
             "twig_curves": len(twig_c), "mesh_points": len(trunk.points) + len(branches.points)}
    return Geometry(trunk, branches, twigs, proxy, stats)


def rebind_points(skel_points, offsets, parent_curve, parent_point, skel_index, skel_offset):
    """Reference rebind: reconstruct geometry points from a (deformed) skeleton."""
    T, N, B = parallel_transport(skel_points, offsets, parent_curve, parent_point)
    k = skel_index
    o = skel_offset
    return skel_points[k] + o[:, :1] * T[k] + o[:, 1:2] * N[k] + o[:, 2:3] * B[k]
