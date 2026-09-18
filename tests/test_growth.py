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
