#!/usr/bin/env bash
# Phase 1 definition of done: one oak, three ages x three competition scenarios,
# no parameter change other than age and neighbour placement.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=${1:-out/dod}
treegen species/quercus.toml --seed 42 \
  --ages 20,80,200 \
  --scenes scenes/open_field.toml,scenes/dense_forest.toml,scenes/forest_edge.toml \
  -o "$OUT" --png --contact-sheet "$OUT/contact_sheet.png"
