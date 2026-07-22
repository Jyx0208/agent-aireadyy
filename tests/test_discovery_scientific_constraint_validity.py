from __future__ import annotations

import pytest

from agent.discovery.models import DatasetRequest
from agent.discovery.scoring import build_discovered_project, score_file, score_project


def _project_record(accession: str, description: str) -> dict[str, object]:
    return {
        "accession": accession,
        "title": description,
        "projectDescription": description,
    }


def _file_record(name: str = "sample.raw") -> dict[str, object]:
    return {
        "fileName": name,
        "fileSizeBytes": 1_024,
        "publicFileLocations": [
            {"value": f"https://example.test/{name}"},
        ],
    }


@pytest.mark.parametrize("labeling_strategy", ["SILAC", "label_free", "dimethyl"])
def test_hard_concrete_labeling_requires_observed_project_and_file_evidence(
    labeling_strategy: str,
) -> None:
    request = DatasetRequest(
        goal="general",
        labeling_strategy=labeling_strategy,
        hard_constraint_fields=["repository", "labeling_strategy"],
    )
    raw_project = _project_record("PXD_LABEL_UNKNOWN", "Generic proteomics study")

    score = score_project(raw_project, request)
    project = build_discovered_project(raw_project, request, score)
    file = score_file(_file_record(), project, request)

    assert score.labeling_strategy is None
    assert project.labeling_strategy is None
    assert project.validity_status == "needs_review"
    assert "missing_labeling_strategy_evidence" in project.validity_reasons
    assert file is not None
    assert file.labeling_strategy is None
    assert file.validity_status == "needs_review"
    assert "missing_labeling_strategy_evidence" in file.validity_reasons


@pytest.mark.parametrize(
    ("labeling_strategy", "evidence_text"),
    [
        ("SILAC", "SILAC quantitative proteomics"),
        ("label_free", "Label-free LFQ proteomics"),
        ("dimethyl", "Reductive dimethylation quantitative proteomics"),
    ],
)
def test_hard_concrete_labeling_accepts_matching_observed_evidence(
    labeling_strategy: str,
    evidence_text: str,
) -> None:
    request = DatasetRequest(
        goal="general",
        labeling_strategy=labeling_strategy,
        hard_constraint_fields=["repository", "labeling_strategy"],
    )
    raw_project = _project_record("PXD_LABEL_MATCH", evidence_text)

    project = build_discovered_project(raw_project, request, score_project(raw_project, request))
    file = score_file(_file_record(), project, request)

    assert project.labeling_strategy == labeling_strategy
    assert "missing_labeling_strategy_evidence" not in project.validity_reasons
    assert project.validity_status != "exclude"
    assert file is not None
    assert file.labeling_strategy == labeling_strategy
    assert "missing_labeling_strategy_evidence" not in file.validity_reasons
    assert file.validity_status != "exclude"


def test_future_concrete_hard_labeling_also_requires_observed_evidence() -> None:
    request = DatasetRequest(
        goal="general",
        labeling_strategy="future_plex",
        hard_constraint_fields=["repository", "labeling_strategy"],
    )
    raw_project = _project_record("PXD_LABEL_FUTURE", "Generic proteomics study")

    project = build_discovered_project(raw_project, request, score_project(raw_project, request))

    assert project.labeling_strategy is None
    assert project.validity_status == "needs_review"
    assert "missing_labeling_strategy_evidence" in project.validity_reasons


@pytest.mark.parametrize("labeling_strategy", ["TMT", "SILAC"])
def test_soft_labeling_preference_only_ranks_matching_evidence(
    labeling_strategy: str,
) -> None:
    request = DatasetRequest(
        goal="general",
        labeling_strategy=labeling_strategy,
        hard_constraint_fields=["repository"],
    )
    matching = score_project(
        _project_record("PXD_LABEL_MATCH", f"{labeling_strategy} quantitative proteomics"),
        request,
    )
    unknown = score_project(
        _project_record("PXD_LABEL_UNKNOWN", "Generic quantitative proteomics"),
        request,
    )

    assert matching.project_score > unknown.project_score
    assert matching.excluded is False
    assert unknown.excluded is False
    assert matching.needs_review is False
    assert unknown.needs_review is False
    unknown_project = build_discovered_project(
        _project_record("PXD_LABEL_UNKNOWN", "Generic quantitative proteomics"),
        request,
        unknown,
    )
    assert unknown_project.validity_status != "exclude"
    assert "missing_labeling_strategy_evidence" not in unknown_project.validity_reasons


@pytest.mark.parametrize("acquisition_mode", ["dda", "dia", "targeted"])
def test_hard_concrete_acquisition_requires_observed_project_and_file_evidence(
    acquisition_mode: str,
) -> None:
    request = DatasetRequest(
        goal="general",
        acquisition_mode=acquisition_mode,
        hard_constraint_fields=["repository", "acquisition_mode"],
    )
    raw_project = _project_record("PXD_ACQ_UNKNOWN", "Generic proteomics study")

    score = score_project(raw_project, request)
    project = build_discovered_project(raw_project, request, score)
    file = score_file(_file_record(), project, request)

    assert score.acquisition_mode is None
    assert project.acquisition_mode is None
    assert project.validity_status == "needs_review"
    assert "missing_acquisition_evidence" in project.validity_reasons
    assert file is not None
    assert file.acquisition_mode is None
    assert file.validity_status == "needs_review"
    assert "missing_acquisition_evidence" in file.validity_reasons


@pytest.mark.parametrize(
    ("acquisition_mode", "evidence_text", "file_name"),
    [
        ("dda", "DDA data-dependent acquisition proteomics", "sample_DDA.raw"),
        ("dia", "DIA SWATH data-independent acquisition proteomics", "sample_DIA.raw"),
        ("targeted", "PRM targeted proteomics", "sample_PRM.raw"),
    ],
)
def test_hard_concrete_acquisition_accepts_matching_observed_evidence(
    acquisition_mode: str,
    evidence_text: str,
    file_name: str,
) -> None:
    request = DatasetRequest(
        goal="general",
        acquisition_mode=acquisition_mode,
        hard_constraint_fields=["repository", "acquisition_mode"],
    )
    raw_project = _project_record(f"PXD_ACQ_{acquisition_mode.upper()}", evidence_text)

    score = score_project(raw_project, request)
    project = build_discovered_project(raw_project, request, score)
    file = score_file(_file_record(file_name), project, request)

    assert score.excluded is False
    assert score.acquisition_mode == acquisition_mode
    assert project.acquisition_mode == acquisition_mode
    assert "unsupported_acquisition" not in project.validity_reasons
    assert "missing_acquisition_evidence" not in project.validity_reasons
    assert project.validity_status != "exclude"
    assert file is not None
    assert file.acquisition_mode == acquisition_mode
    assert "unsupported_acquisition" not in file.validity_reasons
    assert "missing_acquisition_evidence" not in file.validity_reasons
    assert file.validity_status != "exclude"
    if acquisition_mode == "dia":
        assert all(item.source != "unsupported_acquisition" for item in score.evidence)


def test_soft_dia_preference_only_ranks_matching_evidence() -> None:
    request = DatasetRequest(
        goal="general",
        acquisition_mode="dia",
        hard_constraint_fields=["repository"],
    )
    matching = score_project(
        _project_record("PXD_DIA_MATCH", "DIA data-independent acquisition proteomics"),
        request,
    )
    unknown = score_project(
        _project_record("PXD_DIA_UNKNOWN", "Generic proteomics study"),
        request,
    )
    nonmatching = score_project(
        _project_record("PXD_DIA_NONMATCH", "DDA data-dependent acquisition proteomics"),
        request,
    )

    assert matching.project_score > unknown.project_score
    assert matching.project_score > nonmatching.project_score
    assert all(not score.excluded for score in (matching, unknown, nonmatching))
    assert all(not score.needs_review for score in (matching, unknown, nonmatching))
    nonmatching_project = build_discovered_project(
        _project_record("PXD_DIA_NONMATCH", "DDA data-dependent acquisition proteomics"),
        request,
        nonmatching,
    )
    assert nonmatching_project.validity_status != "exclude"
    assert "acquisition_hard_constraint_conflict" not in nonmatching_project.validity_reasons
    assert "missing_acquisition_evidence" not in nonmatching_project.validity_reasons
