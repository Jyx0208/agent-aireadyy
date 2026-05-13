from datetime import date

from agent.models import ProjectCandidate
from agent.pride.resolver import _score_file_match, resolve_primary_project


def test_resolve_primary_project_marks_equal_exact_matches_for_review():
    candidates = [
        ProjectCandidate(
            project_accession="PXD000002",
            matched_file="sample.raw",
            match_type="exact",
            match_score=100,
            publication_date=date(2020, 1, 1),
            submission_date=date(2019, 12, 1),
            evidence=["exact file match"],
            metadata_consistency=1.0,
        ),
        ProjectCandidate(
            project_accession="PXD000001",
            matched_file="sample.raw",
            match_type="exact",
            match_score=100,
            publication_date=date(2018, 1, 1),
            submission_date=date(2017, 12, 1),
            evidence=["exact file match"],
            metadata_consistency=1.0,
        ),
    ]

    resolution = resolve_primary_project(candidates)

    assert resolution.primary_project.project_accession == "PXD000001"
    assert [c.project_accession for c in resolution.alternative_projects] == ["PXD000002"]
    assert "earliest project date wins" in resolution.resolution_reason.lower()
    assert resolution.needs_review is True
    assert "multiple equally strong" in resolution.resolution_reason.lower()


def test_resolve_primary_project_prefers_metadata_consistency_before_date():
    candidates = [
        ProjectCandidate(
            project_accession="PXD_LOW",
            matched_file="sample.raw",
            match_type="exact",
            match_score=100,
            publication_date=date(2017, 1, 1),
            submission_date=date(2016, 12, 1),
            evidence=["exact file match"],
            metadata_consistency=0.3,
        ),
        ProjectCandidate(
            project_accession="PXD_HIGH",
            matched_file="sample.raw",
            match_type="exact",
            match_score=100,
            publication_date=date(2019, 1, 1),
            submission_date=date(2018, 12, 1),
            evidence=["exact file match"],
            metadata_consistency=0.9,
        ),
    ]

    resolution = resolve_primary_project(candidates)

    assert resolution.primary_project.project_accession == "PXD_HIGH"


def test_resolve_primary_project_marks_prefix_only_match_for_review():
    candidates = [
        ProjectCandidate(
            project_accession="PXD_PREFIX",
            matched_file="prefix-sample.raw",
            match_type="prefix",
            match_score=70,
            publication_date=date(2020, 1, 1),
            submission_date=date(2019, 12, 1),
            evidence=["prefix file match"],
            metadata_consistency=1.0,
        )
    ]

    resolution = resolve_primary_project(candidates)

    assert resolution.needs_review is True
    assert "review" in resolution.resolution_reason.lower()


def test_score_file_match_treats_d_zip_as_exact_archive_wrapper():
    scored = _score_file_match(
        "LFQ_timsTOFPro_PASEF_Ecoli_01.d",
        "LFQ_timsTOFPro_PASEF_Ecoli_01.d.zip",
        "exact",
    )

    assert scored == (100, "exact file match via archive wrapper")
