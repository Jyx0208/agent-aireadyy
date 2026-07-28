from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from agent.discovery.task_profiles import get_task_profile
from agent.ai_ready.release_predicates import evaluate_export_science, evaluate_release
from agent.models import JsonModel
from agent.utils import write_json


ValidationStatus = Literal[
    "export_completed",
    "export_empty",
    "export_missing",
    "planned_not_exported",
]


class AiReadyValidationRow(JsonModel):
    task_type: str
    exporter: str
    status: ValidationStatus
    report_path: str | None = None
    parquet_path: str | None = None
    parquet_exists: bool = False
    rows_in: int = 0
    rows_out: int = 0
    rows_filtered: int = 0
    filter_counts: dict[str, int] = Field(default_factory=dict)
    missing_required_column_counts: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    required_labels: list[str] = Field(default_factory=list)
    missing_task_requirements: dict[str, int] = Field(default_factory=dict)
    next_pipeline_steps: list[str] = Field(default_factory=list)
    quality_gate: list[str] = Field(default_factory=list)
    spectrum_evidence: dict[str, Any] = Field(default_factory=dict)
    science_contract: dict[str, Any] = Field(default_factory=dict)


class AiReadyValidationReport(JsonModel):
    status: str
    build_dir: str
    task_type: str
    task_profile: str
    implementation_status: str
    rows: list[AiReadyValidationRow] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


EXPORTER_REPORTS = {
    "rt_prediction": {
        "exporter": "rt",
        "report_names": ["rt_export_report.json", "rt_ai_ready/rt_export_report.json"],
        "parquet_output_keys": ["rt_train_parquet"],
        "default_parquet_names": ["rt_train.parquet", "rt_ai_ready/rt_train.parquet"],
    },
    "fragment_intensity_prediction": {
        "exporter": "fragment_intensity",
        "report_names": [
            "fragment_intensity_export_report.json",
            "fragment_intensity_ai_ready/fragment_intensity_export_report.json",
        ],
        "parquet_output_keys": ["fragment_intensity_train_parquet"],
        "default_parquet_names": [
            "fragment_intensity_train.parquet",
            "fragment_intensity_ai_ready/fragment_intensity_train.parquet",
        ],
    },
    "psm_scoring": {
        "exporter": "psm_scoring",
        "report_names": [
            "psm_scoring_export_report.json",
            "psm_scoring_ai_ready/psm_scoring_export_report.json",
        ],
        "parquet_output_keys": ["psm_scoring_train_parquet"],
        "default_parquet_names": [
            "psm_scoring_train.parquet",
            "psm_scoring_ai_ready/psm_scoring_train.parquet",
        ],
    },
    "denovo": {
        "exporter": "denovo",
        "report_names": ["denovo_export_report.json", "denovo_ai_ready/denovo_export_report.json"],
        "parquet_output_keys": ["denovo_train_parquet"],
        "default_parquet_names": ["denovo_train.parquet", "denovo_ai_ready/denovo_train.parquet"],
    },
    "ptm_denovo": {
        "exporter": "ptm_denovo",
        "report_names": ["ptm_denovo_export_report.json", "ptm_denovo_ai_ready/ptm_denovo_export_report.json"],
        "parquet_output_keys": ["ptm_denovo_train_parquet"],
        "default_parquet_names": ["ptm_denovo_train.parquet", "ptm_denovo_ai_ready/ptm_denovo_train.parquet"],
    },
    "chimeric_interpretation": {
        "exporter": "chimeric",
        "report_names": ["chimeric_export_report.json", "chimeric_ai_ready/chimeric_export_report.json"],
        "parquet_output_keys": ["chimeric_train_parquet"],
        "default_parquet_names": ["chimeric_train.parquet", "chimeric_ai_ready/chimeric_train.parquet"],
    },
}


def validate_ai_ready_build(build_dir: str | Path, task_type: str) -> dict[str, Path]:
    build_dir = Path(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    profile = get_task_profile(task_type)
    build_summary = _read_json(build_dir / "agentic_dataset_build_summary.json")
    task_plan = _read_json(build_dir / "discovery_task_build_plan.json")
    if profile.implementation_status == "planned":
        row = _planned_row(profile, task_plan)
    else:
        row = _active_exporter_row(build_dir, profile.task_type, task_plan)
    report = AiReadyValidationReport(
        status=_overall_status(row),
        build_dir=str(build_dir),
        task_type=profile.task_type,
        task_profile=profile.display_name,
        implementation_status=profile.implementation_status,
        rows=[row],
        summary={
            "builder_status": build_summary.get("status"),
            "builder_next_step": build_summary.get("next_step"),
            "selected_files": build_summary.get("selected_files", 0),
            "task_candidate_files": build_summary.get("task_candidate_files", 0),
            "handoff_ready_files": build_summary.get("handoff_ready_files", 0),
            "validation_status_counts": {row.status: 1},
            "warnings": row.warnings,
            "missing_required_column_counts": row.missing_required_column_counts,
            "missing_task_requirements": row.missing_task_requirements,
        },
    )
    json_path = build_dir / "ai_ready_validation_report.json"
    csv_path = build_dir / "ai_ready_validation_report.csv"
    summary_path = build_dir / "ai_ready_build_summary.json"
    markdown_path = build_dir / "ai_ready_build_report.md"
    write_json(json_path, report.model_dump(mode="json"))
    _write_validation_csv(csv_path, report.rows)
    build_summary_payload = _build_summary_payload(
        report=report,
        builder_summary=build_summary,
        task_plan=task_plan,
        input_profile=_read_json(build_dir / "ai_ready_input_profile.json"),
    )
    write_json(summary_path, build_summary_payload)
    markdown_path.write_text(_build_markdown_report(build_summary_payload), encoding="utf-8")
    return {
        "ai_ready_validation_report_json": json_path,
        "ai_ready_validation_report_csv": csv_path,
        "ai_ready_build_summary_json": summary_path,
        "ai_ready_build_report_md": markdown_path,
    }


def _science_contract_fields(payload: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "rt_unit_source",
        "retention_time_unit",
        "has_confidence_column",
        "confidence_column",
        "target_count",
        "decoy_count",
        "unknown_decoy_count",
        "decoy_fraction",
        "decoy_label_source",
    ]
    return {key: payload.get(key) for key in keys if key in payload}


def _active_exporter_row(build_dir: Path, task_type: str, task_plan: dict[str, Any]) -> AiReadyValidationRow:
    config = EXPORTER_REPORTS[task_type]
    report_path = _first_existing(build_dir, config["report_names"])
    if report_path is None:
        return AiReadyValidationRow(
            task_type=task_type,
            exporter=config["exporter"],
            status="export_missing",
            required_labels=list(task_plan.get("required_labels") or []),
            missing_task_requirements=_missing_counts(task_plan),
            next_pipeline_steps=list(task_plan.get("next_pipeline_steps") or []),
            quality_gate=list(task_plan.get("quality_gate") or []),
        )
    payload = _read_json(report_path)
    parquet_path = _resolve_parquet_path(build_dir, payload, config)
    rows_in = int(payload.get("rows_in") or 0)
    rows_out = int(payload.get("rows_out") or payload.get("psm_rows_out") or 0)
    return AiReadyValidationRow(
        task_type=task_type,
        exporter=config["exporter"],
        status="export_completed" if rows_out > 0 else "export_empty",
        report_path=str(report_path),
        parquet_path=str(parquet_path) if parquet_path is not None else None,
        parquet_exists=bool(parquet_path and parquet_path.exists()),
        rows_in=rows_in,
        rows_out=rows_out,
        rows_filtered=int(payload.get("rows_filtered") or max(rows_in - rows_out, 0)),
        filter_counts=_int_dict(payload.get("filter_counts") or {}),
        missing_required_column_counts=_missing_required_column_counts(payload),
        warnings=list(payload.get("warnings") or []),
        required_labels=list(task_plan.get("required_labels") or []),
        missing_task_requirements=_missing_counts(task_plan),
        next_pipeline_steps=list(task_plan.get("next_pipeline_steps") or []),
        quality_gate=list(task_plan.get("quality_gate") or []),
        spectrum_evidence=payload.get("spectrum_evidence") if isinstance(payload.get("spectrum_evidence"), dict) else {},
        science_contract=_science_contract_fields(payload),
    )


def _planned_row(profile, task_plan: dict[str, Any]) -> AiReadyValidationRow:
    return AiReadyValidationRow(
        task_type=profile.task_type,
        exporter="planned_task",
        status="planned_not_exported",
        required_labels=profile.required_labels,
        missing_task_requirements=_missing_counts(task_plan),
        next_pipeline_steps=profile.next_pipeline_steps,
        quality_gate=profile.quality_gate,
        warnings=["planned_task_exporter_not_implemented"],
    )


def _overall_status(row: AiReadyValidationRow) -> str:
    if row.status == "planned_not_exported":
        return "planned_not_exported"
    if row.status == "export_missing":
        return "export_missing"
    if row.status == "export_empty":
        return "export_empty"
    if row.status == "export_completed":
        science = evaluate_export_science(
            row.task_type,
            export_report={
                "status": "completed",
                "rows_out": row.rows_out,
                "parquet_path": row.parquet_path,
                "parquet_exists": row.parquet_exists,
                "warnings": row.warnings,
                "quality_gate": row.quality_gate,
                "filter_counts": row.filter_counts,
                **(row.science_contract or {}),
            },
            validation_row=row.model_dump(mode="json"),
        )
        if not science.ok:
            # Zero-row already handled; science contract failures are needs_review not completed.
            return "needs_review"
        return "completed"
    return "needs_review"


def _first_existing(build_dir: Path, names: list[str]) -> Path | None:
    for name in names:
        path = build_dir / name
        if path.exists():
            return path
    return None


def _resolve_parquet_path(build_dir: Path, payload: dict[str, Any], config: dict[str, Any]) -> Path | None:
    outputs = payload.get("outputs") if isinstance(payload.get("outputs"), dict) else {}
    for key in config["parquet_output_keys"]:
        value = outputs.get(key)
        if value:
            path = Path(str(value))
            return path if path.is_absolute() else build_dir / path
    for name in config["default_parquet_names"]:
        path = build_dir / name
        if path.exists():
            return path
    return build_dir / config["default_parquet_names"][-1]


def _missing_required_column_counts(payload: dict[str, Any]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    validation_counts = payload.get("missing_required_column_counts")
    if isinstance(validation_counts, dict):
        counts.update(_int_dict(validation_counts))
    for item in payload.get("inputs") or []:
        if not isinstance(item, dict):
            continue
        for column in item.get("missing_required_columns") or []:
            counts[str(column)] += int(item.get("rows_in") or 1)
        for warning in item.get("warnings") or []:
            text = str(warning)
            if text.startswith("missing_required_column:"):
                counts[text.split(":", 1)[1]] += int(item.get("rows_in") or 1)
    return dict(sorted(counts.items()))


def _missing_counts(task_plan: dict[str, Any]) -> dict[str, int]:
    summary = task_plan.get("summary") if isinstance(task_plan.get("summary"), dict) else {}
    counts = summary.get("missing_requirement_counts")
    return _int_dict(counts if isinstance(counts, dict) else {})


def _int_dict(payload: dict[Any, Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    for key, value in payload.items():
        try:
            result[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return dict(sorted(result.items()))


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _write_validation_csv(path: Path, rows: list[AiReadyValidationRow]) -> None:
    fieldnames = [
        "task_type",
        "exporter",
        "status",
        "report_path",
        "parquet_path",
        "parquet_exists",
        "rows_in",
        "rows_out",
        "rows_filtered",
        "filter_counts",
        "missing_required_column_counts",
        "warnings",
        "required_labels",
        "missing_task_requirements",
        "next_pipeline_steps",
        "quality_gate",
        "spectrum_evidence",
        "science_contract",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            payload = row.model_dump(mode="json")
            writer.writerow({key: _csv_value(payload.get(key)) for key in fieldnames})


def _build_summary_payload(
    *,
    report: AiReadyValidationReport,
    builder_summary: dict[str, Any],
    task_plan: dict[str, Any],
    input_profile: dict[str, Any],
) -> dict[str, Any]:
    generated_parquet = [
        {
            "task_type": row.task_type,
            "exporter": row.exporter,
            "parquet_path": row.parquet_path,
            "rows_out": row.rows_out,
        }
        for row in report.rows
        if row.status == "export_completed" and row.parquet_exists
    ]
    blockers: list[str] = []
    warnings: list[str] = []
    recommendations: list[str] = []
    spectrum_evidence: list[dict[str, Any]] = []
    for row in report.rows:
        warnings.extend(row.warnings)
        blockers.extend(_row_blockers(row))
        recommendations.extend(_row_recommendations(row))
        if row.spectrum_evidence:
            spectrum_evidence.append({"task_type": row.task_type, **row.spectrum_evidence})
    profile_tasks = input_profile.get("task_profiles") if isinstance(input_profile.get("task_profiles"), list) else []
    for item in profile_tasks:
        if not isinstance(item, dict):
            continue
        blockers.extend(item.get("blockers") or [])
        warnings.extend(item.get("warnings") or [])
    return {
        "status": report.status,
        "build_dir": report.build_dir,
        "task_type": report.task_type,
        "implementation_status": report.implementation_status,
        "builder_summary": builder_summary,
        "task_plan_summary": task_plan.get("summary") if isinstance(task_plan.get("summary"), dict) else {},
        "generated_parquet": generated_parquet,
        "validation_rows": [row.model_dump(mode="json") for row in report.rows],
        "input_profile_status": input_profile.get("status"),
        "input_profile_tasks": profile_tasks,
        "blockers": _dedupe(blockers),
        "warnings": _dedupe(warnings),
        "next_recommendations": _dedupe(recommendations),
        "spectrum_evidence": spectrum_evidence,
    }


def _row_blockers(row: AiReadyValidationRow) -> list[str]:
    blockers: list[str] = []
    if row.status == "export_missing":
        blockers.append("export_missing")
    if row.status == "export_empty":
        blockers.append("export_empty")
    if row.status == "export_completed":
        science = evaluate_export_science(
            row.task_type,
            export_report={
                "status": "completed",
                "rows_out": row.rows_out,
                "parquet_path": row.parquet_path,
                "parquet_exists": row.parquet_exists,
                "warnings": row.warnings,
                "quality_gate": row.quality_gate,
                **(row.science_contract or {}),
            },
            validation_row=row.model_dump(mode="json"),
        )
        blockers.extend(science.blockers)
    if row.status == "planned_not_exported":
        blockers.append("planned_task_exporter_not_implemented")
    if "spectrum_not_matched" in row.filter_counts:
        blockers.append("spectrum_not_matched")
    if row.status == "export_empty" and "no_multi_peptide_assignment" in row.filter_counts:
        blockers.append("no_multi_peptide_assignment")
    for column in row.missing_required_column_counts:
        blockers.append(f"missing_column:{column}")
    return blockers


def _row_recommendations(row: AiReadyValidationRow) -> list[str]:
    if row.status == "export_completed":
        science = evaluate_export_science(
            row.task_type,
            export_report={
                "status": "completed",
                "rows_out": row.rows_out,
                "parquet_path": row.parquet_path,
                "parquet_exists": row.parquet_exists,
                "warnings": row.warnings,
                "quality_gate": row.quality_gate,
                **(row.science_contract or {}),
            },
            validation_row=row.model_dump(mode="json"),
        )
        recommendations = [] if not science.ok else ["ready_for_training_preview"]
        if not science.ok:
            recommendations.extend(f"science_blocker:{b}" for b in science.blockers)
        methods = row.spectrum_evidence.get("fragmentation_methods") if isinstance(row.spectrum_evidence, dict) else None
        if row.task_type == "fragment_intensity_prediction":
            if methods:
                recommendations.append("fragmentation_evidence_confirmed")
            else:
                recommendations.append("inspect_mzml_or_msdt_activation_metadata")
        return recommendations
    recommendations: list[str] = []
    if row.status == "export_missing":
        recommendations.append("provide_search_results")
        if row.task_type in {"fragment_intensity_prediction", "denovo", "ptm_denovo", "chimeric_interpretation"}:
            recommendations.append("provide_peaklist")
        if row.task_type == "psm_scoring":
            recommendations.append("run_psm_with_target_decoy")
        if row.task_type == "ptm_denovo":
            recommendations.append("run_ptm_localization_export")
    if row.status == "export_empty":
        if "spectrum_not_matched" in row.filter_counts:
            recommendations.append("review_spectrum_id_or_peaklist")
        if "missing_modified_sequence" in row.filter_counts:
            recommendations.append("run_ptm_localization_export")
        if "no_multi_peptide_assignment" in row.filter_counts:
            recommendations.append("provide_chimeric_search_or_multi_peptide_labels")
        recommendations.append("review_export_filter_counts")
    if row.status == "planned_not_exported":
        recommendations.append("run_batch_parameters_then_implement_task_exporter")
    return recommendations


def _build_markdown_report(payload: dict[str, Any]) -> str:
    lines = [
        "# AI-ready Build Report",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Task type: `{payload.get('task_type')}`",
        f"- Implementation: `{payload.get('implementation_status')}`",
        f"- Build dir: `{payload.get('build_dir')}`",
        "",
        "## Generated Parquet",
        "",
    ]
    generated = payload.get("generated_parquet") or []
    if not generated:
        lines.append("- None")
    for item in generated:
        lines.append(f"- `{item.get('task_type')}`: {item.get('rows_out', 0)} rows -> `{item.get('parquet_path')}`")
    lines.extend(["", "## Blockers", ""])
    blockers = payload.get("blockers") or []
    if not blockers:
        lines.append("- None")
    for blocker in blockers:
        lines.append(f"- `{blocker}`")
    lines.extend(["", "## Warnings", ""])
    warnings = payload.get("warnings") or []
    if not warnings:
        lines.append("- None")
    for warning in warnings:
        lines.append(f"- `{warning}`")
    lines.extend(["", "## Next Recommendations", ""])
    recommendations = payload.get("next_recommendations") or []
    if not recommendations:
        lines.append("- `review_validation_report`")
    for recommendation in recommendations:
        lines.append(f"- `{recommendation}`")
    lines.extend(["", "## Spectrum Evidence", ""])
    spectrum_evidence = payload.get("spectrum_evidence") or []
    if not spectrum_evidence:
        lines.append("- None")
    for item in spectrum_evidence:
        methods = item.get("fragmentation_methods") or []
        level = item.get("fragmentation_evidence_level") or "unknown"
        lines.append(
            f"- `{item.get('task_type')}`: level=`{level}`, "
            f"methods={', '.join(methods) if methods else 'unknown'}, "
            f"spectra_scanned={item.get('spectra_scanned', 0)}, rows_scanned={item.get('rows_scanned', 0)}"
        )
    lines.extend(["", "## Validation Rows", ""])
    for row in payload.get("validation_rows") or []:
        lines.append(
            f"- `{row.get('task_type')}` / `{row.get('exporter')}`: `{row.get('status')}`, "
            f"rows_out={row.get('rows_out', 0)}, parquet_exists={row.get('parquet_exists')}"
        )
    return "\n".join(lines) + "\n"


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


def _csv_value(value: Any) -> str:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if value is None:
        return ""
    return str(value)
