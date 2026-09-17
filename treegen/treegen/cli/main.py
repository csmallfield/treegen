"""treegen command line.

    treegen species/quercus.toml --seed 42 --age 80 -o out/oak_042.usda
    treegen species/quercus.toml --ages 20,80,200 --scenes scenes/open_field.toml,scenes/dense_forest.toml -o out/
    treegen species/quercus.toml --seeds 1-12 --contact-sheet out/variants.png
    treegen species/quercus.toml --age 80 -o out/oak.usda --watch      # regenerate on TOML save
"""
from __future__ import annotations

import argparse
import itertools
import sys
import time
from pathlib import Path

from .. import __version__
from ..pipeline import Pipeline
from ..schema import SchemaError, group_hash, load_scene, load_species


def _ints(spec: str) -> list[int]:
    out = []
    for part in spec.split(","):
        if "-" in part.strip()[1:]:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        elif part.strip():
            out.append(int(part))
    return out


def _floats(spec: str) -> list[float]:
    return [float(x) for x in spec.split(",") if x.strip()]


def build_parser():
    p = argparse.ArgumentParser(prog="treegen", description="Headless USD tree generator (Phase 1)")
    p.add_argument("species", help="species profile .toml")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--seeds", help="batch seeds, e.g. 1-12 or 3,7,9")
    p.add_argument("--age", type=float, default=None, help="years (default: species reference_age)")
    p.add_argument("--ages", help="batch ages, e.g. 20,80,200")
    p.add_argument("--scene", help="neighbour scene .toml (default: open field)")
    p.add_argument("--scenes", help="batch scenes, comma separated")
    p.add_argument("-o", "--output", help="root .usda/.usdc, or a directory for batch runs")
    p.add_argument("--lod", type=float, default=1.0, help="geometry resolution only — never the simulation")
    p.add_argument("--flatten", action="store_true", help="write a single flattened file instead of layers")
    p.add_argument("--skeleton-only", action="store_true", help="skip geometry (M1-M4 fast loop)")
    p.add_argument("--png", action="store_true", help="also write a silhouette preview next to each output")
    p.add_argument("--png-mode", default="shed", choices=["skeleton", "light", "shed"])
    p.add_argument("--contact-sheet", help="write a grid of silhouettes for all runs to this PNG")
    p.add_argument("--cache-dir", default=".treegen_cache")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--watch", action="store_true", help="poll the species/scene files and regenerate on change")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--version", action="version", version=f"treegen {__version__}")
    return p


def _run_once(args, pipeline: Pipeline) -> list[dict]:
    params = load_species(args.species)
    seeds = _ints(args.seeds) if args.seeds else [args.seed]
    ages = _floats(args.ages) if args.ages else [args.age if args.age is not None else params["meta"]["reference_age"]]
    scene_paths = args.scenes.split(",") if args.scenes else [args.scene]
    batch = len(seeds) * len(ages) * len(scene_paths) > 1
    name = params["meta"]["name"]
    results = []

    for scene_path, age, seed in itertools.product(scene_paths, ages, seeds):
        scene = load_scene(scene_path)
        label = f"{name}_{scene.name}_a{age:g}_s{seed}"
        t0 = time.perf_counter()

        def progress(frac, msg, _label=label):
            if not args.quiet:
                print(f"\r  {_label}: {frac * 100:5.1f}%  {msg}      ", end="", flush=True)

        if args.skeleton_only or not args.output:
            g, sk = pipeline.skeleton(params, scene, seed, age, progress)
            geo = None
        else:
            g, sk, geo = pipeline.geometry(params, scene, seed, age, args.lod, progress)
        if not args.quiet:
            print("\r", end="")

        written = []
        if args.output:
            out = Path(args.output)
            if batch or out.suffix == "" or out.is_dir():
                out = out / f"{label}.usda"
            if geo is not None:
                from ..usd.stage import write_tree
                info = {"version": __version__, "species": name, "seed": seed, "age": float(age),
                        "scene": scene.name, "lod": float(args.lod),
                        "paramsHash": group_hash(params, list(params.keys()))}
                written = write_tree(out, sk, geo, params, info, flatten=args.flatten)
            if args.png:
                from ..preview import render_png
                png = out.with_suffix(".png")
                png.parent.mkdir(parents=True, exist_ok=True)
                render_png(sk, png, growth=g, mode=args.png_mode, title=label)
                written.append(png)

        dt = time.perf_counter() - t0
        results.append({"label": label, "skeleton": sk, "growth": g, "files": written})
        if not args.quiet:
            extra = f"  {geo.stats}" if geo else ""
            print(f"  {label}: {sk.curve_count} branches, height {sk.height:.1f} m, "
                  f"trunk r {sk.radius[0]:.2f} m  [{dt:.1f}s]{extra}")
            for f in written:
                print(f"    -> {f}")

    if args.contact_sheet:
        from ..preview import contact_sheet
        contact_sheet(results, args.contact_sheet)
        if not args.quiet:
            print(f"    -> {args.contact_sheet}")
    return results


def main(argv=None):
    args = build_parser().parse_args(argv)
    pipeline = Pipeline(cache_dir=None if args.no_cache else args.cache_dir, verbose=not args.quiet)
    try:
        _run_once(args, pipeline)
    except SchemaError as e:
        print(f"error: {e}", file=sys.stderr)
        if not args.watch:
            return 2
    if not args.watch:
        return 0

    watched = [Path(args.species)] + [Path(s) for s in (args.scenes.split(",") if args.scenes else [args.scene]) if s]
    stamp = {p: p.stat().st_mtime for p in watched}
    print(f"watching {', '.join(map(str, watched))} (Ctrl-C to stop)")
    try:
        while True:
            time.sleep(0.5)
            now = {p: p.stat().st_mtime for p in watched}
            if now != stamp:
                stamp = now
                try:
                    _run_once(args, pipeline)
                except SchemaError as e:
                    print(f"error: {e}", file=sys.stderr)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
