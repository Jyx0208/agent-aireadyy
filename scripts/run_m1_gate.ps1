$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "M1 gate requires the repository .venv: $python"
}

& $python -m pytest -q `
    tests/test_discovery_authority_peer_audit.py `
    tests/test_discovery_authority_properties.py `
    tests/test_discovery_publication_contracts.py `
    tests/test_discovery_repair_controller.py `
    tests/test_discovery_evidence_store.py `
    tests/test_discovery_wiring_dev_publication.py `
    tests/test_discovery_wiring_publication_to_record.py `
    tests/test_discovery_wiring_repair_authority.py `
    tests/test_discovery_agenda.py `
    tests/test_discovery_agent_turn.py `
    tests/test_discovery_task_build_plan.py `
    tests/test_control_plane.py `
    tests/test_discovery_build_ready_materialization.py `
    tests/test_discovery_m1_audit_extra.py `
    tests/test_web_discovery.py

exit $LASTEXITCODE
