"""Crown envelope: a revolved profile curve around +Y. Needs only a point-inside test."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .age import AgeState
from .util import curve_eval


@dataclass(frozen=True)
class Envelope:
    profile: tuple
    height: float
    radius: float
    clear_height: float = 0.0

    @classmethod
    def at_age(cls, params: dict, st: AgeState) -> "Envelope":
        e = params["envelope"]
        flat = st.crown_flatten_delta
        h = e["height"] * st.height_mult * (1.0 - 0.4 * flat)
        r = 0.5 * e["height"] * e["spread_ratio"] * st.height_mult * (1.0 + 0.3 * flat)
        return cls(tuple(map(tuple, e["profile"])), max(h, 0.05), max(r, 0.02), e["clear_height"])

    def radius_at(self, t):
        return self.radius * np.clip(curve_eval(self.profile, t), 0.0, None)

    def inside(self, pts: np.ndarray) -> np.ndarray:
        t = pts[:, 1] / self.height
        rr = np.hypot(pts[:, 0], pts[:, 2])
        return (t >= self.clear_height) & (t <= 1.0) & (rr <= self.radius_at(t))

    def volume(self, samples: int = 256) -> float:
        t = (np.arange(samples) + 0.5) / samples
        t = t[t >= self.clear_height]
        return float(np.sum(np.pi * self.radius_at(t) ** 2) * self.height / samples)
