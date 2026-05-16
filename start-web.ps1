param(
    [int]$Port = 8000,
    [Alias("Host")]
    [string]$ListenHost = "127.0.0.1",
    [switch]$UseConda,
    [string]$CondaEnvName = "agent-aiready"
)

$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot
Set-Location $RepoRoot

$ArgsForWeb = @{
    Port = $Port
    ListenHost = $ListenHost
}
if ($UseConda) {
    $ArgsForWeb.UseConda = $true
    $ArgsForWeb.CondaEnvName = $CondaEnvName
}

& (Join-Path $RepoRoot "scripts\run_web.ps1") @ArgsForWeb
