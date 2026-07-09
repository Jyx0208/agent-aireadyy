from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field

from agent.ai_ready.agent_run_locator import locate_agent_run_inputs
from agent.ai_ready.input_locator import _build_location_summary, _inspect_file
from agent.ai_ready.real_smoke import (
    AiReadyRealSmokeTaskResult,
    _normalize_tasks,
    _overall_status,
    _run_task,
)
from agent.models import JsonModel
from agent.utils import write_json


class AgentRunAiReadyBuildResult(JsonModel):
    status: str
    agent_run_dir: str
    output_dir: str
    locator_summary: dict[str, Any] = Field(default_factory=dict)
    task_results: list[AiReadyRealSmokeTaskResult] = Field(default_factory=list)
    summary_path: str
    report_path: str


def build_ai_ready_from_agent_run(
    *,
    agent_run_dir: str | Path,
    task_types: list[str] | None,
    output_dir: str | Path,
    project_accession: str | None = None,
    source_file: str | None = None,
    q_value_threshold: float = 0.01,
    probability_threshold: float = 0.9,
    require_confidence: bool = False,
    search_engine: str | None = None,
    max_input_file_mb: int = 2048,
    allow_large_input: bool = False,
    peaklists: list[str | Path] | None = None,
) -> AgentRunAiReadyBuildResult:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    normalized_tasks = _normalize_tasks(task_types)
    locator = locate_agent_run_inputs(
        agent_run_dir=agent_run_dir,
        output_dir=output_dir,
        max_input_file_mb=max_input_file_mb,
        allow_large_input=allow_large_input,
    )
    _append_explicit_peaklists(locator, peaklists or [])
    task_results: list[AiReadyRealSmokeTaskResult] = []
    for task_type in normalized_tasks:
        task_result = _run_task(
            task_type=task_type,
            output_dir=output_dir / "task_runs" / task_type,
            locator=_compatible_locator(locator),
            project_accession=project_accession,
            source_file=source_file,
            q_value_threshold=q_value_threshold,
            probability_threshold=probability_threshold,
            require_confidence=require_confidence,
            search_engine=search_engine,
        )
        if (
            task_result.status == "blocked"
            and not locator.summary.get("search_result_count")
            and locator.summary.get("generic_ai_ready_available")
        ):
            task_result.warnings = _dedupe(
                task_result.warnings
                + ["generic_ai_ready_available", "task_specific_training_labels_not_found"]
            )
        task_results.append(task_result)

    status = _overall_status(task_results)
    summary_path = output_dir / "agent_run_build_summary.json"
    report_path = output_dir / "agent_run_build_report.md"
    result = AgentRunAiReadyBuildResult(
        status=status,
        agent_run_dir=str(agent_run_dir),
        output_dir=str(output_dir),
        locator_summary=locator.summary,
        task_results=task_results,
        summary_path=str(summary_path),
        report_path=str(report_path),
    )
    write_json(summary_path, result.model_dump(mode="json"))
    report_path.write_text(_markdown_report(result), encoding="utf-8")
    return result


def _compatible_locator(locator):
    from agent.ai_ready.input_locator import AiReadyInputLocationResult

    return AiReadyInputLocationResult(
        status=locator.status,
        search_dir=locator.agent_run_dir,
        output_dir=locator.output_dir,
        entries=locator.ai_ready_inputs,
        summary=locator.summary,
        json_path=locator.json_path,
        csv_path=locator.csv_path,
    )


def _append_explicit_peaklists(locator, peaklists: list[str | Path]) -> None:
    existing = {str(Path(item.path).resolve()).casefold() for item in locator.ai_ready_inputs}
    added: list[str] = []
    for peaklist in peaklists:
        path = Path(peaklist)
        if not path.exists():
            raise ValueError(f"Peaklist does not exist: {path}")
        key = str(path.resolve()).casefold()
        if key in existing:
            continue
        inspected = _inspect_file(path)
        if inspected is None or inspected.file_role != "peaklist_mgf":
            raise ValueError(f"Peaklist must be an MGF file: {path}")
        locator.ai_ready_inputs.append(inspected)
        existing.add(key)
        added.append(str(path))
    if added:
        ai_ready_summary = _build_location_summary(locator.ai_ready_inputs)
        locator.summary = {
            **locator.summary,
            "search_result_count": ai_ready_summary["search_result_count"],
            "peaklist_count": ai_ready_summary["peaklist_count"],
            "total_rows": ai_ready_summary["total_rows"],
            "total_mgf_spectra": ai_ready_summary["total_mgf_spectra"],
            "has_rt_table": ai_ready_summary["has_rt_table"],
            "has_target_decoy_table": ai_ready_summary["has_target_decoy_table"],
            "has_modified_sequence_table": ai_ready_summary["has_modified_sequence_table"],
            "warnings": _dedupe(locator.summary.get("warnings", []) + ai_ready_summary["warnings"]),
            "external_peaklists": added,
        }


def _markdown_report(result: AgentRunAiReadyBuildResult) -> str:
    lines = [
        "# Original Agent Run AI-ready Build Report",
        "",
        f"- Status: `{result.status}`",
        f"- Agent run dir: `{result.agent_run_dir}`",
        f"- Located artifacts: {result.locator_summary.get('located_artifacts', 0)}",
        f"- Search result files: {result.locator_summary.get('search_result_count', 0)}",
        f"- Peaklists: {result.locator_summary.get('peaklist_count', 0)}",
        f"- Generic AI-ready available: {result.locator_summary.get('generic_ai_ready_available', False)}",
        "",
        "## Task Results",
        "",
    ]
    for task in result.task_results:
        lines.extend(
            [
                f"### {task.task_type}",
                "",
                f"- Status: `{task.status}`",
                f"- Rows out: {task.rows_out}",
                f"- Search results: {len(task.search_results)}",
                f"- Peaklists: {len(task.peaklists)}",
                f"- Blockers: {', '.join(task.blockers) if task.blockers else 'None'}",
                f"- Warnings: {', '.join(task.warnings) if task.warnings else 'None'}",
                "",
            ]
        )
    return "\n".join(lines)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result
