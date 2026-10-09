from treegen.core.growth import grow
from treegen.metrics import tree_metrics
from treegen.pipeline import assemble_skeleton
from treegen.schema import Scene


def test_metrics_are_sane(small_oak):
    m = tree_metrics(assemble_skeleton(grow(small_oak, Scene(), 3, 40), small_oak, 3))
    assert 0 < m["crown_base"] < m["height"]
    assert 0 < m["bole_fraction"] < 1
    assert m["crown_width"] > 0 and m["dbh"] > 0 and m["branches"] > 0
    assert abs(m["crown_offset_x"]) < 0.25 * m["crown_width"]     # open field: no lean
