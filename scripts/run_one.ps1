param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$RawFileName,

    [Parameter(Position = 1)]
    [string]$OutputDir,

    [switch]$NoAutoConfirm,

    [switch]$RunFull
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Import-DotEnv {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        return
    }

    Get-Content $Path | ForEach-Object {
        $Line = $_.Trim()
        if (-not $Line -or $Line.StartsWith("#") -or -not $Line.Contains("=")) {
            return
        }

        $Name, $Value = $Line.Split("=", 2)
        $Name = $Name.Trim()
        $Value = $Value.Trim().Trim('"').Trim("'")
        if ($Name) {
            [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
        }
    }
}

function ConvertTo-SafeName {
    param([string]$Name)
    $BaseName = [System.IO.Path]::GetFileNameWithoutExtension($Name)
    $SafeName = $BaseName -replace "[^A-Za-z0-9_.-]+", "_"
    if (-not $SafeName) {
        $SafeName = "pride_run"
    }
    return $SafeName
}

Import-DotEnv (Join-Path $RepoRoot ".env")

if (-not $OutputDir) {
    $OutputDir = Join-Path "runs" (ConvertTo-SafeName $RawFileName)
}

$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

$CommandName = "prepare-pride-msdt-docker-input"
if ($RunFull) {
    $CommandName = "run-pride-dda-msdt-docker"
}

$Arguments = @("-m", "agent.cli", $CommandName, $RawFileName, $OutputDir)
if (-not $NoAutoConfirm) {
    $Arguments += "-y"
}

Write-Host "Running: $Python $($Arguments -join ' ')"
& $Python @Arguments
