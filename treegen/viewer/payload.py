"""Pack a generated tree into one binary blob for the browser viewer.

Layout:  [uint32 meta_len][meta json utf-8][arrays back to back, in meta["arrays"] order]
Arrays are float32 / uint32, little endian, so they map straight onto typed arrays.
"""
from __future__ import annotations

import json
import struct

import numpy as np


def _vertex_channels(sk, skel_index):
    """Per-vertex display channels sampled from the skeleton."""
    order_per_point = np.zeros(len(sk.points), np.float32)
    for c in range(sk.curve_count):
        order_per_point[sk.offsets[c]:sk.offsets[c + 1]] = sk.order[c]
    return {
        "exposure": sk.light_exposure[skel_index].astype(np.float32),
        "order": order_per_point[skel_index],
        "birth": sk.birth_age[skel_index].astype(np.float32),
        "radius": sk.radius[skel_index].astype(np.float32),
        "dead": sk.dead[skel_index].astype(np.float32),
    }


def _line_segments(points, counts):
    """Consecutive-point index pairs for THREE.LineSegments."""
    if len(counts) == 0:
        return np.zeros(0, np.uint32)
    ends = np.cumsum(counts)
    starts = ends - counts
    segs = [np.stack([np.arange(a, b - 1), np.arange(a + 1, b)], 1) for a, b in zip(starts, ends) if b - a > 1]
    return np.concatenate(segs).astype(np.uint32).reshape(-1) if segs else np.zeros(0, np.uint32)


def debug_arrays(growth, params, age, scene=None):
    """Attractor cloud and envelope wireframe — the two views that show why growth went where it did.

    With a scene, the envelope is drawn with its shade-avoidance stretch at this age.
    """
    from ..core.age import age_state
    from ..core.envelope import Envelope
    from ..core.growth import envelope_stretch
    out = {}
    if growth is not None and len(growth.attractors):
        out["attr_pos"] = growth.attractors.astype(np.float32).reshape(-1)
        out["attr_state"] = growth.attractor_state.astype(np.float32)
    stretch = envelope_stretch(params, scene)(age) if scene is not None else 1.0
    env = Envelope.at_age(params, age_state(params, age), stretch)
    t = np.linspace(0.0, 1.0, 33)
    out["env_profile"] = np.stack([t * env.height, env.radius_at(t)], 1).astype(np.float32).reshape(-1)
    return out


def pack(sk, geo, growth, meta: dict, extras: dict | None = None) -> bytes:
    arrays: dict[str, np.ndarray] = {}

    # meshes: trunk + branches merged, quads and triangles triangulated
    pts, tris, chans = [], [], []
    base = 0
    for md in (geo.trunk, geo.branches):
        if md.empty:
            continue
        pts.append(md.points.astype(np.float32))
        chans.append(md.skel_index)
        i = 0
        out = []
        for n in np.unique(md.face_counts):
            sel = np.flatnonzero(md.face_counts == n)
            starts = np.concatenate([[0], np.cumsum(md.face_counts)])[sel]
            idx = md.face_indices[(starts[:, None] + np.arange(n)[None])]
            for k in range(1, n - 1):
                out.append(np.stack([idx[:, 0], idx[:, k], idx[:, k + 1]], 1))
        tris.append(np.concatenate(out) + base)
        base += len(md.points)
        i += 1
    if pts:
        arrays["mesh_pos"] = np.concatenate(pts).reshape(-1)
        arrays["mesh_idx"] = np.concatenate(tris).astype(np.uint32).reshape(-1)
        ci = np.concatenate(chans)
        for k, v in _vertex_channels(sk, ci).items():
            arrays[f"mesh_{k}"] = v
    else:
        arrays["mesh_pos"] = np.zeros(0, np.float32)
        arrays["mesh_idx"] = np.zeros(0, np.uint32)

    # twigs
    c = geo.twigs
    arrays["twig_pos"] = c.points.astype(np.float32).reshape(-1)
    arrays["twig_idx"] = _line_segments(c.points, c.counts)
    for k, v in _vertex_channels(sk, c.skel_index).items():
        arrays[f"twig_{k}"] = v

    # skeleton overlay
    arrays["skel_pos"] = sk.points.astype(np.float32).reshape(-1)
    arrays["skel_idx"] = _line_segments(sk.points, sk.counts)

    # shed limbs, as loose segments
    if growth is not None:
        shed = np.flatnonzero((growth.state == 2) & (growth.parent >= 0))
        if len(shed):
            seg = np.stack([growth.pos[growth.parent[shed]], growth.pos[shed]], 1).astype(np.float32)
            arrays["shed_pos"] = seg.reshape(-1)
        else:
            arrays["shed_pos"] = np.zeros(0, np.float32)

    if extras:
        arrays.update(extras)

    meta = dict(meta)
    meta["arrays"] = [{"name": k, "type": "u32" if v.dtype == np.uint32 else "f32", "length": int(v.size)}
                      for k, v in arrays.items()]
    blob = json.dumps(meta).encode()
    parts = [struct.pack("<I", len(blob)), blob]
    parts += [np.ascontiguousarray(v).tobytes() for v in arrays.values()]
    return b"".join(parts)
