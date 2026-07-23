# Deploy current main to lab host 172.16.13.5:8000 with FULL workflow enabled.
# Prerequisites: SSH key accepted on the server; /opt/pride-agent exists with docker compose.
# Usage:
#   powershell -File scripts\deploy_lab_172.ps1
#   powershell -File scripts\deploy_lab_172.ps1 -ServerUser jyx
param(
  [string]$ServerHost = "172.16.13.5",
  [string]$ServerUser = $(if ($env:AGENT_DEPLOY_USER) { $env:AGENT_DEPLOY_USER } else { "root" }),
  [string]$ServerPath = $(if ($env:AGENT_DEPLOY_PATH) { $env:AGENT_DEPLOY_PATH } else { "/opt/pride-agent" }),
  [string]$Branch = "main",
  [string]$Remote = "origin",
  [switch]$NoPush
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

if (-not $NoPush) {
  Write-Host "==> Ensure main is up to date on GitHub first (push separately if needed)"
}

$Target = "$ServerUser@$ServerHost"
Write-Host "==> SSH deploy $Remote/$Branch -> ${Target}:$ServerPath (full workflow ON)"

$script = @"
set -euxo pipefail
cd '$ServerPath'
git config --global --add safe.directory '$ServerPath' || true
git fetch origin $Branch
git checkout $Branch
git pull --ff-only origin $Branch
if [ "`$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi
# Enable real batch/full execution on this lab host
if [ -f .env ]; then
  if grep -q '^AGENT_WEB_FULL_WORKFLOW_ENABLED=' .env; then
    sed -i 's/^AGENT_WEB_FULL_WORKFLOW_ENABLED=.*/AGENT_WEB_FULL_WORKFLOW_ENABLED=1/' .env
  else
    echo 'AGENT_WEB_FULL_WORKFLOW_ENABLED=1' >> .env
  fi
else
  cp -n .env.example .env || true
  echo 'AGENT_WEB_FULL_WORKFLOW_ENABLED=1' >> .env
fi
# Bind hints (compose usually maps 8000:8000)
`${SUDO} docker compose build web
`${SUDO} docker compose up -d
`${SUDO} docker compose ps
curl -fsS http://127.0.0.1:8000/api/health || true
curl -fsS http://127.0.0.1:8000/api/health | tr -d '\n' || true
echo
"@

$tmp = Join-Path $env:TEMP ("deploy-lab172-" + [guid]::NewGuid().ToString("N") + ".sh")
[System.IO.File]::WriteAllText($tmp, $script, [System.Text.UTF8Encoding]::new($false))
try {
  $cmd = 'ssh {0} "bash -s" < "{1}"' -f $Target, $tmp
  cmd.exe /d /c $cmd
  if ($LASTEXITCODE -ne 0) { throw "Remote deploy failed (exit $LASTEXITCODE). Fix SSH auth to $Target then re-run." }
} finally {
  Remove-Item $tmp -Force -ErrorAction SilentlyContinue
}

Write-Host "Lab deploy finished. Open http://${ServerHost}:8000/benchmark-review"
Write-Host "Confirm health.full_workflow_enabled is true for real runs."
