"""Silhouette previews (matplotlib) — a fast sanity check without any USD viewer.

Side (X/Y) and front (Z/Y) orthographic views, line width from radius. Modes:
skeleton (default), light (exposure heatmap), shed (culled branches ghosted).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np


def _segments(sk, axes):
    idx_a, idx_b = [], []
    for c in range(sk.curve_count):
        a, b = sk.offsets[c], sk.offsets[c + 1]
        idx_a.append(np.arange(a, b - 1))
        idx_b.append(np.arange(a + 1, b))
    ia, ib = np.concatenate(idx_a), np.concatenate(idx_b)
    P = sk.points[:, axes]
    return np.stack([P[ia], P[ib]], 1), ib


def render_png(sk, path, growth=None, mode="skeleton", title=None, size=6.0, extent=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection

    fig, axs = plt.subplots(1, 2, figsize=(size * 2, size), dpi=110)
    H = extent or max(sk.height * 1.05, 1.0)
    for ax, axes, label in ((axs[0], [0, 1], "side (X)"), (axs[1], [2, 1], "front (Z)")):
        if mode == "shed" and growth is not None:
            shed = np.flatnonzero((growth.state == 2) & (growth.parent >= 0))
            if len(shed):
                segs = np.stack([growth.pos[growth.parent[shed]][:, axes], growth.pos[shed][:, axes]], 1)
                ax.add_collection(LineCollection(segs, colors=(0.85, 0.3, 0.2, 0.25), linewidths=0.5))
        segs, ib = _segments(sk, axes)
        scale = 72 * size / (1.1 * H)          # metres -> points
        lw = np.clip(2 * sk.radius[ib] * scale, 0.15, None)
        if mode == "light":
            cmap = plt.get_cmap("inferno")
            colors = cmap(np.clip(sk.light_exposure[ib], 0, 1))
        else:
            colors = np.where(sk.dead[ib][:, None], [[0.55, 0.5, 0.45, 1]], [[0.18, 0.14, 0.1, 1]])
        ax.add_collection(LineCollection(segs, colors=colors, linewidths=lw, capstyle="round"))
        ax.set_xlim(-H * 0.55, H * 0.55)
        ax.set_ylim(-0.02 * H, H * 1.08)
        ax.set_aspect("equal")
        ax.axhline(0, color="0.7", lw=0.5)
        ax.set_title(label, fontsize=9)
        ax.tick_params(labelsize=7)
    if title:
        fig.suptitle(title, fontsize=11)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def contact_sheet(results, path, cols=None, cell=3.0):
    """Grid of side-view silhouettes, one per run (variant browsing without a viewer)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection

    n = len(results)
    cols = min(cols or int(np.ceil(np.sqrt(n))), n)
    rows = (n + cols - 1) // cols
    H = max(r["skeleton"].height for r in results) * 1.08
    W = max(max(np.abs(r["skeleton"].points[:, 0]).max() for r in results) * 1.05, H * 0.55)
    fig, axs = plt.subplots(rows, cols, figsize=(cols * cell, rows * cell), dpi=100, squeeze=False)
    for ax in axs.flat:
        ax.axis("off")
    for ax, r in zip(axs.flat, results):
        sk = r["skeleton"]
        segs, ib = _segments(sk, [0, 1])
        scale = 72 * cell / (2.2 * W)
        ax.add_collection(LineCollection(segs, colors="#2b2119", capstyle="round",
                                         linewidths=np.clip(2 * sk.radius[ib] * scale, 0.1, None)))
        ax.set_xlim(-W, W)
        ax.set_ylim(0, H)
        ax.set_aspect("equal")
        ax.set_title(r["label"], fontsize=7)
    fig.tight_layout()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
