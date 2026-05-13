param(
    [string]$Version = "0.1.2",

    [string]$PackageName = "pride-ai-ready-agent"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$DistDir = Join-Path $RepoRoot "dist"
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

$ArchiveName = "$PackageName-v$Version.zip"
$ArchivePath = Join-Path $DistDir $ArchiveName

if (Test-Path $ArchivePath) {
    Remove-Item $ArchivePath
}

$TempDir = Join-Path $DistDir "$PackageName-v$Version"
if (Test-Path $TempDir) {
    Remove-Item -Recurse -Force $TempDir
}
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null

$RootFiles = @(
    ".dockerignore",
    ".env.example",
    "Dockerfile",
    "docker-compose.yml",
    "files.example.txt",
    "pyproject.toml",
    "README.md",
    "README_RELEASE.md",
    "run.bat",
    "start.ps1",
    "start-web.bat",
    "start-web.ps1"
)
$RootDirs = @("src", "profiles", "scripts", "docs")
$ExcludedDirNames = @(
    ".agent_cache",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".test_tmp",
    ".venv",
    "__pycache__",
    "benchmark_runs",
    "dist",
    "external",
    "pride_planning_smoke_runs",
    "pride_planning_smoke_verify",
    "pride_resolution_smoke_verify",
    "pride_resolution_smoke_verify_40",
    "pride_smoke_runs",
    "runs",
    "tests"
)
$ExcludedExtensions = @(".parquet", ".raw", ".mzML", ".mzXML", ".wiff", ".scan", ".d", ".zip", ".xlsx")

function Copy-ReleaseFile {
    param([string]$RelativePath)

    $Source = Join-Path $RepoRoot $RelativePath
    if (-not (Test-Path $Source -PathType Leaf)) {
        return
    }

    $Target = Join-Path $TempDir $RelativePath
    $TargetDir = Split-Path -Parent $Target
    if ($TargetDir) {
        New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
    }
    Copy-Item $Source $Target
}

function Get-ReleaseRelativePath {
    param([string]$FullPath)

    $Root = (Resolve-Path $RepoRoot).Path.TrimEnd('\', '/')
    $Resolved = (Resolve-Path $FullPath).Path
    if (-not $Resolved.StartsWith($Root, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path is outside repository root: $FullPath"
    }
    return $Resolved.Substring($Root.Length).TrimStart('\', '/')
}

function Test-ReleasePathAllowed {
    param([System.IO.FileInfo]$File)

    $Relative = Get-ReleaseRelativePath $File.FullName
    $Parts = $Relative -split '[\\/]'
    foreach ($Part in $Parts) {
        if ($ExcludedDirNames -contains $Part) {
            return $false
        }
    }
    if ($File.Name -like "*.pyc") {
        return $false
    }
    if ($ExcludedExtensions -contains $File.Extension) {
        return $false
    }
    return $true
}

foreach ($File in $RootFiles) {
    Copy-ReleaseFile $File
}

foreach ($Dir in $RootDirs) {
    $SourceDir = Join-Path $RepoRoot $Dir
    if (-not (Test-Path $SourceDir -PathType Container)) {
        continue
    }
    Get-ChildItem $SourceDir -Recurse -File | ForEach-Object {
        if (Test-ReleasePathAllowed $_) {
            $Relative = Get-ReleaseRelativePath $_.FullName
            Copy-ReleaseFile $Relative
        }
    }
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$Zip = [System.IO.Compression.ZipFile]::Open($ArchivePath, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    Get-ChildItem $TempDir -Recurse -File | ForEach-Object {
        $EntryName = $_.FullName.Substring($TempDir.Length).TrimStart('\', '/') -replace '\\', '/'
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($Zip, $_.FullName, $EntryName) | Out-Null
    }
}
finally {
    $Zip.Dispose()
}
Remove-Item -Recurse -Force $TempDir

Write-Host "Release package created:"
Write-Host "  $ArchivePath"
Write-Host "Package size:"
Write-Host ("  {0:N2} MB" -f ((Get-Item $ArchivePath).Length / 1MB))
Write-Host ""
Write-Host "Recommended GitHub Release tag:"
Write-Host "  v$Version"
