param(
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$serviceNames = @("PRIDEAgentWeb", "PRIDEAgentWorker")
$serviceRows = foreach ($name in $serviceNames) {
    $service = Get-Service -Name $name -ErrorAction SilentlyContinue
    [pscustomobject]@{
        Name = $name
        Status = if ($service) { [string]$service.Status } else { "NotInstalled" }
    }
}
$serviceRows | Format-Table -AutoSize

$healthUrl = "http://${HostName}:$Port/api/ops/health"
$health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 15
if (-not $health.ok -or $health.database -ne "ready") {
    throw "Operations health check failed: $($health | ConvertTo-Json -Compress)"
}
Write-Host ($health | ConvertTo-Json -Depth 5)

