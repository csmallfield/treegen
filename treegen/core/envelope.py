"""Crown envelope: a revolved profile curve around +Y. Needs only a point-inside test.

`lean` shears it: the axis stays at the origin on the ground and moves linearly
with height to lean * radius at the top, so the crown leans while the trunk base
stays where the tree stands.
"""
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
    lean: tuple = (0.0, 0.0)        # (x, z) axis offset at the top, as a fraction of radius

    @classmethod
    def at_age(cls, params: dict, st: AgeState, stretch: float = 1.0, lean=(0.0, 0.0)) -> "Envelope":
        """stretch > 1 makes the crown taller and narrower by the same factor; lean shears it
        (both from shade avoidance, see growth.envelope_response)."""
        e = params["envelope"]
        flat = st.crown_flatten_delta
        h = e["height"] * st.height_mult * (1.0 - 0.4 * flat) * stretch
        r = 0.5 * e["height"] * e["spread_ratio"] * st.height_mult * (1.0 + 0.3 * flat) / stretch
        return cls(tuple(map(tuple, e["profile"])), max(h, 0.05), max(r, 0.02), e["clear_height"],
                   (float(lean[0]), float(lean[1])))

    def axis_at(self, t):
        """(x, z) of the envelope axis at normalised height t."""
        t = np.asarray(t, dtype=float)
        return self.lean[0] * self.radius * t, self.lean[1] * self.radius * t

    @property
    def reach(self) -> float:
        """Largest horizontal distance from the trunk any part of the envelope can be."""
        return self.radius * (1.0 + float(np.hypot(*self.lean)))

    def radius_at(self, t):
        return self.radius * np.clip(curve_eval(self.profile, t), 0.0, None)

    def inside(self, pts: np.ndarray) -> np.ndarray:
        t = pts[:, 1] / self.height
        if self.lean == (0.0, 0.0):
            rr = np.hypot(pts[:, 0], pts[:, 2])
        else:
            ax, az = self.axis_at(np.clip(t, 0.0, 1.0))
            rr = np.hypot(pts[:, 0] - ax, pts[:, 2] - az)
        return (t >= self.clear_height) & (t <= 1.0) & (rr <= self.radius_at(t))

    def volume(self, samples: int = 256) -> float:
        t = (np.arange(samples) + 0.5) / samples
        t = t[t >= self.clear_height]
        return float(np.sum(np.pi * self.radius_at(t) ** 2) * self.height / samples)
