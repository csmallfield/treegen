import numpy as np
from pxr import Usd, UsdGeom

from treegen.geo.sweep import build_geometry, rebind_points
from treegen.pipeline import Pipeline
from treegen.schema import Scene
from treegen.usd.stage import write_tree


def _tree(small_oak):
    return Pipeline().geometry(small_oak, Scene(), 11, 40)


def test_stage_contract(small_oak, tmp_path):
    g, sk, geo = _tree(small_oak)
    files = write_tree(tmp_path / "t.usda", sk, geo, small_oak, {"species": "t", "seed": 11, "age": 40.0})
    assert len(files) == 4
    st = Usd.Stage.Open(str(files[0]))
    assert UsdGeom.GetStageUpAxis(st) == "Y" and UsdGeom.GetStageMetersPerUnit(st) == 1.0
    assert st.GetDefaultPrim().GetPath() == "/Tree"
    for path in ("/Tree/Skeleton", "/Tree/Geom/Trunk", "/Tree/Geom/Branches", "/Tree/Geom/Twigs",
                 "/Tree/Proxy", "/Tree/Foliage"):
        assert st.GetPrimAtPath(path).IsValid(), path
    skel = UsdGeom.PrimvarsAPI(st.GetPrimAtPath("/Tree/Skeleton"))
    for name in ("parentIndex", "parentPointIndex", "branchOrder", "arcLength", "radius",
                 "downstreamMass", "lightExposure", "birthAge"):
        assert skel.HasPrimvar(name), name
    trunk = UsdGeom.PrimvarsAPI(st.GetPrimAtPath("/Tree/Geom/Trunk"))
    assert trunk.HasPrimvar("skelPointIndex") and trunk.HasPrimvar("skelLocalOffset")


def test_rebind_roundtrip(small_oak):
    g, sk, geo = _tree(small_oak)
    md = geo.branches if len(geo.branches.points) else geo.trunk
    rebuilt = rebind_points(sk.points, sk.offsets, sk.parent_curve, sk.parent_point, md.skel_index, md.skel_offset)
    assert np.abs(rebuilt - md.points).max() < 1e-6


def test_rebind_follows_deformation(small_oak):
    """Rigidly tilt the skeleton: geometry rebuilt from it must be the same rotation of the original."""
    g, sk, geo = _tree(small_oak)
    md = geo.trunk
    # The root frame is seeded from world +X, so the convention is not invariant to arbitrary
    # rotations — but it is stable under the small tilts a wind sim produces.
    t = 0.05
    Rt = np.array([[1, 0, 0], [0, np.cos(t), -np.sin(t)], [0, np.sin(t), np.cos(t)]])
    P2 = sk.points @ Rt.T
    rebuilt = rebind_points(P2, sk.offsets, sk.parent_curve, sk.parent_point, md.skel_index, md.skel_offset)
    expected = md.points @ Rt.T
    assert np.abs(rebuilt - expected).max() < 0.05 * sk.radius.max() + 1e-3


def test_lod_changes_geometry_not_skeleton(small_oak):
    pl = Pipeline()
    _, sk1, geo1 = pl.geometry(small_oak, Scene(), 11, 40, lod=1.0)
    _, sk2, geo2 = pl.geometry(small_oak, Scene(), 11, 40, lod=0.5)
    assert sk1 is sk2
    assert len(geo2.trunk.points) < len(geo1.trunk.points)
