$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "M5 staged gate requires the repository .venv: $python"
}

Set-Location -LiteralPath $repoRoot

& (Join-Path $PSScriptRoot "run_m1_gate.ps1")
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& $python -m pytest -q `
    tests/test_discovery_production_authority.py `
    tests/test_discovery_m5_staged_e2e.py `
    tests/test_discovery_authority_peer_audit.py `
    tests/test_discovery_authority_properties.py `
    tests/test_discovery_publication_contracts.py `
    tests/test_discovery_repair_controller.py `
    tests/test_discovery_evidence_store.py `
    tests/test_discovery_build_ready_materialization.py

exit $LASTEXITCODE
