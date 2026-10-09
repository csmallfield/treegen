# Phase 1 definition of done: one oak, three ages x three competition scenarios,
# no parameter change other than age and neighbour placement.
#
#   .\scripts\definition_of_done.ps1            # writes out\dod
#   .\scripts\definition_of_done.ps1 out\other
#
# The comma-separated lists are quoted on purpose: unquoted, PowerShell turns
# 20,80,200 into an array and passes three separate arguments.
param([string]$Out = "out\dod")

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

treegen species/quercus.toml --seed 42 `
  --ages "20,80,200" `
  --scenes "scenes/open_field.toml,scenes/dense_forest.toml,scenes/forest_edge.toml" `
  -o $Out --png --contact-sheet "$Out/contact_sheet.png"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
