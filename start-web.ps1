param(
    [int]$Port = 8000,
    [Alias("Host")]
    [string]$ListenHost = "127.0.0.1",
    [switch]$UseConda,
    [string]$CondaEnvName = "agent-aiready",
    [int]$WorkerCount = 4
)

$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot
Set-Location $RepoRoot

$ArgsForWeb = @{
    Port = $Port
    ListenHost = $ListenHost
    WorkerCount = $WorkerCount
}
if ($UseConda) {
    $ArgsForWeb.UseConda = $true
    $ArgsForWeb.CondaEnvName = $CondaEnvName
}

if ($UseConda) {
    throw "The industrial operations worker currently requires the project .venv on Windows."
}

& (Join-Path $RepoRoot "scripts\run_platform.ps1") @ArgsForWeb
