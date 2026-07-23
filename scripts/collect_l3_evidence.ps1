param(
    [Parameter(Mandatory = $true)]
    [string]$RunJson,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath,

    [string]$EnvironmentName = "unspecified",
    [string]$DeploymentId = "",
    [string]$BuildStamp = ""
)

$ErrorActionPreference = "Stop"

function Get-Field {
    param([object]$Value, [string]$Name)
    if ($null -eq $Value) { return $null }
    $property = $Value.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Get-Sha256Fingerprint {
    param([object]$Value)
    $normalized = [string]$Value
    if ([string]::IsNullOrWhiteSpace($normalized)) { return $null }
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($normalized)
        $digest = $sha.ComputeHash($bytes)
        return "sha256:" + ([System.BitConverter]::ToString($digest).Replace("-", "").ToLowerInvariant())
    }
    finally {
        $sha.Dispose()
    }
}

function Get-SafeScalar {
    param([object]$Value)
    $normalized = [string]$Value
    if ([string]::IsNullOrWhiteSpace($normalized)) { return $null }
    if ($normalized.Length -le 200 -and $normalized -match '^[A-Za-z0-9_.:@-]+$') {
        return $normalized
    }
    return Get-Sha256Fingerprint $normalized
}

function Get-SafeInteger {
    param([object]$Value)
    if ($null -eq $Value) { return $null }
    $parsed = [long]0
    if (-not [long]::TryParse([string]$Value, [ref]$parsed)) { return $null }
    if ($parsed -lt 0) { return $null }
    return $parsed
}

function Get-SafeBoolean {
    param([object]$Value)
    if ($Value -is [bool]) { return [bool]$Value }
    return $null
}

$resolvedRunJson = (Resolve-Path -LiteralPath $RunJson).Path
$raw = Get-Content -LiteralPath $resolvedRunJson -Raw -Encoding UTF8
$run = $raw | ConvertFrom-Json
$audit = Get-Field $run "latest_discovery_audit"
$completion = Get-Field $run "business_completion"
$progress = Get-Field $completion "progress"
$package = Get-Field $completion "build_ready_package"
$authority = Get-Field $run "publication_authority"
$builder = Get-Field $run "builder_dry_run_result"
$repairKeys = @(Get-Field $run "repair_execution_keys")
$limitations = @(Get-Field $audit "limitations")
$packageProjects = @(Get-Field $package "project_ids")
$packageFiles = @(Get-Field $package "files")

$evidence = [ordered]@{
    schema_version = "l3-evidence-draft/v1"
    generated_at_utc = [DateTime]::UtcNow.ToString("o")
    evidence_status = "DRAFT_NOT_SIGNED_OFF"
    environment = [ordered]@{
        name = Get-SafeScalar $EnvironmentName
        deployment_id = Get-SafeScalar $DeploymentId
        build_stamp = Get-SafeScalar $BuildStamp
        authority_mode = Get-SafeScalar (Get-Field $authority "authority_mode")
    }
    run = [ordered]@{
        run_id = Get-SafeScalar (Get-Field $run "run_id")
        workflow = Get-SafeScalar (Get-Field $run "workflow")
        runtime = Get-SafeScalar (Get-Field $run "runtime")
        status = Get-SafeScalar (Get-Field $run "status")
    }
    audit = [ordered]@{
        status = Get-SafeScalar (Get-Field $audit "status")
        limitation_count = Get-SafeInteger $limitations.Count
    }
    progress = [ordered]@{
        candidate_projects = Get-SafeInteger (Get-Field $progress "candidate_projects")
        reviewed_projects = Get-SafeInteger (Get-Field $progress "reviewed_projects")
        judgment_qualified_projects = Get-SafeInteger (Get-Field $progress "judgment_qualified_projects")
        build_ready_projects = Get-SafeInteger (Get-Field $progress "build_ready_projects")
        build_ready_files = Get-SafeInteger (Get-Field $progress "build_ready_files")
    }
    publication = [ordered]@{
        succeeded = Get-SafeBoolean (Get-Field $completion "succeeded")
        status = Get-SafeScalar (Get-Field $completion "status")
        audit_ref_fingerprint = Get-Sha256Fingerprint (Get-Field $package "audit_ref")
        manifest_ref_fingerprint = Get-Sha256Fingerprint (Get-Field $package "manifest_ref")
        evidence_store_ref_fingerprint = Get-Sha256Fingerprint (Get-Field $package "evidence_store_ref")
        package_id_fingerprint = Get-Sha256Fingerprint (Get-Field $package "package_id")
        package_digest = Get-SafeScalar (Get-Field $authority "authorized_package_digest")
        project_count = Get-SafeInteger $packageProjects.Count
        file_count = Get-SafeInteger $packageFiles.Count
        builder_entrypoint = Get-SafeScalar (Get-Field $package "builder_entrypoint")
        builder_preflight_ref_fingerprint = Get-Sha256Fingerprint (Get-Field $package "builder_preflight_ref")
        key_id = Get-SafeScalar (Get-Field $authority "key_id")
    }
    repair = [ordered]@{
        authority_id = Get-SafeScalar (Get-Field $completion "repair_authority_id")
        attempt_id = Get-SafeScalar (Get-Field $completion "repair_attempt_id")
        idempotency_key_count = Get-SafeInteger $repairKeys.Count
    }
    builder = [ordered]@{
        accepted = Get-SafeBoolean (Get-Field $builder "accepted")
        status = Get-SafeScalar (Get-Field $builder "status")
        package_digest = Get-SafeScalar (Get-Field $builder "package_digest")
        key_id = Get-SafeScalar (Get-Field $builder "key_id")
        receipt_ref_fingerprint = Get-Sha256Fingerprint (Get-Field $builder "receipt_ref")
    }
    fingerprints = [ordered]@{
        source_json = "sha256:" + (Get-FileHash -LiteralPath $resolvedRunJson -Algorithm SHA256).Hash.ToLowerInvariant()
        publication_token = Get-Sha256Fingerprint (Get-Field $authority "issuance_token")
        completion_token = Get-Sha256Fingerprint (Get-Field $completion "issuance_token")
        completion_nonce = Get-Sha256Fingerprint (Get-Field $completion "repair_attempt_nonce")
        idempotency_keys = Get-Sha256Fingerprint (($repairKeys | ForEach-Object { [string]$_ }) -join "`n")
    }
    signoff = [ordered]@{
        science = "PENDING"
        security = "PENDING"
        operations = "PENDING"
        independent_audit = "PENDING"
        product_go = "NOT_APPROVED"
    }
}

$outputDirectory = Split-Path -Parent ([System.IO.Path]::GetFullPath($OutputPath))
if ($outputDirectory -and -not (Test-Path -LiteralPath $outputDirectory)) {
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
}
$encoded = $evidence | ConvertTo-Json -Depth 10
$temporaryOutput = $OutputPath + ".tmp." + $PID + "." + [System.Guid]::NewGuid().ToString("N")
try {
    Set-Content -LiteralPath $temporaryOutput -Value $encoded -Encoding UTF8
    Move-Item -LiteralPath $temporaryOutput -Destination $OutputPath -Force
}
finally {
    Remove-Item -LiteralPath $temporaryOutput -Force -ErrorAction SilentlyContinue
}
Write-Output ("L3 evidence draft written: " + ([System.IO.Path]::GetFullPath($OutputPath)))
