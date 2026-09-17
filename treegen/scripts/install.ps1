# treegen installer for Windows PowerShell 5.1+ / PowerShell 7
# Usage:  .\scripts\install.ps1                      (auto-detect Python)
#         .\scripts\install.ps1 -Python "C:\path\to\python.exe"
param(
    [string]$Python = "",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$repo = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repo

function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }
function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }

# --- 1. find a real python.exe ---------------------------------------------------------
function Test-Python($exe) {
    if (-not $exe -or -not (Test-Path $exe)) { return $false }
    if ($exe -like "*WindowsApps*") { return $false }            # Microsoft Store stub
    $v = & $exe -c "import sys; print(sys.version_info >= (3, 11))" 2>$null
    return ($LASTEXITCODE -eq 0 -and $v -eq "True")
}

Step "Locating Python"
$candidates = @()
if ($Python) { $candidates += $Python }

# Start Menu shortcuts (per-user and all-users) -> resolve their targets
$shell = New-Object -ComObject WScript.Shell
$menus = @("$env:APPDATA\Microsoft\Windows\Start Menu\Programs",
           "$env:ProgramData\Microsoft\Windows\Start Menu\Programs")
foreach ($m in $menus) {
    Get-ChildItem $m -Recurse -Filter "Python*.lnk" -ErrorAction SilentlyContinue |
        Where-Object Name -notmatch "IDLE|Manual|Docs|Module" |
        ForEach-Object { $candidates += $shell.CreateShortcut($_.FullName).TargetPath }
}

# usual install locations, newest first
$roots = @("$env:LOCALAPPDATA\Programs\Python", "$env:ProgramFiles", "$env:LOCALAPPDATA\Python")
foreach ($r in $roots) {
    Get-ChildItem $r -Directory -Filter "*ython*" -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending |
        ForEach-Object { $candidates += (Join-Path $_.FullName "python.exe") }
}
$onPath = Get-Command python -ErrorAction SilentlyContinue
if ($onPath) { $candidates += $onPath.Source }

$py = $candidates | Where-Object { Test-Python $_ } | Select-Object -First 1
if (-not $py) {
    Fail "No Python 3.11+ found. Pass it explicitly: .\scripts\install.ps1 -Python 'C:\...\python.exe'"
}
Write-Host "Using $py ($(& $py --version))"

# --- 2. virtual environment ------------------------------------------------------------
Step "Creating .venv"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & $py -m venv .venv
    if ($LASTEXITCODE -ne 0) { Fail "venv creation failed" }
} else {
    Write-Host ".venv already exists, reusing it"
}
$venvPy = Join-Path $repo ".venv\Scripts\python.exe"

& $venvPy -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) { Fail "pip upgrade failed" }

# --- 3. check usd-core has a wheel for this Python before installing everything ---------
Step "Checking usd-core wheel availability"
& $venvPy -m pip install usd-core --only-binary=:all: --quiet
if ($LASTEXITCODE -ne 0) {
    $ver = & $venvPy -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    Fail ("usd-core has no prebuilt wheel for Python $ver on this platform.`n" +
          "       Install Python 3.12 alongside, delete .venv, and rerun with:`n" +
          "       .\scripts\install.ps1 -Python `"$env:LOCALAPPDATA\Programs\Python\Python312\python.exe`"")
}

# --- 4. install treegen ----------------------------------------------------------------
Step "Installing treegen (editable, with dev extras)"
& $venvPy -m pip install -e ".[dev]" --quiet
if ($LASTEXITCODE -ne 0) { Fail "pip install -e .[dev] failed" }

# --- 5. verify -------------------------------------------------------------------------
if (-not $SkipTests) {
    Step "Running tests"
    & $venvPy -m pytest -q
    if ($LASTEXITCODE -ne 0) { Fail "tests failed" }
}

Step "Smoke run"
& (Join-Path $repo ".venv\Scripts\treegen.exe") species/quercus.toml --seed 42 -o out/oak_042.usda --png -q
if ($LASTEXITCODE -ne 0) { Fail "treegen smoke run failed" }
Write-Host "Wrote out\oak_042.usda and out\oak_042.png"

Write-Host "`nDone. In each new PowerShell session:" -ForegroundColor Green
Write-Host "  cd $repo"
Write-Host "  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass"
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  treegen species/quercus.toml --age 80 -o out/oak.usda --watch"