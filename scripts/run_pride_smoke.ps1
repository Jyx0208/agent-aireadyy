param(
    [ValidateSet("resolution", "planning")]
    [string]$Mode = "resolution",

    [int]$SampleSize = 60,

    [int]$Jobs = 4,

    [string]$OutputRoot = ".\pride_smoke_runs",

    [double]$MaxOutputMB = 50,

    [double]$MinFreeGB = 2,

    [string]$Keywords = "lfq,tmt,phospho,dia,hela,ecoli"
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

& $Python (Join-Path $PSScriptRoot "pride_smoke_test.py") `
    --mode $Mode `
    --sample-size $SampleSize `
    --jobs $Jobs `
    --output-root $OutputRoot `
    --max-output-mb $MaxOutputMB `
    --min-free-gb $MinFreeGB `
    --keywords $Keywords
