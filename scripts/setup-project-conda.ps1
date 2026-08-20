param(
    [string]$Prefix = ".conda-env",
    [string]$PythonVersion = "3.13"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$resolvedPrefix = Join-Path $projectRoot $Prefix
$packageCache = Join-Path $projectRoot ".conda-pkgs"
$env:CONDA_PKGS_DIRS = $packageCache

conda create --prefix $resolvedPrefix "python=$PythonVersion" pip -y
if ($LASTEXITCODE -ne 0) {
    throw "Conda environment creation failed with exit code $LASTEXITCODE"
}

$python = Join-Path $resolvedPrefix "python.exe"
& $python -m pip install -e "${projectRoot}[agents-sdk,dev,dataset-construction,dataset-construction-ortools-worker,web]"
if ($LASTEXITCODE -ne 0) {
    throw "Product dependency installation failed with exit code $LASTEXITCODE"
}

& $python -m pip check
if ($LASTEXITCODE -ne 0) {
    throw "Installed dependencies are inconsistent"
}

Write-Output "Unified product environment ready: $resolvedPrefix"
Write-Output "Activate with: conda activate $resolvedPrefix"
