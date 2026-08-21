[CmdletBinding()]
param(
    [string]$HostAlias = "pride-server",
    [string]$InstallRoot = "E:\pride-agent",
    [string]$PublicBaseUrl = "http://172.16.13.174:8000",
    [string]$TransferAddress,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($HostAlias -notmatch "^[A-Za-z0-9._-]+$") {
    throw "HostAlias contains unsupported characters: $HostAlias"
}
if ($InstallRoot -notmatch "^[A-Za-z]:\\[A-Za-z0-9._\\-]+$") {
    throw "InstallRoot contains unsupported characters: $InstallRoot"
}

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$frontendRoot = Join-Path $repoRoot "frontend\benchmark-review"
$staticRoot = Join-Path $repoRoot "src\agent\web\static\benchmark-review-next"
$helperLocal = Join-Path $PSScriptRoot "apply-windows-frontend-release.ps1"
$releaseId = "frontend-ui-" + (Get-Date -Format "yyyyMMdd-HHmmss")
$workRoot = Join-Path $repoRoot ".run\deploy"
$archivePath = Join-Path $workRoot "${releaseId}.zip"
$servedHelperName = "apply-windows-frontend-release.ps1"
$servedHelper = Join-Path $workRoot $servedHelperName
$remoteIncoming = "$InstallRoot\incoming"
$remoteArchive = "$remoteIncoming\${releaseId}.zip"
$remoteTools = "$InstallRoot\deploy-tools"
$remoteHelper = "$remoteTools\apply-windows-frontend-release.ps1"

function ConvertTo-EncodedPowerShell {
    param([Parameter(Mandatory = $true)][string]$Script)
    return [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Script))
}

function Invoke-SshPowerShell {
    param([Parameter(Mandatory = $true)][string]$Script)
    $encoded = ConvertTo-EncodedPowerShell -Script $Script
    & ssh $HostAlias powershell -NoProfile -NonInteractive -EncodedCommand $encoded
    if ($LASTEXITCODE -ne 0) {
        throw "Remote PowerShell command failed with exit code $LASTEXITCODE."
    }
}

function Resolve-TransferAddress {
    if ($TransferAddress) {
        return $TransferAddress
    }

    $sshConfig = & ssh -G $HostAlias
    if ($LASTEXITCODE -ne 0) {
        throw "Could not resolve SSH configuration for $HostAlias."
    }
    $hostnameLine = $sshConfig | Where-Object { $_ -match "^hostname\s+" } | Select-Object -First 1
    if (-not $hostnameLine) {
        throw "SSH configuration did not provide a hostname for $HostAlias."
    }
    $remoteHost = ($hostnameLine -split "\s+", 2)[1]
    $socket = [Net.Sockets.Socket]::new(
        [Net.Sockets.AddressFamily]::InterNetwork,
        [Net.Sockets.SocketType]::Dgram,
        [Net.Sockets.ProtocolType]::Udp
    )
    try {
        $socket.Connect($remoteHost, 22)
        return $socket.LocalEndPoint.Address.ToString()
    } finally {
        $socket.Dispose()
    }
}

function Get-FreeTcpPort {
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    try {
        $listener.Start()
        return ([Net.IPEndPoint]$listener.LocalEndpoint).Port
    } finally {
        $listener.Stop()
    }
}

if (-not $SkipBuild) {
    Write-Host "==> Building production frontend"
    & npm --prefix $frontendRoot run build
    if ($LASTEXITCODE -ne 0) {
        throw "Frontend build failed."
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $staticRoot "index.html") -PathType Leaf)) {
    throw "Built frontend index is missing: $staticRoot"
}

New-Item -ItemType Directory -Path $workRoot -Force | Out-Null
if (Test-Path -LiteralPath $archivePath) {
    Remove-Item -LiteralPath $archivePath -Force
}
Copy-Item -LiteralPath $helperLocal -Destination $servedHelper -Force
Compress-Archive -LiteralPath $staticRoot -DestinationPath $archivePath -CompressionLevel Optimal
$archiveSha256 = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash
$helperSha256 = (Get-FileHash -LiteralPath $servedHelper -Algorithm SHA256).Hash
$advertisedAddress = Resolve-TransferAddress
$transferPort = Get-FreeTcpPort
$python = (Get-Command python -ErrorAction Stop).Source
$transferServer = Start-Process `
    -FilePath $python `
    -ArgumentList @(
        "-m",
        "http.server",
        $transferPort,
        "--bind",
        "0.0.0.0",
        "--directory",
        $workRoot
    ) `
    -WindowStyle Hidden `
    -PassThru

try {
    $transferBaseUrl = "http://${advertisedAddress}:${transferPort}"
    $localReadyUrl = "http://127.0.0.1:${transferPort}/${servedHelperName}"
    $ready = $false
    foreach ($attempt in 1..20) {
        try {
            $probe = Invoke-WebRequest -UseBasicParsing -Uri $localReadyUrl -TimeoutSec 1
            if ($probe.StatusCode -eq 200) {
                $ready = $true
                break
            }
        } catch {
            Start-Sleep -Milliseconds 100
        }
    }
    if (-not $ready) {
        throw "Temporary transfer server did not become ready."
    }

    Write-Host "==> Transferring $([math]::Round((Get-Item $archivePath).Length / 1MB, 2)) MB and applying $releaseId"
    $applyScript = @"
`$ErrorActionPreference = "Stop"
`$ProgressPreference = "SilentlyContinue"
`$remoteHelper = '$($remoteHelper.Replace("'", "''"))'
`$helperDownload = "`$remoteHelper.download"
New-Item -ItemType Directory -Path '$($remoteTools.Replace("'", "''"))','$($remoteIncoming.Replace("'", "''"))' -Force | Out-Null
Invoke-WebRequest -UseBasicParsing -Uri '$transferBaseUrl/$servedHelperName' -OutFile `$helperDownload
if ((Get-FileHash -LiteralPath `$helperDownload -Algorithm SHA256).Hash -ne '$helperSha256') {
    throw "Remote helper checksum mismatch."
}
Move-Item -LiteralPath `$helperDownload -Destination `$remoteHelper -Force
Invoke-WebRequest -UseBasicParsing -Uri '$transferBaseUrl/$releaseId.zip' -OutFile '$($remoteArchive.Replace("'", "''"))'
& `$remoteHelper ``
  -Archive '$($remoteArchive.Replace("'", "''"))' ``
  -ExpectedSha256 '$archiveSha256' ``
  -ReleaseId '$releaseId' ``
  -InstallRoot '$($InstallRoot.Replace("'", "''"))'
"@
    Invoke-SshPowerShell -Script $applyScript

    Write-Host "==> Verifying health and frontend"
    $health = Invoke-RestMethod -Uri "${PublicBaseUrl}/api/ops/health" -TimeoutSec 20
    if (-not $health.ok -or $health.database -ne "ready") {
        throw "Operations health check failed after frontend deployment."
    }
    $page = Invoke-WebRequest -UseBasicParsing -Uri "${PublicBaseUrl}/benchmark-review?release=$releaseId" -TimeoutSec 20
    if ($page.StatusCode -ne 200 -or $page.Content -notmatch "/benchmark-review/assets/") {
        throw "Frontend page verification failed after deployment."
    }

    Remove-Item -LiteralPath $archivePath -Force
    Write-Host "Frontend deployment complete: $releaseId"
    Write-Host "${PublicBaseUrl}/benchmark-review"
} finally {
    Stop-Process -Id $transferServer.Id -Force -ErrorAction SilentlyContinue
}
