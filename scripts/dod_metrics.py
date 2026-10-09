"""Measure the definition-of-done batch: one species, three ages x three scenes.

    python scripts/dod_metrics.py                               # print the table
    python scripts/dod_metrics.py --json docs/metrics/dod_baseline.json
    python scripts/dod_metrics.py --compare docs/metrics/dod_baseline.json
    python scripts/dod_metrics.py --set envelope.softness=0.3 --compare docs/metrics/dod_baseline.json

--set overrides one species parameter (value parsed as JSON, so 0.3, [0, 1] and
{"mean": 40} all work) without editing the TOML. Runs go to worker processes and
use the same disk cache as the CLI, so an unchanged run is nearly free.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENES = ["open_field", "dense_forest", "forest_edge"]
AGES = [20.0, 80.0, 200.0]
COLUMNS = ["height", "first_fork", "crown_base", "bole_fraction", "crown_width", "crown_offset_x", "dbh", "branches",
           "whips", "limb_ld_p95"]


def _one(job):
    species, overrides, scene_name, age, seed, cache = job
    from treegen.metrics import tree_metrics
    from treegen.pipeline import Pipeline
    from treegen.schema import load_scene, load_species, validate

    params = load_species(species)
    for key, value in overrides:
        group, name = key.split(".", 1)
        params[group][name] = value
    params = validate(params, "--set")
    scene = load_scene(ROOT / "scenes" / f"{scene_name}.toml")
    _, sk = Pipeline(cache_dir=cache).skeleton(params, scene, seed, age)
    return f"{scene_name}/a{age:g}", tree_metrics(sk)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--species", default=str(ROOT / "species" / "quercus.toml"))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--set", action="append", default=[], metavar="GROUP.NAME=VALUE")
    ap.add_argument("--json", help="write the results here")
    ap.add_argument("--compare", help="print differences against an earlier --json file")
    ap.add_argument("--cache-dir", default=str(ROOT / ".treegen_cache"))
    ap.add_argument("--no-cache", action="store_true",
                    help="re-simulate everything (the cache is keyed on parameters, not code)")
    args = ap.parse_args(argv)

    overrides = []
    for s in args.set:
        k, v = s.split("=", 1)
        overrides.append((k.strip(), json.loads(v)))
    cache = None if args.no_cache else args.cache_dir
    jobs = [(args.species, overrides, sc, age, args.seed, cache) for sc in SCENES for age in AGES]
    with ProcessPoolExecutor(max_workers=max(1, min(len(jobs), (os.cpu_count() or 2) - 1))) as pool:
        results = dict(pool.map(_one, jobs))

    base = json.loads(Path(args.compare).read_text())["runs"] if args.compare else None
    head = f"{'run':<22}" + "".join(f"{c:>16}" for c in COLUMNS)
    print(head)
    print("-" * len(head))
    for key, m in results.items():
        row = f"{key:<22}"
        for c in COLUMNS:
            v = m[c]
            cell = f"{v:.2f}" if isinstance(v, float) else str(v)
            if base and key in base and c in base[key]:
                d = v - base[key][c]
                cell += f" ({d:+.2f})" if isinstance(v, float) else f" ({d:+d})"
            row += f"{cell:>16}"
        print(row)

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"species": Path(args.species).name, "seed": args.seed,
                                   "overrides": dict(overrides), "runs": results}, indent=2) + "\n")
        print(f"\n-> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
