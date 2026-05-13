param(
    [switch]$NoDev,

    [switch]$Web,

    [switch]$UseConda,

    [string]$CondaEnvName = "agent-aiready",

    [string]$PythonVersion = "3.13"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Write-AgentPythonPath {
    param([string]$PythonPath)
    Set-Content -Path (Join-Path $RepoRoot ".agent_python_path") -Value $PythonPath -Encoding UTF8
}

function Get-CondaPython {
    param(
        [string]$EnvName,
        [string]$Version
    )

    if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
        throw "Conda was requested but the 'conda' command was not found. Open Anaconda Prompt/Miniconda PowerShell or use setup.ps1 without -UseConda."
    }

    conda run -n $EnvName python --version *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Creating conda environment: $EnvName (python=$Version)"
        conda create -y -n $EnvName "python=$Version" pip
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create conda environment: $EnvName"
        }
    }

    $CondaPython = conda run -n $EnvName python -c "import sys; print(sys.executable)"
    if ($LASTEXITCODE -ne 0 -or -not $CondaPython) {
        throw "Failed to locate Python in conda environment: $EnvName"
    }

    return ($CondaPython | Select-Object -First 1).Trim()
}

if ($UseConda) {
    $Python = Get-CondaPython -EnvName $CondaEnvName -Version $PythonVersion
    Write-Host "Using conda Python: $Python"
} else {
    if (-not (Test-Path ".venv")) {
        Write-Host "Creating Python virtual environment: .venv"
        python -m venv .venv
    }

    $Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    Write-Host "Using venv Python: $Python"
}

Write-AgentPythonPath $Python

Write-Host "Upgrading pip"
& $Python -m pip install -U pip

if ($NoDev) {
    if ($Web) {
        Write-Host "Installing package with web dependencies"
        & $Python -m pip install ".[web]"
    } else {
        Write-Host "Installing package"
        & $Python -m pip install .
    }
} else {
    if ($Web) {
        Write-Host "Installing package with dev and web dependencies"
        & $Python -m pip install -e ".[dev,web]"
    } else {
        Write-Host "Installing package with dev dependencies"
        & $Python -m pip install -e ".[dev]"
    }
}

Write-Host "Checking CLI import"
& $Python -m agent.cli --help *> $null

if (-not (Test-Path ".env") -and (Test-Path ".env.example")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example. Please edit AGENT_LLM_API_KEY before real runs."
}

Write-Host ""
Write-Host "Setup complete."
Write-Host "Next:"
Write-Host "  notepad .env"
Write-Host "  .\start.ps1 `"P17_severe_NoPOTS.raw`""
