# treegen — handoff

**Read "Start here" first.** Everything below it is the history of earlier sessions,
kept for the reasoning behind decisions; where it disagrees with "Start here", this
section wins.

---

## Start here (2026-11-11)

### State of the repo

- GitHub `main` is the source of truth and was up to date when this was written. No
  other checkout matters.
- `out/` is gitignored and was copied by hand alongside the clone, for reference. It holds
  renders, the growth cache and calibration results; the useful parts:
  - `out/buds/calib*.json`: search results, read by `scripts/bud_eval.py --calib FILE --rank N`.
    `calib11.json` rank 2 is the current reference oak; `calib4.json` rank 1 is the user's
    favourite 80-year look.
  - `out/buds/again_a80.png`, `out/buds/fav_a80.png`: that favourite at 80 years.
  - `out/buds/c11r2_a80.png`, `c11r2_a200.png`: the current reference at 80 and 200.
  - The growth cache in `out/` is keyed on a hash of the growth code, so stale entries are
    ignored, never served.
- Setup: `python -m venv .venv`, activate, `pip install -e ".[dev]"`. Python 3.12.
  `pytest`: 55 tests, about 1-3 minutes. CI runs them on Ubuntu and Windows
  (`.github/workflows/tests.yml`).

### What the user wants

- Treegen should get realistic trees from **established simulation models**, not from
  hand-tuned settings. When something looks wrong, look for the missing or wrong piece of
  the simulation first; a new knob is the last resort. The user raised this explicitly
  ("we are deviating from sims by hacking different settings").
- Get the **oak really right before any other species**. Birch and pine wait.
- The user judges by eye from renders. Show trees next to the reference images and
  explain what changed in plain terms.

### Two growth models

- **Colonization oak** (`species/quercus.toml`, `growth.model = "colonization"`): the
  approved Phase 1 oak. Frozen; the definition-of-done sheet and
  `docs/metrics/dod_baseline.json` guard it.
- **Bud model** (`species/quercus_buds.toml`, `growth.model = "buds"`,
  `treegen/core/buds.py`): Palubicki et al. 2009, where all active work is. Design,
  experiments and every result are in `docs/BUD_BACKEND_DESIGN.md`; sections 14-20
  cover the current model. What it has now, each grounded in a published mechanism:
  - the paper's shadow propagation (`light_model = "shadow"`), Borchert-Honda allocation
    with apical control that weakens with age, shedding by light (Takenaka);
  - annual rings for thickness (pipe model), hydraulic limits by height and by path length;
  - maintenance respiration (upkeep per metre of sapwood from each live tip to the root);
  - growth-time elastic droop (green-oak stiffness, `wood_set`) and reaction wood, both per
    metre of wood;
  - bud viability (`bud_life`: lateral buds that do not break within a few years go latent);
  - leader dieback (the stem above the point where it carries less than half the surviving
    tips is judged like a branch);
  - optional gravitropic set-point angles for side shoots (`lateral_angle`). Off for oak:
    oak follows Rauh's architectural model, with branches orthotropic like the trunk.

### Reference trees

- **The user's favourite**: calib4 rank 1 at 80 years (25 m crown, first fork 4.1 m,
  arching limbs, limbs about 49 deg from vertical). This is the look to keep at 80 years.
  It was grown before upkeep, droop and dieback existed and overgrows by 200 years.
- **Photos** in `reference/`: `200_year_oak.jpg`, the target 200-year character (a few
  massive limbs spidering out at 55-65 deg, sagging, with upturned ends; probably a valley
  oak, so a little more sprawling than a typical *Q. robur*); `quercus_robur_winter.jpg`
  (long inner runs, clumped twig ends).
- **Current reference in the species file**: calib11 rank 2. An open, spidering 200-year
  frame (crown 31 m, limbs 66 deg). Weak points:
  - short: 19 m at 200 years against 22-32 m;
  - low first fork: 2.0 m against 3 m;
  - **stiff limbs**: they stick out straight instead of sagging (the user's complaint);
  - it relies on `tropism = -0.115` (shoots pulled downward), which is not oak biology.

### The open problem and the agreed next step

Every calibration has drifted to negative tropism, and forcing tropism to 0 or above
made every target worse; strong upward tropism stalls the tree at a few hundred nodes.
Cause: the paper's shadows fall **straight down**, so shoots stacked vertically shade
each other and are shed, and the only way to spread is to grow sideways. Limbs grown
sideways and held out straight are the stiff look. Details:
`docs/BUD_BACKEND_DESIGN.md` section 20.

**Next step (proposed, not started):** cast shadows from the sky instead of straight
down. Shadow pyramids along a handful of sky directions (sheared grids, one integral-image
pass each), weighted by sky radiance (for example the standard overcast sky, brightest at
the zenith), replacing the single vertical pyramid in `_Shadow`. A bud at the crown edge
then sees open sky to its side, upward growth stops defeating itself, and spread can come
from light. Then:

1. Check that tropism 0 or above now grows a spreading tree.
2. Recalibrate with `--weights oak` and tropism held at 0 or above, droop and reaction
   wood free, seeded from calib11 rank 2 and the favourite.
3. Judge against the favourite at 80 and the photo at 200.

### Tools

- `scripts/bud_eval.py`: grows 20/80/200 years and prints DBH, height, crown width, first
  fork, limb angle and bend, fork gaps, node counts. `--set group.name=value`,
  `--calib FILE --rank N`, `--ages 80,200`, `--png PREFIX`, `--scene`.
- `scripts/bud_calibrate.py`: random plus local search, 7 worker processes. Weight
  presets: `even`, `spread80`, `oak` (spread80 plus limb angle 50 deg at 80 and 60 deg at
  200, plus height at 200). `--around FILE --top N --local SHARE --spread S` samples near
  earlier results. A 100-sample search takes about 50 minutes; run it in the background.
- Metrics (`treegen/metrics.py`): `limb_angle` reads the spidering character well.
  `limb_bend` does **not** read stiffness (the favourite scores -6 deg); a better stiffness
  measure is still wanted (for example a limb's maximum departure from its chord, relative
  to its length). `fork_gap_inner` barely responds to how open an old crown is.

### Other open items

- **Forest scenes** (bud model): the forest-edge lean emerges with no rule for it. Dense
  forest is unconvincing because the neighbour proxies top out near 16 m while the oak
  grows past them; a same-age stand should keep pace with the tree.
- **Envelope modes** (agreed, not built): `none` / `guide` / `mask` for the bud model,
  with the oak's typical open-grown silhouette in the species file's `[envelope]` section
  and per-instance overrides in the scene.
- The 2D renders clip 200-year crowns wider than the frame.

### Working notes for Claude

- Commit messages follow the user's global rules (see section 6). Ask before pushing; the
  user often pushes themselves.
- On Windows, write multi-line patch scripts to a file with the Write tool and run them;
  heredoc quoting has broken them. Multiprocessing needs the `__main__` guard and cannot
  run from stdin.
- Use `rc=$?` (not a piped `tail`) when checking pytest's result before committing.

---

## History: handoff of 2026-10-09 and later notes

Written 2026-10-09, at the end of the chat session that built Phase 1 (M1–M6) plus a
first pass at M7. Sections 1-6 below are from then, with dated notes added as work went on.

---

## 1. Where the code actually lives

> **Update 2026-10-09 (Claude Desktop session):** GitHub `main` @ `ce3c966` already matched the
> working copy below. This file moved to `docs/`, and `scripts/definition_of_done.ps1` now exists.

**The working copy on disk is the source of truth, not GitHub.**

| | state |
| --- | --- |
| `D:\06_github\treegen` (Windows, Python 3.12 at `C:\Python312`, venv at `.venv`) | current — flat layout, viewer pass 1 **and** pass 2 applied |
| `D:\01_github\treegen` | older checkout, abandoned mid-fix. Ignore or delete. |
| `github.com/csmallfield/treegen` @ `1d36fa2` "treegen updates" (2026-09-21) | **behind**: flat layout + viewer pass 1 only |

Missing from GitHub (all present locally):

- `treegen/viewer/jobs.py` — parallel variant rendering
- pass-2 edits to `treegen/viewer/index.html`, `server.py`, `payload.py`
- pass-2 edits to `treegen/core/light.py` — bounded neighbour rasterisation
- pass-2 additions to `tests/test_viewer.py` (23 tests total)
- the pass-2 README section
- `scripts/definition_of_done.ps1` — was designed but never written to the repo
  (only `definition_of_done.sh` exists; the PowerShell version needs
  `--ages "20,80,200"` and `--scenes "..."` quoted, or PowerShell splits them into arrays)

**First action in the new session: commit and push the local state** so the repo and the
working copy agree. Check `git status` for `.venv/`, `out/`, `__pycache__` first — they
are gitignored but worth confirming.

---

## 2. What the project is

Headless, DCC-agnostic procedural tree generator. Space colonization growth plus a light
occlusion field; USD out, with the skeleton as a first-class FX deliverable. Geometry only
in Phase 1 — no foliage, no texturing, no junction welding.

Phase 1 design doc: `Treegen — Phase 1 Design Doc.md` (uploaded in the original chat; keep
a copy in the repo if it isn't there). The definition of done — one oak, three ages × three
competition scenarios, no parameter change other than age and neighbour placement — is met;
see `docs/definition_of_done.png` and `scripts/definition_of_done.sh`.

### Layout

```
pyproject.toml, README.md, LICENSE (Apache-2.0)
treegen/
  schema.py        every parameter: default, range, unit, group, doc. Single source of truth;
                   the viewer panel is generated from it, so adding a parameter is a one-file change
  pipeline.py      stage caching: growth -> skeleton -> geometry; assemble_skeleton()
  core/            age, envelope, light, growth, skeleton (branch extraction), radii, refine, util
  geo/             frames (parallel transport — part of the USD contract), sweep (meshes, curves, UVs)
  usd/stage.py     layers, primvars, GeomSubsets, materials
  cli/main.py      argparse entry point, including --serve
  viewer/          server.py (stdlib http), payload.py (binary packer), jobs.py (variant processes),
                   index.html (single-file three.js frontend)
  preview.py       matplotlib silhouettes and contact sheets
species/           quercus.toml (tuned), pinus.toml + betula.toml (untuned first passes)
scenes/            open_field, dense_forest, forest_edge
docs/              USD_CONTRACT.md, definition_of_done.png, this file
tests/             23 tests, ~18 s
scripts/           install.ps1 (Windows installer, auto-detects Python), definition_of_done.sh / .ps1
```

### Running it

```powershell
cd D:\06_github\treegen
.\.venv\Scripts\Activate.ps1          # Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass if blocked
treegen --serve                       # viewer on http://127.0.0.1:8765/
pytest                                # 23 tests
.\scripts\install.ps1                 # rebuilds the venv from scratch if needed
```

---

## 3. What is built

**M1–M5** growth, light competition, skeleton extraction, radii and refinement, USD output.
**M6** browser viewer. **M7** two extra species, untuned.

Behaviour that works, verified visually in the definition-of-done sheet:

- Open field gives a short trunk and a broad crown; dense forest a narrower, raised crown;
  forest edge leans into the clearing with no lean parameter anywhere in the code.
- Ages 20 / 80 / 200 read as one tree growing — attractors are seeded once in the largest
  lifetime envelope and activate as the envelope reaches them, so a young tree is the
  literal past of the old one.

### Deviations from the design doc, and why

1. **The doc's example influence/kill radii (2.2× / 0.9× step) stall growth** at 8000
   attractors — attractor spacing exceeds the influence radius. Shipped values: 10× and 2×,
   with 20 000 attractors.
2. **Three parameters were added** because pure colonization + light weighting failed:
   - Borchert–Honda-style vigor allocation (`_vigor` in `core/growth.py`) — without it,
     forest trees fork at 2 m instead of forming a bole.
   - `growth.straightness` (heading memory) and a gravitropism clamp — without them, limbs
     trace the lit shell of the envelope and form loops.
   - Neighbour proxies mark space as occupied, so the tree cannot grow inside a neighbour.
3. **`age_response` semantics resolved**: curves that modify a base value are applied as
   `curve(n) / curve(n_ref)`, so a species file is exactly true at `reference_age`.
   `crown_flatten` is a delta; `reiteration` is absolute; `n = age / max_age`.
4. **USD contract additions**: `parentPointIndex` (a rebind cannot rebuild frames without
   it), `widths` authored as diameter per USD convention alongside a `radius` primvar,
   skeleton at `purpose = guide`, sidecar layers prefixed with the root's stem.
   The frame convention is documented in `docs/USD_CONTRACT.md` and in `geo/frames.py`,
   and is part of the contract.
5. **The viewer is not Hydra.** The `usd-core` wheel ships no imaging, so the viewer draws
   treegen's own arrays with three.js. A real Hydra view would mean NVIDIA's prebuilt USD
   and its bundled Python. usdview (NVIDIA build) is installed on the Windows box and is
   the tool for verifying the USD itself.

### Performance, measured

Single-threaded CPU throughout — numpy and scipy in one thread. The GPU only draws the
viewport; extra cores help batches and the variant grid, not one tree.

| | time |
| --- | --- |
| oak age 80, open field | ~7 s |
| oak age 80, dense forest | ~7 s (was 12.4 s before the pass-2 neighbour fix) |
| oak age 200, dense forest | ~15 s |
| skeleton + geometry only (cheap-tier param edit) | ~0.05–0.10 s |
| fast age scrub after the first max-age sim | 40–80 ms |

Profile of a forest sim before the fix: 7.0 s of 12.2 s was re-rasterising 36 neighbour
proxies into the voxel grid once per light epoch. Now bounded to each proxy's own voxel
range and cached. Next largest cost is hemisphere ray marching, ~3.8 s, harder to cut.

### Viewer specifics

- Parameters are tiered by cost, from `STAGE_GROUPS`: tier 1 `geometry`/`materials`,
  tier 2 `radii`/`refinement`, tier 3 everything else. Tiers 1–2 update live on drag
  (40 ms debounce); tier 3 is debounced 350 ms and re-simulates.
- Age slider: dragging truncates a cached max-age simulation by birth year (`core/growth.truncate`);
  releasing re-simulates exactly when *exact on release* is ticked. The approximation differs
  because shedding, vigor and refinement ran at the older age — heights land within a few
  percent in the middle of the range.
- Payload is one binary blob: `[uint32 meta_len][meta json][float32/uint32 arrays]`.
  About 0.8 MB for an 80-year oak, ~1.8 MB with the attractor cloud.
- Debug overlays: attractor cloud (grey = never reached, green = active, blue = consumed,
  red = shaded out), crown envelope, skeleton, shed limbs.
- Buttons: regenerate, export USD, save species to TOML (round-trip tested; strips comments),
  variant grid (9 seeds in worker processes, fills in as each lands).

---

## 4. Open bugs, with diagnoses

> **Fixed 2026-10-09 (viewer pass 3)**, all five plus: the fast-scrub cache was keyed on
> every parameter, so any tier-1/2 edit threw away the max-age simulation; `<fieldset>`'s
> default `min-width:min-content` was the real cause of item 1; and the HUD now shows DBH
> (the 1.343 m noted below the list was the flare-base radius, not a radii bug).

These were reported at the very end of the session. Diagnoses are from
reading the code, not from reproducing, except where noted.

1. **Right panel is a fixed 340 px and always horizontally scrolls.**
   `#panel { width:340px }` with rows whose label is `flex:0 0 120px` and number box
   `width:72px`; the range input has no `min-width:0`, so the row's minimum content width
   exceeds the panel at any window size. Fix: `min-width:0` on the flexible children,
   `overflow-x:hidden` on the panel, plus a drag handle (or `resize:horizontal` on a wrapper)
   with the width persisted in `localStorage`.

2. **Group headings read `METARESIM`, `ENVELOPERESIM`, `GROWTHRESIM`.**
   Confirmed in the screenshot. `buildPanel()` appends a `.legendkey` span inside `<legend>`
   and styles it `float:right`; floats do not work inside a legend box, so the word runs on.
   Fix: drop the float, use a coloured dot plus a `title`, or put the key outside the legend.

3. **The species dropdown does not match the loaded species.**
   Screenshot shows `betula.toml` selected while the panel holds `quercus_robur`.
   `init()` calls `loadSpecies("quercus.toml")` but never sets `$("species").value`, so the
   select stays on the first option alphabetically. One line to fix; worth fixing early
   because it makes every other observation ambiguous.

4. **Variant grid is hard to read and the nine results look alike.**
   Two causes. It renders `preview.render_png`, which draws *two* panels (side and front),
   then squeezes them into a 340 px column — so each silhouette is tiny. And yes, only the
   seed varies; that is what the grid is for, but with 20 000 attractors in a fixed envelope
   the seed-to-seed variation is genuinely low. Options: render a single side view at a
   larger size; show the grid as an overlay in the main viewport instead of in the panel;
   make a thumbnail click apply that seed; and consider whether a species wants a little
   per-seed jitter (envelope height/spread) to read as a population rather than clones.

5. **`ConnectionAbortedError: [WinError 10053]` in the terminal, and updates stop.**
   The client aborts the in-flight fetch whenever a new generate starts (slider drags do
   this constantly); the server then raises while writing headers to a closed socket.
   Two fixes needed:
   - Swallow `ConnectionAbortedError`, `ConnectionResetError` and `BrokenPipeError` in
     `Handler._send` / `handle_one_request` — noise, not an error.
   - The real stall: an aborted request **still runs to completion inside `Session.lock`**,
     so the next request queues behind a simulation nobody wants. Add a request counter;
     after acquiring the lock, if a newer generate has arrived, return 409 immediately and
     let the client ignore it. Without this, dragging a tier-3 slider builds a backlog.

Also worth a look while in there: the HUD in the screenshot reads 68.5 m and trunk r 1.343 m
at age 135, which is a plausible result of a dragged `envelope.height`, but check that
`radii` scaling is not compounding with `age_response.trunk_radius` beyond what the pipe
model implies.

---

## 5. Planned next work, in the order discussed

1. **Fix the five items above** (QOL pass 3).
2. **Fast-scrub cache keyed to more than one parameter set.** Today any tier-3 edit
   invalidates it, so the next age drag pays for a full max-age simulation.
3. **Envelope versus light** — the main open design question from the doc, deferred twice.
   *Status 2026-10-09: done for the oak (shade_response = 0.6); see the note below the list.*
   The envelope caps height, so a forest tree cannot outgrow the stand and the bare bole
   stays shorter than it should be. Candidate: make the envelope a soft bias once the light
   field exists, rather than a hard cap. This changes silhouettes everywhere and will need
   the definition-of-done sheet regenerated and the oak re-tuned.
4. **M7 properly**: tune `pinus.toml` and `betula.toml`. Conifers need whorled branching,
   which the model does not have — the current pine is a silhouette with a strong leader,
   not pine architecture. Decide whether whorls are a Phase 2 feature or a species hack.
5. **CI**: GitHub Actions running pytest on `ubuntu-latest` and `windows-latest`, to catch a
   future `usd-core` release dropping a Python version. (Python 3.14 works today on Windows;
   the local venv is 3.12.)
6. **Phase 2 territory**: junction welds, UsdSkel export, foliage (the empty
   `/Tree/Foliage` PointInstancer is the hook), mesh/implicit envelopes.

### Smaller known limitations

- `branchPath` is hierarchical but inserting a lateral renames later siblings.
- `/Tree/Geom/Twigs` gets one material binding (majority order range); per-curve lookdev
  should key off `primvars:branchOrder`.
- The root frame seeds from world +X: stable under sim-scale tilts, not under arbitrary
  rotation of the whole skeleton. If FX will rotate trees before rebinding, author the root
  normal as a primvar — cheap now, painful after people depend on contract v1.
- Scenes look identical at age 20 because same-age neighbours have not closed canopy yet.
- Occasional looping limbs in shaded crowns.
- The viewer needs internet on first load (three.js from unpkg); vendoring instructions are
  in the README.

---

### Envelope versus light: what was tried (2026-10-09)

Measured with `scripts/dod_metrics.py` against `docs/metrics/dod_baseline.json`
(oak, seed 42; the baseline is today's hard envelope). Baseline facts worth knowing:
height is the same in every scene (~21 m at 80 y, ~24.5 m at 200 y) because the
envelope is the binding limit, and the dense-forest crown at 80 y is nearly as wide as
the open-field one (25.5 vs 26.5 m), because the same-age neighbour proxies top out
around 16 m at reference age, so the oak escapes above them and spreads.

1. **Soft boundary (rejected).** Attractor density fading as exp(-distance / s) outside
   the envelope. At s = 0.15 every scene, open field included, grew from 21 m to ~35 m tall
   and 26 m to ~56 m wide at 80 y, and the scenes converged again. Colonization grows
   toward any attractor in reach however sparse, and outside the envelope everything is
   lit in every scene, so light cannot tell forest from open there. Code removed.
2. **Shade response (kept, `envelope.shade_response`, default 0).** The envelope
   stretches with the side shade the neighbour proxies cast (measured before the
   simulation, neighbours only). Open field is unchanged by construction (tested).
   At k = 0.6, 80 y: dense forest 29.7 m tall (was 21.2), crown base 10.7 m (was 6.4),
   crown width 19.9 m (was 25.5). The cost: forest-edge lean, measured as crown
   x-offset into the clearing at 200 y, drops from 3.5 m to 0.9 m, because the narrower
   envelope also narrows the open side. Height-only stretching (radius unchanged) was
   tried; it loses the forest narrowing (28 m wide) and still reduces the lean.
3. **Shade response + lean (kept).** The same neighbour-only measurement also gives a
   light-asymmetry vector (about 0.30 toward +X for forest_edge, 0.01 for dense_forest).
   The envelope axis is sheared by `k * asymmetry` of its radius at the top, base fixed
   at the trunk. At k = 0.6 the edge crown offset is 2.3 m at 80 y and 3.7 m at 200 y
   (baseline 1.4 and 3.5), while dense forest keeps its gain (29.7 m tall, crown base
   9.9 m, 20.2 m wide at 80 y). Lean still comes only from neighbour placement.
   Visible side effect worth watching: a slight S-bend low on the dense-forest trunk.

The disk cache file name now carries `CACHE_VERSION` (it did not before, so a bump
had no effect on disk). Bump it whenever growth output changes for the same params.

**Adopted:** `quercus.toml` uses `shade_response = 0.6`. `docs/definition_of_done.png`
and `docs/metrics/dod_baseline.json` were regenerated with it; the hard-envelope numbers
are kept as `docs/metrics/dod_hard_envelope.json`. Pine and birch are still at 0.

### First fork and trunk controls (2026-10-09)

Photographed *Q. robur* fork higher than the sim did. Measured (`first_fork` in
`scripts/dod_metrics.py`: lowest point where a live limb >= 30% of the stem's radius
leaves it): open field 1.4 m at 80 y, 0.7 m at 20 y. Open-grown oaks typically keep
2-5 m of clear trunk; photos skew higher still (browse lines on pasture trees, crown
lifting in parks, photographers favouring landmark trees). Cause in the sim: the year-0
envelope is already ~2.7 m tall with attractors all round the seedling, so it forks at
once, and the model has no juvenile phase where low branches are later shed.
`envelope.clear_height` does not fix it (first fork stays 1-1.7 m at 0.15-0.3).

Added `architecture.bole_height` (crown lifting, tier 3) and `radii.trunk_thickness`
(tier 2, live), both off by default; a 1.8 m figure 3 m from the trunk in the viewer
and the PNGs. First fork at 80 y, open / dense / edge: off 1.4 / 7.3 / 1.4 m,
bole 3 m 4.5 / 6.0 / 3.8 m, bole 5 m 6.0 / 6.3 / 5.8 m, with height and width nearly
unchanged. **Adopted:** `quercus.toml` uses `bole_height = 3.0` (trunk_thickness stays 1);
the definition-of-done sheet and `docs/metrics/dod_baseline.json` were regenerated with it.

### Whips: long thin limbs (2026-10-09)

Reported: very long thin branches, mostly low in the crown, that could not hold
themselves up. Measured: 11-20 m limbs with 5-9 cm bases (length/diameter 180-250,
median ~70) and only 1-5 side branches each, some near-vertical. Elastic similarity
(diameter ~ length^1.5) puts a 20 m limb at roughly 30-60 cm.

Radii-only fixes were tried and reverted: a length^1.5 thickness floor along the path
inflated the trunk to 2.5 m DBH at 80 y; the same floor on horizontal lever arm missed
the vertical whips and still inflated old trunks. The limbs were whips, so thickening
them alone would make poles.

Kept: `architecture.max_unbranched` (growth, `_stall` in core/growth.py). A tip whose
run since its limb's last real fork (smaller side >= 15% of the larger's tips) exceeds
`max_unbranched x (D / 0.1 m)^(2/3)` leaves the growing set until the limb forks. Run
and limit are carried per node as it is added and recomputed exactly each epoch;
checking only per epoch let tips overshoot ~3.5 m and left a slenderness floor.
Oak: `max_unbranched = 4.0` with `trunk_thickness = 0.8` (the better-branched crown has
more tips, which thickened the trunk through the pipe sums). Whips per tree went from
18-79 to 0-5; p95 limb length/diameter from 160-200 to ~95-130; height, width, bole
and DBH within about 10%; edge lean a little weaker (2.9 m vs 3.4 m at 200 y).
`whips` and `limb_ld_p95` are now dod_metrics columns.

Also: light sampling no longer bounds-tests every ray sample (zero-padded grid) and
vigor/shedding share one tip sample per epoch, bit-identical results; the growth disk
cache key now includes a hash of the growth sources (`pipeline.CODE_HASH`), after a
stale cache served old-code trees twice. Open field 200 y runs ~30 s (20 s without the
whip rule).

### Thin branches carrying many (2026-10-09)

Reported in 3D: a thin branch holding up 20 others. The radii were fine (no branch
under 3 cm carries 10 or more others; the loaded ones are 7-13 cm in radius). They were
order 2, and only orders 0-1 were meshed, so the viewer drew 20 cm limbs as 1-pixel
lines. The oak now meshes every order (`geometry.mesh_orders = [0, 1, 2, 3, 4]`) and
`min_radius_for_mesh` (4 cm) alone decides tube versus curve: ~30k faces at 80 y, ~70k at
200 y, geometry rebuild 0.1-0.2 s. USD contract unchanged (it already defines
Branches by `mesh_orders` plus the radius test). Other species still mesh orders 0-1.

### Winter-oak branching: long inner runs, clumped edge (2026-10-09, in progress)

Reference: `reference/quercus_robur_winter.jpg` (user's photo, untracked). A winter oak
has long bare runs on its scaffold limbs and short clustered branching at the crown
edge; the sim forked evenly everywhere. Measured with `stick_inner` / `stick_mid` in
dod_metrics (median wood length between visible forks, i.e. splits into children that
each carry 2+ tips; inner = carries 30+ tips): the oak had ~1.0 m inner and 1.2-1.6 m
middle, longer outside than inside, the reverse of the photo. (A first per-branch
side-branch-gap measure was misleading: it counted twigs and stubs; removed.)

Tried and measured on the definition-of-done batch:
- `growth.bud_years` alone (old wood cannot sprout side branches): little effect.
- kill_radius 3-4x, attractor_count 10000: sparser tree, spacing unchanged; colonization
  sprouts a lateral every few nodes behind any tip whatever the density.
- `light.prune_share` alone (shed side branches with < share of their strongest
  sibling's tips once `prune_age` old): pruned shoots simply regrow from the same wood.
- Both together work. At 0.3 / 2 y: inner sticks 2.2-4.6 m (2-4x), height and width
  within ~1 m, whips ~0, edge lean kept, DBH back near earlier values with
  trunk_thickness 1.0.

Both settings are committed with defaults off; the oak file does NOT use them yet.
Reason: at 200 y the crowns turn umbrella-like (interior emptied) and scaffold limbs show
wavy S-curves and loops that the interior twigs used to hide. Real oak limbs run fairly
straight between elbow kinks. Next: fix limb tortuosity, then decide the oak values,
then the planned geometry-stage twig-cluster pass (tufts below the 0.35 m node spacing).

### Limb waviness (2026-10-09, in progress)

Measured as sinuosity (path / chord over 3 m windows) on orders 1-2. A real oak limb is
~1.05 over 3 m, changing direction at forks (elbows). The oak was 1.14-1.18 median at
80 y; with pruning on, worst 10% of windows reached 2.1-2.4 at 200 y (U-turns). The
waviness comes from growth, not refinement (refinement smooths it slightly).
`growth.straightness` up to 3 does not help: the loops are not per-step jitter.

Traced the worst windows node by node (scratch script, see session); causes and fixes:
1. Gravitropism clamp ran before the branch-angle rule, so side shoots could be rotated
   steeply downward (y -0.88 seen). Fixed: clamp last. Applies to every tree; the
   approved oak moved by height +/-0.4 m, width +/-0.6 m, lean slightly stronger, up to
   5 whips at the forest edge. Baseline and sheet re-recorded.
2. A tip resuming after a stall could turn 77 degrees in one step. New `growth.max_turn`
   (degrees per step for an extending tip).
3. Side branches could sprout backwards (151 degrees seen). New
   `architecture.branch_angle_max`.
4. Self-pruning could kill a limb's own leader, so limbs became chains of side branches
   turning the same way. Pruning now never removes a leader (first child).
5. Edge shoots hooked back into the interior that pruning had emptied (its attractors
   stay live). New `growth.centrifugal` (1 = no new wood heads back toward the axis).

With 10 deg / 65 deg / 1.0 plus pruning: sinuosity median ~1.11-1.12 at 80 and 200 y,
worst 10% 1.26-1.34 at 80 y and 1.55-1.60 at 200 y (was 2.1-2.4). All three default off.

Not adopted for the oak. At 80 y the trees look more like the winter photo than any
version so far (straighter, angular limbs, branching at the edge). At 200 y the
open-field and edge oaks become flat umbrellas (tall bare trunk, a table of straight
horizontal limbs, vertical spikes) and the dense-forest oak keeps loops in the vertical
plane (centrifugal only acts horizontally). Cause: over 200 years pruning + bud lifetime
strip the lower and inner crown with nothing able to regrow it, while the age curve
raises shed_threshold to 0.3, leaving a shell on top. That is the next problem: the
old-tree crown model, not the limbs. Candidate settings to try in the viewer are in
`out/straight/quercus_straight.toml` (untracked).

### Old crowns (2026-10-09, open)

Ablation on the experimental oak (pruning + straightening) at 200 y, open field
(scratch ablation script; variants as --set style overrides):
- Pruning and bud lifetime are what empty the crown: either off brings the first fork
  from 10.4 m back to 5.0 m.
- Cause: self-pruning compared a side branch with its sibling, so big scaffold limbs
  (100 tips) were pruned for losing to the trunk's continuation (1000 tips). Added
  `light.prune_max_diameter` (only thinner side branches are self-pruned; big limbs are
  left to light shedding). At 6 cm: first fork 5.0 m, inner sticks 3.5 m, crown base
  ~10 m on a 26 m tree (the winter photo starts its limbs at ~1/3 height).
- `growth.epicormic` (old wood may still sprout): refills the lower crown but brings the
  short even branching back (inner sticks ~1.5 m); the two goals pull against each other.
- Large loops (5-10 m) remain at 200 y. They come from growth, not gravity droop (8 m
  window sinuosity p90 1.76 with droop, 1.69 without; the approved oak is 1.50).
  Mechanism: straight limbs run out to the envelope, find no attractors beyond, and
  have to turn back along it; with max_turn they do so in slow arcs. Tried: a tip that
  would need > 3x max_turn stops instead of arcing (an elbow). 3 m sinuosity improves
  (p90 1.29-1.32, better than the approved oak) but 8 m stays 1.76-1.81, and branch
  counts halve.

All of this is committed with defaults off (the stop rule only acts when max_turn > 0);
the approved oak is unchanged (checked: identical metrics). Age 80 with these settings
is the best match to the winter photo so far. Age 200 still loops: space colonization
inside a fixed envelope gives straight limbs nowhere to go once the interior is pruned.
Candidate next directions: let the envelope soften/grow at its top edge for old trees,
or move old-age crown behaviour into a different growth rule (e.g. sympodial/
reiteration-driven renewal from the scaffold rather than tips chasing attractors).

### Direction change (2026-10-13)

The rules added since the handoff patch biology into space colonization, which is a
volume-filling algorithm, not a growth simulation; old crowns are shaped mostly by
those rules interacting. Agreed with the user to stop adding rules and to design a
bud-based backend (Palubicki et al. 2009) as a second growth model, judged on the
same metrics. Proposal: `docs/BUD_BACKEND_DESIGN.md`. The experimental settings from
the last three commits stay off; `species/quercus.toml` in git is the approved oak.

### Bud backend parked (2026-10-14)

Built to B2 and parked after a time-boxed round; see `docs/BUD_BACKEND_DESIGN.md`
sections 10-13. Selectable with `growth.model = "buds"` (`species/quercus_buds.toml`),
off for every shipped species. The user judges it to be heading in the right
direction; the first thing to fix on resuming is trunk thickness (unbounded pipe
memory of shed wood), which makes every limb read too heavy.

Back on the colonization oak. Next, as agreed: the geometry-stage twig-cluster pass,
then the birch; 200-year crowns stay a known limitation.

### Twig clusters (2026-10-14, done)

`geometry.cluster_twigs` / `cluster_length` / `cluster_spread`: a fan of short twigs
at every live branch tip, for the tufts at the end of each year's shoots that the
0.35 m node spacing cannot resolve. Geometry only (tier 1, live in the viewer), so
skeleton metrics and the definition-of-done sheet are unchanged (checked). Written
into `/Tree/Geom/Twigs` with a uniform `isCluster` primvar; every point is bound to the
tip's skeleton point with a non-zero `skelLocalOffset`, and the published rebind
reconstructs them exactly (tested). Oak: 5 per tip, 0.25 m, 35 degrees (about 5,000
cluster twigs at 80 y). Contract note added to `docs/USD_CONTRACT.md`.

## 6. Working agreements from the session

- Commit messages follow the user's global rules: imperative subject of 50 characters
  or fewer, body wrapped at 72, ASCII, intent only, no attribution trailers.

- Windows PowerShell is the environment; quote comma-separated CLI lists.
- When extracting a zip into the repo, extract to a temp folder first and copy in — a
  drop-in `Expand-Archive` at the repo root once created a nested `treegen/treegen` copy,
  and a later `git mv` sequence left the package at `treegen/treegen_pkg`, which is the
  layout mistake still sitting on GitHub's `main` until the local state is pushed.
- Verify with `pytest` before committing; the suite is fast and has caught real regressions.
