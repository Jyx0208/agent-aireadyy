param(
    [int]$Port = 8000,
    [Alias("Host")]
    [string]$ListenHost = "127.0.0.1",
    [int]$WorkerCount = 4
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$runDir = Join-Path $repoRoot ".run"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Project virtual environment is missing: $python"
}
if (-not (Test-Path -LiteralPath $runDir)) {
    New-Item -ItemType Directory -Path $runDir | Out-Null
}

$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = Join-Path $repoRoot "src"
$workerStartInfo = [System.Diagnostics.ProcessStartInfo]::new()
$workerStartInfo.FileName = $python
$workerStartInfo.Arguments = (
    "-m huey.bin.huey_consumer agent.operations.queue.huey " +
    "-k thread -w $WorkerCount"
)
$workerStartInfo.WorkingDirectory = $repoRoot
$workerStartInfo.UseShellExecute = $false
$workerStartInfo.CreateNoWindow = $true
# Avoid Windows PowerShell Start-Process here. Some managed desktop hosts
# inherit both `Path` and `PATH`; Start-Process copies them into a
# case-insensitive dictionary and fails before the worker starts. Process.Start
# passes the existing environment block through without that lossy conversion.
$worker = [System.Diagnostics.Process]::Start($workerStartInfo)

try {
    Set-Location -LiteralPath $repoRoot
    & $python -m uvicorn --app-dir (Join-Path $repoRoot "src") agent.web.app:app --host $ListenHost --port $Port
    exit $LASTEXITCODE
}
finally {
    if ($worker -and -not $worker.HasExited) {
        Stop-Process -Id $worker.Id
        $worker.WaitForExit(10000) | Out-Null
    }
    $env:PYTHONPATH = $previousPythonPath
}
