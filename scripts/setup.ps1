param(
    [switch]$NoDev
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

if (-not (Test-Path ".venv")) {
    Write-Host "Creating Python virtual environment: .venv"
    python -m venv .venv
}

$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"

Write-Host "Upgrading pip"
& $Python -m pip install -U pip

if ($NoDev) {
    Write-Host "Installing package"
    & $Python -m pip install .
} else {
    Write-Host "Installing package with dev dependencies"
    & $Python -m pip install -e ".[dev]"
}

if (-not (Test-Path ".env") -and (Test-Path ".env.example")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example. Please edit AGENT_LLM_API_KEY before real runs."
}

Write-Host ""
Write-Host "Setup complete."
Write-Host "Next:"
Write-Host "  notepad .env"
Write-Host "  .\scripts\run_one.ps1 `"P17_severe_NoPOTS.raw`""
