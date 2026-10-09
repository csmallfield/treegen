"""Voxel occlusion field and hemisphere exposure sampling.

One field drives attractor weighting, shedding, asymmetry and field-vs-forest
behaviour. Occlusion comes from the tree's own foliage (splatted from growing
tips — the dominant term), from neighbour proxies, and an optional sky bias.

Exposure at a point = weighted mean hemisphere transmittance, in [0, 1].
Light direction = normalised transmittance-weighted mean direction.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import uniform_filter

from ..schema import Neighbour


def hemisphere_dirs(n: int, sky_bias: float):
    """Deterministic Fibonacci directions over the upper hemisphere (+Y), with weights."""
    i = np.arange(n) + 0.5
    y = 1.0 - i / n                      # cos(zenith) uniformly in (0, 1]
    phi = np.pi * (1 + 5 ** 0.5) * i
    s = np.sqrt(1 - y * y)
    d = np.stack([s * np.cos(phi), y, s * np.sin(phi)], axis=1)
    w = y ** sky_bias + 0.02             # small floor keeps near-horizon rays meaningful
    return d, w / w.sum()


_NB_CACHE: dict = {}


def _neighbour_grid(bmin, vox, shape, neighbours, hscale):
    """Rasterise neighbour proxies into a voxel grid, cached.

    Proxies are static apart from the age scale, so this is recomputed only when the
    grid or the (quantised) scale changes instead of once per light epoch.
    """
    key = (tuple(np.round(bmin, 4)), round(float(vox), 6), tuple(shape),
           tuple((n.kind, n.a, n.b, n.radius, n.density, n.age_scaled) for n in neighbours),
           round(float(hscale), 2))
    hit = _NB_CACHE.get(key)
    if hit is not None:
        return hit
    dens = np.zeros(tuple(shape), np.float32)
    for nb in neighbours:
        # only test voxels inside the proxy's own bounds, not the whole grid
        s_ = hscale if nb.age_scaled else 1.0
        lo, hi = neighbour_bounds([nb], hscale)
        i0 = np.maximum(np.floor((lo - bmin) / vox).astype(int), 0)
        i1 = np.minimum(np.ceil((hi - bmin) / vox).astype(int) + 1, shape)
        if np.any(i1 <= i0):
            continue
        c = [bmin[k] + (np.arange(i0[k], i1[k]) + 0.5) * vox for k in range(3)]
        X, Y, Z = np.meshgrid(*c, indexing="ij")
        P = np.stack([X, Y, Z], -1).reshape(-1, 3)
        inside = _inside(nb, P, s_).reshape(X.shape)
        dens[i0[0]:i1[0], i0[1]:i1[1], i0[2]:i1[2]] += inside * nb.density
    if len(_NB_CACHE) > 24:
        _NB_CACHE.clear()
    _NB_CACHE[key] = dens
    return dens


@dataclass
class LightField:
    origin: np.ndarray      # (3,) min corner
    voxel: np.ndarray       # (3,) voxel size
    density: np.ndarray     # (nx, ny, nz) optical depth per metre
    dirs: np.ndarray
    weights: np.ndarray

    @property
    def shape(self):
        return np.array(self.density.shape)

    # ------------------------------------------------------------------ build
    @classmethod
    def build(cls, bounds_min, bounds_max, resolution: int, neighbours: list[Neighbour],
              tips: np.ndarray, foliage_density: float, ray_count: int, sky_bias: float,
              neighbour_height_scale: float = 1.0) -> "LightField":
        bmin, bmax = np.asarray(bounds_min, float), np.asarray(bounds_max, float)
        ext = bmax - bmin
        vox = ext.max() / resolution
        shape = np.maximum(np.ceil(ext / vox).astype(int), 1)
        dens = np.zeros(shape, np.float32)

        if neighbours:
            dens = _neighbour_grid(bmin, vox, shape, neighbours, neighbour_height_scale).copy()

        if len(tips) and foliage_density > 0:
            idx = np.floor((tips - bmin) / vox).astype(int)
            ok = np.all((idx >= 0) & (idx < shape), axis=1)
            idx = idx[ok]
            foliage = np.zeros(shape, np.float32)
            np.add.at(foliage, (idx[:, 0], idx[:, 1], idx[:, 2]), 1.0)
            foliage = uniform_filter(foliage, size=3, mode="constant")   # 3³ box: anti-alias the splat
            dens += foliage * (foliage_density / vox ** 3)

        d, w = hemisphere_dirs(ray_count, sky_bias)
        return cls(bmin, np.full(3, vox), dens, d, w)

    # ----------------------------------------------------------------- sample
    def sample(self, pts: np.ndarray, chunk: int = 2048, max_steps: int = 96):
        """Return (exposure (N,), light_dir (N,3)) for points."""
        n = len(pts)
        expo = np.ones(n, np.float32)
        ldir = np.tile(np.array([0, 1, 0], np.float32), (n, 1))
        if n == 0:
            return expo, ldir
        vox = float(self.voxel[0])
        shape = self.shape
        diag = float(np.linalg.norm(shape)) * vox
        step = 1.25 * vox
        steps = int(min(max_steps, np.ceil(diag / step)))
        t = (1.0 + np.arange(steps)) * step          # skip the sample's own voxel
        flat = self.density.reshape(-1)
        strides = np.array([shape[1] * shape[2], shape[2], 1])
        D, W = self.dirs.astype(np.float32), self.weights.astype(np.float32)

        for s in range(0, n, chunk):
            p = pts[s:s + chunk].astype(np.float32)
            q = p[:, None, None, :] + D[None, :, None, :] * t[None, None, :, None].astype(np.float32)
            idx = np.floor((q - self.origin.astype(np.float32)) / vox).astype(np.int32)
            ok = np.all((idx >= 0) & (idx < shape), axis=-1)
            lin = (idx * strides).sum(-1)
            lin[~ok] = 0
            tau = np.where(ok, flat[lin], 0.0).sum(-1) * step    # (m, K)
            T = np.exp(-tau)
            expo[s:s + chunk] = (T * W).sum(-1)
            v = (T * W)[..., None] * D[None]
            ldir[s:s + chunk] = v.sum(1)
        norm = np.linalg.norm(ldir, axis=1, keepdims=True)
        ldir = np.where(norm > 1e-6, ldir / np.maximum(norm, 1e-6), np.array([0, 1, 0], np.float32))
        return expo, ldir


def _inside(nb: Neighbour, P: np.ndarray, hscale: float) -> np.ndarray:
    s = np.array([1.0, hscale, 1.0])
    if nb.kind == "sphere":
        return np.linalg.norm(P - np.array(nb.a) * s, axis=1) <= nb.radius * hscale
    if nb.kind == "box":
        c, h = np.array(nb.a) * s, np.array(nb.b) * s
        return np.all(np.abs(P - c) <= h, axis=1)
    # capsule
    a, b = np.array(nb.a) * s, np.array(nb.b) * s
    ab = b - a
    tt = np.clip(((P - a) @ ab) / max(ab @ ab, 1e-12), 0, 1)
    return np.linalg.norm(P - (a + tt[:, None] * ab), axis=1) <= nb.radius * hscale


def neighbour_bounds(neighbours: list[Neighbour], hscale: float = 1.0):
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    for nb in neighbours:
        s = np.array([1.0, hscale if nb.age_scaled else 1.0, 1.0])
        if nb.kind == "box":
            c, h = np.array(nb.a) * s, np.array(nb.b) * s
            pts = [c - h, c + h]
        else:
            r = nb.radius * (hscale if nb.age_scaled else 1.0)
            ends = [np.array(nb.a) * s] + ([np.array(nb.b) * s] if nb.kind == "capsule" else [])
            pts = [e - r for e in ends] + [e + r for e in ends]
        for p in pts:
            lo, hi = np.minimum(lo, p), np.maximum(hi, p)
    return lo, hi


def crown_shade(params: dict, neighbours: list[Neighbour], env, hscale: float):
    """How the neighbours alone light the sides of a crown: (shade, (ax, az)).

    shade is the fraction of sky light they take, in [0, 1]. (ax, az) is the light
    asymmetry: the exposure-weighted mean of the horizontal directions to the sample
    points, 0 when the stand is symmetric and pointing at the open side when not
    (about 0.3 toward the clearing for forest_edge). Samples sit just inside the
    envelope's surface on three rings and march through a neighbour-only field,
    without the tree's own foliage, so this measures the stand, not self-shading.
    """
    if not neighbours:
        return 0.0, (0.0, 0.0)
    L = params["light"]
    R, H = env.radius, env.height
    lo, hi = np.array([-2.5 * R, 0.0, -2.5 * R]), np.array([2.5 * R, 1.6 * H, 2.5 * R])
    nlo, nhi = neighbour_bounds(neighbours, hscale)
    lo, hi = np.maximum(np.minimum(lo, nlo), [-3 * R, 0.0, -3 * R]), np.minimum(np.maximum(hi, nhi), [3 * R, 2 * H, 3 * R])
    field_ = LightField.build(lo, hi, 32, neighbours, np.zeros((0, 3)), 0.0, L["ray_count"], L["sky_bias"], hscale)
    t = np.array([0.35, 0.55, 0.75])
    a = np.linspace(0, 2 * np.pi, 16, endpoint=False)
    T, A = np.meshgrid(t, a, indexing="ij")
    r = 0.9 * env.radius_at(T)
    pts = np.stack([r * np.cos(A), T * H, r * np.sin(A)], -1).reshape(-1, 3)
    expo, _ = field_.sample(pts)
    u = np.stack([np.cos(A), np.sin(A)], -1).reshape(-1, 2)
    asym = (expo[:, None] * u).sum(0) / max(float(expo.sum()), 1e-9)
    return float(np.clip(1.0 - expo.mean(), 0.0, 1.0)), (float(asym[0]), float(asym[1]))
