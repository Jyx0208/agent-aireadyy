[CmdletBinding()]
param(
    [string]$ServerHost = $(if ($env:AGENT_DEPLOY_HOST) { $env:AGENT_DEPLOY_HOST } else { "47.253.243.164" }),
    [string]$ServerUser = $(if ($env:AGENT_DEPLOY_USER) { $env:AGENT_DEPLOY_USER } else { "root" }),
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
    '$SUDO docker compose build --no-cache web'
} else {
    '$SUDO docker compose build web'
}

$RemoteScript = @'
set -euxo pipefail
SERVER_PATH='__SERVER_PATH__'
REMOTE_NAME='__REMOTE__'
BRANCH_NAME='__BRANCH__'
cd "$SERVER_PATH"
git config --global --add safe.directory "$SERVER_PATH" || true
git fetch "$REMOTE_NAME" "$BRANCH_NAME"
git checkout "$BRANCH_NAME"
git pull --ff-only "$REMOTE_NAME" "$BRANCH_NAME"
if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
else
  SUDO="sudo"
fi
__BUILD_COMMAND__
$SUDO docker compose up -d
$SUDO docker compose ps
'@

$RemoteScript = $RemoteScript.Replace("__SERVER_PATH__", $ServerPath.Replace("'", "'\''"))
$RemoteScript = $RemoteScript.Replace("__REMOTE__", $Remote.Replace("'", "'\''"))
$RemoteScript = $RemoteScript.Replace("__BRANCH__", $Branch.Replace("'", "'\''"))
$RemoteScript = $RemoteScript.Replace("__BUILD_COMMAND__", $BuildCommand)

$Target = "$ServerUser@$ServerHost"
Write-Host ""
Write-Host "==> Deploying ${Remote}/${Branch} to ${Target}:${ServerPath}"
$RemoteScript | ssh $Target "bash -s"
if ($LASTEXITCODE -ne 0) {
    throw "Remote deploy failed."
}

Write-Host ""
Write-Host "Deploy finished. Service should be available on the server."
