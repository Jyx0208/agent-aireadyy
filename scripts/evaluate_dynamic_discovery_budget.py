from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from pydantic import Field

from agent.models import JsonModel


DEFAULT_REPLAY_FIXTURE = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "dynamic_budget_replays.json"
)


class EvaluationInputError(ValueError):
    pass


class EvaluationRun(JsonModel):
    usable: int = Field(ge=0)
    search_requests: int = Field(ge=0)
    false_early_stop: bool = False
    quality_regression: bool = False
    hard_constraint_violations: int = Field(default=0, ge=0)


class EvaluationReport(JsonModel):
    evaluated_runs: int = Field(ge=0)
    baseline_usable: int = Field(ge=0)
    dynamic_usable: int = Field(ge=0)
    baseline_search_requests: int = Field(ge=0)
    dynamic_search_requests: int = Field(ge=0)
    usable_recall: float
    tool_reduction: float
    false_early_stops: int = Field(ge=0)
    false_early_stop_rate: float
    quality_regressions: int = Field(ge=0)
    hard_constraint_violations: int = Field(ge=0)
    release_gate_passed: bool


def evaluate_runs(
    *,
    baseline: Sequence[EvaluationRun],
    dynamic: Sequence[EvaluationRun],
) -> EvaluationReport:
    if len(baseline) != len(dynamic):
        raise ValueError("baseline and dynamic runs must be paired")
    if not baseline:
        raise ValueError("at least one paired evaluation run is required")

    baseline_usable = sum(run.usable for run in baseline)
    dynamic_usable = sum(run.usable for run in dynamic)
    baseline_search_requests = sum(run.search_requests for run in baseline)
    dynamic_search_requests = sum(run.search_requests for run in dynamic)
    false_early_stops = sum(run.false_early_stop for run in dynamic)
    quality_regressions = sum(run.quality_regression for run in dynamic)
    hard_constraint_violations = sum(run.hard_constraint_violations for run in dynamic)
    evaluated_runs = len(dynamic)

    usable_recall = dynamic_usable / max(1, baseline_usable)
    tool_reduction = 1.0 - dynamic_search_requests / max(1, baseline_search_requests)
    false_early_stop_rate = false_early_stops / max(1, evaluated_runs)
    release_gate_passed = (
        usable_recall >= 0.95
        and tool_reduction >= 0.20
        and false_early_stop_rate < 0.05
        and quality_regressions == 0
        and hard_constraint_violations == 0
    )
    return EvaluationReport(
        evaluated_runs=evaluated_runs,
        baseline_usable=baseline_usable,
        dynamic_usable=dynamic_usable,
        baseline_search_requests=baseline_search_requests,
        dynamic_search_requests=dynamic_search_requests,
        usable_recall=usable_recall,
        tool_reduction=tool_reduction,
        false_early_stops=false_early_stops,
        false_early_stop_rate=false_early_stop_rate,
        quality_regressions=quality_regressions,
        hard_constraint_violations=hard_constraint_violations,
        release_gate_passed=release_gate_passed,
    )


def load_paired_runs(
    baseline_dir: str | Path,
    dynamic_dir: str | Path,
    *,
    replay_ids: Sequence[str],
) -> tuple[list[EvaluationRun], list[EvaluationRun]]:
    baseline_root = Path(baseline_dir)
    dynamic_root = Path(dynamic_dir)
    baseline: list[EvaluationRun] = []
    dynamic: list[EvaluationRun] = []

    for replay_id in replay_ids:
        baseline_artifact = _load_run_artifact(baseline_root / replay_id, replay_id, "baseline")
        dynamic_artifact = _load_run_artifact(dynamic_root / replay_id, replay_id, "dynamic")
        baseline_quality = baseline_artifact.valid / max(1, baseline_artifact.usable)
        dynamic_quality = dynamic_artifact.valid / max(1, dynamic_artifact.usable)
        baseline.append(
            EvaluationRun(
                usable=baseline_artifact.usable,
                search_requests=baseline_artifact.search_requests,
                hard_constraint_violations=baseline_artifact.hard_constraint_violations,
            )
        )
        dynamic.append(
            EvaluationRun(
                usable=dynamic_artifact.usable,
                search_requests=dynamic_artifact.search_requests,
                false_early_stop=(baseline_artifact.usable > 0 and dynamic_artifact.usable == 0),
                quality_regression=(dynamic_quality + 1e-9 < baseline_quality),
                hard_constraint_violations=dynamic_artifact.hard_constraint_violations,
            )
        )
    return baseline, dynamic


class _RunArtifact(JsonModel):
    usable: int = Field(ge=0)
    valid: int = Field(ge=0)
    search_requests: int = Field(ge=0)
    hard_constraint_violations: int = Field(ge=0)


def _load_run_artifact(run_dir: Path, replay_id: str, role: str) -> _RunArtifact:
    label = f"{role} replay {replay_id!r}"
    if not run_dir.is_dir():
        raise EvaluationInputError(f"{label}: missing replay directory: {run_dir}")
    summary = _read_json_object(run_dir / "agents_discovery_summary.json", label)
    manifest = _read_json_object(run_dir / "dataset_manifest.json", label)

    usage = summary.get("dynamic_usage")
    if not isinstance(usage, dict):
        raise EvaluationInputError(f"{label}: malformed summary dynamic_usage")
    search_requests = _non_negative_int(
        usage.get("repository_requests"),
        f"{label}: dynamic_usage.repository_requests",
    )
    blockers = summary.get("blockers", [])
    if not isinstance(blockers, list) or not all(isinstance(item, str) for item in blockers):
        raise EvaluationInputError(f"{label}: malformed summary blockers")

    manifest_summary = manifest.get("summary")
    if not isinstance(manifest_summary, dict):
        raise EvaluationInputError(f"{label}: malformed manifest summary")
    validity_counts = manifest_summary.get("validity_status_counts")
    if validity_counts is None:
        validity_counts = manifest_summary.get("validity_counts")
    if not isinstance(validity_counts, dict):
        raise EvaluationInputError(f"{label}: missing manifest validity counts")
    valid = _non_negative_int(validity_counts.get("valid", 0), f"{label}: valid count")
    weak_keep = _non_negative_int(
        validity_counts.get("weak_keep", 0),
        f"{label}: weak_keep count",
    )
    return _RunArtifact(
        usable=valid + weak_keep,
        valid=valid,
        search_requests=search_requests,
        hard_constraint_violations=sum(
            blocker.startswith("hard_constraint_violation:") for blocker in blockers
        ),
    )


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise EvaluationInputError(f"{label}: missing artifact: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvaluationInputError(f"{label}: cannot read {path.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise EvaluationInputError(f"{label}: {path.name} must contain a JSON object")
    return payload


def _non_negative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EvaluationInputError(f"{label} must be a non-negative integer")
    return value


def load_replay_ids(path: str | Path = DEFAULT_REPLAY_FIXTURE) -> list[str]:
    fixture_path = Path(path)
    try:
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvaluationInputError(f"cannot read replay fixture {fixture_path}: {exc}") from exc
    if not isinstance(payload, list) or not payload:
        raise EvaluationInputError("replay fixture must contain a non-empty JSON list")
    replay_ids: list[str] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
            raise EvaluationInputError(f"replay fixture entry {index} has no valid id")
        replay_ids.append(item["id"])
    if len(replay_ids) != len(set(replay_ids)):
        raise EvaluationInputError("replay fixture contains duplicate ids")
    return replay_ids


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate the dynamic Discovery budget release gate.")
    parser.add_argument("--baseline-dir", required=True, type=Path)
    parser.add_argument("--dynamic-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        replay_ids = load_replay_ids()
        baseline, dynamic = load_paired_runs(
            args.baseline_dir,
            args.dynamic_dir,
            replay_ids=replay_ids,
        )
        report = evaluate_runs(baseline=baseline, dynamic=dynamic)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except (EvaluationInputError, OSError, ValueError) as exc:
        print(f"evaluation error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if report.release_gate_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
