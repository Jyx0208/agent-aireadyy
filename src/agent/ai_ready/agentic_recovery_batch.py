from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from pydantic import Field

from agent.ai_ready.agentic_recovery import (
    AgenticRecoveryResult,
    RecoveryLLMClient,
    run_agentic_recovery,
)
from agent.models import JsonModel
from agent.utils import write_json


class AgenticRecoveryBatchRun(JsonModel):
    mini_e2e_dir: str
    agent_run_dir: str | None = None
    output_dir: str
    status: str
    primary_issue: str | None = None
    planned_actions: list[dict[str, Any]] = Field(default_factory=list)
    executed_actions: list[dict[str, Any]] = Field(default_factory=list)
    final_recommendation: str = ""
    files: dict[str, str] = Field(default_factory=dict)


class AgenticRecoveryBatchResult(JsonModel):
    status: str
    mode: str
    batch_dir: str
    output_dir: str
    run_results: list[AgenticRecoveryBatchRun] = Field(default_factory=list)
    status_counts: dict[str, int] = Field(default_factory=dict)
    primary_issue_counts: dict[str, int] = Field(default_factory=dict)
    planned_action_counts: dict[str, int] = Field(default_factory=dict)
    executed_action_count: int = 0
    files: dict[str, str] = Field(default_factory=dict)


def run_agentic_recovery_batch(
    *,
    batch_dir: str | Path,
    output_dir: str | Path | None = None,
    allow_safe_actions: bool = False,
    llm_client: RecoveryLLMClient | None = None,
) -> AgenticRecoveryBatchResult:
    batch_dir = Path(batch_dir)
    if not batch_dir.exists():
        raise ValueError(f"Mini E2E batch directory does not exist: {batch_dir}")
    summary_path = batch_dir / "mini_e2e_batch_summary.json"
    if not summary_path.exists():
        raise ValueError(f"mini_e2e_batch_summary.json not found: {summary_path}")
    payload = _read_json(summary_path)
    output_dir = Path(output_dir) if output_dir is not None else batch_dir / "agentic_recovery"
    output_dir.mkdir(parents=True, exist_ok=True)

    run_results: list[AgenticRecoveryBatchRun] = []
    for index, item in enumerate(payload.get("run_results") or [], start=1):
        if not isinstance(item, dict):
            continue
        mini_dir_value = item.get("output_dir") or item.get("summary_path")
        if not mini_dir_value:
            continue
        mini_dir = Path(str(mini_dir_value))
        if mini_dir.name == "mini_e2e_summary.json":
            mini_dir = mini_dir.parent
        run_output_dir = output_dir / f"{index:02d}_{_safe_stem(mini_dir.name)}"
        recovery_result = run_agentic_recovery(
            mini_e2e_dir=mini_dir,
            output_dir=run_output_dir,
            allow_safe_actions=allow_safe_actions,
            llm_client=llm_client,
        )
        run_results.append(_summarize_run(recovery_result))

    status_counts = _count_values([item.status for item in run_results])
    primary_issue_counts = _count_values([item.primary_issue or "none" for item in run_results])
    planned_action_counts = _count_values(
        [
            str(action.get("action_type") or "unknown")
            for item in run_results
            for action in item.planned_actions
        ]
    )
    executed_action_count = sum(len(item.executed_actions) for item in run_results)
    status = "executed" if executed_action_count else "planned" if _has_planned_action(run_results) else "no_action"
    result = AgenticRecoveryBatchResult(
        status=status,
        mode="llm_react" if llm_client else "deterministic_react",
        batch_dir=str(batch_dir),
        output_dir=str(output_dir),
        run_results=run_results,
        status_counts=status_counts,
        primary_issue_counts=primary_issue_counts,
        planned_action_counts=planned_action_counts,
        executed_action_count=executed_action_count,
    )
    files = _write_outputs(result, output_dir)
    result.files = files
    write_json(output_dir / "agentic_recovery_batch_summary.json", result.model_dump(mode="json"))
    return result


def _summarize_run(result: AgenticRecoveryResult) -> AgenticRecoveryBatchRun:
    return AgenticRecoveryBatchRun(
        mini_e2e_dir=result.mini_e2e_dir,
        agent_run_dir=result.observation.agent_run_dir,
        output_dir=result.output_dir,
        status=result.status,
        primary_issue=result.observation.primary_issue,
        planned_actions=[item.model_dump(mode="json") for item in result.planned_actions],
        executed_actions=result.executed_actions,
        final_recommendation=result.final_recommendation,
        files=result.files,
    )


def _write_outputs(result: AgenticRecoveryBatchResult, output_dir: Path) -> dict[str, str]:
    summary_path = output_dir / "agentic_recovery_batch_summary.json"
    csv_path = output_dir / "agentic_recovery_batch_summary.csv"
    report_path = output_dir / "agentic_recovery_batch_report.md"
    _write_csv(csv_path, result)
    report_path.write_text(_markdown_report(result), encoding="utf-8")
    return {
        "agentic_recovery_batch_summary_json": str(summary_path),
        "agentic_recovery_batch_summary_csv": str(csv_path),
        "agentic_recovery_batch_report_md": str(report_path),
    }


def _write_csv(path: Path, result: AgenticRecoveryBatchResult) -> None:
    fieldnames = [
        "mini_e2e_dir",
        "agent_run_dir",
        "status",
        "primary_issue",
        "planned_actions",
        "executed_actions",
        "final_recommendation",
        "output_dir",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in result.run_results:
            payload = item.model_dump(mode="json")
            writer.writerow(
                {
                    key: json.dumps(payload[key], ensure_ascii=False, sort_keys=True)
                    if isinstance(payload.get(key), (dict, list))
                    else payload.get(key, "")
                    for key in fieldnames
                }
            )


def _markdown_report(result: AgenticRecoveryBatchResult) -> str:
    lines = [
        "# Agentic Recovery Batch Report",
        "",
        f"- Status: `{result.status}`",
        f"- Mode: `{result.mode}`",
        f"- Runs: {len(result.run_results)}",
        f"- Status counts: `{json.dumps(result.status_counts, ensure_ascii=False, sort_keys=True)}`",
        f"- Primary issue counts: `{json.dumps(result.primary_issue_counts, ensure_ascii=False, sort_keys=True)}`",
        f"- Planned action counts: `{json.dumps(result.planned_action_counts, ensure_ascii=False, sort_keys=True)}`",
        f"- Executed action count: {result.executed_action_count}",
        "",
        "## Runs",
        "",
    ]
    for item in result.run_results:
        lines.extend(
            [
                f"### {Path(item.mini_e2e_dir).name}",
                "",
                f"- Status: `{item.status}`",
                f"- Primary issue: `{item.primary_issue or 'none'}`",
                f"- Planned actions: `{json.dumps(item.planned_actions, ensure_ascii=False, sort_keys=True)}`",
                f"- Executed actions: `{json.dumps(item.executed_actions, ensure_ascii=False, sort_keys=True)}`",
                f"- Final recommendation: {item.final_recommendation}",
                "",
            ]
        )
    return "\n".join(lines)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _count_values(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _has_planned_action(runs: list[AgenticRecoveryBatchRun]) -> bool:
    return any(
        str(action.get("action_type") or "") != "no_action"
        for run in runs
        for action in run.planned_actions
    )


def _safe_stem(value: str) -> str:
    text = "".join(char if char.isalnum() or char in "._-" else "_" for char in str(value or "").strip())
    return text.strip("._-") or "mini_e2e"
