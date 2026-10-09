# treegen — handoff

Written 2026-10-09, at the end of the chat session that built Phase 1 (M1–M6) plus a
first pass at M7. Attach this file to a new conversation to pick up where that one left off.

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
   *Status 2026-10-09: first pass done, oak not re-tuned yet; see the note below the list.*
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

Open decision: what k the oak should use, and whether to recover the edge lean, for
example by offsetting the envelope toward the side the neighbour shade leaves open
(still emergent from neighbour placement, not a lean parameter).

## 6. Working agreements from the session

- Windows PowerShell is the environment; quote comma-separated CLI lists.
- When extracting a zip into the repo, extract to a temp folder first and copy in — a
  drop-in `Expand-Archive` at the repo root once created a nested `treegen/treegen` copy,
  and a later `git mv` sequence left the package at `treegen/treegen_pkg`, which is the
  layout mistake still sitting on GitHub's `main` until the local state is pushed.
- Verify with `pytest` before committing; the suite is fast and has caught real regressions.
