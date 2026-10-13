$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Config = Join-Path $ProjectRoot "config.json"

if (-not (Test-Path $Python)) {
  throw "The project environment is missing. Create it with: python -m venv .venv, then install requirements.txt."
}

if (-not (Test-Path $Config)) {
  Copy-Item (Join-Path $ProjectRoot "config.example.json") $Config
}

& $Python (Join-Path $PSScriptRoot "run_bigquery_extracts.py")
& $Python (Join-Path $PSScriptRoot "run_salesforce_extract.py")
& $Python (Join-Path $PSScriptRoot "reconcile_mexico_orders.py")
