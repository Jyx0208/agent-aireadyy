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

function ConvertTo-BashSingleQuoted {
    param([string]$Value)
    return "'" + $Value.Replace("'", "'\''") + "'"
}

$QuotedServerPath = ConvertTo-BashSingleQuoted $ServerPath
$QuotedRemote = ConvertTo-BashSingleQuoted $Remote
$QuotedBranch = ConvertTo-BashSingleQuoted $Branch
$BuildCommand = if ($NoCache) {
    '${SUDO} docker compose build --no-cache web'
} else {
    '${SUDO} docker compose build web'
}

$RemoteCommandLines = @(
    "set -euxo pipefail",
    "cd $QuotedServerPath",
    "git config --global --add safe.directory $QuotedServerPath || true",
    "git fetch $QuotedRemote $QuotedBranch",
    "git checkout $QuotedBranch",
    "git pull --ff-only $QuotedRemote $QuotedBranch",
    'if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi',
    'if command -v timedatectl >/dev/null 2>&1; then ${SUDO} timedatectl set-timezone Asia/Shanghai || true; fi',
    'date "+server time: %F %T %Z %z"',
    $BuildCommand,
    '${SUDO} docker compose up -d',
    '${SUDO} docker compose ps',
    '${SUDO} docker compose exec -T web date "+container time: %F %T %Z %z" || true'
)
$RemoteScript = ($RemoteCommandLines -join "`n") + "`n"
$LocalRemoteScriptPath = Join-Path ([System.IO.Path]::GetTempPath()) ("pride-agent-deploy-{0}.sh" -f ([System.Guid]::NewGuid().ToString("N")))
[System.IO.File]::WriteAllText(
    $LocalRemoteScriptPath,
    $RemoteScript,
    [System.Text.UTF8Encoding]::new($false)
)

$Target = "$ServerUser@$ServerHost"
Write-Host ""
Write-Host "==> Deploying ${Remote}/${Branch} to ${Target}:${ServerPath}"
try {
    $SshCommand = 'ssh {0} "bash -s" < "{1}"' -f $Target, $LocalRemoteScriptPath
    cmd.exe /d /c $SshCommand
    $DeployExitCode = $LASTEXITCODE
} finally {
    Remove-Item -LiteralPath $LocalRemoteScriptPath -Force -ErrorAction SilentlyContinue
}
if ($DeployExitCode -ne 0) {
    throw "Remote deploy failed."
}

Write-Host ""
Write-Host "Deploy finished. Service should be available on the server."
