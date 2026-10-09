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
    p = {**small_oak, "envelope": {**small_oak["envelope"], "shade_response": 1.0}}
    assert envelope_response(p, Scene())(80) == (1.0, (0.0, 0.0))
    a = grow(small_oak, Scene(), 7, 30)
    b = grow(p, Scene(), 7, 30)
    assert np.array_equal(a.pos, b.pos)


def test_shade_response_makes_a_forest_tree_taller_and_narrower(small_oak):
    from treegen.core.growth import envelope_response
    from treegen.metrics import tree_metrics
    forest = load_scene(ROOT / "scenes" / "dense_forest.toml")
    p = {**small_oak, "envelope": {**small_oak["envelope"], "shade_response": 0.6}}
    stretch, lean = envelope_response(p, forest)(80)
    assert stretch > 1.2                                  # the stand shades the crown's sides
    assert abs(lean[0]) < 0.05 and abs(lean[1]) < 0.05    # ...evenly, so no lean
    plain = tree_metrics(assemble_skeleton(grow(small_oak, forest, 5, 80), small_oak, 5))
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
