param(
    [string]$Version = "0.1.0",

    [string]$PackageName = "agent-aireadyy"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$DistDir = Join-Path $RepoRoot "dist"
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

$ArchiveName = "$PackageName-v$Version.zip"
$ArchivePath = Join-Path $DistDir $ArchiveName

if (Test-Path $ArchivePath) {
    Remove-Item $ArchivePath
}

$Tracked = git -c core.quotePath=false ls-files
if ($LASTEXITCODE -ne 0) {
    throw "git ls-files failed. Please run this script inside a Git checkout."
}

$TempDir = Join-Path $DistDir "$PackageName-v$Version"
if (Test-Path $TempDir) {
    Remove-Item -Recurse -Force $TempDir
}
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null

foreach ($File in $Tracked) {
    if ($File -match "^(runs|\.agent_cache|\.venv|dist|external|tests|task_out|task_run_real|\.test_tmp|pytest-cache-files-)/" -or $File -match "^test_") {
        continue
    }
    if ($File -match "(__pycache__|\.pyc$|\.pytest_cache)") {
        continue
    }

    $Source = Join-Path $RepoRoot $File
    $Target = Join-Path $TempDir $File
    $TargetDir = Split-Path -Parent $Target
    New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
    Copy-Item $Source $Target
}

Compress-Archive -Path (Join-Path $TempDir "*") -DestinationPath $ArchivePath -Force
Remove-Item -Recurse -Force $TempDir

Write-Host "Release package created:"
Write-Host "  $ArchivePath"
Write-Host ""
Write-Host "Recommended GitHub Release tag:"
Write-Host "  v$Version"
