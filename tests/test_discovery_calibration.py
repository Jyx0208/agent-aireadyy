from __future__ import annotations

from pathlib import Path

from agent.discovery.calibration import (
    DiscoveryCalibrationStore,
    fit_scoring_calibration,
    score_project_with_calibration,
)


def _candidate(index: int, grade: int) -> dict:
    strong = grade >= 2
    return {
        "candidate_id": f"candidate-{index}",
        "grade": grade,
        "judgment_source": "model_expert_consensus",
        "model_expert_consensus": {
            "status": "model_expert_consensus",
            "consensus_grade": grade,
        },
        "evidence_completeness": 0.9 if strong else 0.2,
        "instrument_families": ["Orbitrap"] if strong else [],
        "fragmentation_methods": ["HCD"] if strong else [],
        "immunopeptide_scope": "hla_ligandome" if strong else None,
        "species": ["Homo sapiens"] if strong else [],
        "acquisition_mode": "dda" if strong else None,
        "validity_status": "valid" if strong else "needs_review",
        "selected_file_count": 1 if index % 2 else 100,
        "task_semantics": {
            "quantity_scope": "portfolio",
            "penalize_small_project": False,
        },
    }


def test_calibration_fits_interpretable_weights_without_portfolio_file_count() -> None:
    candidates = [_candidate(index, index % 4) for index in range(40)]

    report = fit_scoring_calibration(candidates)

    assert report["eligible"] is True
    assert report["sample_count"] == 40
    assert "selected_file_count" not in report["weights"]
    assert abs(sum(report["weights"].values()) - 1.0) < 1e-6
    assert report["metrics"]["mae"] <= report["metrics"]["equal_weight_mae"]


def test_calibration_requires_enough_resolved_labels() -> None:
    report = fit_scoring_calibration([_candidate(index, index % 4) for index in range(8)])

    assert report["eligible"] is False
    assert "minimum_30_labels_required" in report["warnings"]


def test_calibration_counts_one_verified_label_per_project() -> None:
    candidates = [_candidate(index, index % 4) for index in range(40)]
    duplicate = dict(candidates[0])
    duplicate["candidate_id"] = "duplicate-build-candidate"
    duplicate["calibration_project_id"] = "same-project"
    candidates[0]["calibration_project_id"] = "same-project"
    unverified_human = dict(candidates[1])
    unverified_human["candidate_id"] = "legacy-human-shape"
    candidates[1]["calibration_project_id"] = "second-project"
    unverified_human["calibration_project_id"] = "second-project"
    unverified_human["human_grades"] = [{"grade": 3, "source": "legacy_unverified"}]

    report = fit_scoring_calibration([*candidates, duplicate, unverified_human])

    assert report["sample_count"] == 40
    assert report["label_sources"] == {"model_expert_consensus": 40}
    assert report["preview_id"]


def test_active_calibration_round_trip_and_project_score(tmp_path: Path) -> None:
    store = DiscoveryCalibrationStore(tmp_path / "calibration.json")
    report = fit_scoring_calibration([_candidate(index, index % 4) for index in range(40)])
    active = store.activate(report)

    loaded = store.load_active()
    assert loaded is not None
    assert loaded["version_id"] == active["version_id"]
    scored = score_project_with_calibration(
        {
            "evidence_completeness": 0.9,
            "instrument_families": ["Orbitrap"],
            "fragmentation_methods": ["HCD"],
            "immunopeptide_scope": "hla_ligandome",
            "species": ["Homo sapiens"],
            "acquisition_mode": "dda",
            "validity_status": "valid",
        },
        loaded,
    )
    assert 0.0 <= scored["score"] <= 100.0
    assert scored["version_id"] == active["version_id"]
