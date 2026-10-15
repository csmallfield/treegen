import numpy as np
import pytest

from conftest import ROOT
from treegen.core.buds import grow_buds
from treegen.pipeline import Pipeline, assemble_skeleton
from treegen.schema import Scene, load_species


@pytest.fixture
def bud_oak():
    return load_species(ROOT / "species" / "quercus_buds.toml")


def test_grows_a_tree_with_no_envelope_or_attractors(bud_oak):
    g = grow_buds(bud_oak, Scene(), 3, 20)
    assert len(g.pos) > 200 and len(g.attractors) == 0
    assert g.pos[:, 1].max() > 3.0                                  # it gained height
    assert np.all(g.parent[1:] >= 0) and np.all(g.parent[1:] < np.arange(1, len(g.parent)))
    sk = assemble_skeleton(g, bud_oak, 3)                           # and the rest of the pipeline takes it
    assert sk.curve_count > 10 and sk.dbh > 0


def test_deterministic_and_seeded(bud_oak):
    a, b, c = (grow_buds(bud_oak, Scene(), s, 15) for s in (7, 7, 8))
    assert np.array_equal(a.pos, b.pos)
    assert len(a.pos) != len(c.pos) or not np.allclose(a.pos, c.pos)


def test_young_tree_is_the_start_of_the_old_one(bud_oak):
    young, old = grow_buds(bud_oak, Scene(), 3, 10), grow_buds(bud_oak, Scene(), 3, 16)
    assert len(young.pos) < len(old.pos)
    # short-lived shed twigs are dropped from the output, so compare wood rather than arrays:
    # everything the old tree grew by year 10 is in the young tree, in the same place
    then = old.pos[old.birth <= 10]
    have = {tuple(p) for p in np.round(young.pos, 9)}
    assert len(then) > 20 and all(tuple(p) in have for p in np.round(then, 9))


def test_apical_control_trades_height_for_spread(bud_oak):
    def shape(lam):
        p = {**bud_oak, "buds": {**bud_oak["buds"], "apical_control": lam}}
        g = grow_buds(p, Scene(), 3, 25)
        return g.pos[:, 1].max(), 2 * np.hypot(g.pos[:, 0], g.pos[:, 2]).max()
    (h_lo, w_lo), (h_hi, w_hi) = shape(0.46), shape(0.54)
    assert h_hi / w_hi > h_lo / w_lo                                # leaders favoured: taller, narrower


def test_pipeline_dispatches_on_growth_model(bud_oak):
    g, sk = Pipeline().skeleton(bud_oak, Scene(), 3, 12.0)
    assert len(g.attractors) == 0 and sk.height > 1.0


def test_shedding_removes_wood_and_keeps_the_tree_valid(bud_oak):
    from treegen.core.growth import SHED
    off = {**bud_oak, "buds": {**bud_oak["buds"], "shed_threshold": 0.0}}
    p = {**bud_oak, "buds": {**bud_oak["buds"], "shed_threshold": 0.4}}
    kept, shed = grow_buds(off, Scene(), 3, 25), grow_buds(p, Scene(), 3, 25)
    # (shed wood that lived under KEEP_SHED_YEARS is counted but not kept in the output)
    assert kept.stats["shed_nodes"] == 0 and shed.stats["shed_nodes"] > 0
    assert shed.shed_tips.sum() > 0                                 # the wood below remembers them
    assert np.all(shed.shed_year[shed.state == SHED] - shed.birth[shed.state == SHED] >= 10)
    sk = assemble_skeleton(shed, p, 3)                              # shed wood is gone from the skeleton
    # (extraction also drops branch orders above max_order, hence <=)
    assert len(np.unique(sk.node)) <= (shed.state != SHED).sum()
    assert shed.state[0] != SHED                                    # never the main stem


def test_apical_control_changes_over_the_trees_life(bud_oak):
    def height(old):
        p = {**bud_oak, "buds": {**bud_oak["buds"], "apical_control": 0.5, "apical_control_old": old},
             "meta": {**bud_oak["meta"], "max_age": 30}}
        return grow_buds(p, Scene(), 3, 30).pos[:, 1].max()
    assert height(0.56) > height(0.44)                              # leaders favoured late: taller



def test_annual_rings_thicken_long_lived_wood_most(bud_oak):
    g = grow_buds(bud_oak, Scene(), 3, 25)
    assert g.ring_area is not None and g.ring_area[0] == g.ring_area.max()   # the root carried the most
    sk = assemble_skeleton(g, bud_oak, 3)
    assert sk.radius[0] > 10 * sk.radius[sk.offsets[1:] - 1].min()            # trunk base far above a twig


def test_hydraulic_limit_slows_height(bud_oak):
    free = {**bud_oak, "buds": {**bud_oak["buds"], "max_height": 0.0}}
    capped = {**bud_oak, "buds": {**bud_oak["buds"], "max_height": 8.0}}
    h_free = grow_buds(free, Scene(), 3, 30).pos[:, 1].max()
    h_cap = grow_buds(capped, Scene(), 3, 30).pos[:, 1].max()
    assert h_cap < h_free and h_cap < 9.0


def test_shadow_propagation_darkens_the_inside_of_a_crown(bud_oak):
    from treegen.core.buds import _Shadow
    # a dense block of buds: the cells below its middle get far less light than its top
    xs = np.linspace(-1, 1, 9)
    block = np.array([[x, y, z] for x in xs for z in xs for y in (2.0, 2.25, 2.5)])
    sh = _Shadow(block, 0.25, 0.1, 2.0, 6)
    q, v = sh.sample(np.array([[0.0, 2.5, 0.0], [0.0, 1.5, 0.0], [5.0, 2.0, 5.0]]))
    assert q[0] > q[1] and q[2] == pytest.approx(1.0)                # lit top, shaded below, open space
    assert v[1, 1] < 0 or abs(v[1, 1]) < 1.0                          # a direction, not NaN
    p = {**bud_oak, "buds": {**bud_oak["buds"], "light_model": "shadow", "shadow_a": 0.05}}
    g = grow_buds(p, Scene(), 3, 20)
    assert g.stats["alive"] > 50 and g.exposure.min() < 0.1 < g.exposure.max()


def test_growth_time_droop_bends_long_limbs_and_spares_the_trunk(bud_oak):
    from treegen.core.buds import _Grow, _sag
    # a vertical trunk of 10 nodes with a horizontal limb of 20 from its top
    n_trunk, n_limb = 10, 20
    nodes = _Grow({"pos": (np.float64, (3,), 0.0), "parent": (np.int64, (), -1), "depth": (np.int32, (), 0),
                   "state": (np.int8, (), 0), "ring": (np.float64, (), 0.0)})
    nodes.reserve(n_trunk + n_limb)
    for i in range(n_trunk):
        nodes.pos[i] = (0.0, 0.3 * i, 0.0)
        nodes.parent[i], nodes.depth[i], nodes.ring[i] = i - 1, i, 4000.0      # thick, stiff
    for j in range(n_limb):
        k = n_trunk + j
        nodes.pos[k] = (0.3 * (j + 1), 0.3 * (n_trunk - 1), 0.0)
        nodes.parent[k], nodes.depth[k], nodes.ring[k] = k - 1, k, 30.0       # a slender limb
    tip = nodes.n - 1
    tip_before, trunk_before = nodes.pos[tip].copy(), nodes.pos[:n_trunk].copy()
    _sag(nodes, bud_oak, wood_set=1.0)
    assert nodes.pos[tip, 1] < tip_before[1] - 0.01                         # the limb tip dropped
    assert np.allclose(nodes.pos[:n_trunk], trunk_before, atol=1e-3)       # the trunk barely moved
