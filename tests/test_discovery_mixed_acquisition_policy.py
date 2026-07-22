from __future__ import annotations

import pytest

from agent.discovery.models import (
    DatasetRequest,
    DiscoveredFile,
    DiscoveredProject,
    DiscoveryEvidence,
    MixedAcquisitionPolicy,
)
from agent.discovery.validity import assess_file_validity, assess_project_validity


def _request(policy: MixedAcquisitionPolicy) -> DatasetRequest:
    return DatasetRequest(
        goal="general",
        acquisition_mode="dda",
        mixed_acquisition_policy=policy,
        hard_constraint_fields=["repository", "acquisition_mode"],
    )


def _mixed_evidence() -> DiscoveryEvidence:
    return DiscoveryEvidence(
        field="title",
        source="mixed_acquisition",
        text="DIA",
        weight=-20,
    )


@pytest.mark.parametrize(
    ("policy", "expected_status", "expected_needs_review", "has_mixed_reason"),
    [
        ("reject_mixed", "exclude", True, True),
        ("review_mixed", "weak_keep", False, True),
        ("allow", "valid", False, False),
    ],
)
def test_project_mixed_acquisition_policy_controls_validity(
    policy: MixedAcquisitionPolicy,
    expected_status: str,
    expected_needs_review: bool,
    has_mixed_reason: bool,
) -> None:
    project = DiscoveredProject(
        project_accession="PXD_MIXED",
        species=["Homo sapiens"],
        acquisition_mode="dda",
        instrument_families=["orbitrap"],
        fragmentation_methods=["HCD"],
        evidence=[_mixed_evidence()],
    )

    decision = assess_project_validity(project, _request(policy))

    assert decision.status == expected_status
    assert decision.needs_review is expected_needs_review
    assert ("mixed_acquisition_project" in decision.reasons) is has_mixed_reason


@pytest.mark.parametrize(
    (
        "policy",
        "has_file_level_evidence",
        "expected_status",
        "expected_needs_review",
        "needs_file_confirmation",
    ),
    [
        ("reject_mixed", False, "exclude", True, False),
        ("reject_mixed", True, "exclude", True, False),
        ("review_mixed", False, "needs_review", True, True),
        ("review_mixed", True, "valid", False, False),
        ("allow", False, "valid", False, False),
        ("allow", True, "valid", False, False),
    ],
)
def test_file_mixed_acquisition_policy_controls_delivery_review(
    policy: MixedAcquisitionPolicy,
    has_file_level_evidence: bool,
    expected_status: str,
    expected_needs_review: bool,
    needs_file_confirmation: bool,
) -> None:
    evidence = [_mixed_evidence()]
    if has_file_level_evidence:
        evidence.append(
            DiscoveryEvidence(
                field="file_name",
                source="file_name",
                text="DDA",
                weight=4,
            )
        )
    file = DiscoveredFile(
        project_accession="PXD_MIXED",
        file_name="fraction_01.raw",
        file_type=".raw",
        file_role="raw_acquisition",
        download_url="https://example.test/fraction_01.raw",
        expected_size_bytes=1_000,
        species=["Homo sapiens"],
        acquisition_mode="dda",
        instrument_families=["orbitrap"],
        fragmentation_methods=["HCD"],
        evidence=evidence,
    )

    decision = assess_file_validity(file, _request(policy))

    assert decision.status == expected_status
    assert decision.needs_review is expected_needs_review
    assert (
        "needs_file_level_acquisition_confirmation" in decision.reasons
    ) is needs_file_confirmation
    if policy == "reject_mixed":
        assert "mixed_acquisition_project" in decision.reasons


def test_review_mixed_file_level_evidence_resolves_project_uncertainty_at_the_file() -> None:
    request = _request("review_mixed")
    project = DiscoveredProject(
        project_accession="PXD_MIXED",
        species=["Homo sapiens"],
        acquisition_mode="dda",
        instrument_families=["orbitrap"],
        fragmentation_methods=["HCD"],
        evidence=[_mixed_evidence()],
    )
    project_decision = assess_project_validity(project, request)
    file = DiscoveredFile(
        project_accession="PXD_MIXED",
        file_name="fraction_DDA_01.raw",
        file_accession_or_path="fraction_DDA_01.raw",
        file_type=".raw",
        file_role="raw_acquisition",
        download_url="https://example.test/fraction_DDA_01.raw",
        expected_size_bytes=1_000,
        species=["Homo sapiens"],
        acquisition_mode="dda",
        instrument_families=["orbitrap"],
        fragmentation_methods=["HCD"],
        evidence=[
            _mixed_evidence(),
            DiscoveryEvidence(
                field="file_name",
                source="file_name",
                text="DDA",
                weight=4,
            ),
        ],
    )
    file_decision = assess_file_validity(file, request)

    assert project_decision.status == "weak_keep"
    assert project_decision.needs_review is False
    assert file_decision.status == "valid"
    assert file_decision.needs_review is False


def test_review_mixed_keeps_unknown_file_mode_in_review_even_when_acquisition_is_open() -> None:
    request = DatasetRequest(
        goal="general",
        acquisition_mode="unknown",
        mixed_acquisition_policy="review_mixed",
    )
    file = DiscoveredFile(
        project_accession="PXD_MIXED",
        file_name="fraction_01.raw",
        file_type=".raw",
        file_role="raw_acquisition",
        download_url="https://example.test/fraction_01.raw",
        expected_size_bytes=1_000,
        evidence=[_mixed_evidence()],
    )

    decision = assess_file_validity(file, request)

    assert decision.status == "needs_review"
    assert decision.needs_review is True
    assert "needs_file_level_acquisition_confirmation" in decision.reasons


@pytest.mark.parametrize("policy", ["reject_mixed", "review_mixed", "allow"])
def test_mixed_policy_does_not_override_other_hard_file_acquisition_conflicts(
    policy: MixedAcquisitionPolicy,
) -> None:
    file = DiscoveredFile(
        project_accession="PXD_MIXED",
        file_name="fraction_DIA_01.raw",
        file_type=".raw",
        file_role="raw_acquisition",
        download_url="https://example.test/fraction_DIA_01.raw",
        expected_size_bytes=1_000,
        species=["Homo sapiens"],
        acquisition_mode="dia",
        instrument_families=["orbitrap"],
        fragmentation_methods=["HCD"],
        evidence=[_mixed_evidence()],
    )

    decision = assess_file_validity(file, _request(policy))

    assert decision.status == "exclude"
    assert "acquisition_hard_constraint_conflict" in decision.reasons
