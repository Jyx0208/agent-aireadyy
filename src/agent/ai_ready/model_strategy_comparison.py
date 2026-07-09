from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from pydantic import Field

from agent.models import JsonModel
from agent.utils import write_json


DEFAULT_EVAL_SLICES = ["heldout_project", "heldout_instrument", "heldout_organism"]


class ModelStrategyComparisonResult(JsonModel):
    status: str
    case_file: str
    output_dir: str
    primary_metric: str
    agent_strategy: str = "agent_data_value"
    best_baseline_strategy: str = ""
    agent_minus_best_baseline: float | None = None
    interpretation: str = "not_evaluated"
    warnings: list[str] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict)


def compare_dataset_model_strategies(
    *,
    case_file: str | Path,
    output_dir: str | Path,
    primary_metric: str | None = None,
    higher_is_better: bool | None = None,
    agent_strategy: str = "agent_data_value",
) -> ModelStrategyComparisonResult:
    case_file = Path(case_file)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    case = _read_json(case_file)
    strategies = case.get("strategies") if isinstance(case.get("strategies"), list) else []
    metric = primary_metric or str(case.get("primary_metric") or "score")
    if higher_is_better is None:
        higher_is_better = bool(case.get("higher_is_better", True))
    eval_slices = case.get("eval_slices") if isinstance(case.get("eval_slices"), list) else DEFAULT_EVAL_SLICES
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    for item in strategies:
        if not isinstance(item, dict):
            continue
        row, row_warnings = _strategy_row(
            item,
            case_dir=case_file.parent,
            primary_metric=metric,
            eval_slices=[str(value) for value in eval_slices],
            higher_is_better=higher_is_better,
        )
        rows.append(row)
        warnings.extend(row_warnings)
    agent_row = next((row for row in rows if row["strategy"] == agent_strategy), None)
    baselines = [row for row in rows if row["strategy"] != agent_strategy]
    best_baseline = _best_row(baselines, higher_is_better=higher_is_better)
    delta = None
    if agent_row and best_baseline and agent_row.get("primary_eval_score") is not None and best_baseline.get("primary_eval_score") is not None:
        delta = round(float(agent_row["primary_eval_score"]) - float(best_baseline["primary_eval_score"]), 6)
        if not higher_is_better:
            delta = round(-delta, 6)
    summary = {
        "status": "ready" if rows else "blocked",
        "case_file": str(case_file),
        "goal": case.get("goal") or "",
        "task_type": case.get("task_type") or "",
        "primary_metric": metric,
        "higher_is_better": higher_is_better,
        "eval_slices": eval_slices,
        "agent_strategy": agent_strategy,
        "best_baseline_strategy": best_baseline.get("strategy") if best_baseline else "",
        "agent_primary_eval_score": agent_row.get("primary_eval_score") if agent_row else None,
        "best_baseline_primary_eval_score": best_baseline.get("primary_eval_score") if best_baseline else None,
        "agent_minus_best_baseline": delta,
        "interpretation": _interpretation(agent_row, best_baseline, delta),
        "strategy_rows": rows,
        "warnings": sorted(set(warnings)),
        "notes": [
            "This compares existing model metrics for dataset-selection strategies.",
            "It does not trigger training; use external adapters or run-dataset-model-loop to create metrics first.",
        ],
    }
    files = {
        "model_strategy_comparison_json": str(output_dir / "model_strategy_comparison.json"),
        "model_strategy_comparison_csv": str(output_dir / "model_strategy_comparison.csv"),
        "model_strategy_comparison_md": str(output_dir / "model_strategy_comparison.md"),
    }
    write_json(files["model_strategy_comparison_json"], summary)
    _write_csv(Path(files["model_strategy_comparison_csv"]), rows)
    Path(files["model_strategy_comparison_md"]).write_text(_markdown_comparison(summary), encoding="utf-8")
    return ModelStrategyComparisonResult(
        status=str(summary["status"]),
        case_file=str(case_file),
        output_dir=str(output_dir),
        primary_metric=metric,
        agent_strategy=agent_strategy,
        best_baseline_strategy=str(summary["best_baseline_strategy"]),
        agent_minus_best_baseline=delta,
        interpretation=str(summary["interpretation"]),
        warnings=summary["warnings"],
        files=files,
    )


def _strategy_row(
    item: dict[str, Any],
    *,
    case_dir: Path,
    primary_metric: str,
    eval_slices: list[str],
    higher_is_better: bool,
) -> tuple[dict[str, Any], list[str]]:
    strategy = str(item.get("strategy") or item.get("name") or "unknown")
    metrics, source, warnings = _load_metrics(item, case_dir)
    slice_scores = {
        slice_name: _metric_for_slice(metrics, slice_name, primary_metric)
        for slice_name in eval_slices
    }
    available_scores = [float(value) for value in slice_scores.values() if value is not None]
    primary_eval_score = round(sum(available_scores) / len(available_scores), 6) if available_scores else _metric_for_slice(metrics, "metrics", primary_metric)
    train_score = _metric_for_slice(metrics, "train", primary_metric)
    apparent_gap = None
    if train_score is not None and primary_eval_score is not None:
        apparent_gap = round(float(train_score) - float(primary_eval_score), 6)
        if not higher_is_better:
            apparent_gap = round(-apparent_gap, 6)
    return (
        {
            "strategy": strategy,
            "source": source,
            "primary_metric": primary_metric,
            "primary_eval_score": primary_eval_score,
            "train_score": train_score,
            "apparent_generalization_gap": apparent_gap,
            "slice_scores": slice_scores,
            "total_rows": _metric_for_slice(metrics, "metrics", "total_rows"),
            "failure_mode_count": _metric_for_slice(metrics, "metrics", "failure_mode_count"),
            "selection_report": str(item.get("selection_report") or ""),
            "recipe_dir": str(item.get("recipe_dir") or ""),
            "model_loop_dir": str(item.get("model_loop_dir") or ""),
            "rank_key": _rank_key(primary_eval_score, higher_is_better),
        },
        warnings,
    )


def _load_metrics(item: dict[str, Any], case_dir: Path) -> tuple[dict[str, Any], str, list[str]]:
    warnings: list[str] = []
    if isinstance(item.get("metrics"), dict):
        return item["metrics"], "inline_metrics", warnings
    for key, default_name in [
        ("metrics_file", ""),
        ("model_eval_summary", "model_eval_summary.json"),
        ("model_loop_dir", "model_eval_summary.json"),
    ]:
        value = item.get(key)
        if not value:
            continue
        path = _resolve_path(value, case_dir)
        if key == "model_loop_dir":
            path = path / default_name
        payload = _read_json(path)
        if payload:
            metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else payload
            if isinstance(metrics, dict):
                merged = dict(metrics)
                if "failure_mode_count" in payload:
                    merged.setdefault("failure_mode_count", payload.get("failure_mode_count"))
                return merged, str(path), warnings
        warnings.append(f"metrics_unavailable:{path}")
    warnings.append(f"metrics_missing_for_strategy:{item.get('strategy') or item.get('name') or 'unknown'}")
    return {}, "", warnings


def _metric_for_slice(metrics: dict[str, Any], slice_name: str, metric: str) -> float | None:
    if not metrics:
        return None
    candidates: list[Any] = []
    if slice_name == "metrics":
        candidates.extend([metrics.get(metric), metrics.get(metric.lower())])
    section = metrics.get(slice_name)
    if isinstance(section, dict):
        candidates.extend([section.get(metric), section.get(metric.lower())])
    candidates.extend(
        [
            metrics.get(f"{slice_name}_{metric}"),
            metrics.get(f"{slice_name}.{metric}"),
            metrics.get(f"{slice_name}:{metric}"),
        ]
    )
    for value in candidates:
        try:
            if value is not None and value != "":
                return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _best_row(rows: list[dict[str, Any]], *, higher_is_better: bool) -> dict[str, Any] | None:
    scored = [row for row in rows if row.get("primary_eval_score") is not None]
    if not scored:
        return None
    return sorted(scored, key=lambda row: _rank_key(row.get("primary_eval_score"), higher_is_better))[0]


def _rank_key(value: Any, higher_is_better: bool) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return float("inf")
    return -score if higher_is_better else score


def _interpretation(agent_row: dict[str, Any] | None, baseline: dict[str, Any] | None, delta: float | None) -> str:
    if not agent_row:
        return "agent_strategy_missing"
    if not baseline:
        return "baseline_metrics_missing"
    if delta is None:
        return "strategy_metrics_incomplete"
    if delta > 0.01:
        return "agent_selected_dataset_outperforms_best_baseline_on_heldout_metrics"
    if delta >= -0.01:
        return "agent_selected_dataset_matches_best_baseline_on_heldout_metrics"
    return "agent_selected_dataset_underperforms_best_baseline_on_heldout_metrics"


def _markdown_comparison(summary: dict[str, Any]) -> str:
    lines = [
        "# Model Strategy Comparison",
        "",
        f"- Status: `{summary.get('status')}`",
        f"- Goal: `{summary.get('goal')}`",
        f"- Task type: `{summary.get('task_type')}`",
        f"- Primary metric: `{summary.get('primary_metric')}`",
        f"- Agent strategy: `{summary.get('agent_strategy')}`",
        f"- Best baseline: `{summary.get('best_baseline_strategy')}`",
        f"- Agent minus best baseline: `{summary.get('agent_minus_best_baseline')}`",
        f"- Interpretation: `{summary.get('interpretation')}`",
        "",
        "## Strategy Metrics",
        "",
    ]
    for row in summary.get("strategy_rows") or []:
        lines.append(
            f"- `{row.get('strategy')}` score={row.get('primary_eval_score')} "
            f"train={row.get('train_score')} gap={row.get('apparent_generalization_gap')} "
            f"slices=`{json.dumps(row.get('slice_scores') or {}, ensure_ascii=False, sort_keys=True)}`"
        )
    warnings = summary.get("warnings") or []
    if warnings:
        lines.extend(["", "## Warnings", ""])
        for item in warnings:
            lines.append(f"- {item}")
    lines.extend(["", "## Notes", ""])
    for item in summary.get("notes") or []:
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    if not fieldnames:
        fieldnames = ["status"]
        rows = [{"status": "empty"}]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(row.get(key), ensure_ascii=False, sort_keys=True)
                    if isinstance(row.get(key), (dict, list))
                    else row.get(key, "")
                    for key in fieldnames
                }
            )


def _resolve_path(value: Any, case_dir: Path) -> Path:
    path = Path(str(value))
    if path.is_absolute():
        return path
    return case_dir / path


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}
