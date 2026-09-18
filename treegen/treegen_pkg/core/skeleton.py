"""Node graph clean-up and branch extraction.

Space colonization yields nodes, not branches. Extraction walks the tree and at
each bifurcation picks a continuation child (largest downstream subtree); the
others become laterals of order + 1. This gives branch chains with order
indices, which UVs, taper, materials and later leaves all depend on.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .growth import SHED, GrowthResult
from .util import depth_levels


@dataclass
class NodeGraph:
    pos: np.ndarray
    parent: np.ndarray
    birth: np.ndarray
    dead: np.ndarray
    depth: np.ndarray
    shed_tips: np.ndarray
    exposure: np.ndarray
    light_dir: np.ndarray
    src_index: np.ndarray      # index into the GrowthResult arrays

    @property
    def n(self):
        return len(self.parent)

    def children(self):
        """CSR children: order, starts, ends."""
        par = self.parent
        order = np.argsort(par[1:], kind="stable") + 1
        keys = par[order]
        idx = np.arange(self.n)
        return order, np.searchsorted(keys, idx), np.searchsorted(keys, idx, side="right")

    def levels(self):
        return depth_levels(self.depth)

    def subtree_sum(self, values):
        out = np.array(values, dtype=np.float64, copy=True)
        for lvl in reversed(self.levels()[1:]):
            np.add.at(out, self.parent[lvl], out[lvl])
        return out


def build_graph(g: GrowthResult) -> NodeGraph:
    n = len(g.parent)
    valid = g.state != SHED
    # a node is valid only if its whole ancestry is (orphans of shed limbs are dropped)
    for lvl in depth_levels(g.depth)[1:]:
        valid[lvl] &= valid[g.parent[lvl]]
    keep = np.flatnonzero(valid)
    remap = np.full(n, -1, np.int64)
    remap[keep] = np.arange(len(keep))
    par = g.parent[keep]
    return NodeGraph(
        pos=g.pos[keep].copy(), parent=np.where(par >= 0, remap[np.maximum(par, 0)], -1),
        birth=g.birth[keep], dead=g.state[keep] == 1, depth=g.depth[keep], shed_tips=g.shed_tips[keep],
        exposure=g.exposure[keep], light_dir=g.light_dir[keep], src_index=keep)


@dataclass
class Branches:
    """Branch chains over a NodeGraph. Curve i = nodes[offsets[i]:offsets[i+1]]."""
    nodes: np.ndarray          # flat node indices per curve point
    offsets: np.ndarray        # (C+1,)
    order: np.ndarray          # (C,)
    parent_curve: np.ndarray   # (C,) -1 for the root
    parent_point: np.ndarray   # (C,) flat point index of the attach point on the parent curve
    path: list                 # (C,) deterministic hierarchical names

    @property
    def count(self):
        return len(self.order)

    def counts(self):
        return np.diff(self.offsets)


def extract_branches(ng: NodeGraph, max_order: int, reiteration: float) -> Branches:
    order_csr, starts, ends = ng.children()
    weight = ng.subtree_sum(np.ones(ng.n))
    total = weight[0]

    flat_nodes: list[int] = []
    offsets = [0]
    b_order, b_parent, b_ppoint, b_path = [], [], [], []

    # stack entries: (start_node, attach_node, order, parent_curve, attach_flat_index, path)
    stack = [(0, -1, 0, -1, -1, "b0")]
    while stack:
        start, attach, order, pcurve, ppoint, path = stack.pop()
        cid = len(b_order)
        b_order.append(order)
        b_parent.append(pcurve)
        b_ppoint.append(ppoint)
        b_path.append(path)
        chain = [attach] if attach >= 0 else []
        pending = []
        k = start
        while True:
            chain.append(k)
            kids = order_csr[starts[k]:ends[k]]
            if len(kids) == 0:
                break
            w = weight[kids]
            # deterministic tie-break: heavier, then smaller index
            srt = kids[np.lexsort((kids, -w))]
            cont = int(srt[0])
            for c in srt[1:]:
                c = int(c)
                codominant = (order <= 1 and weight[c] >= 0.05 * total
                              and weight[c] >= (1.0 - reiteration) * weight[cont] and reiteration > 0)
                lo = order if codominant else order + 1
                if lo <= max_order:
                    pending.append((len(chain) - 1, c, lo))
            k = cont
        base = offsets[-1]
        flat_nodes.extend(chain)
        offsets.append(base + len(chain))
        # push laterals in reverse so they pop in attach order -> stable DFS
        lats = sorted(pending, key=lambda t: (t[0], t[1]))
        for n_lat in range(len(lats) - 1, -1, -1):
            pi, c, lo = lats[n_lat]
            stack.append((c, chain[pi], lo, cid, base + pi, f"{path}_{n_lat}"))

    return Branches(np.array(flat_nodes, np.int64), np.array(offsets, np.int64), np.array(b_order, np.int32),
                    np.array(b_parent, np.int64), np.array(b_ppoint, np.int64), b_path)
