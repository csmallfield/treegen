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

    # whips: limbs over 3 m long and more than 150x their base diameter, which wood
    # could not hold up (a self-supporting limb thickens as length^1.5)
    whips, slender = 0, []
    for c in np.flatnonzero(sk.order >= 1):
        a, b = sk.offsets[c], sk.offsets[c + 1]
        if b - a < 3:
            continue
        length = sk.arc_length[b - 1] - sk.arc_length[a]
        if length > 3.0:
            ld = length / (2 * sk.radius[a + 1])
            slender.append(ld)
            whips += ld > 150

    stick_inner, stick_mid = _sticks(sk)

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
        "whips": int(whips),
        "limb_ld_p95": float(np.percentile(slender, 95)) if slender else 0.0,
        "stick_inner": stick_inner,
        "stick_mid": stick_mid,
    }


def _sticks(sk):
    """Median length of wood between visible forks: (inner, middle).

    A visible fork splits into two or more children that each carry 2+ tips. Inner
    sticks carry 30+ tips (scaffold wood), middle ones 5-29. A winter oak has long
    inner sticks and short outer ones; colonization alone gives roughly even spacing.
    """
    P = len(sk.points)
    par = np.full(P, -1)
    alias = np.zeros(P, bool)                    # first point of a lateral = its attach point
    for c in range(sk.curve_count):
        a, b = sk.offsets[c], sk.offsets[c + 1]
        par[a + 1:b] = np.arange(a, b - 1)
        if c > 0:
            alias[a] = True
            if b - a > 1:
                par[a + 1] = sk.parent_point[c]
    nkids = np.zeros(P, int)
    real = np.flatnonzero((par >= 0) & ~alias)
    np.add.at(nkids, par[real], 1)
    tc = ((nkids == 0) & ~alias).astype(float)
    for i in real[::-1]:                         # curves are parents-first: children come later
        tc[par[i]] += tc[i]
    seglen = np.zeros(P)
    seglen[real] = np.linalg.norm(sk.points[real] - sk.points[par[real]], axis=1)
    big = np.zeros(P, int)
    np.add.at(big, par[real], (tc[real] >= 2).astype(int))
    fork = big >= 2
    order = np.argsort(par[real], kind="stable")
    kids_sorted = real[order]
    starts = np.searchsorted(par[kids_sorted], np.arange(P))
    ends = np.searchsorted(par[kids_sorted], np.arange(P), side="right")

    inner, mid = [], []
    stack = [int(sk.offsets[0])]
    while stack:
        k = stack.pop()
        carried, length = tc[k], 0.0
        while not fork[k]:
            kids = kids_sorted[starts[k]:ends[k]]
            if len(kids) == 0:
                break
            k = int(kids[np.argmax(tc[kids])])
            length += seglen[k]
        if fork[k] and length > 0:
            (inner if carried >= 30 else mid if carried >= 5 else []).append(length)
        if fork[k]:
            for c in kids_sorted[starts[k]:ends[k]]:
                if tc[c] >= 2:
                    stack.append(int(c))
    med = lambda v: float(np.median(v)) if v else 0.0           # noqa: E731
    return med(inner), med(mid)
