"""Silhouette measurements for regression-checking the definition of done.

Numbers that describe what the contact sheet shows, so a change that is meant to
move one of them (say, bole length in a forest) can be checked against the ones
that are meant to stay put.
"""
from __future__ import annotations

import numpy as np


def tree_metrics(sk) -> dict:
    """Height, DBH, first fork, crown base, crown width and lean of one skeleton (metres)."""
    live = ~sk.dead
    pts = sk.points

    # crown base: 5th percentile height of living branch wood (everything but the main stem).
    # A percentile, not the lowest limb, so one surviving twig low on the bole does not count.
    branch = live.copy()
    branch[sk.offsets[0]:sk.offsets[1]] = False
    base = float(np.percentile(pts[branch, 1], 5)) if branch.any() else float(sk.height)

    # first fork: lowest point on the main stem where a living limb at least 30% of the
    # stem's radius leaves it. What the eye reads as "where the trunk ends".
    first_fork = float(sk.height)
    for c in np.flatnonzero(sk.parent_curve == 0):
        a, b = sk.offsets[c], sk.offsets[c + 1]
        pp = sk.parent_point[c]
        if b - a < 2 or not live[a + 1:b].any():
            continue
        if sk.radius[a + 1] >= 0.3 * sk.radius[pp]:
            first_fork = min(first_fork, float(pts[pp, 1]))

    lp = pts[live]
    rad = np.hypot(lp[:, 0], lp[:, 2])
    crown = lp[lp[:, 1] >= base] if (lp[:, 1] >= base).any() else lp
    return {
        "height": float(sk.height),
        "dbh": float(sk.dbh),
        "first_fork": first_fork,
        "crown_base": base,
        "bole_fraction": base / max(sk.height, 1e-9),
        "crown_width": float(2 * np.percentile(rad, 99)) if len(rad) else 0.0,
        "crown_offset_x": float(crown[:, 0].mean()) if len(crown) else 0.0,
        "branches": int(sk.curve_count),
    }
