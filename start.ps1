param(
    [Parameter(Position = 0)]
    [string]$RawFileName,

    [string]$BatchFile,

    [string]$OutputDir,

    [string]$OutputRoot = ".\runs",

    [switch]$SetupOnly,

    [switch]$Configure,

    [switch]$NoAutoConfirm,

    [switch]$RunFull,

    [switch]$SkipSetupCheck
)

$ErrorActionPreference = "Stop"

$RepoRoot = $PSScriptRoot
Set-Location $RepoRoot

function Write-Title {
    param([string]$Text)
    Write-Host ""
    Write-Host "============================================================"
    Write-Host $Text
    Write-Host "============================================================"
}

function Write-Usage {
    Write-Title "PRIDE AI-ready Agent"
    Write-Host "Most common:"
    Write-Host "  .\start.ps1 `"P17_severe_NoPOTS.raw`""
    Write-Host ""
    Write-Host "Batch:"
    Write-Host "  Copy-Item .\files.example.txt .\files.txt"
    Write-Host "  notepad .\files.txt"
    Write-Host "  .\start.ps1 -BatchFile .\files.txt"
    Write-Host ""
    Write-Host "Configure API key:"
    Write-Host "  .\start.ps1 -Configure"
    Write-Host ""
    Write-Host "Install only:"
    Write-Host "  .\start.ps1 -SetupOnly"
    Write-Host ""
    Write-Host "Full Docker execution instead of only preparing input:"
    Write-Host "  .\start.ps1 `"P17_severe_NoPOTS.raw`" -RunFull"
}

function Ensure-EnvFile {
    $EnvPath = Join-Path $RepoRoot ".env"
    $ExamplePath = Join-Path $RepoRoot ".env.example"

    if (-not (Test-Path $EnvPath) -and (Test-Path $ExamplePath)) {
        Copy-Item $ExamplePath $EnvPath
        Write-Host "Created .env from .env.example"
    }

    return $EnvPath
}

function Test-ApiKeyConfigured {
    param([string]$EnvPath)

    if (-not (Test-Path $EnvPath)) {
        return $false
    }

    $ApiKeyLine = Get-Content $EnvPath |
        Where-Object { $_ -match "^\s*AGENT_LLM_API_KEY\s*=" } |
        Select-Object -First 1

    if (-not $ApiKeyLine) {
        return $false
    }

    $Value = ($ApiKeyLine -split "=", 2)[1].Trim().Trim('"').Trim("'")
    return ($Value -and $Value -ne "your_siliconflow_api_key")
}

function Ensure-Setup {
    $Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if ($SkipSetupCheck -and (Test-Path $Python)) {
        return
    }

    if (-not (Test-Path $Python)) {
        Write-Title "First-time setup"
        & (Join-Path $RepoRoot "scripts\setup.ps1") -NoDev
        return
    }

    Write-Host "Python environment found: .venv"
}

function Show-DockerHint {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Host "Warning: Docker command was not found. RAW conversion fallback and full MSDT Docker runs may fail."
        return
    }

    docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Warning: Docker is installed but not running. Start Docker Desktop before RAW conversion or full Docker runs."
    }
}

$EnvPath = Ensure-EnvFile

if ($Configure) {
    Write-Title "Configure API key"
    if (-not (Test-Path $EnvPath)) {
        throw ".env could not be created. Please check .env.example."
    }
    notepad $EnvPath
    exit 0
}

if ($SetupOnly) {
    Ensure-Setup
    Show-DockerHint
    Write-Host "Setup complete."
    exit 0
}

if (-not $RawFileName -and -not $BatchFile) {
    Write-Usage
    exit 0
}

Ensure-Setup
Show-DockerHint

if (-not (Test-ApiKeyConfigured $EnvPath)) {
    Write-Host ""
    Write-Host "Warning: AGENT_LLM_API_KEY is not configured in .env."
    Write-Host "The agent can still run with rule-based inference, but LLM FASTA/search-parameter recommendation will be weaker."
    Write-Host "Run this to configure it:"
    Write-Host "  .\start.ps1 -Configure"
    Write-Host ""
}

if ($BatchFile) {
    $Arguments = @($BatchFile, "-OutputRoot", $OutputRoot)
    if ($NoAutoConfirm) {
        $Arguments += "-NoAutoConfirm"
    }
    if ($RunFull) {
        $Arguments += "-RunFull"
    }
    & (Join-Path $RepoRoot "scripts\run_batch.ps1") @Arguments
    exit $LASTEXITCODE
}

$RunOneArguments = @($RawFileName)
if ($OutputDir) {
    $RunOneArguments += $OutputDir
}
if ($NoAutoConfirm) {
    $RunOneArguments += "-NoAutoConfirm"
}
if ($RunFull) {
    $RunOneArguments += "-RunFull"
}

& (Join-Path $RepoRoot "scripts\run_one.ps1") @RunOneArguments
exit $LASTEXITCODE
