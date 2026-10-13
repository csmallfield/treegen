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
    n = len(young.pos)
    assert n < len(old.pos)
    assert np.allclose(young.pos, old.pos[:n]) and np.array_equal(young.parent, old.parent[:n])


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
