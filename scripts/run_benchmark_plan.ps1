param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$FileList,

    [string]$OutputRoot = ".\benchmark_runs",

    [string]$Excel = ".\benchmark_results.xlsx",

    [int]$Jobs = 3
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$PythonPathFile = Join-Path $RepoRoot ".agent_python_path"
$Python = $null
if (Test-Path $PythonPathFile) {
    $Python = (Get-Content $PythonPathFile -TotalCount 1).Trim()
}

if (-not $Python -or -not (Test-Path $Python)) {
    $Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path $Python)) {
    $Python = "python"
}

& $Python (Join-Path $PSScriptRoot "run_benchmark_plan.py") $FileList --output-root $OutputRoot --excel $Excel --jobs $Jobs
