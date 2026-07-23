$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Staging Authority smoke requires the repository .venv: $python"
}

$temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    "discovery-authority-staging-" + [System.Guid]::NewGuid().ToString("N")
)
New-Item -ItemType Directory -Path $temporaryRoot | Out-Null

$previous = @{}
$exitCode = 1
$names = @(
    "DISCOVERY_AUTHORITY_MODE",
    "DISCOVERY_AUTHORITY_LEDGER_PATH",
    "DISCOVERY_REPAIR_AUTHORITY_ID",
    "DISCOVERY_STAGING_TEST_ED25519_PRIVATE_KEY_B64",
    "DISCOVERY_STAGING_SMOKE"
)
foreach ($name in $names) {
    $previous[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}

try {
    $ephemeralPrivateKey = & $python -c "import base64; from cryptography.hazmat.primitives import serialization; from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey; k=Ed25519PrivateKey.generate(); print(base64.b64encode(k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())).decode('ascii'))"
    if ($LASTEXITCODE -ne 0 -or -not $ephemeralPrivateKey) {
        throw "Failed to generate an ephemeral staging Ed25519 key"
    }
    $env:DISCOVERY_AUTHORITY_MODE = "production"
    $env:DISCOVERY_AUTHORITY_LEDGER_PATH = Join-Path $temporaryRoot "authority.sqlite"
    $env:DISCOVERY_REPAIR_AUTHORITY_ID = "repair-authority:local-staging-smoke"
    $env:DISCOVERY_STAGING_TEST_ED25519_PRIVATE_KEY_B64 = $ephemeralPrivateKey.Trim()
    $env:DISCOVERY_STAGING_SMOKE = "1"
    Set-Location -LiteralPath $repoRoot

    & $python -m pytest -q `
        tests/test_discovery_m5_staged_e2e.py::test_stage3_builder_dry_run_accepts_only_issued_canonical_package `
        tests/test_discovery_production_run_path.py::test_normal_ready_audit_issues_durable_publication_attempt `
        tests/test_discovery_production_run_path.py::test_normal_ready_audit_rejects_caller_invented_publication_attempt `
        tests/test_discovery_production_run_path.py::test_production_repair_cycle_uses_same_durable_ledger_for_completion
    $exitCode = $LASTEXITCODE
}
finally {
    foreach ($name in $names) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], "Process")
    }
    Remove-Item -LiteralPath $temporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
}

exit $exitCode
