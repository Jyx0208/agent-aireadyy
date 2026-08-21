param(
    [string]$DataRoot = (Join-Path $env:ProgramData "PRIDEAgent"),
    [string]$BackupRoot = (Join-Path $env:ProgramData "PRIDEAgent\backups"),
    [ValidateRange(1, 3650)]
    [int]$RetentionDays = 30
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Project virtual environment is missing: $python"
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$destination = Join-Path $BackupRoot $stamp
New-Item -ItemType Directory -Force -Path $destination | Out-Null
$backupCode = @"
import sqlite3, sys
source = sqlite3.connect(sys.argv[1], timeout=30)
target = sqlite3.connect(sys.argv[2], timeout=30)
with target:
    source.backup(target)
target.close()
source.close()
"@

$databaseNames = @("operations.sqlite", "queue.sqlite")
$backedUp = @()
foreach ($databaseName in $databaseNames) {
    $source = Join-Path $DataRoot "operations\$databaseName"
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        continue
    }
    $target = Join-Path $destination $databaseName
    & $python -c $backupCode $source $target
    if ($LASTEXITCODE -ne 0) {
        throw "SQLite backup failed for $source"
    }
    $backedUp += [pscustomobject]@{
        Name = $databaseName
        Bytes = (Get-Item -LiteralPath $target).Length
        Sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $target).Hash
    }
}
if ($backedUp.Count -eq 0) {
    throw "No operations database was found below $DataRoot."
}

$manifest = [ordered]@{
    schema = "pride-operations-backup/v1"
    created_at = (Get-Date).ToUniversalTime().ToString("o")
    source_root = $DataRoot
    databases = $backedUp
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $destination "manifest.json") -Encoding UTF8

$resolvedBackupRoot = (Resolve-Path -LiteralPath $BackupRoot).Path.TrimEnd("\")
$cutoff = (Get-Date).AddDays(-$RetentionDays)
Get-ChildItem -LiteralPath $BackupRoot -Directory |
    Where-Object { $_.LastWriteTime -lt $cutoff } |
    ForEach-Object {
        $resolvedTarget = $_.FullName
        if (-not $resolvedTarget.StartsWith(
            "$resolvedBackupRoot\",
            [StringComparison]::OrdinalIgnoreCase
        )) {
            throw "Refusing to remove backup outside $resolvedBackupRoot"
        }
        Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
    }

Write-Host "Operations backup complete: $destination"

