$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Production gate requires the repository .venv: $python"
}

Set-Location -LiteralPath $repoRoot

# run_m5_staged includes the canonical M1 gate before its M4/M5 aggregation.
& (Join-Path $PSScriptRoot "run_m5_staged.ps1")
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& $python -m pytest -q `
    tests/test_discovery_production_run_path.py `
    tests/test_discovery_production_authority.py `
    tests/test_discovery_m5_staged_e2e.py `
    tests/test_discovery_ledger_multi_worker.py `
    tests/test_lab_https_signer.py `
    tests/test_l3_evidence_collection.py
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& (Join-Path $PSScriptRoot "run_staging_authority_smoke.ps1")
exit $LASTEXITCODE
