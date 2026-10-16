"""Grow the bud-model oak at 20, 80 and 200 years and print it against oak targets.

    python scripts/bud_eval.py                                     # species/quercus_buds.toml
    python scripts/bud_eval.py --set buds.shed_threshold=0.2       # try a value without editing the TOML
    python scripts/bud_eval.py --scene dense_forest --png out/buds/forest
    python scripts/bud_eval.py --calib out/buds/calib.json --rank 0   # a result of bud_calibrate.py

Targets are for an open-grown Quercus robur:
  DBH     Mitchell's rule of thumb, about 2.5 cm of girth a year in the open
          (0.16 m at 20 y, 0.64 m at 80 y, 1.6 m at 200 y; old trees slow down)
  height  mature open-grown oaks are typically 20-30 m, rarely above 35 m
  fork    first major fork 2-5 m in the open (the winter reference photo)
  gap     wood between forks on limbs at least 5 cm thick, judged by thickness (the smaller
          side at least a quarter of the parent): 2 m or more (the approved colonization oak: 2.1 m)
Each age runs in its own process.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TARGETS = {          # age: (DBH m, height range m)
    20.0: (0.16, (6, 10)),
    80.0: (0.64, (18, 26)),
    200.0: (1.6, (22, 32)),
}


def _run(job):
    species, overrides, scene_name, age, png = job
    sys.path.insert(0, str(ROOT))
    from treegen.core.buds import grow_buds
    from treegen.metrics import tree_metrics
    from treegen.pipeline import assemble_skeleton
    from treegen.schema import load_scene, load_species, validate

    p = load_species(species)
    for key, value in overrides:
        g, n = key.split(".", 1)
        p[g][n] = value
    p = validate(p, "--set")
    t = time.perf_counter()
    try:
        g = grow_buds(p, load_scene(ROOT / "scenes" / f"{scene_name}.toml"), 42, age)
    except RuntimeError as e:
        return age, None, str(e), time.perf_counter() - t
    dt = time.perf_counter() - t
    if g.stats["alive"] < 20:
        return age, None, f"stalled at {g.stats['alive']} live nodes", dt
    sk = assemble_skeleton(g, p, 42)
    m = tree_metrics(sk)
    m.update(live=g.stats["alive"], total=g.stats["nodes"])
    if png:
        from treegen.preview import render_png
        render_png(sk, f"{png}_a{age:g}.png", mode="skeleton", size=6.0, views=("side",),
                   title=f"bud model, {scene_name}, age {age:g}")
    return age, m, "", dt


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--species", default=str(ROOT / "species" / "quercus_buds.toml"))
    ap.add_argument("--set", action="append", default=[], metavar="GROUP.NAME=VALUE")
    ap.add_argument("--scene", default="open_field")
    ap.add_argument("--ages", default="20,80,200")
    ap.add_argument("--png", help="write a side-view silhouette per age with this path prefix")
    ap.add_argument("--calib", help="take parameters from a bud_calibrate.py result file")
    ap.add_argument("--rank", type=int, default=0, help="which result in --calib (0 = best)")
    args = ap.parse_args(argv)
    overrides = []
    if args.calib:
        overrides += list(json.loads(Path(args.calib).read_text())[args.rank]["params"].items())
    overrides += [(k.strip(), json.loads(v)) for k, v in (s.split("=", 1) for s in args.set)]
    ages = [float(a) for a in args.ages.split(",")]
    if args.png:
        Path(args.png).parent.mkdir(parents=True, exist_ok=True)

    jobs = [(args.species, overrides, args.scene, a, args.png) for a in ages]
    with ProcessPoolExecutor(max_workers=len(jobs)) as pool:
        results = list(pool.map(_run, jobs))

    print(f"{args.scene}  " + "  ".join(f"{k}={v}" for k, v in overrides))
    print(f"{'age':>5} {'DBH':>12} {'height':>16} {'width':>6} {'1st fork':>9} {'limbs':>6} {'gap in/out':>11} "
          f"{'branches':>8} {'whips':>5} {'live/total nodes':>18} {'time':>7}")
    for age, m, err, dt in results:
        if m is None:
            print(f"{age:5g}  {err}  ({dt:.0f} s)")
            continue
        dbh_t, (h_lo, h_hi) = TARGETS.get(age, (float("nan"), (0, 0)))
        print(f"{age:5g} {m['dbh']:5.2f} ({dbh_t:4.2f}) {m['height']:5.1f} ({h_lo:g}-{h_hi:g}) {m['crown_width']:6.1f} "
              f"{m['first_fork']:9.1f} {m['limb_angle']:6.0f} {m['fork_gap_inner']:5.2f}/{m['fork_gap_outer']:4.2f} {m['branches']:8d} {m['whips']:5d} "
              f"{m['live']:8d}/{m['total']:<9d} {dt:6.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
