"""USD authoring — the output contract (design doc §6, docs/USD_CONTRACT.md).

    <name>.usda                root: defaultPrim, upAxis, metersPerUnit, sublayers
      <name>.materials.usda    bindings + placeholder materials   (strongest)
      <name>.geometry.usdc     derived meshes, twigs, proxy, foliage hook
      <name>.skeleton.usdc     authoritative skeleton              (weakest)

Sidecar layers are prefixed with the root's stem so several trees can share a folder.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, Vt

from .. import __version__

TREE = Sdf.Path("/Tree")


def _stage(path: Path):
    layer = Sdf.Layer.CreateNew(str(path))
    stage = Usd.Stage.Open(layer)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    stage.SetDefaultPrim(stage.DefinePrim(TREE, "Xform"))
    return stage


def _v3f(a):
    return Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(a, dtype=np.float32))


def _pv(gprim, name, vtype, values, interp):
    api = UsdGeom.PrimvarsAPI(gprim)
    pv = api.CreatePrimvar(name, vtype, interp)
    pv.Set(values)
    return pv


def _extent(points, pad=0.0):
    if len(points) == 0:
        return Vt.Vec3fArray([Gf.Vec3f(0), Gf.Vec3f(0)])
    lo, hi = points.min(0) - pad, points.max(0) + pad
    return Vt.Vec3fArray([Gf.Vec3f(*map(float, lo)), Gf.Vec3f(*map(float, hi))])


# ------------------------------------------------------------------------------------ skeleton
def write_skeleton(stage, sk, info: dict):
    tree = stage.GetPrimAtPath(TREE)
    Usd.ModelAPI(tree).SetKind("component")
    tree.SetCustomDataByKey("treegen", info)

    cv = UsdGeom.BasisCurves.Define(stage, TREE.AppendChild("Skeleton"))
    cv.CreatePurposeAttr(UsdGeom.Tokens.guide)
    cv.CreateTypeAttr(UsdGeom.Tokens.linear)
    cv.CreateCurveVertexCountsAttr(Vt.IntArray.FromNumpy(sk.counts.astype(np.int32)))
    cv.CreatePointsAttr(_v3f(sk.points))
    cv.CreateWidthsAttr(Vt.FloatArray.FromNumpy((2 * sk.radius).astype(np.float32)))
    cv.SetWidthsInterpolation(UsdGeom.Tokens.vertex)
    cv.CreateExtentAttr(_extent(sk.points, float(sk.radius.max())))
    T = Sdf.ValueTypeNames
    U, V = UsdGeom.Tokens.uniform, UsdGeom.Tokens.vertex
    _pv(cv, "parentIndex", T.IntArray, Vt.IntArray.FromNumpy(sk.parent_curve.astype(np.int32)), U)
    _pv(cv, "parentPointIndex", T.IntArray, Vt.IntArray.FromNumpy(sk.parent_point.astype(np.int32)), U)
    _pv(cv, "branchOrder", T.IntArray, Vt.IntArray.FromNumpy(sk.order.astype(np.int32)), U)
    _pv(cv, "branchPath", T.StringArray, Vt.StringArray(list(sk.path)), U)
    _pv(cv, "arcLength", T.FloatArray, Vt.FloatArray.FromNumpy(sk.arc_length.astype(np.float32)), V)
    _pv(cv, "radius", T.FloatArray, Vt.FloatArray.FromNumpy(sk.radius.astype(np.float32)), V)
    _pv(cv, "downstreamMass", T.FloatArray, Vt.FloatArray.FromNumpy(sk.downstream_mass.astype(np.float32)), V)
    _pv(cv, "lightExposure", T.FloatArray, Vt.FloatArray.FromNumpy(sk.light_exposure.astype(np.float32)), V)
    _pv(cv, "birthAge", T.FloatArray, Vt.FloatArray.FromNumpy(sk.birth_age.astype(np.float32)), V)
    _pv(cv, "isDead", T.IntArray, Vt.IntArray.FromNumpy(sk.dead.astype(np.int32)), V)


# ------------------------------------------------------------------------------------ geometry
def _write_mesh(stage, path, md, sk, params, subsets=True, purpose=None):
    mesh = UsdGeom.Mesh.Define(stage, path)
    if purpose:
        mesh.CreatePurposeAttr(purpose)
    mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    mesh.CreatePointsAttr(_v3f(md.points))
    mesh.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(md.face_counts.astype(np.int32)))
    mesh.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(md.face_indices.astype(np.int32)))
    mesh.CreateExtentAttr(_extent(md.points))
    if md.empty:
        mesh.CreateVisibilityAttr(UsdGeom.Tokens.invisible)
        return mesh
    if purpose:
        return mesh
    T = Sdf.ValueTypeNames
    V = UsdGeom.Tokens.vertex
    st = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar("st", T.TexCoord2fArray, V)
    st.Set(Vt.Vec2fArray.FromNumpy(md.st.astype(np.float32)))
    _pv(mesh, "skelPointIndex", T.IntArray, Vt.IntArray.FromNumpy(md.skel_index.astype(np.int32)), V)
    _pv(mesh, "skelLocalOffset", T.Vector3fArray, _v3f(md.skel_offset), V)
    _pv(mesh, "branchOrder", T.IntArray, Vt.IntArray.FromNumpy(md.face_order.astype(np.int32)), UsdGeom.Tokens.uniform)
    _pv(mesh, "arcLength", T.FloatArray, Vt.FloatArray.FromNumpy(sk.arc_length[md.skel_index].astype(np.float32)), V)

    if subsets:
        img = UsdGeom.Imageable(mesh)
        for o in np.unique(md.face_order):
            faces = np.flatnonzero(md.face_order == o).astype(np.int32)
            UsdGeom.Subset.CreateGeomSubset(img, f"order_{int(o)}", UsdGeom.Tokens.face,
                                            Vt.IntArray.FromNumpy(faces), "branchOrder",
                                            UsdGeom.Tokens.partition)
        for lo, hi, mat in params["materials"]["order_ranges"]:
            faces = np.flatnonzero((md.face_order >= int(lo)) & (md.face_order <= int(hi))).astype(np.int32)
            if len(faces):
                UsdGeom.Subset.CreateGeomSubset(img, f"mat_{mat}", UsdGeom.Tokens.face,
                                                Vt.IntArray.FromNumpy(faces), UsdShade.Tokens.materialBind)
        UsdGeom.Subset.SetFamilyType(img, UsdShade.Tokens.materialBind, UsdGeom.Tokens.nonOverlapping)
    return mesh


def write_geometry(stage, geo, sk, params):
    UsdGeom.Scope.Define(stage, TREE.AppendChild("Geom"))
    g = TREE.AppendPath("Geom")
    _write_mesh(stage, g.AppendChild("Trunk"), geo.trunk, sk, params)
    _write_mesh(stage, g.AppendChild("Branches"), geo.branches, sk, params)

    tw = UsdGeom.BasisCurves.Define(stage, g.AppendChild("Twigs"))
    tw.CreateTypeAttr(UsdGeom.Tokens.linear)
    c = geo.twigs
    tw.CreateCurveVertexCountsAttr(Vt.IntArray.FromNumpy(c.counts.astype(np.int32)))
    tw.CreatePointsAttr(_v3f(c.points))
    tw.CreateWidthsAttr(Vt.FloatArray.FromNumpy(c.widths.astype(np.float32)))
    tw.SetWidthsInterpolation(UsdGeom.Tokens.vertex)
    tw.CreateExtentAttr(_extent(c.points, float(c.widths.max() / 2) if len(c.widths) else 0.0))
    T = Sdf.ValueTypeNames
    V = UsdGeom.Tokens.vertex
    _pv(tw, "skelPointIndex", T.IntArray, Vt.IntArray.FromNumpy(c.skel_index.astype(np.int32)), V)
    _pv(tw, "skelLocalOffset", T.Vector3fArray, _v3f(np.zeros((len(c.points), 3))), V)
    _pv(tw, "branchOrder", T.IntArray, Vt.IntArray.FromNumpy(c.order.astype(np.int32)), UsdGeom.Tokens.uniform)
    _pv(tw, "arcLength", T.FloatArray, Vt.FloatArray.FromNumpy(c.arc_length.astype(np.float32)), V)

    _write_mesh(stage, TREE.AppendChild("Proxy"), geo.proxy, sk, params, subsets=False, purpose=UsdGeom.Tokens.proxy)
    UsdGeom.Imageable(stage.GetPrimAtPath(g)).CreatePurposeAttr(UsdGeom.Tokens.render)

    fol = UsdGeom.PointInstancer.Define(stage, TREE.AppendChild("Foliage"))
    fol.CreatePrototypesRel()
    fol.CreateProtoIndicesAttr(Vt.IntArray())
    fol.CreatePositionsAttr(Vt.Vec3fArray())
    fol.GetPrim().SetDocumentation("Phase 2 hook: leaf instances bind via skelPointIndex / skelLocalOffset.")


# ----------------------------------------------------------------------------------- materials
_COLORS = {"bark_mature": (0.20, 0.16, 0.12), "bark_young": (0.33, 0.27, 0.20)}


def write_materials(stage, geo, params):
    UsdGeom.Scope.Define(stage, TREE.AppendChild("Materials"))
    mats = {}
    for _, _, name in params["materials"]["order_ranges"]:
        if name in mats:
            continue
        mpath = TREE.AppendPath(f"Materials/{name}")
        mat = UsdShade.Material.Define(stage, mpath)
        sh = UsdShade.Shader.Define(stage, mpath.AppendChild("PreviewSurface"))
        sh.CreateIdAttr("UsdPreviewSurface")
        col = _COLORS.get(name, (0.3, 0.25, 0.2))
        sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*col))
        sh.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.9)
        mat.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")
        mats[name] = mat

    for mesh_name, md in (("Trunk", geo.trunk), ("Branches", geo.branches)):
        if md.empty:
            continue
        for lo, hi, name in params["materials"]["order_ranges"]:
            if np.any((md.face_order >= int(lo)) & (md.face_order <= int(hi))):
                sub = stage.OverridePrim(TREE.AppendPath(f"Geom/{mesh_name}/mat_{name}"))
                UsdShade.MaterialBindingAPI.Apply(sub).Bind(mats[name])
    if len(geo.twigs.order):
        # Curves have no reliable per-curve material subsets across renderers, so the Twigs prim is
        # bound to the material covering most of its curves (thin order-1 branches demoted to curves
        # are a minority). Per-curve lookdev should key off primvars:branchOrder instead.
        counts = {}
        for lo, hi, name in params["materials"]["order_ranges"]:
            counts[name] = counts.get(name, 0) + int(np.sum((geo.twigs.order >= int(lo)) & (geo.twigs.order <= int(hi))))
        name = max(counts, key=lambda k: counts[k])
        if counts[name] > 0:
            tw = stage.OverridePrim(TREE.AppendPath("Geom/Twigs"))
            UsdShade.MaterialBindingAPI.Apply(tw).Bind(mats[name])


# ---------------------------------------------------------------------------------------- root
def write_tree(out: str | Path, sk, geo, params: dict, info: dict, flatten: bool = False) -> list[Path]:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    stem = out.stem
    paths = {k: out.with_name(f"{stem}.{k}.{ext}") for k, ext in
             (("skeleton", "usdc"), ("geometry", "usdc"), ("materials", "usda"))}
    for p in list(paths.values()) + [out]:
        if p.exists():
            p.unlink()

    s = _stage(paths["skeleton"]); write_skeleton(s, sk, info); s.GetRootLayer().Save()
    s = _stage(paths["geometry"]); write_geometry(s, geo, sk, params); s.GetRootLayer().Save()
    s = _stage(paths["materials"]); write_materials(s, geo, params); s.GetRootLayer().Save()

    root = _stage(out)
    root.GetRootLayer().subLayerPaths = [f"./{paths[k].name}" for k in ("materials", "geometry", "skeleton")]
    root.GetRootLayer().comment = f"treegen {__version__} — {info.get('species')} seed={info.get('seed')} age={info.get('age')}"
    root.GetRootLayer().Save()

    if flatten:
        flat = root.Flatten()
        for p in list(paths.values()) + [out]:
            p.unlink()
        flat.Export(str(out))
        return [out]
    return [out] + list(paths.values())
