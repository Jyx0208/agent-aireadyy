param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$FileList,

    [string]$OutputRoot = ".\runs",

    [switch]$NoAutoConfirm,

    [switch]$RunFull
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

if (-not (Test-Path $FileList)) {
    throw "File list not found: $FileList"
}

function ConvertTo-SafeName {
    param([string]$Name)
    $BaseName = [System.IO.Path]::GetFileNameWithoutExtension($Name)
    $SafeName = $BaseName -replace "[^A-Za-z0-9_.-]+", "_"
    if (-not $SafeName) {
        $SafeName = "pride_run"
    }
    return $SafeName
}

$Items = Get-Content $FileList |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ -and -not $_.StartsWith("#") }

foreach ($Item in $Items) {
    $OutputDir = Join-Path $OutputRoot (ConvertTo-SafeName $Item)
    Write-Host ""
    Write-Host "============================================================"
    Write-Host "Processing: $Item"
    Write-Host "Output: $OutputDir"
    Write-Host "============================================================"

    $Arguments = @{
        RawFileName = $Item
        OutputDir = $OutputDir
    }
    if ($NoAutoConfirm) {
        $Arguments.NoAutoConfirm = $true
    }
    if ($RunFull) {
        $Arguments.RunFull = $true
    }

    & (Join-Path $PSScriptRoot "run_one.ps1") @Arguments
}
