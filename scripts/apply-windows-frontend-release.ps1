[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Archive,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[A-Fa-f0-9]{64}$")]
    [string]$ExpectedSha256,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[A-Za-z0-9._-]+$")]
    [string]$ReleaseId,

    [string]$InstallRoot = "E:\pride-agent"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
Set-StrictMode -Version Latest

function Get-ManagedPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Root
    )

    $rootPath = [System.IO.Path]::GetFullPath($Root).TrimEnd("\") + "\"
    $candidate = [System.IO.Path]::GetFullPath($Path)
    if (-not $candidate.StartsWith($rootPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to manage a path outside ${Root}: $candidate"
    }
    return $candidate
}

$root = [System.IO.Path]::GetFullPath($InstallRoot).TrimEnd("\")
$archivePath = Get-ManagedPath -Path $Archive -Root $root
$incomingRoot = Get-ManagedPath -Path (Join-Path $root "incoming") -Root $root
$releasesRoot = Get-ManagedPath -Path (Join-Path $root "releases") -Root $root
$backupsRoot = Get-ManagedPath -Path (Join-Path $root "backups") -Root $root
$deploymentsRoot = Get-ManagedPath -Path (Join-Path $root "deployments") -Root $root
$stageRoot = Get-ManagedPath -Path (Join-Path $incomingRoot "${ReleaseId}-stage") -Root $root
$backupRoot = Get-ManagedPath -Path (Join-Path $backupsRoot $ReleaseId) -Root $root

if (-not (Test-Path -LiteralPath $archivePath -PathType Leaf)) {
    throw "Frontend archive does not exist: $archivePath"
}

$actualSha256 = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash
if ($actualSha256 -ne $ExpectedSha256.ToUpperInvariant()) {
    throw "Frontend archive checksum mismatch. Expected $ExpectedSha256, got $actualSha256."
}

$currentReleaseFile = Join-Path $root "current-release.txt"
if (-not (Test-Path -LiteralPath $currentReleaseFile -PathType Leaf)) {
    throw "Missing current release marker: $currentReleaseFile"
}
$baseRelease = (Get-Content -Raw -LiteralPath $currentReleaseFile).Trim()
if ($baseRelease -notmatch "^[A-Za-z0-9._-]+$") {
    throw "Invalid current release id: $baseRelease"
}

$releaseRoot = Get-ManagedPath -Path (Join-Path $releasesRoot $baseRelease) -Root $root
$staticRoot = Get-ManagedPath -Path (
    Join-Path $releaseRoot "src\agent\web\static"
) -Root $root
$target = Get-ManagedPath -Path (
    Join-Path $staticRoot "benchmark-review-next"
) -Root $root
$backupTarget = Get-ManagedPath -Path (
    Join-Path $backupRoot "benchmark-review-next"
) -Root $root

if (-not (Test-Path -LiteralPath $target -PathType Container)) {
    throw "Current frontend directory does not exist: $target"
}

if (Test-Path -LiteralPath $stageRoot) {
    Remove-Item -LiteralPath $stageRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null
$stagedFrontend = Get-ManagedPath -Path (
    Join-Path $stageRoot "benchmark-review-next"
) -Root $root
New-Item -ItemType Directory -Path $stagedFrontend -Force | Out-Null
# Start from the currently served tree. A future uploader may send only new
# content-hashed assets plus index.html; a full bundle works the same way.
Copy-Item -Path (Join-Path $target "*") -Destination $stagedFrontend -Recurse -Force

$swapped = $false
try {
    Expand-Archive -LiteralPath $archivePath -DestinationPath $stageRoot -Force
    $stagedIndex = Join-Path $stagedFrontend "index.html"
    if (-not (Test-Path -LiteralPath $stagedIndex -PathType Leaf)) {
        throw "Archive is missing benchmark-review-next\index.html."
    }

    $indexHtml = Get-Content -Raw -LiteralPath $stagedIndex
    $assetReferences = [regex]::Matches(
        $indexHtml,
        "/benchmark-review/assets/([^`"']+)"
    ) | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique
    if (-not $assetReferences) {
        throw "Frontend index does not reference any versioned assets."
    }
    foreach ($assetName in $assetReferences) {
        $assetPath = Join-Path (Join-Path $stagedFrontend "assets") $assetName
        if (-not (Test-Path -LiteralPath $assetPath -PathType Leaf)) {
            throw "Frontend index references a missing asset: $assetName"
        }
    }

    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    Move-Item -LiteralPath $target -Destination $backupTarget
    try {
        Move-Item -LiteralPath $stagedFrontend -Destination $target
        $swapped = $true
    } catch {
        Move-Item -LiteralPath $backupTarget -Destination $target
        throw
    }

    $manifest = [ordered]@{
        schema = "pride-agent-frontend-deployment/v1"
        release_id = $ReleaseId
        deployed_at = (Get-Date).ToUniversalTime().ToString("o")
        base_release = $baseRelease
        archive_sha256 = $actualSha256
        frontend_index_sha256 = (
            Get-FileHash -LiteralPath (Join-Path $target "index.html") -Algorithm SHA256
        ).Hash
        backup = $backupTarget
        services_restarted = $false
        data_root_unchanged = (Join-Path $root "data")
    }
    New-Item -ItemType Directory -Path $deploymentsRoot -Force | Out-Null
    $manifestPath = Join-Path $deploymentsRoot "${ReleaseId}.json"
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

    Write-Output ($manifest | ConvertTo-Json -Depth 5 -Compress)
    Remove-Item -LiteralPath $archivePath -Force
} catch {
    if ($swapped -and (Test-Path -LiteralPath $backupTarget)) {
        if (Test-Path -LiteralPath $target) {
            Remove-Item -LiteralPath $target -Recurse -Force
        }
        Move-Item -LiteralPath $backupTarget -Destination $target
    }
    throw
} finally {
    if (Test-Path -LiteralPath $stageRoot) {
        Remove-Item -LiteralPath $stageRoot -Recurse -Force
    }
}
