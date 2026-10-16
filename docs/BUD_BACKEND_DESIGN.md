# Bud-based growth backend: design

Status: 2026-10-15. Open-field oak adopted (rank 3, section 16); shadow fade checked (section 17). Next: forest and edge scenes (B3).

## 1. Why

treegen's premise is that trees come from an established simulation, not from
artistic rules. The current growth core is space colonization (Runions et al.
2007). That algorithm fills a given volume with branches. It has no time, no
buds, no resources and no age, and the crown shape is an input (the envelope).
Run as a 200-year simulation it produced a string of artefacts, each patched
with a rule: even fork spacing, whips, looping limbs, a first fork at 1.4 m,
forest trees no taller than open ones, flat-topped old crowns. About fourteen
rules now sit on top of it, and old trees are shaped almost entirely by those
rules interacting. `docs/HANDOFF.md` has the measurements for each.

The model that matches the premise is the self-organising, bud-based one of
Palubicki et al. 2009 ("Self-organizing tree models for image synthesis"),
the successor to space colonization from the same group. Its unit is the bud.
Structure is decided by light and by a resource budget, year by year, and the
crown shape is an output.

This document proposes building that as a second growth backend, comparing it
with the current one on the measurements already in the repo, and deleting the
patch rules if it wins.

**B0, done 2026-10-13:** section 3 was first written from memory and has now
been checked against the paper (Palubicki et al., SIGGRAPH 2009, sections 4.1
to 4.5 and 6). Section 3 below is the corrected version; section 3.7 lists
what changed and where treegen deliberately departs from the paper.

## 2. Scope

**Replaced:** the growth stage only, `treegen/core/growth.py`: attractor
seeding, the colonization loop, and the rules added to it.

**Kept unchanged:**

| Stage | Why it is unaffected |
| --- | --- |
| Light field (`core/light.py`) | Already what a bud model needs: exposure and light direction at any point, from own foliage plus neighbour proxies |
| Skeleton extraction, pipe-model radii, refinement | Work on the node graph (`pos`, `parent`, `birth`, `state`) and never look at how it was grown |
| Geometry, USD contract, viewer, previews, CLI | Downstream of the skeleton |
| Metrics and the definition-of-done batch | The yardstick for the comparison |
| Stage caching | Keyed on parameters and on a hash of the growth sources |

**Two couplings to fix,** found by searching the downstream code:

- `geo/sweep.py` takes mesh ring spacing from `growth.step_length`. Bud-model
  internodes vary in length, so ring spacing should come from the skeleton's
  own segment lengths.
- The viewer's debug overlay draws the attractor cloud. The bud backend has no
  attractors; the overlay would show buds by state (growing, dormant, dead).

**Out of scope:** roots, foliage, wood mechanics beyond what refinement already
does, and the twig-cluster geometry pass (still wanted, still separate).

## 3. The model

Time advances one growing season per step. The tree is a graph of internodes;
every node carries buds. Each step runs five passes over the whole tree.

### 3.1 Light

Rebuild the occlusion field from the tree's foliage-bearing tips and the
neighbour proxies, as now. Sample exposure `Q` in [0, 1] and the light
direction at every live bud. No change to `core/light.py`.

### 3.2 Resource: gather, then allocate (Borchert-Honda)

- **Gather, tips to root.** Each internode's `Q` is the sum of the light its
  buds and child branches collected.
- **Allocate, root to tips.** The root receives `v = alpha * Q_root`. At each
  fork the resource is split between the main axis and the lateral in
  proportion to the light each side gathered, weighted by apical control
  `lambda`: `lambda` for the main axis, `1 - lambda` for the lateral.
  `lambda > 0.5` favours leaders (excurrent, conifer-like); `lambda < 0.5`
  favours laterals (decurrent, a spreading oak crown). This is the same
  allocation `_vigor` already implements, used here as the driver, not as a
  growth-probability modifier.

### 3.3 Bud fate

A bud with resource `v` grows a shoot of `n = floor(v)` internodes, each of
length `shoot_length * v / n` (the paper's `l = v / n`, in metres). A bud with
`v < 1` stays dormant and may grow in a later season. Development is
proleptic: a bud formed this season cannot grow until the next. This one rule
is the source of most of what we have been patching:

- vigorous leaders make long internodes, weak outer shoots make short ones:
  long inner runs, clustered branching at the edge;
- no shoot grows without paying for it: no whips;
- most lateral buds never break: fork spacing comes from the budget, not from
  point spacing.

### 3.4 Shoot direction

Each internode of a new shoot points along a weighted sum of three vectors,
normalised:

- the default orientation: the bud's direction for the first internode (the
  supporting internode's direction for a terminal bud; rotated out by the
  branching angle and round by the phyllotactic angle for a lateral), and the
  previous internode's direction after that;
- the optimal growth direction from the light field, weight `xi`;
- a tropism vector (up or down), weight `eta`.

There is no steering toward scattered points, so limbs run as straight as the
two weights allow and change direction where a lateral takes over from a weak
leader: the oak's elbow. Every new node carries one lateral bud, placed by
phyllotaxis; the shoot's end carries the terminal bud.

### 3.5 Shedding

A branch is shed when the light it gathered, per unit of its own size, stays
below `shed_threshold`. Shaded lower branches run at a loss and go: this is
what should produce the clear bole, self-pruning inside the crown and the
raised crown of a forest tree, with no height in metres and no envelope
stretch. Shed limbs keep contributing to the pipe radius of the wood below, as
now.

### 3.6 Age

Age is the number of seasons simulated. A young tree is the first N seasons of
the old one by construction, which keeps the age scrub and the
young-is-a-prefix-of-old property. Old-age form should come from two things
already in the model, not from an authored curve:

- apical control `lambda` reduced over the tree's life, so leaders lose
  dominance and the form goes from excurrent to decurrent. This is the paper's
  own mechanism for mature form;
- self-shading, which limits the light, and so the resource, a large crown
  can gather.

Whether these are enough for a convincing 200-year oak is the main open
question (section 8). Nothing else is to be added for old age unless a
measured failure calls for it.

### 3.7 Checked against the paper (B0)

Corrected from the first draft:

- direction is weighted per internode, not once per shoot (3.4);
- one lateral bud per node, and prolepsis (3.3, 3.4);
- the light-driven variant has no occupancy test; buds compete through light
  alone (removed from 3.3);
- "hydraulic cost" and "bud lifetime" are not in the paper and are removed
  from the model and the parameter table.

Reference values from the paper: `alpha = 2`; `lambda` between 0.46 and 0.54
across its figure of forms, so the model is very sensitive near 0.5; pipe
exponent between 2 and 3; trees of 1,000 to 700,000 internodes over 68 to 106
steps; a light grid cell about one internode long.

Deliberate departures:

| Paper | treegen | Reason |
| --- | --- | --- |
| Shadow propagation: each bud casts a pyramid of shadow downward | The existing occlusion field with hemisphere ray marching | Already built and tested, handles neighbour proxies, and gives exposure and light direction, the two inputs the model needs |
| Exposure `Q = max(C - s + a, 0)` | Exposure in [0, 1] | Follows from the above; `alpha` is calibrated to it |
| Internodes near real size, up to 700,000 | Coarser internodes (about 0.3 m), tens of thousands | Downstream stages and interactive use; fine twigs stay a geometry pass |
| Priority model as an alternative allocator | Extended Borchert-Honda only | One allocator to calibrate first |

The paper also states the diagnosis behind this proposal: trees generated by
spatial competition alone "tend to be quite disorganized", because a branch
can attach anywhere at any orientation.

## 4. The envelope

The design doc names the envelope "the primary art control". In this model the
crown shape is emergent, so the envelope stops being required. Proposal:
`envelope.mode` with three values.

| Mode | Behaviour | Use |
| --- | --- | --- |
| `none` (default for the bud backend) | No envelope. Shape comes from species parameters and light | The simulation as intended |
| `guide` | Buds outside the envelope get reduced resource | Nudging a silhouette |
| `mask` | Buds outside the envelope stay dormant | Hard art direction, a hedge, a clipped street tree |

This is a real change to the product's control surface: less direct control of
silhouette, in exchange for trees whose shape is a consequence of where and how
long they grew. It needs a decision before B3.

## 5. Parameters

Target: nine species parameters with biological meaning, replacing the
`growth`, `architecture` and most of the `envelope` and `age_response` groups.

| Parameter | Meaning |
| --- | --- |
| `shoot_length` | Internode length per unit of resource (m) |
| `resource_per_light` (`alpha`) | Converts gathered light into growth resource |
| `apical_control` (`lambda`) | Share of resource to the main axis at a fork |
| `apical_control_old` | `lambda` at `max_age`; `apical_control` moves to it over the tree's life |
| `branching_angle` | Angle of a lateral bud to its parent internode |
| `phyllotaxis` | Rotation between successive lateral buds (137.5 degrees to start) |
| `light_weight` (`xi`) | Pull toward the optimal light direction (3.4) |
| `tropism` (`eta`) | Pull up (positive) or down (negative) (3.4) |
| `leaf_area` | Leaf area on each leaf-bearing node (m2); sets self-shading. Added in B1 |
| `shed_threshold` | Light per internode below which a branch is shed |

Kept as they are: `light.*`, `radii.*`, `refinement.*`, `geometry.*`,
`materials.*`, `meta.*`.

If the backend is adopted, these go: `attractor_count`, `influence_radius`,
`kill_radius`, `step_length`, `iterations_per_year`, `straightness`,
`trunk_bias`, `max_turn`, `centrifugal`, `bud_years`, `epicormic`,
`branch_angle_max`, `max_unbranched`, `bole_height`, `prune_share`,
`prune_age`, `prune_max_diameter`, `envelope.shade_response`, and the
`age_response` curves for height, apical dominance, shed threshold, crown
flatten and reiteration.

## 6. Integration

- `growth.model = "colonization" | "buds"` selects the backend. The pipeline
  dispatches on it; both return the same `GrowthResult`.
- `GrowthResult` gains nothing required. `attractors` is empty for the bud
  backend; bud positions and states travel in the same debug slot.
- `truncate` (fast age scrub) works unchanged: it only needs `birth` and
  `shed_year`.
- New file `treegen/core/buds.py`. `growth.py` is not edited, so the approved
  oak keeps reproducing exactly while the new backend is developed.
- Expected cost: one light build and one bud sample per season, plus two
  tree passes. 200 steps for a 200-year tree against 400 colonization
  iterations today, so similar or lower. To be measured, not assumed.

## 7. How it will be judged

Same batch as today: oak, seed 42, three ages x three scenes, measured by
`scripts/dod_metrics.py`. Targets are from the reference photo
(`reference/quercus_robur_winter.jpg`), the literature values recorded in
`docs/HANDOFF.md`, and the current oak where it is already right.

| Measure | Current oak (80 y, open) | Target | Must come from |
| --- | --- | --- | --- |
| First fork | 4.2 m, via `bole_height` | 2-5 m open, 6 m+ forest | Shedding, no height setting |
| Wood between forks, inner | 1.0 m | 2.5 m+ | Bud fate |
| Wood between forks, outer | longer than inner | shorter than inner | Bud fate |
| Whips | 0-5, via `max_unbranched` | 0 | Resource budget |
| Sinuosity, 3 m median / 8 m worst 10% (200 y) | 1.13 / 1.50 | about 1.05 / 1.2 | Direction set per shoot |
| Forest vs open height | +40%, via envelope stretch | forest taller | Light competition |
| Edge lean | 2-3.6 m, via envelope shear | positive | Phototropism and shedding |
| 200-year crown | acceptable only with patch rules off | rounded, not a flat shell | Section 3.6 |
| Open field 200 y run time | 20-30 s | no slower | - |

**Adopt** if it meets these with `envelope.mode = none` and no rule outside
section 3. **Stop and reassess** if, after calibration, it needs rules of the
kind we are trying to delete in order to pass.

## 8. Risks and open questions

- **Calibration is the work.** The design doc says of The Grove that "the
  realism lives in the tuning rather than the algorithm". Expect the oak to
  need real calibration; the difference is that every parameter means
  something.
- **Old age.** Whether declining apical control and self-shading give a
  convincing 200-year crown is unproven. It is the case the current model
  fails, so it is the first thing to test after a 80-year tree works (B2).
- **Resolution.** A real 200-year oak has far more internodes than we can
  simulate; internodes here are 0.1-0.5 m, standing in for several real ones.
  Fine twig clusters stay a geometry-stage pass.
- **Losing the envelope as the main control** (section 4).
- **Neighbours are proxies.** They compete through light and occupancy only,
  and do not respond to the tree. Same as today.
- **Light grid resolution.** The paper uses cells about one internode long;
  the current field is 64 cells along the longest axis. The bud backend sizes
  cells from `shoot_length` instead; cost to be measured.

## 9. Milestones

| | Deliverable | Check |
| --- | --- | --- |
| B0 | Section 3 checked against the paper; corrections recorded here | Review |
| B1 | `core/buds.py`: light, gather/allocate, bud fate, direction. One open-field oak to 80 y through the existing pipeline and viewer | Runs; metrics table printed beside the current oak |
| B2 | Shedding and age terms. Open field at 20 / 80 / 200 y | Age prefix property holds; 200-year crown assessed |
| B3 | Neighbours: dense forest and forest edge. Envelope modes | Full definition-of-done batch, section 7 table |
| B4 | Oak calibration against the reference photo and targets | Adopt / reassess decision |
| B5 | If adopted: switch the oak, delete the colonization rules listed in section 5, update the design doc and USD contract notes | Tests and CI green |

B1 and B2 are the cheap part and answer the main question. The decision in
section 4 is needed before B3.

## 10. B1 result (2026-10-13)

`treegen/core/buds.py`, selected by `growth.model = "buds"`; species file
`species/quercus_buds.toml`. Sections 3.1 to 3.4 only: no shedding, no change of
apical control with age, no envelope, open field.

Oak, seed 42, age 80, open field, against the approved colonization oak
(shade response, 3 m bole, whip rule, trunk thickness 0.8):

| | Approved oak | Bud model, B1 | Target |
| --- | --- | --- | --- |
| Height / crown width | 20.8 / 27.1 m | 25.6 / 26.8 m | - |
| First fork | 3.8 m (bole setting) | 0.4 m | 2-5 m, from shedding |
| DBH | 0.66 m | 0.90 m | - |
| Branches | 1,055 | 5,277 | - |
| Whips | 0 (whip rule) | 0 | 0 |
| Sinuosity over 3 m, median / worst 10% | 1.16 / 1.49 | 1.03 / 1.11 | about 1.05 |
| Sinuosity over 8 m, worst 10% | 1.75 | 1.19 | about 1.2 |
| Wood between forks, inner | 1.3 m | 0.3 m | 2.5 m+, from shedding |
| Run time | about 7 s | 16 s (13,104 nodes) | no slower |

What it shows:

- Straight limbs with elbows, and no whips, with no rule for either. Both
  straightness targets are met on the first run.
- The young tree is an exact prefix of the old one, and runs are deterministic.
- Every node's lateral bud grows a short twig, so limbs are feathered and the
  first fork is at the ground. Both are what shedding (B2) is for; B1 cannot
  say yet whether B2 delivers them.
- The model is as sensitive as the paper says. `resource_per_light` 1.1 stalls
  as a seedling (every bud below one unit, and nothing then changes), 1.3 gives
  13,000 nodes, 1.5 gives 84,000. `apical_control` 0.46 gives a 36 m wide dome,
  0.54 an 82 m pole.

Found on the way:

- The old model's `light.foliage_density` (2.0) made four seedling nodes shade
  each other to 0.1-0.5 exposure, because there one tip stood for a whole
  foliage cluster. The bud model takes leaf opacity from `buds.leaf_area`
  instead, in real units.
- Light cells are sized from `shoot_length` (1.5 internodes), capped at 128
  cells along the longest axis.
- Cost grows with every node, since each keeps a dormant bud that is sampled
  for light each season: 80 y at the paper's `alpha = 2` is 367,000 nodes and
  over 8 minutes. Shedding should cut this; if not, it is the first performance
  problem to solve.

## 11. B2 status (2026-10-13): mechanisms built, not passed

Built: shedding (a lateral branch whose gathered light per internode is below
`shed_threshold` goes, with its buds; its tips stay in the pipe memory of the
wood below) and `apical_control_old`. Tests cover both.

Open field, seed 42. Candidate: `shoot_length` 0.1, `resource_per_light` 4,
`leaf_area` 0.15, `shed_threshold` 0.15, `apical_control` 0.52 -> 0.45.

| Age | Live / total nodes | Height / width | First fork | DBH | Inner wood between forks | Time |
| --- | --- | --- | --- | --- | --- | --- |
| 20 | 2,127 / 3,874 | 7.7 / 4.5 m | 0.1 m | 0.32 m | 0.26 m | 2 s |
| 80 | 21,375 / 150,787 | 25.6 / 18.5 m | 0.2 m | 1.70 m | 0.44 m | 78 s |
| 200 | stopped at year 124: 400,000 total nodes | - | - | - | - | 129 s |

What the scans showed (age 40 or 80, open field):

- **Coarse internodes put the model on a knife-edge.** A bud needs one unit of
  resource to grow. With 0.3 m internodes a lit bud gets 1-2 units, so at
  `alpha` 1.3 any bud under 77% light is dormant; small changes flip between a
  stalled seedling, a sensible tree and 84,000 nodes. With 0.1 m internodes
  and `alpha` 3-4 the response is smooth, but there are three to ten times as
  many nodes.
- **Apical control has a very narrow usable range**, as in the paper (0.46 to
  0.54). 0.55 gives a 31 m tree at 40 y, 0.6 gives 94 m: allocation funnels
  the crown's resource to the leader and nothing limits one shoot's season.
- **The clear trunk does not appear in the open field.** With `lambda` 0.5 the
  seedling's first lateral buds are all lit and all grow; the tree is a bush
  from year 2 and the first fork stays at 0.1-0.4 m. Shedding does not lift
  it, because low branches on an open-grown tree are lit from the side. Raising
  young `lambda` gives a trunk but with the height problem above.
- **Bare inner limbs with tufted ends do appear** with no rule for them (the
  age-80 candidate). The stick metric still reads 0.3-0.4 m because the tufts
  dominate it.
- **The trunk is far too thick** (DBH 1.7 m at 80 y): with fine internodes
  there are many more tips, and the pipe memory keeps every twig ever shed.
  The radii stage needs recalibrating for this backend.
- **Cost is the blocker for 200 years.** Each season costs in proportion to
  every node ever made: shed nodes stay in the arrays, and every surviving
  node keeps a dormant bud that is sampled for light.

Not yet answered: whether this model gives a convincing old oak. It cannot be
tested until 200 years is affordable.

Next, in order, if the work continues:

1. Performance: take shed nodes out of the working arrays (keep them aside
   for the shed overlay and the age scrub); give deeply shaded dormant buds
   the "abort" fate the paper names, so they stop being sampled.
2. Recalibrate the radii stage for fine internodes.
3. Then return to the section 7 targets, starting with the clear trunk, and
   decide adopt / reassess.

## 12. B2, second round (2026-10-14): stop and reassess

Agreed scope: make 200 years affordable, recalibrate thickness, go after the
clear trunk; if a 200-year oak is not in reach after that, stop and reassess.

**Performance, done.** Light sampling was 58 of 60 s at 80 y. Sampling once per
occupied light cell, with cells no finer than 0.4 m (the paper: about one
internode; a resolution departure only), took 80 y from 60 s to 18 s. The node
cap now counts live nodes. 200 y runs in 140 s.

**The 200-year tree** (same candidate as section 11):

| Age | Live / total nodes | Height / width | First fork | Crown base | DBH | Time |
| --- | --- | --- | --- | --- | --- | --- |
| 20 | 3,827 / 7,628 | 7.9 / 5.6 m | 0.1 m | 0.9 m | 0.36 m | 0.3 s |
| 80 | 28,236 / 242,969 | 27.1 / 19.7 m | 0.8 m | 3.7 m | 2.02 m | 20 s |
| 200 | 40,539 / 1,409,573 | 56.9 / 27.6 m | 15.2 m | 18.2 m | 5.43 m | 140 s |

At 200 years the tree is a 57 m pole with a 15 m clear bole and its crown in a
shell at the top. Three causes, each measured:

- **Height has no limit in the model.** Allocation keeps feeding the leader and
  nothing in the paper's model stops a tree growing taller. In real trees the
  established explanation is hydraulic limitation (water transport gets harder
  with height; Ryan and Yoder 1997, Koch et al. 2004). That is outside the
  paper, and it was removed from this design at B0 for that reason.
- **Self-shading empties the old crown**, as in the colonization model: the
  inside and the bottom shed, the crown survives as a shell on top.
- **The pipe memory never forgets.** The paper keeps the pipes of shed
  branches, but with 1.4 million internodes grown and shed in 200 years that
  memory gives a 5.4 m trunk. Recalibrating the tip radius (4 mm instead of
  10 mm) only scales this.

**Branching texture, not reached.** Forks every 0.2-0.4 m at every scale, and no
clear trunk in the open field at 20-80 y. More realistic leaf area (leaf area
index was 12; a real oak is 4-6) does not change it. Acrotony (`lateral_buds`,
added as a species trait) cannot act at this resolution: a season's shoot is
only 1-4 internodes of 10 cm, while a real oak shoot has 5-15 nodes.

**Assessment.** The model does what the paper describes, and some of it is
genuinely better than colonization: straight limbs and no whips with no rule
for either, bare inner limbs with tufted ends at 80 y. But at a resolution this
pipeline can afford, it does not produce an oak, and old age fails for reasons
the paper does not cover (height, pipe memory) plus the same crown-emptying as
before. Reaching an oak would need, at least: a hydraulic height limit, a
bounded pipe memory, and finer internodes (several times the nodes and cost) so
that architectural traits such as acrotony can act. Each is defensible science;
together they are a research project with an uncertain outcome.

The bud backend stays in the repository, selectable and off by default, with
its tests. Nothing in the colonization oak depends on it.

## 13. Parked (2026-10-14)

Decision: stop here, as agreed for the time-boxed round, and return to the
colonization oak. The bud backend stays selectable and tested.

The user's read of the results, to start from when this resumes: the model is
fundamentally heading in the right direction, and the most visible problem is
thickness. The trunk is far too thick and that excess carries down through the
whole branch hierarchy, so every limb reads heavier than it should.

That matches section 12: the pipe memory keeps every shed internode, and over
200 years that is 1.4 million of them. Suggested first step on resuming, before
height or acrotony: bound that memory (a shed branch's contribution fading over
the years after it is shed, as its pipes stop carrying sap), measured against
DBH targets for an open-grown oak at 20, 80 and 200 years. Then the hydraulic
height limit, then finer internodes.

## 14. Resumed (2026-10-14): thickness and height done, branching blocked on light

Evaluated with `scripts/bud_eval.py` (open field, ages 20 / 80 / 200, targets for
open-grown oak in the script's docstring).

**Thickness: annual rings (Pressler's law).** Counting every tip ever grown as a
full pipe made a 2-year twig weigh as much as a century-old limb, and DBH ran 2-3x
too high, worsening with age. Now each node lays down a ring each season in
proportion to the tips it carries (`GrowthResult.ring_area`, used by the radii
stage when present). Fitted on saved trees: `pipe_exponent` 2.8 (the paper: 2 to
3) and `tip_radius` 4.5 mm (a 9 mm current-year shoot).

**Height: hydraulic limitation** (Ryan and Yoder 1997; Koch et al. 2004).
`buds.max_height`: a bud's light earns resource in proportion to
(1 - height / max_height). Applied to resource only; applying it to the light
used for shedding made high branches look starved and collapsed old crowns.
At 45 m:

| Age | DBH (target) | Height (target) | Crown width |
| --- | --- | --- | --- |
| 20 | 0.16 m (0.16) | 7.9 m (6-10) | 5.6 m |
| 80 | 0.69 m (0.64) | 19.7 m (18-26) | 22.7 m |
| 200 | 1.65 m (1.60) | 30.4 m (22-32) | 54.0 m (too wide) |

**Branching texture: not solved; the cause is now clear.**

- New metric `fork_gap_inner` / `fork_gap_outer`: wood between forks judged by
  thickness (smaller side >= 1/4 of the parent's radius; inner = wood >= 5 cm),
  so it reads the same at any model resolution. Approved colonization oak:
  2.1 m inner. Bud oak: 0.5-0.6 m.
- Stronger apical control: young trees far too tall (13-20 m at 20 y), forks
  unchanged.
- Acrotony with one bud per shoot: 0.5 -> 0.6 m.
- Shedding threshold: knife-edge between twiggy (0.15-0.25) and a pole of a few
  hundred nodes (0.3+).
- Diagnosis on the saved 80-year tree: small inner branches have *more* light per
  internode than big limbs (median 0.24 vs 0.18), because limbs carry more wood
  per leaf. The paper's criterion therefore kills limbs before twigs.
- Mean leaf light per branch (branch autonomy) does not separate them either
  (0.29 vs 0.32). Restricting judgement to thin branches
  (`buds.shed_max_diameter`, added, off) still collapses young trees, whose
  every branch is thin.
- Underlying: almost every branch sees light between about 0.2 and 0.35. With so
  little contrast, any threshold sheds almost nothing or almost everything.

The likely reason is treegen's main departure from the paper (section 3.7): the
smooth hemisphere ray-marched occlusion field on 0.4 m cells in place of the
paper's shadow propagation, in which each bud casts a pyramid of shadow on the
cells below it. The paper notes its shedding criterion is meant for that model.
Shadow propagation gives sharp local contrast (dark inside a crown, bright at its
skin), which is what shedding needs.

Leaf area index also varies with age (13.6 at 20 y, 4.3 at 80 y, 1.9 at 200 y;
real oaks about 4-6 throughout), so foliage per leaf-bearing node is not yet
consistent across sizes.

**Next:** implement the paper's shadow propagation as the bud model's light, as
specified in its section 4.1 (`q_max` 4-8 layers, shadow `a * b^-q`, exposure
`max(C - s + a, 0)`, cells about one internode), keep the occlusion field only
for neighbour proxies, then re-run the shedding and fork-gap measurements.

## 15. Shadow propagation (2026-10-14)

Implemented as the paper specifies (section 4.1): every bud adds `a * b**-q` of
shadow to the square pyramid of cells `q` layers below it, up to `q_max`;
exposure is `max(1 - s + a, 0)`; the best direction is down the shadow gradient.
Pyramids are summed per layer with integral images, so cost follows the grid.
Neighbour proxies still shade through the occlusion field, multiplied in.
`buds.light_model = "shadow"`; the occlusion field stays as `"field"`.

Contrast, as predicted: half the wood of a 30-year tree sits at exposure 0, the
outer tenth at about 0.5 (the occlusion field put nearly everything at 0.2-0.35).
It is also much faster than ray marching.

Hand scans still hit a knife-edge on `shed_threshold` (bushy at 0.25, a collapsed
pole at 0.3 for most settings): shedding removes buds, which lowers the resource,
which grows fewer shoots. So the parameters were searched instead:
`scripts/bud_calibrate.py`, 140 random samples over nine parameters, each grown at
20 and 80 years and scored on log errors against the targets. 82 of 140 grew a
viable tree. The best, now in `species/quercus_buds.toml`:

| | Target | Best sample |
| --- | --- | --- |
| Height at 20 / 80 y | 8 / 22 m | 6.8 / 23.9 m |
| Crown width at 80 y | 22 m | 18.1 m |
| First fork at 80 y | 3 m | 2.1 m |
| Fork gap on inner wood at 80 y | 2 m | 1.26 m |
| DBH at 20 / 80 y | 0.16 / 0.64 m | 0.09 / 0.86 m |

At 80 years it is the most oak-like tree the project has produced: clear trunk to
about 3 m, ascending limbs spreading into a broad crown, straight wood with
elbows, fine branching at the ends, with no rule written for any of it.

Open:

- **200 years.** The top samples pass 400,000 live nodes at 140-190 years: at
  0.1 m internodes an old oak's living wood is that many pieces. A representation
  problem, not a biological one. Candidate: compact old wood (merge chains of
  single-child internodes that no longer carry buds) while keeping the light
  gathered and the internode count the shedding rule needs.
- Young trunks too thin (0.09 m at 20 y against 0.16) and inner fork gaps short
  of 2 m in every top sample.
- `leaf_area` is not used by the shadow model; foliage is implicit in the buds.

## 16. Two hundred years (2026-10-15)

**Reaching it.** Shed wood outnumbered live wood many times over (2.5 million
to 0.3 million at 120 years) and was walked every season. It now moves to an
archive at the end of each season (every node has a permanent `uid`; the tree is
reassembled in creation order at the end). Only shed wood that lived at least
10 years is kept in the output (half of all shed wood lived two years or less;
its rings are already counted in the wood below): a 200-year tree went from
11.6 million output nodes and a 581 MB cache file to 1.2 million and 62 MB.
Merging old unbranched wood was measured first and rejected: only 10-13% of
live nodes qualify. 200 years now runs in 35-80 s; downstream stages take about
5 s. The live-node cap is 1.5 million.

**Calibrating all three ages.** `bud_calibrate.py` now scores 20, 80 and 200
years, and can sample near earlier results (`--around`). 140 samples, half near
the best six of section 15: 94 grew a viable tree. Best combined error 2.48
(0.88 when only 20 and 80 were scored).

Systematic in every top sample, so limits of the model as it stands rather than
of the parameters:

- inner fork gap at 200 years 0.3-0.6 m (target 2 m);
- DBH at 20 years 0.08-0.11 m (target 0.16).

Height, crown width and DBH at 200 years are reachable together (rank 3: 31.9 m
tall, 31.8 m wide, 1.9 m DBH, a clean 5.4 m bole and a broad dome). No sample is
right at every age: rank 3 is forest-like and narrow at 80 years; rank 0 is airy
and photo-like at 80 but leans heavily at 200. `species/quercus_buds.toml` still
holds the section 15 parameters.

**Adopted:** rank 3, chosen by the user by eye at both 80 and 200 years, is now
`species/quercus_buds.toml`.

## 17. Are the shadows too short? (2026-10-15)

With the paper's fade (`b` = 2) a bud's shadow is down to 1/256 eight layers
(about 2.7 m) below it, so a large crown barely shades its own lower half. That
was a candidate for the forest-like 80-year form.

- Longer shadows with everything else fixed (same total shadow per bud, reach
  4.0 / 5.4 / 6.7 m): worse. At 4 m a candelabra of bare wavy stems with tufts at
  80 years; at 5.4-6.7 m dense and over-thick (DBH 2.9-3.6 m at 200 years).
  The 4 m tree scored better than rank 3 on inner fork gap only because its stems
  were bare: a single measure is not a substitute for looking.
- Search with the fade free (1.2-2.5) around rank 3, 100 samples, 75 viable:
  the 15 best all chose 1.6-2.25, clustered at 2.0, reaching 1.4-3.1 m. Long
  shadows were among the runaways (over a million live nodes).

Conclusion: the paper's fade stands; shadow reach is not what makes the 80-year
crown narrow. Rank 3 is kept (the new runner-up, `b` = 1.79, is a close cousin;
the new best scores lower only with a 3.2 m trunk at 200 years).

`bud_calibrate.py` now scores a failed sample instead of stopping, and saves
results every 20 samples.

## 18. Tropism, forest scenes (2026-10-15)

**Tropism.** The user noticed rank 3 looks pulled up rather than growing up and
sagging. Two upward pulls (tropism +0.1, never searched, and light-seeking, which
in the open is mostly up) and no downward one during growth; gravity only as a
refinement droop. Tropism 0 with droop 1.0 gave a far more open-grown 80-year
oak but 8x the live wood: in the shadow model shade falls straight down, so
spreading limbs shade each other less and every gain in spread is a gain in
light. Searched with tropism (-0.15 to 0.15) and droop (0.3 to 1.2) free, 100
samples: no clear optimum (the 15 best span -0.06 to +0.10, scores 2.1-2.5), but
a consistent trade-off. Low tropism gives the best 80-year oaks yet (rank 1:
clean trunk to about 4 m, broad arching limbs, 25 m crown; rank 5: a rounded
dome), and the same trees overgrow by 200 years (crowns 43-53 m, DBH 2.7-3.8 m).
Cause: the hydraulic limit (section 14) counts height only, so long horizontal
limbs are never limited. Next: apply it to path length from the root, which is
what the hydraulic hypothesis is about (height is its usual case).

**Forest scenes (B3), rank 3, envelope off, 80 years.** Forest edge: crown leans
2.3 m into the clearing (-0.75 m in the open), with no rule for it. Dense forest:
cleaner stem, smaller crown, less wood, but no taller than in the open (23.5 vs
23.7 m) and wider than the open tree at 200 years. Cause in the scene, not the
model: the neighbour proxies top out near 16 m when this oak is 80, so it grows
above the stand. A same-age stand should keep pace with the tree.

**Envelope (agreed).** Modes none / guide / mask; the envelope stays in the
species file's `[envelope]` section as the species' typical open-grown silhouette,
read by the bud model only in guide or mask mode; per-instance overrides belong
in the scene. Oak values to be checked against the bud oak's own shape first
(open-grown proportions: spread ratio about 1.0-1.2, crown base at a quarter to a
third of height, a broad dome).

## 19. Path length, droop, upkeep (2026-10-16)

**Added.** Hydraulics by path length from the root (`max_path`, scaling the
whole tree's budget by the light-weighted mean of 1 - path/max_path; scaling
each bud instead moved growth to the base of the tree). Growth-time droop
(`wood_set`: a share of each season's elastic sag under the real wood load, set
into the wood). Reaction wood (`reaction_wood`: each segment bends back toward
the direction it grew in, in proportion to its new ring). Maintenance
respiration (`respiration`: upkeep per metre of sapwood from each live tip to
the root, taken from the root's resource). With droop in growth, the refinement
droop is set to 0 for the bud oak.

**Result.** Path length alone stopped the overgrowth at 200 years but lost the
spreading 80-year oak; droop without reaction wood collapsed old crowns;
respiration and reaction wood together give back both. A search weighted toward
the 80-year spread (`--weights spread80`: crown width 25 m at 80 years counted
four times, first fork twice; 100 samples near the best earlier results) found:

| rank | 80 y DBH / height / width / fork | 200 y DBH / height / width | live nodes 200 y |
|---|---|---|---|
| 0 | 0.85 / 22.3 / 25.3 / 1.9 | 2.03 / 24.4 / 34.8 | 105 k |
| 1 | 0.98 / 24.8 / 22.1 / 1.9 | 2.81 / 29.0 / 35.7 | 298 k |
| 2 | 0.54 / 21.8 / 17.4 / 2.5 | 1.65 / 27.2 / 28.5 | 73 k |

Rank 0 at 80 years is close to the reference tree (section 18, rank 1) and
stays in bounds at 200. The good region: respiration about 0.008-0.017,
reaction wood 0.4-0.8, wood_set 0.14-0.26, tropism slightly negative (-0.05 to
-0.06). Open: the first fork sits low (1.9 m against 3 m), and old crowns are
dense in the interior.

**Rank 0 adopted** into `species/quercus_buds.toml` (refinement droop 0: limbs
droop during growth). Its weak points against the reference 80-year tree: a low
first fork (a limb at 1.9 m, a co-dominant stem at 3.1 m) and a dense old crown.

- Stronger young apical control (0.58, 0.62) does not raise the fork; it feeds
  the leader and the tree runs away (36-44 m at 80 years).
- A carbon balance per branch (shed when its own resource cannot pay its
  sapwood upkeep) never acts: with the calibrated respiration even thick shaded
  limbs earn 8-15 times their upkeep, so it would need a factor of about 8,
  which is a tuning knob, not a rule. Dropped.
- **Bud viability (`bud_life`)**: a lateral bud that has not broken within a
  few years goes latent and is dropped. Without it every 10 cm of old limb
  keeps a bud that can grow into a branch whenever the crown opens. At 4 years
  the 200-year crown opens into a few massive limbs (live nodes 105 k -> 22 k);
  at 8 years the 80-year tree is closest to the reference. `fork_gap_inner`
  barely moves (0.2-0.3 m), so it does not read this change; judged by eye.
