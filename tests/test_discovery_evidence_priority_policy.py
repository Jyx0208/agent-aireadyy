from __future__ import annotations

from agent.discovery.models import DatasetRequest, DiscoveredFile, DiscoveryEvidence
from agent.discovery.validity import assess_file_validity


def _base_file(**overrides: object) -> DiscoveredFile:
    payload: dict[str, object] = {
        "repository": "pride",
        "project_accession": "PXD_TEST",
        "file_accession_or_path": "f1",
        "file_name": "sample.raw",
        "file_type": ".raw",
        "file_role": "raw_acquisition",
        "download_url": "https://example.test/sample.raw",
        "expected_size_bytes": 1024,
        "species": ["human"],
        "species_policy": "include_only",
        "acquisition_mode": "dda",
        "immunopeptide_evidence_terms": ["immunopeptidomics"],
        "evidence_level": "project",
        "sdrf_match_status": "no_sdrf",
        "instrument_families": [],
        "fragmentation_methods": [],
        "evidence": [],
        "evidence_warnings": [],
    }
    payload.update(overrides)
    return DiscoveredFile(**payload)  # type: ignore[arg-type]


def test_project_level_immuno_with_download_is_weak_keep() -> None:
    request = DatasetRequest(
        goal="immunopeptidomics",
        species=["human"],
        species_policy="include_only",
        acquisition_mode="dda",
        hard_constraint_fields=["repository", "acquisition_mode"],
    )
    decision = assess_file_validity(_base_file(), request)
    assert decision.status == "weak_keep"
    assert decision.needs_review is False
    assert "project_level_immunopeptide_evidence" in decision.reasons


def test_sdrf_matched_with_methods_and_domain_is_valid() -> None:
    request = DatasetRequest(
        goal="immunopeptidomics",
        species=["human"],
        species_policy="include_only",
        acquisition_mode="dda",
        hard_constraint_fields=["repository", "acquisition_mode"],
    )
    file = _base_file(
        sdrf_match_status="matched",
        evidence_level="file",
        instrument_families=["orbitrap"],
        fragmentation_methods=["hcd"],
        evidence=[
            DiscoveryEvidence(
                field="sdrf:assay",
                source="immunopeptidomics",
                text="Immunopeptidomics_class_I",
                weight=9,
            )
        ],
    )
    decision = assess_file_validity(file, request)
    assert decision.status == "valid"
    assert decision.needs_review is False
    assert "sdrf_matched" in decision.reasons


def test_no_domain_and_no_method_excludes() -> None:
    request = DatasetRequest(
        goal="immunopeptidomics",
        species=["human"],
        species_policy="include_only",
        acquisition_mode="dda",
        hard_constraint_fields=["repository", "acquisition_mode"],
    )
    file = _base_file(
        immunopeptide_evidence_terms=[],
        evidence_level="unknown",
        instrument_families=[],
        fragmentation_methods=[],
    )
    decision = assess_file_validity(file, request)
    assert decision.status == "exclude"


def test_filename_immuno_hint_alone_not_valid() -> None:
    request = DatasetRequest(
        goal="immunopeptidomics",
        species=["human"],
        species_policy="include_only",
        acquisition_mode="dda",
        hard_constraint_fields=["repository", "acquisition_mode"],
    )
    file = _base_file(
        file_name="HLA_sample.raw",
        immunopeptide_evidence_terms=[],
        instrument_families=["orbitrap"],
        fragmentation_methods=["hcd"],
        evidence_level="file",
        evidence=[
            DiscoveryEvidence(
                field="file_name",
                source="immunopeptidomics",
                text="HLA",
                weight=3,
            )
        ],
    )
    decision = assess_file_validity(file, request)
    assert decision.status != "valid"
