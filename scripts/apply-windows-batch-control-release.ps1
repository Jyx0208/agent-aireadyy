param(
    [string]$ReleaseRoot = "E:\pride-agent\releases\carbon-ops-20260730-141409",
    [string]$OverlayRoot,
    [string]$DataRoot = "E:\pride-agent\data",
    [string]$BackupRoot,
    [string[]]$MigrateBatchIds = @()
)

$ErrorActionPreference = "Stop"
if (-not $OverlayRoot) {
    throw "OverlayRoot is required."
}
if (-not $BackupRoot) {
    $BackupRoot = Join-Path (Split-Path $DataRoot) (
        "deployments\batch-control-{0}-backup" -f (Get-Date -Format "yyyyMMdd-HHmmss")
    )
}

$managedRoot = [IO.Path]::GetFullPath((Split-Path $DataRoot))
foreach ($path in @($ReleaseRoot, $OverlayRoot, $DataRoot, $BackupRoot)) {
    $full = [IO.Path]::GetFullPath($path)
    if (-not $full.StartsWith($managedRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Path escaped managed root: $full"
    }
}

Stop-Service -Name PRIDEAgentWeb -Force
Stop-Service -Name PRIDEAgentWorker -Force
$deadline = (Get-Date).AddSeconds(45)
do {
    $states = Get-Service PRIDEAgentWeb, PRIDEAgentWorker
    if (($states | Where-Object Status -ne "Stopped").Count -eq 0) {
        break
    }
    Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $deadline)
if ((Get-Service PRIDEAgentWeb, PRIDEAgentWorker | Where-Object Status -ne "Stopped").Count -gt 0) {
    throw "Services did not stop within maintenance timeout."
}

New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
foreach ($relative in @(
    "src\agent\web\app.py",
    "src\agent\operations\repository.py",
    "src\agent\msdt_converter\docker_runner.py",
    "src\agent\utils.py",
    "src\agent\web\static\benchmark-review-next",
    "scripts\install-windows-services.ps1",
    ".service\pride-agent-web.xml",
    ".service\pride-agent-worker.xml"
)) {
    $source = Join-Path $ReleaseRoot $relative
    if (-not (Test-Path -LiteralPath $source)) {
        continue
    }
    $destination = Join-Path $BackupRoot $relative
    New-Item -ItemType Directory -Path (Split-Path $destination) -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $destination -Recurse -Force
}

$runsRoot = Join-Path $DataRoot "runs"
$targetRoot = Join-Path $runsRoot "_batches"
New-Item -ItemType Directory -Path $targetRoot -Force | Out-Null
foreach ($batchId in $MigrateBatchIds) {
    if ($batchId -notmatch "^[A-Za-z0-9_-]+$") {
        throw "Unsafe batch id: $batchId"
    }
    $sourceDir = Join-Path $ReleaseRoot "runs\_batches\$batchId"
    $targetDir = Join-Path $targetRoot $batchId
    if ((Test-Path -LiteralPath $sourceDir) -and (Test-Path -LiteralPath $targetDir)) {
        throw "Both source and target batch directories exist: $batchId"
    }
    if (Test-Path -LiteralPath $sourceDir) {
        Move-Item -LiteralPath $sourceDir -Destination $targetDir
    }
    if (-not (Test-Path -LiteralPath $targetDir)) {
        throw "Batch directory missing after migration: $batchId"
    }

    $manifestPath = Join-Path $targetDir "batch_manifest.json"
    $recoveryPath = Join-Path $OverlayRoot "_recovery\$batchId.json"
    $python = Join-Path $ReleaseRoot ".venv\Scripts\python.exe"
    $migrationHelper = Join-Path $OverlayRoot "scripts\migrate-batch-manifest.py"
    & $python $migrationHelper `
        --batch-id $batchId `
        --manifest $manifestPath `
        --recovery $recoveryPath `
        --target-dir $targetDir
    if ($LASTEXITCODE -ne 0) {
        throw "Batch manifest migration failed: $batchId"
    }
}

Copy-Item -Path (Join-Path $OverlayRoot "src\*") -Destination (Join-Path $ReleaseRoot "src") -Recurse -Force
Copy-Item -Path (Join-Path $OverlayRoot "scripts\*") -Destination (Join-Path $ReleaseRoot "scripts") -Recurse -Force

foreach ($xmlPath in @(
    (Join-Path $ReleaseRoot ".service\pride-agent-web.xml"),
    (Join-Path $ReleaseRoot ".service\pride-agent-worker.xml")
)) {
    [xml]$xml = Get-Content -Raw -LiteralPath $xmlPath
    foreach ($entry in @(
        @{ name = "AGENT_WEB_FULL_WORKFLOW_ENABLED"; value = "true" },
        @{ name = "DOCKER_HOST"; value = "npipe:////./pipe/dockerDesktopLinuxEngine" }
    )) {
        $node = @($xml.service.env | Where-Object name -eq $entry.name) | Select-Object -First 1
        if ($null -eq $node) {
            $node = $xml.CreateElement("env")
            [void]$node.SetAttribute("name", $entry.name)
            [void]$xml.service.AppendChild($node)
        }
        [void]$node.SetAttribute("value", $entry.value)
    }
    $xml.Save($xmlPath)
}

Start-Service -Name PRIDEAgentWorker
Start-Service -Name PRIDEAgentWeb
Start-Sleep -Seconds 5
[pscustomobject]@{
    services = @(
        Get-Service PRIDEAgentWeb, PRIDEAgentWorker |
            ForEach-Object { @{ name = $_.Name; status = [string]$_.Status } }
    )
    migrated_batches = @(
        Get-ChildItem -LiteralPath $targetRoot -Directory |
            Select-Object -ExpandProperty Name
    )
    backup = $BackupRoot
} | ConvertTo-Json -Depth 5 -Compress
