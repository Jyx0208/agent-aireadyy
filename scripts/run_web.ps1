param(
    [int]$Port = 8000,
    [Alias("Host")]
    [string]$ListenHost = "127.0.0.1",
    [switch]$UseConda,
    [string]$CondaEnvName = "agent-aiready",
    [switch]$SkipSetupCheck
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Ensure-EnvFile {
    $EnvPath = Join-Path $RepoRoot ".env"
    $ExamplePath = Join-Path $RepoRoot ".env.example"
    if (-not (Test-Path $EnvPath) -and (Test-Path $ExamplePath)) {
        Copy-Item $ExamplePath $EnvPath
        Write-Host "Created .env from .env.example"
    }
}

function Ensure-WebSetup {
    $PythonPathFile = Join-Path $RepoRoot ".agent_python_path"
    $VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    $ConfiguredPython = $null
    if (Test-Path $PythonPathFile) {
        $ConfiguredPython = (Get-Content $PythonPathFile -TotalCount 1).Trim()
    }

    if ($SkipSetupCheck -and $ConfiguredPython -and (Test-Path $ConfiguredPython)) {
        return $ConfiguredPython
    }

    if (-not $ConfiguredPython -or -not (Test-Path $ConfiguredPython)) {
        if ($UseConda) {
            & (Join-Path $RepoRoot "scripts\setup.ps1") -NoDev -Web -UseConda -CondaEnvName $CondaEnvName
        } else {
            & (Join-Path $RepoRoot "scripts\setup.ps1") -NoDev -Web
        }
        $ConfiguredPython = (Get-Content $PythonPathFile -TotalCount 1).Trim()
    } elseif (-not (Test-Path $VenvPython) -and -not $UseConda) {
        & (Join-Path $RepoRoot "scripts\setup.ps1") -NoDev -Web
        $ConfiguredPython = (Get-Content $PythonPathFile -TotalCount 1).Trim()
    }

    & $ConfiguredPython -c "import fastapi, uvicorn" *> $null
    if ($LASTEXITCODE -ne 0) {
        if ($UseConda) {
            & (Join-Path $RepoRoot "scripts\setup.ps1") -NoDev -Web -UseConda -CondaEnvName $CondaEnvName
        } else {
            & (Join-Path $RepoRoot "scripts\setup.ps1") -NoDev -Web
        }
        $ConfiguredPython = (Get-Content $PythonPathFile -TotalCount 1).Trim()
    }

    return $ConfiguredPython
}

Ensure-EnvFile
$Python = Ensure-WebSetup
$Url = "http://localhost:$Port"
if (-not $env:AGENT_WEB_FULL_WORKFLOW_ENABLED) {
    $env:AGENT_WEB_FULL_WORKFLOW_ENABLED = "0"
}

Write-Host "Starting PRIDE AI-ready Agent Web..." -ForegroundColor Green
Write-Host "Open: $Url" -ForegroundColor Cyan
Write-Host "Full workflow enabled: $env:AGENT_WEB_FULL_WORKFLOW_ENABLED" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop." -ForegroundColor Yellow
Write-Host ""

Start-Process $Url
& $Python -m uvicorn agent.web.app:app --host $ListenHost --port $Port
