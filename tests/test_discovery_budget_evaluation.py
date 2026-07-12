from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.evaluate_dynamic_discovery_budget import (
    EvaluationInputError,
    EvaluationRun,
    evaluate_runs,
    load_paired_runs,
    main,
)


REPLAY_FIXTURE = Path("tests/fixtures/dynamic_budget_replays.json")


def test_release_gate_requires_quality_and_cost_targets() -> None:
    report = evaluate_runs(
        baseline=[EvaluationRun(usable=20, search_requests=100)],
        dynamic=[EvaluationRun(usable=19, search_requests=75)],
    )

    assert report.usable_recall == pytest.approx(0.95)
    assert report.tool_reduction == pytest.approx(0.25)
    assert report.release_gate_passed is True


def test_release_gate_fails_false_early_stops() -> None:
    report = evaluate_runs(
        baseline=[EvaluationRun(usable=10, search_requests=20) for _ in range(20)],
        dynamic=[EvaluationRun(usable=0, search_requests=2, false_early_stop=True)]
        + [EvaluationRun(usable=10, search_requests=15) for _ in range(19)],
    )

    assert report.false_early_stop_rate == pytest.approx(0.05)
    assert report.release_gate_passed is False


def test_load_paired_runs_derives_quality_and_constraint_flags(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    dynamic_dir = tmp_path / "dynamic"
    _write_run(baseline_dir / "replay_a", valid=3, weak_keep=1, search_requests=20)
    _write_run(
        dynamic_dir / "replay_a",
        valid=2,
        weak_keep=2,
        search_requests=15,
        blockers=["hard_constraint_violation:species"],
    )

    baseline, dynamic = load_paired_runs(
        baseline_dir,
        dynamic_dir,
        replay_ids=["replay_a"],
    )

    assert baseline == [EvaluationRun(usable=4, search_requests=20)]
    assert dynamic == [
        EvaluationRun(
            usable=4,
            search_requests=15,
            quality_regression=True,
            hard_constraint_violations=1,
        )
    ]


def test_load_paired_runs_rejects_missing_replay_artifacts(tmp_path: Path) -> None:
    with pytest.raises(EvaluationInputError, match="missing replay directory"):
        load_paired_runs(tmp_path / "baseline", tmp_path / "dynamic", replay_ids=["missing"])


def test_replay_fixture_defines_all_required_scenarios() -> None:
    definitions = json.loads(REPLAY_FIXTURE.read_text(encoding="utf-8"))

    assert definitions == [
        {"id": "empty_then_success", "expected_usable_min": 1, "must_preserve_pool": True},
        {"id": "success_then_empty", "expected_usable_min": 1, "must_preserve_pool": True},
        {
            "id": "small_incremental_gains",
            "expected_usable_min": 3,
            "must_stop_before_hard_limit": True,
        },
        {
            "id": "quantity_good_quality_poor",
            "expected_review_or_valid_min": 1,
            "must_target_quality_gap": True,
        },
        {"id": "review_only", "expected_status": "completed_with_review"},
        {"id": "repeated_queries", "max_duplicate_query_rate": 0.05},
        {"id": "persistent_no_results", "expected_status": "blocked"},
        {
            "id": "cross_repository_success",
            "expected_usable_min": 1,
            "required_repository_count": 2,
        },
    ]


def test_cli_writes_report_for_complete_passing_pairs(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    dynamic_dir = tmp_path / "dynamic"
    output = tmp_path / "dynamic_budget_evaluation.json"
    replay_ids = [item["id"] for item in json.loads(REPLAY_FIXTURE.read_text(encoding="utf-8"))]
    for replay_id in replay_ids:
        _write_run(baseline_dir / replay_id, valid=20, weak_keep=0, search_requests=100)
        _write_run(dynamic_dir / replay_id, valid=19, weak_keep=0, search_requests=75)

    exit_code = main(
        [
            "--baseline-dir",
            str(baseline_dir),
            "--dynamic-dir",
            str(dynamic_dir),
            "--output",
            str(output),
        ]
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert report["evaluated_runs"] == 8
    assert report["usable_recall"] == pytest.approx(0.95)
    assert report["tool_reduction"] == pytest.approx(0.25)
    assert report["release_gate_passed"] is True


def _write_run(
    run_dir: Path,
    *,
    valid: int,
    weak_keep: int,
    search_requests: int,
    blockers: list[str] | None = None,
) -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "agents_discovery_summary.json").write_text(
        json.dumps(
            {
                "dynamic_usage": {"repository_requests": search_requests},
                "blockers": blockers or [],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "dataset_manifest.json").write_text(
        json.dumps(
            {
                "summary": {
                    "validity_status_counts": {
                        "valid": valid,
                        "weak_keep": weak_keep,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
