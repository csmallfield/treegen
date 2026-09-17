"""Pipe model radii: r_parent^n = sum r_child^n (plus shed history), flare and junction swell."""
from __future__ import annotations

import numpy as np

from .age import AgeState
from .skeleton import Branches, NodeGraph

WOOD_DENSITY = 750.0  # kg/m^3


def pipe_radii(ng: NodeGraph, params: dict) -> np.ndarray:
    R = params["radii"]
    n_exp, rt = R["pipe_exponent"], R["tip_radius"]
    order_csr, starts, ends = ng.children()
    nkids = ends - starts
    tips = (nkids == 0).astype(np.float64)
    # shed limbs still thickened the trunk while they lived: keep their pipes
    units = ng.subtree_sum(tips + ng.shed_tips)
    return rt * np.power(np.maximum(units, 1.0), 1.0 / n_exp)


def shape_radii(ng: NodeGraph, br: Branches, r: np.ndarray, params: dict, st: AgeState) -> np.ndarray:
    R = params["radii"]
    r = r.copy()
    rmax = r.max()
    # age: thicken the trunk relative to height as the tree ages, fading out toward twigs
    m = st.trunk_radius_mult
    r *= 1.0 + (m - 1.0) * 0.5 * (r / rmax)

    # junction swell at bifurcations, feathered one node each way
    _, starts, ends = ng.children()
    fork = (ends - starts) >= 2
    swell = np.ones(ng.n)
    swell[fork] = R["junction_swell"]
    feather = 1 + 0.5 * (R["junction_swell"] - 1)
    nb = np.ones(ng.n)
    kids_of_fork = np.flatnonzero(fork[ng.parent[1:]]) + 1
    nb[kids_of_fork] = feather
    fork_nodes = np.flatnonzero(fork)
    nb[ng.parent[fork_nodes[fork_nodes > 0]]] = feather
    r *= np.maximum(swell, nb)

    # trunk flare on the root curve
    height = max(ng.pos[:, 1].max(), 1e-3)
    fl = R["trunk_flare"]
    h = fl["height"] * height
    root_nodes = br.nodes[br.offsets[0]:br.offsets[1]]
    y = ng.pos[root_nodes, 1]
    f = np.where(y < h, 1 + fl["amount"] * (1 - np.clip(y / max(h, 1e-6), 0, 1)) ** 2, 1.0)
    r[root_nodes] *= f
    return r


def downstream_mass(ng: NodeGraph, r: np.ndarray) -> np.ndarray:
    seg = np.zeros(ng.n)
    seg[1:] = np.linalg.norm(ng.pos[1:] - ng.pos[ng.parent[1:]], axis=1)
    mass = np.pi * r * r * seg * WOOD_DENSITY
    return ng.subtree_sum(mass)
