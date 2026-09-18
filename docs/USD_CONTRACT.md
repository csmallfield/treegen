# USD output contract (Phase 1, v1)

Changing anything here breaks downstream work. Additive changes only without a version bump.

## Layers

```
oak.usda                   root: defaultPrim=/Tree, upAxis=Y, metersPerUnit=1
  subLayers (strongest first)
    oak.materials.usda     placeholder UsdPreviewSurface materials + bindings — override freely
    oak.geometry.usdc      derived from the skeleton; safe to regenerate at another --lod
    oak.skeleton.usdc      AUTHORITATIVE
```

Sidecars are prefixed with the root's stem so trees can share a directory.
`/Tree.customData["treegen"]` records version, species, seed, age, scene, lod and paramsHash.

## Prims

| Path | Type | Purpose | Notes |
| --- | --- | --- | --- |
| `/Tree` | Xform | default | kind = component |
| `/Tree/Skeleton` | BasisCurves (linear) | guide | one curve per branch chain |
| `/Tree/Geom/Trunk` | Mesh | render | order-0 chains; GeomSubsets `order_N` (family branchOrder, partition) and `mat_<name>` (materialBind) |
| `/Tree/Geom/Branches` | Mesh | render | other `mesh_orders` with base radius >= `min_radius_for_mesh` |
| `/Tree/Geom/Twigs` | BasisCurves (linear) | render | everything else; `widths` per vertex |
| `/Tree/Proxy` | Mesh | proxy | low-res orders 0–1 |
| `/Tree/Foliage` | PointInstancer | default | empty Phase 2 hook |
| `/Tree/Materials/*` | Material | — | names from `[materials].order_ranges` |

## Skeleton primvars

| Primvar | Type | Interp | Meaning |
| --- | --- | --- | --- |
| `widths` (attribute) | float[] | vertex | **diameter** = 2 × radius (USD convention) |
| `radius` | float[] | vertex | radius in metres |
| `parentIndex` | int[] | uniform | parent curve index, −1 for the root |
| `parentPointIndex` | int[] | uniform | flat point index of the attach point on the parent curve, −1 for the root. *Addition to the design doc; required to rebuild frames.* |
| `branchOrder` | int[] | uniform | 0 = trunk / co-dominant stems |
| `branchPath` | string[] | uniform | hierarchical name, e.g. `b0_3_1` |
| `arcLength` | float[] | vertex | metres from the tree base along the hierarchy |
| `downstreamMass` | float[] | vertex | kg of wood beyond this point (750 kg/m³) — wind stiffness channel |
| `lightExposure` | float[] | vertex | 0–1 sky visibility at final age |
| `birthAge` | float[] | vertex | year the node grew |
| `isDead` | int[] | vertex | 1 for dead stubs / dieback |

Curves are ordered parents-first. The first point of every non-root curve coincides
with its attach point on the parent (`parentPointIndex`).

## Geometry primvars

| Primvar | Type | Interp | Meaning |
| --- | --- | --- | --- |
| `skelPointIndex` | int[] | vertex | flat index into `/Tree/Skeleton.points` |
| `skelLocalOffset` | vector3f[] | vertex | offset in that point's (T, N, B) frame |
| `st` | texCoord2f[] | vertex | metres: U around (base circumference), V = arcLength; seam duplicated |
| `branchOrder` | int[] | uniform | per face (meshes) / per curve (twigs) |
| `arcLength` | float[] | vertex | copied from the bound skeleton point |

## Frame convention (rebind)

1. `T[i]` = normalize(P[i+1] − P[i−1]); forward/backward difference at the ends.
2. Root curve: `N[0]` = world +X projected onto the plane ⟂ T[0] (world +Z if degenerate).
3. Other curves: `N[0]` = parent's `N[parentPointIndex]` projected onto the plane ⟂ T[0].
4. `N[i]` = minimal rotation of `N[i−1]` taking `T[i−1]` → `T[i]`, re-orthogonalised. `B = T × N`.

Reconstruction: `P = S[k] + o.x·T[k] + o.y·N[k] + o.z·B[k]`, with `k = skelPointIndex`
and `o = skelLocalOffset`. The reference implementation is `treegen.geo.sweep.rebind_points`.

Houdini sketch (Detail wrangle on the geometry; skeleton frames precomputed into point attribs `T, N`):

```c
int k = i@skelPointIndex;
vector S = point(1, "P", k), T = point(1, "T", k), N = point(1, "N", k);
vector B = cross(T, N);
vector o = v@skelLocalOffset;
@P = S + o.x*T + o.y*N + o.z*B;
```
