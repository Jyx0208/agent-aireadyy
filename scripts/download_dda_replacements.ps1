[CmdletBinding()]
param(
    [string]$DataRoot = "E:\PRIDE_benchmark_20260817",
    [string]$ManifestPath = "",
    [string]$CurlExecutable = "E:\anaconda\Library\bin\curl.exe",
    [string]$ProxyUrl = "http://127.0.0.1:7897",
    [int]$PollSeconds = 10
)

$ErrorActionPreference = "Stop"
if (-not $ManifestPath) { $ManifestPath = Join-Path $DataRoot "dataset_manifest.csv" }
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { throw "Manifest not found: $ManifestPath" }
if (-not (Test-Path -LiteralPath $CurlExecutable -PathType Leaf)) { throw "curl not found: $CurlExecutable" }

$rows = @(Import-Csv -LiteralPath $ManifestPath | Where-Object { $_.project_accession -eq "PXD079900" })
if ($rows.Count -ne 2) { throw "Expected exactly two PXD079900 replacement rows, found $($rows.Count)" }
$allRows = @(Import-Csv -LiteralPath $ManifestPath)
if ($allRows.Count -ne 16) { throw "Final benchmark manifest must contain 16 files, found $($allRows.Count)" }
if (@($allRows | Where-Object { $_.acquisition_mode -ieq "dia" }).Count -ne 0) { throw "Final benchmark manifest still contains DIA rows; refuse to download/process it" }
if (@($allRows.project_accession | Sort-Object -Unique).Count -ne 8) { throw "Final benchmark manifest must contain 8 projects" }

$jobs = New-Object System.Collections.Generic.List[object]
$completed = New-Object System.Collections.Generic.List[object]
foreach ($row in $rows) {
    $projectDir = Join-Path $DataRoot ([string]$row.project_accession)
    New-Item -ItemType Directory -Force -Path $projectDir | Out-Null
    $fileName = [System.IO.Path]::GetFileName([string]$row.file_name)
    $target = Join-Path $projectDir $fileName
    $part = "$target.part"
    $expected = [int64]$row.expected_size_bytes
    if ((Test-Path -LiteralPath $target -PathType Leaf) -and ((Get-Item -LiteralPath $target).Length -eq $expected)) {
        Write-Host "already complete: $fileName ($expected bytes)"
        Remove-Item -LiteralPath "$part.stdout.log", "$part.stderr.log" -Force -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath $part -PathType Leaf) {
            if ((Get-Item -LiteralPath $part).Length -eq $expected) { Remove-Item -LiteralPath $part -Force }
        }
        $completed.Add([ordered]@{ project_accession = $row.project_accession; file_name = $fileName; expected_size_bytes = $expected; actual_size_bytes = $expected; status = "already_complete"; downloaded_at = (Get-Date).ToString("o") })
        continue
    }
    $stdout = "$part.stdout.log"
    $stderr = "$part.stderr.log"
    $args = @(
        "--fail", "--location", "--silent", "--show-error",
        "--continue-at", "-", "--retry", "10", "--retry-all-errors",
        "--retry-delay", "5", "--connect-timeout", "60",
        "--speed-time", "60", "--speed-limit", "1024",
        "--output", $part, [string]$row.download_url
    )
    if ($ProxyUrl) { $args = @("--proxy", $ProxyUrl) + $args }
    $process = Start-Process -FilePath $CurlExecutable -ArgumentList $args -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru -WindowStyle Hidden
    $jobs.Add([pscustomobject]@{ row = $row; process = $process; target = $target; part = $part; stderr = $stderr; expected = $expected; file = $fileName; started = Get-Date; lastBytes = 0L; lastAt = Get-Date })
    Write-Host "started: $fileName (expected $([math]::Round($expected / 1GB, 2)) GiB)"
}

while (@($jobs | Where-Object { -not $_.process.HasExited }).Count -gt 0) {
    Start-Sleep -Seconds $PollSeconds
    foreach ($job in $jobs) {
        $job.process.Refresh()
        $bytes = if (Test-Path -LiteralPath $job.part -PathType Leaf) { (Get-Item -LiteralPath $job.part).Length } else { 0L }
        $now = Get-Date
        $elapsed = [math]::Max(1, ($now - $job.lastAt).TotalSeconds)
        $rate = ($bytes - $job.lastBytes) / $elapsed
        $pct = if ($job.expected -gt 0) { 100.0 * $bytes / $job.expected } else { 0 }
        $state = if ($job.process.HasExited) { "exited($($job.process.ExitCode))" } else { "downloading" }
        Write-Host ("[{0}] {1}: {2:N2} / {3:N2} GiB ({4:N1}%), {5:N2} MiB/s, {6}" -f (Get-Date -Format "HH:mm:ss"), $job.file, ($bytes / 1GB), ($job.expected / 1GB), $pct, ($rate / 1MB), $state)
        $job.lastBytes = $bytes
        $job.lastAt = $now
    }
}

$summary = New-Object System.Collections.Generic.List[object]
foreach ($item in $completed) { $summary.Add($item) }
foreach ($job in $jobs) {
    $job.process.WaitForExit()
    $job.process.Refresh()
    $exitCode = [int]$job.process.ExitCode
    if ($exitCode -ne 0) { throw "Download failed for $($job.file), exit code $exitCode. See $($job.stderr)" }
    if (-not (Test-Path -LiteralPath $job.part -PathType Leaf)) { throw "Download produced no partial file: $($job.part)" }
    $actual = (Get-Item -LiteralPath $job.part).Length
    if ($actual -ne $job.expected) { throw "Size mismatch for $($job.file): actual=$actual expected=$($job.expected)" }
    Move-Item -LiteralPath $job.part -Destination $job.target -Force
    Remove-Item -LiteralPath "$($job.part).stdout.log", "$($job.part).stderr.log" -Force -ErrorAction SilentlyContinue
    $summary.Add([ordered]@{ project_accession = $job.row.project_accession; file_name = $job.file; expected_size_bytes = $job.expected; actual_size_bytes = $actual; status = "complete"; downloaded_at = (Get-Date).ToString("o") })
}

$summaryPath = Join-Path $DataRoot "dda_replacement_download_summary_20260819.json"
$summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $summaryPath -Encoding UTF8
Write-Host "complete: $summaryPath"
