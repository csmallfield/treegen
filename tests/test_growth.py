import numpy as np

from conftest import ROOT
from treegen.core.age import age_state
from treegen.core.growth import grow
from treegen.pipeline import assemble_skeleton
from treegen.schema import Scene, load_scene


def test_deterministic(small_oak):
    a = grow(small_oak, Scene(), 7, 30)
    b = grow(small_oak, Scene(), 7, 30)
    assert np.array_equal(a.pos, b.pos) and np.array_equal(a.state, b.state)


def test_seed_changes_tree(small_oak):
    a = grow(small_oak, Scene(), 1, 30)
    b = grow(small_oak, Scene(), 2, 30)
    assert len(a.pos) != len(b.pos) or not np.allclose(a.pos, b.pos)


def test_species_file_true_at_reference_age(small_oak):
    st = age_state(small_oak, small_oak["meta"]["reference_age"])
    assert abs(st.height_mult - 1) < 1e-9 and abs(st.crown_flatten_delta) < 1e-9


def test_young_tree_is_prefix_of_old_tree(small_oak):
    """Same seed: the first N nodes of the old tree match the young tree before any epoch diverges."""
    young = grow(small_oak, Scene(), 3, 4)
    old = grow(small_oak, Scene(), 3, 12)
    n = len(young.pos)
    assert np.allclose(young.pos, old.pos[:n])


def test_forest_sheds_more_than_open(small_oak):
    forest = load_scene(ROOT / "scenes" / "dense_forest.toml")
    o = assemble_skeleton(grow(small_oak, Scene(), 5, 80), small_oak, 5)
    f = assemble_skeleton(grow(small_oak, forest, 5, 80), small_oak, 5)
    # lowest living branch attaches higher in the forest
    def first_branch_height(sk):
        pts = sk.parent_point[sk.parent_point >= 0]
        return sk.points[pts, 1].min()
    assert first_branch_height(f) > first_branch_height(o)


def test_shade_response_leaves_the_open_field_alone(small_oak):
    from treegen.core.growth import envelope_response
    off = {**small_oak, "envelope": {**small_oak["envelope"], "shade_response": 0.0}}
    p = {**small_oak, "envelope": {**small_oak["envelope"], "shade_response": 1.0}}
    assert envelope_response(p, Scene())(80) == (1.0, (0.0, 0.0))
    a = grow(off, Scene(), 7, 30)
    b = grow(p, Scene(), 7, 30)
    assert np.array_equal(a.pos, b.pos)


def test_shade_response_makes_a_forest_tree_taller_and_narrower(small_oak):
    from treegen.core.growth import envelope_response
    from treegen.metrics import tree_metrics
    forest = load_scene(ROOT / "scenes" / "dense_forest.toml")
    off = {**small_oak, "envelope": {**small_oak["envelope"], "shade_response": 0.0}}
    p = {**small_oak, "envelope": {**small_oak["envelope"], "shade_response": 0.6}}
    stretch, lean = envelope_response(p, forest)(80)
    assert stretch > 1.2                                  # the stand shades the crown's sides
    assert abs(lean[0]) < 0.05 and abs(lean[1]) < 0.05    # ...evenly, so no lean
    plain = tree_metrics(assemble_skeleton(grow(off, forest, 5, 80), off, 5))
    shaded = tree_metrics(assemble_skeleton(grow(p, forest, 5, 80), p, 5))
    assert shaded["height"] > plain["height"] * 1.15
    assert shaded["crown_base"] > plain["crown_base"]
    assert shaded["crown_width"] < plain["crown_width"]


def test_shade_response_leans_the_edge_tree_toward_the_clearing(small_oak):
    from treegen.core.envelope import Envelope
    from treegen.core.growth import envelope_response
    edge = load_scene(ROOT / "scenes" / "forest_edge.toml")      # stand on -X, clearing on +X
    p = {**small_oak, "envelope": {**small_oak["envelope"], "shade_response": 0.6}}
    stretch, (lx, lz) = envelope_response(p, edge)(80)
    assert lx > 0.1 and abs(lz) < 0.05
    env = Envelope.at_age(p, age_state(p, 80), stretch, (lx, lz))
    t = 0.9
    ax = env.axis_at(t)[0]
    edge_pt = np.array([[env.radius_at(t) + 0.5 * ax, t * env.height, 0.0]])   # rim, clearing side
    mirrored = edge_pt * [-1, 1, 1]                                            # same point, stand side
    assert env.inside(edge_pt)[0] and not env.inside(mirrored)[0]             # the axis really moved


def _with(params, group, **kw):
    return {**params, group: {**params[group], **kw}}


def test_bole_height_raises_the_first_fork(small_oak):
    from treegen.metrics import tree_metrics
    off = _with(small_oak, "architecture", bole_height=0.0)
    plain = tree_metrics(assemble_skeleton(grow(off, Scene(), 5, 80), off, 5))
    p = _with(small_oak, "architecture", bole_height=4.0)
    lifted = tree_metrics(assemble_skeleton(grow(p, Scene(), 5, 80), p, 5))
    assert lifted["first_fork"] >= 4.0 > plain["first_fork"]
    assert lifted["height"] > 0.8 * plain["height"]                 # the crown survives


def test_bole_height_never_sheds_the_crown(small_oak):
    """After the leader dies back the crown rides on a former side limb; lifting must follow it."""
    edge = load_scene(ROOT / "scenes" / "forest_edge.toml")
    p = _with(small_oak, "architecture", bole_height=5.0)
    sk = assemble_skeleton(grow(p, edge, 42, 200), p, 42)
    assert sk.height > 15.0


def test_trunk_thickness_scales_the_trunk_not_the_twigs(small_oak):
    g = grow(small_oak, Scene(), 5, 40)
    one = _with(small_oak, "radii", trunk_thickness=1.0)
    a = assemble_skeleton(g, one, 5)
    p = _with(small_oak, "radii", trunk_thickness=1.5)
    b = assemble_skeleton(g, p, 5)
    assert abs(b.radius.max() / a.radius.max() - 1.5) < 1e-6
    assert b.radius.min() / a.radius.min() < 1.1                    # twigs barely change



def test_max_unbranched_stops_whips(small_oak):
    from treegen.metrics import tree_metrics
    off = _with(small_oak, "architecture", max_unbranched=0.0)
    on = _with(small_oak, "architecture", max_unbranched=4.0)
    a = tree_metrics(assemble_skeleton(grow(off, Scene(), 42, 120), off, 42))
    b = tree_metrics(assemble_skeleton(grow(on, Scene(), 42, 120), on, 42))
    assert b["limb_ld_p95"] < 0.85 * a["limb_ld_p95"]           # limbs fork before they whip
    assert abs(b["height"] - a["height"]) < 0.1 * a["height"]    # without reshaping the tree


def test_growth_cache_key_tracks_the_code():
    from treegen.pipeline import CODE_HASH
    assert len(CODE_HASH) == 8
