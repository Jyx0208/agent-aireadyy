param(
    [int]$Workers = 4
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Project virtual environment is missing: $python"
}

Set-Location -LiteralPath $repoRoot
& $python -m huey.bin.huey_consumer agent.operations.queue.huey --worker-type thread --workers $Workers
exit $LASTEXITCODE
