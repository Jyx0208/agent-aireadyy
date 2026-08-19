[CmdletBinding()]
param(
    [string]$NasRoot = "N:\members\jiangyuxuan\PRIDE_benchmark_20260817",
    [string]$WorkRoot = "E:\pride_processing",
    [string]$PwizExecutable = "E:\pride_processing\tools\pwiz\msconvert.exe",
    [string]$ProjectFilter = "",
    [string]$WorkerId = "main",
    [switch]$Resume
)

$ErrorActionPreference = "Stop"

$ManifestPath = Join-Path $NasRoot "dataset_manifest.csv"
$BatchRoot = Join-Path $NasRoot "batch_processing_20260818"
$PreparedRoot = Join-Path $BatchRoot "prepared"
$LogRoot = Join-Path $BatchRoot "logs"
$logFileName = if ($WorkerId -eq "main") { "preparation.log" } else { "preparation_$WorkerId.log" }
$LogPath = Join-Path $LogRoot $logFileName
$WorkItemsRoot = Join-Path $WorkRoot "work"
$resultStem = if ($WorkerId -eq "main") { "preparation_manifest" } else { "preparation_manifest_$WorkerId" }
$ResultJson = Join-Path $BatchRoot ("$resultStem.json")
$ResultCsv = Join-Path $BatchRoot ("$resultStem.csv")

foreach ($dir in @($PreparedRoot, $LogRoot, $WorkItemsRoot)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
}

if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
    throw "Manifest not found: $ManifestPath. Verify that N: is available in this PowerShell session."
}
if (-not (Test-Path -LiteralPath $PwizExecutable -PathType Leaf)) {
    throw "ProteoWizard msconvert not found: $PwizExecutable"
}

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    $line | Tee-Object -FilePath $LogPath -Append
}

function Get-ExpectedSize {
    param($Row)
    $value = 0L
    if ([long]::TryParse([string]$Row.expected_size_bytes, [ref]$value)) { return $value }
    return 0L
}

function Invoke-RobocopyFile {
    param(
        [string]$SourceDir,
        [string]$FileName,
        [string]$DestinationDir
    )
    New-Item -ItemType Directory -Force -Path $DestinationDir | Out-Null
    & robocopy $SourceDir $DestinationDir $FileName /J /R:2 /W:5 /NFL /NDL /NP /TEE
    $code = $LASTEXITCODE
    if ($code -gt 7) { throw "robocopy failed ($code): $SourceDir\$FileName -> $DestinationDir" }
}

function Invoke-Msconvert {
    param(
        [string]$SourcePath,
        [string]$OutputDir,
        [string]$OutputName
    )
    New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
    Write-Log "msconvert: $([System.IO.Path]::GetFileName($SourcePath)) -> $OutputName"
    & $PwizExecutable $SourcePath --mzML --filter "peakPicking true 1-" --outfile $OutputName -o $OutputDir 2>&1 | Tee-Object -FilePath $LogPath -Append
    if ($LASTEXITCODE -ne 0) { throw "msconvert failed ($LASTEXITCODE): $SourcePath" }
    $result = Join-Path $OutputDir $OutputName
    if (-not (Test-Path -LiteralPath $result -PathType Leaf)) { throw "msconvert did not create $result" }
    if ((Get-Item -LiteralPath $result).Length -le 0) { throw "msconvert created an empty file: $result" }
    return $result
}

$allRows = @(Import-Csv -LiteralPath $ManifestPath)
if ($allRows.Count -eq 0) { throw "Manifest is empty: $ManifestPath" }
if (@($allRows | Where-Object { $_.acquisition_mode -ieq "dia" }).Count -gt 0) {
    throw "DIA rows are not supported by this preparation lane; update the manifest before starting a batch"
}
$rows = $allRows
if ($ProjectFilter.Trim()) {
    $allowedProjects = @($ProjectFilter.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $rows = @($rows | Where-Object { $allowedProjects -contains [string]$_.project_accession })
    if ($rows.Count -eq 0) { throw "ProjectFilter selected no rows: $ProjectFilter" }
}

$results = New-Object System.Collections.Generic.List[object]
$index = 0
foreach ($row in $rows) {
    $index++
    $project = [string]$row.project_accession
    $fileName = [System.IO.Path]::GetFileName([string]$row.file_name)
    $sourcePath = Join-Path (Join-Path $NasRoot $project) $fileName
    $expected = Get-ExpectedSize $row
    $extension = [System.IO.Path]::GetExtension($fileName).ToLowerInvariant()
    $baseName = [System.IO.Path]::GetFileNameWithoutExtension($fileName)
    $itemId = if ($row.ms_run_id) { [string]$row.ms_run_id } else { "$project`:$fileName" }
    $itemWork = Join-Path $WorkItemsRoot (($project + "__" + $baseName) -replace '[^A-Za-z0-9_.-]', '_')
    $itemOut = Join-Path (Join-Path $PreparedRoot $project) $baseName
    $lane = if ([string]$row.acquisition_mode -ieq "dia") { "dia_deferred" } elseif ([string]$row.file_role -match "target|prm") { "targeted_review" } else { "dda_preparation" }
    $status = "blocked"
    $outputPath = ""
    $message = ""
    $started = Get-Date

    try {
        Write-Log "[$index/$($rows.Count)] $project/$fileName"
        if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
            throw "Source file is not visible in this N: session: $sourcePath"
        }
        $sourceInfo = Get-Item -LiteralPath $sourcePath
        if ($expected -gt 0 -and $sourceInfo.Length -ne $expected) {
            throw "Source size mismatch: actual $($sourceInfo.Length), expected $expected"
        }
        $targetName = "$baseName.mzML"
        $targetPath = Join-Path $itemOut $targetName
        if ($Resume -and (Test-Path -LiteralPath $targetPath -PathType Leaf) -and (Get-Item -LiteralPath $targetPath).Length -gt 0) {
            $status = "resumed_existing"
            $outputPath = $targetPath
            $message = "reused existing mzML"
        } else {
            if (Test-Path -LiteralPath $itemWork) { Remove-Item -LiteralPath $itemWork -Recurse -Force }
            New-Item -ItemType Directory -Force -Path $itemWork | Out-Null
            $localSource = Join-Path $itemWork $fileName
            switch ($extension) {
                ".raw" {
                    Invoke-RobocopyFile (Split-Path $sourcePath -Parent) $fileName $itemWork
                    $converted = Invoke-Msconvert $localSource $itemWork $targetName
                }
                ".mzxml" {
                    Invoke-RobocopyFile (Split-Path $sourcePath -Parent) $fileName $itemWork
                    $converted = Invoke-Msconvert $localSource $itemWork $targetName
                }
                ".mzml" {
                    Invoke-RobocopyFile (Split-Path $sourcePath -Parent) $fileName $itemWork
                    $converted = $localSource
                    $targetName = $fileName
                }
                ".zip" {
                    Invoke-RobocopyFile (Split-Path $sourcePath -Parent) $fileName $itemWork
                    $extractDir = Join-Path $itemWork "extract"
                    Expand-Archive -LiteralPath $localSource -DestinationPath $extractDir -Force
                    $dPath = Get-ChildItem -LiteralPath $extractDir -Recurse -Directory | Where-Object { $_.Name -like "*.d" } | Select-Object -First 1
                    if (-not $dPath) { throw "No Bruker .d directory found inside archive: $fileName" }
                    $converted = Invoke-Msconvert $dPath.FullName $itemWork $targetName
                }
                default { throw "Unsupported input extension: $extension" }
            }
            New-Item -ItemType Directory -Force -Path $itemOut | Out-Null
            Copy-Item -LiteralPath $converted -Destination $targetPath -Force
            if ((Get-Item -LiteralPath $targetPath).Length -le 0) { throw "NAS output is empty: $targetPath" }
            $outputPath = $targetPath
            $status = "prepared"
            $message = "prepared and copied to NAS"
        }
    } catch {
        $message = $_.Exception.Message
        Write-Log "BLOCKED $project/$fileName : $message"
    } finally {
        if (Test-Path -LiteralPath $itemWork) { Remove-Item -LiteralPath $itemWork -Recurse -Force -ErrorAction SilentlyContinue }
    }

    $results.Add([ordered]@{
        item_id = $itemId
        project_accession = $project
        source_file = $fileName
        source_path = $sourcePath
        expected_size_bytes = $expected
        acquisition_mode = [string]$row.acquisition_mode
        species = [string]$row.canonical_species
        instrument_families = [string]$row.instrument_families
        fragmentation_methods = [string]$row.fragmentation_methods
        processing_lane = $lane
        status = $status
        output_path = $outputPath
        message = $message
        started_at = $started.ToString("o")
        finished_at = (Get-Date).ToString("o")
    })
    Write-Log "$status $project/$fileName"
}

$results | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ResultJson -Encoding UTF8
$results | Export-Csv -LiteralPath $ResultCsv -NoTypeInformation -Encoding UTF8
$counts = $results | Group-Object status | ForEach-Object { "$($_.Name)=$($_.Count)" }
Write-Log ("DONE: " + ($counts -join ", ") + "; result: " + $ResultJson)
