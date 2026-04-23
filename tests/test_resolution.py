from datetime import date

from agent.models import ProjectCandidate
from agent.pride.resolver import resolve_primary_project


def test_resolve_primary_project_prefers_earliest_when_scores_are_equal():
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
