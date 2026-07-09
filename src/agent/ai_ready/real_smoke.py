from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import Field

from agent.ai_ready.chimeric_exporter import export_chimeric_ai_ready
from agent.ai_ready.denovo_exporter import export_denovo_ai_ready
from agent.ai_ready.fragment_intensity_exporter import export_fragment_intensity_ai_ready
from agent.ai_ready.input_locator import locate_ai_ready_inputs, select_ai_ready_inputs
from agent.ai_ready.input_profile import TASKS_REQUIRING_PEAKLIST, profile_ai_ready_inputs
from agent.ai_ready.psm_scoring_exporter import export_psm_scoring_ai_ready
from agent.ai_ready.ptm_denovo_exporter import export_ptm_denovo_ai_ready
from agent.ai_ready.rt_exporter import export_rt_ai_ready
from agent.ai_ready.validation import validate_ai_ready_build
from agent.discovery.task_readiness import normalize_task_type
from agent.models import JsonModel
from agent.utils import write_json


DEFAULT_REAL_SMOKE_TASKS = [
    "rt_prediction",
    "denovo",
    "ptm_denovo",
    "chimeric_interpretation",
    "fragment_intensity_prediction",
    "psm_scoring",
]


class AiReadyRealSmokeTaskResult(JsonModel):
    task_type: str
    status: str
    output_dir: str
    search_results: list[str] = Field(default_factory=list)
    peaklists: list[str] = Field(default_factory=list)
    rows_out: int = 0
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict)


class AiReadyRealSmokeResult(JsonModel):
    status: str
    search_dir: str
    output_dir: str
    locator_summary: dict[str, Any] = Field(default_factory=dict)
    task_results: list[AiReadyRealSmokeTaskResult] = Field(default_factory=list)
    summary_path: str
    report_path: str
    discovery_feedback_preview_path: str


def run_ai_ready_real_smoke(
    *,
    search_dir: str | Path,
    task_types: list[str] | None,
    output_dir: str | Path,
    project_accession: str | None = None,
    source_file: str | None = None,
    q_value_threshold: float = 0.01,
    probability_threshold: float = 0.9,
    require_confidence: bool = False,
    search_engine: str | None = None,
) -> AiReadyRealSmokeResult:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    normalized_tasks = _normalize_tasks(task_types)
    locator = locate_ai_ready_inputs(search_dir=search_dir, output_dir=output_dir)
    task_results = [
        _run_task(
            task_type=task_type,
            output_dir=output_dir / "task_runs" / task_type,
            locator=locator,
            project_accession=project_accession,
            source_file=source_file,
            q_value_threshold=q_value_threshold,
            probability_threshold=probability_threshold,
            require_confidence=require_confidence,
            search_engine=search_engine,
        )
        for task_type in normalized_tasks
    ]
    status = _overall_status(task_results)
    summary_path = output_dir / "real_smoke_summary.json"
    report_path = output_dir / "real_smoke_report.md"
    feedback_path = output_dir / "discovery_feedback_preview.json"
    result = AiReadyRealSmokeResult(
        status=status,
        search_dir=str(search_dir),
        output_dir=str(output_dir),
        locator_summary=locator.summary,
        task_results=task_results,
        summary_path=str(summary_path),
        report_path=str(report_path),
        discovery_feedback_preview_path=str(feedback_path),
    )
    write_json(summary_path, result.model_dump(mode="json"))
    write_json(feedback_path, _feedback_preview(result))
    report_path.write_text(_markdown_report(result), encoding="utf-8")
    return result


def _run_task(
    *,
    task_type: str,
    output_dir: Path,
    locator,
    project_accession: str | None,
    source_file: str | None,
    q_value_threshold: float,
    probability_threshold: float,
    require_confidence: bool,
    search_engine: str | None,
) -> AiReadyRealSmokeTaskResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    search_results, peaklists = select_ai_ready_inputs(locator, task_type=task_type)
    files: dict[str, str] = {}
    blockers: list[str] = []
    warnings: list[str] = []
    rows_out = 0

    if not search_results:
        blockers.append("needs_search_results")
    if task_type in TASKS_REQUIRING_PEAKLIST and not peaklists:
        blockers.append("needs_peaklist")

    if search_results:
        try:
            profile = profile_ai_ready_inputs(
                search_results=search_results,
                peaklists=peaklists,
                task_types=[task_type],
                output_dir=output_dir,
            )
            files["ai_ready_input_profile_json"] = profile.json_path
            files["ai_ready_input_profile_csv"] = profile.csv_path
            task_profile = profile.task_profiles[0] if profile.task_profiles else None
            if task_profile is not None:
                blockers.extend(task_profile.blockers)
                warnings.extend(task_profile.warnings)
        except ValueError as exc:
            blockers.append(str(exc))

    blockers = _dedupe(blockers)
    if not blockers:
        try:
            rows_out, export_files = _export_task(
                task_type=task_type,
                search_results=search_results,
                peaklists=peaklists,
                output_dir=output_dir,
                project_accession=project_accession,
                source_file=source_file,
                q_value_threshold=q_value_threshold,
                probability_threshold=probability_threshold,
                require_confidence=require_confidence,
                search_engine=search_engine,
            )
            files.update(export_files)
        except ValueError as exc:
            blockers.extend(_blockers_from_exception(str(exc)))
            warnings.append(str(exc))

    try:
        validation_paths = validate_ai_ready_build(output_dir, task_type)
        files.update({key: str(path) for key, path in validation_paths.items()})
        validation_report = json.loads(validation_paths["ai_ready_validation_report_json"].read_text(encoding="utf-8"))
        for row in validation_report.get("rows") or []:
            if isinstance(row, dict):
                rows_out = max(rows_out, int(row.get("rows_out") or 0))
                warnings.extend(row.get("warnings") or [])
                blockers.extend(_validation_blockers(row))
    except Exception as exc:
        warnings.append(f"validation_failed:{exc}")

    blockers = _dedupe(blockers)
    warnings = _dedupe(warnings)
    if rows_out > 0:
        status = "completed"
    elif blockers:
        status = "blocked"
    else:
        status = "needs_review"
    return AiReadyRealSmokeTaskResult(
        task_type=task_type,
        status=status,
        output_dir=str(output_dir),
        search_results=[str(path) for path in search_results],
        peaklists=[str(path) for path in peaklists],
        rows_out=rows_out,
        blockers=blockers,
        warnings=warnings,
        files=files,
    )


def _export_task(
    *,
    task_type: str,
    search_results: list[Path],
    peaklists: list[Path],
    output_dir: Path,
    project_accession: str | None,
    source_file: str | None,
    q_value_threshold: float,
    probability_threshold: float,
    require_confidence: bool,
    search_engine: str | None,
) -> tuple[int, dict[str, str]]:
    if task_type == "rt_prediction":
        result = export_rt_ai_ready(
            search_results=search_results,
            output_dir=output_dir,
            project_accession=project_accession,
            source_file=source_file,
            q_value_threshold=q_value_threshold,
            probability_threshold=probability_threshold,
            require_confidence=require_confidence,
            search_engine=search_engine,
        )
        return result.rows_out, {
            "rt_train_parquet": result.output_parquet,
            "rt_export_report": result.report_json,
        }
    if task_type == "fragment_intensity_prediction":
        result = export_fragment_intensity_ai_ready(
            search_results=search_results,
            peaklists=peaklists,
            output_dir=output_dir,
            project_accession=project_accession,
            source_file=source_file,
            q_value_threshold=q_value_threshold,
            probability_threshold=probability_threshold,
            search_engine=search_engine,
        )
        return result.rows_out, {
            "fragment_intensity_train_parquet": result.output_parquet,
            "fragment_intensity_export_report": result.report_json,
        }
    if task_type == "psm_scoring":
        result = export_psm_scoring_ai_ready(
            search_results=search_results,
            output_dir=output_dir,
            project_accession=project_accession,
            source_file=source_file,
            search_engine=search_engine,
        )
        return result.rows_out, {
            "psm_scoring_train_parquet": result.output_parquet,
            "psm_scoring_export_report": result.report_json,
        }
    if task_type == "denovo":
        result = export_denovo_ai_ready(
            search_results=search_results,
            peaklists=peaklists,
            output_dir=output_dir,
            project_accession=project_accession,
            source_file=source_file,
            q_value_threshold=q_value_threshold,
            probability_threshold=probability_threshold,
            search_engine=search_engine,
        )
        return result.rows_out, {
            "denovo_train_parquet": result.output_parquet,
            "denovo_export_report": result.report_json,
        }
    if task_type == "ptm_denovo":
        result = export_ptm_denovo_ai_ready(
            search_results=search_results,
            peaklists=peaklists,
            output_dir=output_dir,
            project_accession=project_accession,
            source_file=source_file,
            q_value_threshold=q_value_threshold,
            probability_threshold=probability_threshold,
            search_engine=search_engine,
        )
        return result.rows_out, {
            "ptm_denovo_train_parquet": result.output_parquet,
            "ptm_denovo_export_report": result.report_json,
        }
    if task_type == "chimeric_interpretation":
        result = export_chimeric_ai_ready(
            search_results=search_results,
            peaklists=peaklists,
            output_dir=output_dir,
            project_accession=project_accession,
            source_file=source_file,
            q_value_threshold=q_value_threshold,
            probability_threshold=probability_threshold,
            search_engine=search_engine,
        )
        return result.rows_out, {
            "chimeric_train_parquet": result.output_parquet,
            "chimeric_export_report": result.report_json,
        }
    raise ValueError(f"Real smoke does not support task type: {task_type}")


def _normalize_tasks(task_types: list[str] | None) -> list[str]:
    values = task_types or DEFAULT_REAL_SMOKE_TASKS
    result: list[str] = []
    for value in values:
        task_type = normalize_task_type(value)
        if task_type not in DEFAULT_REAL_SMOKE_TASKS:
            raise ValueError(f"Real smoke does not support task type: {value}")
        if task_type not in result:
            result.append(task_type)
    return result


def _overall_status(tasks: list[AiReadyRealSmokeTaskResult]) -> str:
    if any(task.status == "completed" for task in tasks):
        return "completed"
    if all(task.status == "blocked" for task in tasks):
        return "blocked"
    return "needs_review"


def _blockers_from_exception(message: str) -> list[str]:
    lower = message.casefold()
    blockers: list[str] = []
    if "target_decoy_label_missing" in lower or "target/decoy" in lower:
        blockers.append("needs_target_decoy_labels")
    if "modified" in lower:
        blockers.append("needs_modified_sequence_labels")
    if "peaklist" in lower or "mgf" in lower:
        blockers.append("needs_peaklist")
    if not blockers:
        blockers.append("export_failed")
    return blockers


def _validation_blockers(row: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if row.get("status") in {"export_missing", "export_empty", "planned_not_exported"}:
        blockers.append(str(row.get("status")))
    try:
        rows_out = int(row.get("rows_out") or 0)
    except (TypeError, ValueError):
        rows_out = 0
    if rows_out > 0:
        return blockers
    filter_counts = row.get("filter_counts") if isinstance(row.get("filter_counts"), dict) else {}
    if filter_counts.get("spectrum_not_matched"):
        blockers.append("spectrum_not_matched")
    if filter_counts.get("missing_modified_sequence"):
        blockers.append("needs_modified_sequence_labels")
    if filter_counts.get("no_multi_peptide_assignment"):
        blockers.append("no_multi_peptide_assignment")
    missing = row.get("missing_required_column_counts")
    if isinstance(missing, dict):
        blockers.extend(f"missing_column:{key}" for key in sorted(missing))
    return blockers


def _feedback_preview(result: AiReadyRealSmokeResult) -> dict[str, Any]:
    return {
        "status": "preview_only",
        "search_dir": result.search_dir,
        "successful_tasks": [task.task_type for task in result.task_results if task.status == "completed"],
        "blocked_tasks": [
            {"task_type": task.task_type, "blockers": task.blockers}
            for task in result.task_results
            if task.status == "blocked"
        ],
        "scoring_update_policy": "not_applied",
        "suggested_calibration_targets": [
            "validity_status",
            "task_readiness",
            "trust_score",
        ],
    }


def _markdown_report(result: AiReadyRealSmokeResult) -> str:
    lines = [
        "# AI-ready Real Smoke Report",
        "",
        f"- Status: `{result.status}`",
        f"- Search dir: `{result.search_dir}`",
        f"- Located files: {result.locator_summary.get('located_files', 0)}",
        f"- Search result files: {result.locator_summary.get('search_result_count', 0)}",
        f"- Peaklists: {result.locator_summary.get('peaklist_count', 0)}",
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
