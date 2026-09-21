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
