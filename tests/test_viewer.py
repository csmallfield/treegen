import json
import struct

import numpy as np

from conftest import ROOT
from treegen.core.growth import grow, truncate
from treegen.schema import load_species, validate
from treegen.viewer.server import Session, to_toml


def _meta(blob):
    n = struct.unpack("<I", blob[:4])[0]
    return json.loads(blob[4:4 + n])


def _arrays(blob):
    meta = _meta(blob)
    off = 4 + struct.unpack("<I", blob[:4])[0]
    out = {}
    for a in meta["arrays"]:
        n = a["length"] * 4
        dt = np.uint32 if a["type"] == "u32" else np.float32
        out[a["name"]] = np.frombuffer(blob[off:off + n], dtype=dt)
        off += n
    assert off == len(blob)
    return meta, out


def test_payload_is_self_describing(small_oak, tmp_path):
    s = Session(ROOT, None)
    blob = s.generate({"params": small_oak, "seed": 2, "age": 30})
    meta, arr = _arrays(blob)
    assert meta["curves"] > 0 and meta["fast"] is False
    assert len(arr["mesh_pos"]) % 3 == 0 and len(arr["mesh_idx"]) % 3 == 0
    assert arr["mesh_idx"].max() < len(arr["mesh_pos"]) // 3       # indices in range
    assert len(arr["mesh_exposure"]) == len(arr["mesh_pos"]) // 3  # one channel value per vertex
    assert len(arr["twig_idx"]) % 2 == 0


def test_fast_scrub_is_cheaper_and_close_enough(small_oak):
    s = Session(ROOT, None)
    exact = _meta(s.generate({"params": small_oak, "seed": 2, "age": 40, "fast": False}))
    s.generate({"params": small_oak, "seed": 2, "age": small_oak["meta"]["max_age"], "fast": True})  # warm
    fast = _meta(s.generate({"params": small_oak, "seed": 2, "age": 40, "fast": True}))
    assert fast["elapsed"] < exact["elapsed"]
    assert abs(fast["height"] - exact["height"]) < 0.25 * exact["height"]


def test_truncate_keeps_a_valid_tree(small_oak):
    from treegen.schema import Scene
    g = grow(small_oak, Scene(), 6, 40)
    t = truncate(g, 15)
    assert len(t.pos) < len(g.pos)
    assert t.birth.max() <= 15
    assert np.all(t.parent[1:] >= 0) and np.all(t.parent < np.arange(len(t.parent)))


def test_species_toml_roundtrip(tmp_path):
    params = load_species(ROOT / "species" / "quercus.toml")
    p = tmp_path / "rt.toml"
    p.write_text(to_toml(params), encoding="utf-8")
    assert load_species(p) == params


def test_viewer_rejects_bad_parameters():
    import pytest
    from treegen.schema import SchemaError
    with pytest.raises(SchemaError):
        validate({"radii": {"pipe_exponent": 99}}, "viewer")


def test_tiers_cover_every_group():
    from treegen.schema import GROUPS
    from treegen.viewer.server import TIERS
    assert set(TIERS) == set(GROUPS)
    assert TIERS["geometry"] == 1 and TIERS["refinement"] == 2 and TIERS["growth"] == 3


def test_cheap_tier_edit_reuses_the_simulation(small_oak):
    import copy
    s = Session(ROOT, None)
    first = _meta(s.generate({"params": small_oak, "seed": 2, "age": 30}))
    edited = copy.deepcopy(small_oak)
    edited["refinement"]["gravity_droop"] = 1.4
    again = _meta(s.generate({"params": edited, "seed": 2, "age": 30}))
    assert again["elapsed"] < first["elapsed"] / 4       # growth came from cache


def test_debug_arrays_are_optional(small_oak):
    s = Session(ROOT, None)
    plain = {a["name"] for a in _meta(s.generate({"params": small_oak, "seed": 2, "age": 30}))["arrays"]}
    debug = {a["name"] for a in _meta(s.generate({"params": small_oak, "seed": 2, "age": 30, "debug": True}))["arrays"]}
    assert "attr_pos" not in plain
    assert {"attr_pos", "attr_state", "env_profile"} <= debug


def test_variant_renders_a_png(small_oak):
    from treegen.viewer.jobs import render_variant
    seed, png = render_variant(str(ROOT), small_oak, 5, 25, None)
    assert seed == 5 and png[:4] == b"\x89PNG"


def test_neighbour_rasterisation_is_cached():
    """The bounded + cached neighbour grid must not change results, only cost."""
    import numpy as np
    from treegen.core.light import LightField, _NB_CACHE
    from treegen.schema import load_scene
    sc = load_scene(ROOT / "scenes" / "dense_forest.toml")
    kw = dict(bounds_min=[-20, 0, -20], bounds_max=[20, 26, 20], resolution=32, neighbours=sc.neighbours,
              tips=np.zeros((0, 3)), foliage_density=0.0, ray_count=8, sky_bias=1.5)
    a = LightField.build(**kw, neighbour_height_scale=1.0)
    _NB_CACHE.clear()
    b = LightField.build(**kw, neighbour_height_scale=1.0)
    assert np.array_equal(a.density, b.density) and a.density.sum() > 0
