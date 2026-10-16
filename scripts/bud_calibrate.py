"""Random search over bud-model parameters, scored against open-grown oak targets.

    python scripts/bud_calibrate.py --samples 120 --out out/buds/calib.json
    python scripts/bud_calibrate.py --around out/buds/calib.json --top 5 --local 0.5

Each sample grows the oak at 20, 80 and 200 years (open field) and scores it on log
errors against the targets below, so a factor of two either way costs the same.
With --around, a share of the samples (--local) is drawn near the best results of
an earlier search instead of across the whole space.
The point is less to find one best tree than to see whether any region of the
parameter space meets all targets at once, and which parameters matter.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# name: (low, high, log-scale)
SPACE = {
    "buds.shadow_a": (0.02, 0.15, True),
    "buds.shed_threshold": (0.12, 0.35, False),
    "buds.apical_control": (0.46, 0.54, False),
    "buds.apical_control_old": (0.42, 0.50, False),
    "buds.resource_per_light": (2.5, 6.0, True),
    "buds.shadow_depth": (3, 12, False),
    "buds.shadow_b": (1.6, 2.4, False),
    "buds.tropism": (0.0, 0.15, False),           # oak shoots grow up; spread comes from droop
    "buds.wood_set": (0.1, 0.6, False),
    "buds.max_path": (50.0, 90.0, False),
    "buds.respiration": (0.001, 0.05, True),
    "buds.reaction_wood": (0.0, 2.0, False),
    "buds.shadow_cell": (0.18, 0.35, False),
    "buds.light_weight": (0.0, 0.6, False),
    "buds.branching_angle": (30.0, 60.0, False),
    "buds.bud_life": (2, 12, False),
}
INTS = {"buds.shadow_depth", "buds.bud_life"}

# age: {metric: target}; first fork and fork gap are offset so zero is not infinitely bad
TARGETS = {
    20.0: {"dbh": 0.16, "height": 8.0, "crown_width": 7.0},
    80.0: {"dbh": 0.64, "height": 22.0, "crown_width": 22.0, "first_fork": 3.0, "fork_gap_inner": 2.0},
    200.0: {"dbh": 1.6, "height": 27.0, "crown_width": 32.0, "first_fork": 3.0, "fork_gap_inner": 2.0},
}
MAX_LIVE = {20.0: 20_000, 80.0: 200_000, 200.0: 1_200_000}
# how much each error counts (default 1); --weights selects a preset
WEIGHT_PRESETS = {
    "even": {},
    # favour the spreading open-grown crown at 80 years (the user's reference tree spreads 25 m)
    "spread80": {(80.0, "crown_width"): 4.0, (80.0, "first_fork"): 2.0,
                 (80.0, "fork_gap_inner"): 0.5, (200.0, "fork_gap_inner"): 0.5},
    # spread80, plus old limbs that spider out from the trunk instead of rising
    "oak": {(80.0, "crown_width"): 4.0, (80.0, "first_fork"): 2.0,
            (80.0, "fork_gap_inner"): 0.5, (200.0, "fork_gap_inner"): 0.5,
            (80.0, "limb_angle"): 2.0, (200.0, "limb_angle"): 3.0, (200.0, "height"): 2.0},
}
SPREAD80_WIDTH = 25.0
LIMB_ANGLE = {80.0: 50.0, 200.0: 60.0}   # deg from vertical; the reference tree's limbs are at 49 at 80
ANGLE_SCALE = 30.0                       # deg off that counts like a factor of e in size


def _sample(rng):
    out = {"buds.light_model": "shadow", "refinement.gravity_droop": 0.0}   # droop happens during growth
    for k, (lo, hi, log) in SPACE.items():
        v = math.exp(rng.uniform(math.log(lo), math.log(hi))) if log else rng.uniform(lo, hi)
        out[k] = int(round(v)) if k in INTS else round(v, 4)
    return out


def _near(rng, base, spread):
    """A sample perturbed around an earlier result: spread is a fraction of each range."""
    out = {"buds.light_model": "shadow", "refinement.gravity_droop": 0.0}   # droop happens during growth
    for k, (lo, hi, log) in SPACE.items():
        v = base.get(k, (lo + hi) / 2)
        if log:
            v = math.exp(math.log(v) + rng.gauss(0, spread) * (math.log(hi) - math.log(lo)))
        else:
            v = v + rng.gauss(0, spread) * (hi - lo)
        v = min(max(v, lo), hi)
        out[k] = int(round(v)) if k in INTS else round(v, 4)
    return out


def _score(job):
    """Score one sample; any error in growing or measuring it scores as a failed tree."""
    try:
        return _score_one(job)
    except Exception as e:                                          # noqa: BLE001
        return job[0], 99.0, {"failed": f"{type(e).__name__}: {e}"}, 0.0


def _targets(preset):
    """Targets and error weights for a weighting preset (built here so worker processes agree)."""
    targets = {age: dict(t) for age, t in TARGETS.items()}
    if preset in ("spread80", "oak"):
        targets[80.0]["crown_width"] = SPREAD80_WIDTH
    if preset == "oak":
        for age, a in LIMB_ANGLE.items():
            targets[age]["limb_angle"] = a
    return targets, WEIGHT_PRESETS[preset]


def _score_one(job):
    sets, species, preset = job
    targets_all, weights = _targets(preset)
    sys.path.insert(0, str(ROOT))
    from treegen.core.buds import grow_buds
    from treegen.metrics import tree_metrics
    from treegen.pipeline import assemble_skeleton
    from treegen.schema import Scene, load_species, validate

    p = load_species(species)
    for key, value in sets.items():
        g, n = key.split(".", 1)
        p[g][n] = value
    p = validate(p, "calibrate")
    t = time.perf_counter()
    err, detail = 0.0, {}
    for age, targets in targets_all.items():
        try:
            g = grow_buds(p, Scene(), 42, age)
        except RuntimeError:
            return sets, 99.0, {"failed": f"too many nodes by {age:g} y"}, time.perf_counter() - t
        if g.stats["alive"] < 50 or g.stats["alive"] > MAX_LIVE[age]:
            return sets, 50.0, {"failed": f"{g.stats['alive']} live nodes at {age:g} y"}, time.perf_counter() - t
        m = tree_metrics(assemble_skeleton(g, p, 42))
        for key, target in targets.items():
            v = m[key]
            if key == "limb_angle":
                e = (v - target) / ANGLE_SCALE
            elif key in ("first_fork", "fork_gap_inner"):                 # a limb can droop below 0
                e = math.log((max(v, 0.0) + 0.5) / (target + 0.5))
            else:
                e = math.log(max(v, 1e-3) / target)
            err += weights.get((age, key), 1.0) * e * e
            detail[f"{key}@{age:g}"] = round(v, 2)
        detail[f"live@{age:g}"] = g.stats["alive"]
    return sets, err, detail, time.perf_counter() - t


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--species", default=str(ROOT / "species" / "quercus_buds.toml"))
    ap.add_argument("--samples", type=int, default=120)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--workers", type=int, default=7)
    ap.add_argument("--out", default=str(ROOT / "out" / "buds" / "calib.json"))
    ap.add_argument("--around", help="an earlier result file to sample near")
    ap.add_argument("--top", type=int, default=5, help="how many of its best results to sample near")
    ap.add_argument("--local", type=float, default=0.5, help="share of samples drawn near them")
    ap.add_argument("--spread", type=float, default=0.12, help="perturbation, as a fraction of each range")
    ap.add_argument("--weights", default="even", choices=sorted(WEIGHT_PRESETS), help="error weighting preset")
    args = ap.parse_args(argv)
    rng = random.Random(args.seed)
    bases = []
    if args.around:
        bases = [r["params"] for r in json.loads(Path(args.around).read_text())[: args.top]]
    jobs = []
    for i in range(args.samples):
        if bases and rng.random() < args.local:
            jobs.append((_near(rng, rng.choice(bases), args.spread), args.species, args.weights))
        else:
            jobs.append((_sample(rng), args.species, args.weights))
    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for i, (sets, err, detail, dt) in enumerate(pool.map(_score, jobs), 1):
            results.append({"error": round(err, 3), "params": sets, "metrics": detail, "seconds": round(dt, 1)})
            if i % 20 == 0:
                print(f"{i}/{len(jobs)} done", flush=True)
                Path(args.out).parent.mkdir(parents=True, exist_ok=True)       # keep partial results
                Path(args.out).write_text(json.dumps(sorted(results, key=lambda r: r["error"]), indent=1))
    results.sort(key=lambda r: r["error"])
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results, indent=1))
    print(f"\nbest of {len(results)} (error = sum of squared log errors; 0 = every target met)")
    for r in results[:8]:
        print(f"  error {r['error']:6.2f}  {r['metrics']}")
        print("      " + "  ".join(f"{k.split('.')[1]}={v}" for k, v in r["params"].items() if k != "buds.light_model"))
    ok = sum(1 for r in results if r["error"] < 50)
    print(f"\n{ok} of {len(results)} samples grew a tree at all (the rest stalled or ran away)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
