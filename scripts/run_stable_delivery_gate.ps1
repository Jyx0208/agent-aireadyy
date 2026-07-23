# Stable-delivery gate for Discovery build-ready path.
# Exit 0 only if objective checks pass. No product GO claim.
$ErrorActionPreference = "Stop"
$wt = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $wt "pyproject.toml"))) {
  $wt = "E:\ai-agent-already\github-publish\agent-aireadyy\.claude\worktrees\benchmark-review-planning"
}
Set-Location $wt

$py = Join-Path $wt ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "py" }

Write-Host "GATE cwd=$wt"
Write-Host "GATE python=$py"

$tests = @(
  "tests/test_discovery_evidence_priority_policy.py",
  "tests/test_discovery_sdrf_assay_evidence.py",
  "tests/test_discovery_build_ready_materialization.py",
  "tests/test_discovery_wiring_publication_to_record.py",
  "tests/test_discovery_wiring_repair_authority.py",
  "tests/test_discovery_publication_contracts.py"
)

# Optional extra files if present
foreach ($extra in @(
  "tests/test_discovery_project_method_inherit.py",
  "tests/test_discovery_mixed_acquisition_policy.py",
  "tests/test_discovery.py"
)) {
  if (Test-Path (Join-Path $wt $extra)) { $tests += $extra }
}

$failed = $false
& $py -m pytest @tests -q --tb=line -m "not future_project"
if ($LASTEXITCODE -ne 0) {
  Write-Host "GATE_FAIL pytest exit=$LASTEXITCODE"
  $failed = $true
} else {
  Write-Host "GATE_OK pytest"
}

# Contract smoke: weak_keep must not be materializable as valid-only package
& $py -c @"
from agent.discovery.publication import materialize_build_ready_package
from agent.discovery.models import DatasetManifest, DatasetRequest
# minimal empty should block
r = materialize_build_ready_package({'run_id': 'gate', 'audit': {}, 'manifest': None})
assert r.package is None and r.blockers, 'materialize must fail-closed without package'
print('GATE_OK materialize_fail_closed', len(r.blockers))
"@
if ($LASTEXITCODE -ne 0) {
  Write-Host "GATE_FAIL materialize smoke"
  $failed = $true
}

# Validity policy smoke
& $py -c @"
from agent.discovery.models import DatasetRequest, DiscoveredFile, DiscoveryEvidence
from agent.discovery.validity import assess_file_validity

req = DatasetRequest(
    goal='immunopeptidomics', species=['human'], species_policy='include_only',
    acquisition_mode='dda', hard_constraint_fields=['repository','acquisition_mode'],
)
base = dict(
    repository='pride', project_accession='PXD_G', file_accession_or_path='f1',
    file_name='sample.raw', file_type='.raw', file_role='raw_acquisition',
    download_url='https://example.test/a.raw', expected_size_bytes=10,
    species=['human'], species_policy='include_only', acquisition_mode='dda',
    immunopeptide_evidence_terms=['immunopeptidomics'], evidence_level='project',
    sdrf_match_status='no_sdrf', instrument_families=[], fragmentation_methods=[],
    evidence=[], evidence_warnings=[],
)
d1 = assess_file_validity(DiscoveredFile(**base), req)
assert d1.status == 'weak_keep', d1
base2 = dict(base)
base2.update(
    sdrf_match_status='matched', evidence_level='file',
    instrument_families=['orbitrap'], fragmentation_methods=['hcd'],
    evidence=[DiscoveryEvidence(field='sdrf:assay', source='immunopeptidomics', text='HLA', weight=9)],
)
d2 = assess_file_validity(DiscoveredFile(**base2), req)
assert d2.status == 'valid', d2
print('GATE_OK validity_policy', d1.status, d2.status)
"@
if ($LASTEXITCODE -ne 0) {
  Write-Host "GATE_FAIL validity smoke"
  $failed = $true
}

if ($failed) {
  Write-Host "STABLE_DELIVERY_GATE=FAIL"
  exit 1
}
Write-Host "STABLE_DELIVERY_GATE=PASS"
exit 0
