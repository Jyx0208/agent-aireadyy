[CmdletBinding()]
param(
    [string]$ServerHost = $(if ($env:AGENT_DEPLOY_HOST) { $env:AGENT_DEPLOY_HOST } else { "47.253.243.164" }),
    [string]$ServerUser = $(if ($env:AGENT_DEPLOY_USER) { $env:AGENT_DEPLOY_USER } else { "admin" }),
    [string]$ServerPath = $(if ($env:AGENT_DEPLOY_PATH) { $env:AGENT_DEPLOY_PATH } else { "/opt/pride-agent" }),
    [string]$Branch = $(if ($env:AGENT_DEPLOY_BRANCH) { $env:AGENT_DEPLOY_BRANCH } else { "main" }),
    [string]$Remote = $(if ($env:AGENT_DEPLOY_REMOTE) { $env:AGENT_DEPLOY_REMOTE } else { "origin" }),
    [string]$CommitMessage = "",
    [switch]$NoCommit,
    [switch]$NoPush,
    [switch]$NoCache
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found in PATH: $Name"
    }
}

function Invoke-Checked {
    param(
        [string]$Title,
        [scriptblock]$Command
    )
    Write-Host ""
    Write-Host "==> $Title"
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Step failed: $Title"
    }
}

Assert-Command git
Assert-Command ssh

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

if (-not $CommitMessage) {
    $CommitMessage = "deploy: update server $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
}

$Status = & git status --porcelain
if ($Status) {
    if ($NoCommit) {
        throw "Working tree has local changes. Remove -NoCommit or commit manually before deploy."
    }
    Invoke-Checked "Stage local changes" { git add -A }
    Invoke-Checked "Commit local changes" { git commit -m $CommitMessage }
} else {
    Write-Host "No local changes to commit."
}

if (-not $NoPush) {
    Invoke-Checked "Push current HEAD to $Remote/$Branch" { git push $Remote "HEAD:$Branch" }
} else {
    Write-Host "Skipping git push because -NoPush was supplied."
}

$BuildCommand = if ($NoCache) {
    "sudo docker compose build --no-cache web"
} else {
    "sudo docker compose build web"
}

$RemoteScript = @"
set -euo pipefail
cd '$ServerPath'
git fetch '$Remote' '$Branch'
git checkout '$Branch'
git pull --ff-only '$Remote' '$Branch'
$BuildCommand
sudo docker compose up -d
sudo docker compose ps
"@

$Target = "$ServerUser@$ServerHost"
Write-Host ""
Write-Host "==> Deploying ${Remote}/${Branch} to ${Target}:${ServerPath}"
$RemoteScript | ssh $Target "bash -s"
if ($LASTEXITCODE -ne 0) {
    throw "Remote deploy failed."
}

Write-Host ""
Write-Host "Deploy finished. Service should be available on the server."
