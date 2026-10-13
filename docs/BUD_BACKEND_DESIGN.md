# Bud-based growth backend: design

Status: proposal, 2026-10-13. Nothing here is built.

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

**Check before coding:** the model description below is written from memory of
the paper and of the Borchert-Honda model it builds on. Section 3 must be
checked against the paper itself as milestone B0. Where this document and the
paper disagree, the paper wins unless there is a recorded reason.

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

A bud with resource `v` grows a shoot of `floor(v)` internodes, each of length
`shoot_length * v / floor(v)`. A bud with `v < 1` stays dormant. This one rule
is the source of most of what we have been patching:

- vigorous leaders make long internodes, weak outer shoots make short ones:
  long inner runs, clustered branching at the edge;
- no shoot grows without paying for it: no whips;
- most lateral buds never break: fork spacing comes from the budget, not from
  point spacing.

A bud also stays dormant if the space it would grow into is occupied (own wood
within a small radius, or inside a neighbour proxy).

### 3.4 Shoot direction

A new shoot's direction is a weighted sum of three terms, then normalised:

- the bud's own direction (the parent's direction for a terminal bud; rotated
  out by the branching angle and round by the phyllotactic angle for a lateral);
- the light direction from the field, weight `phototropism`;
- gravity, weight `gravitropism` (negative values lift shoots, positive droop).

Direction is set once per shoot, not re-steered every 35 cm, so limbs run
straight and change direction where a lateral takes over from a dead or weak
leader: the oak's elbow. Every new internode carries a terminal bud and lateral
buds placed by phyllotaxis.

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

- `lambda` declining with tree size, so leaders lose dominance and co-dominant
  stems take over (reiteration);
- resource per unit of light falling with path length from the root (hydraulic
  limitation), which slows height growth and is what caps a tree's height.

Whether these two are enough for a convincing 200-year oak is the main open
question (section 8).

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

Target: about a dozen species parameters with biological meaning, replacing the
`growth`, `architecture` and most of the `envelope` and `age_response` groups.

| Parameter | Meaning |
| --- | --- |
| `shoot_length` | Internode length per unit of resource (m) |
| `resource_per_light` (`alpha`) | Converts gathered light into growth resource |
| `apical_control` (`lambda`) | Share of resource to the main axis at a fork |
| `apical_control_decline` | How `lambda` falls with tree size |
| `hydraulic_cost` | Loss of resource per metre of path from the root |
| `branching_angle` | Angle of a lateral to its parent |
| `phyllotaxis` | Rotation between successive lateral buds (about 137.5 or 144 degrees for oak) |
| `buds_per_node` | Lateral buds per node (oak clusters several at shoot tips) |
| `phototropism`, `gravitropism` | Direction weights (3.4) |
| `shed_threshold` | Light per unit size below which a branch is shed |
| `bud_lifetime` | Seasons a dormant bud stays viable |

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
- **Old age.** Whether declining apical control and hydraulic cost give a
  convincing 200-year crown is unproven. It is the case the current model
  fails, so it is the first thing to test after a 80-year tree works (B2).
- **Resolution.** A real 200-year oak has far more internodes than we can
  simulate; internodes here are 0.1-0.5 m, standing in for several real ones.
  Fine twig clusters stay a geometry-stage pass.
- **Losing the envelope as the main control** (section 4).
- **Neighbours are proxies.** They compete through light and occupancy only,
  and do not respond to the tree. Same as today.
- **Fidelity to the paper** (section 1, B0).

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
