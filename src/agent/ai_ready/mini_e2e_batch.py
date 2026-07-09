from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from pydantic import Field

from agent.ai_ready.mini_e2e import MiniE2EResult, validate_agent_run_ai_ready_mini
from agent.discovery.ontology import (
    interpret_ptm_metadata,
    labeling_from_text,
    normalize_labeling_strategy,
    normalize_species_values,
    species_from_text,
)
from agent.inference.mzml_metadata import infer_instrument_family_from_name
from agent.models import JsonModel
from agent.utils import write_json


class MiniE2EBatchRun(JsonModel):
    agent_run_dir: str
    output_dir: str
    run_name: str = ""
    repository: str = "unknown"
    project_accession: str | None = None
    source_file: str | None = None
    input_size_mb: float | None = None
    instrument: str | None = None
    fragmentation: str | None = None
    species_policy: str = "open"
    canonical_species: list[str] = Field(default_factory=list)
    organism_taxon_id: list[str] = Field(default_factory=list)
    acquisition_mode: str | None = None
    ptm_type: str | None = None
    ptm_subtype: str | None = None
    ptm_evidence_terms: list[str] = Field(default_factory=list)
    ptm_enrichment_methods: list[str] = Field(default_factory=list)
    semantic_metadata_confidence: float = 0.0
    semantic_interpretation_trace: list[str] = Field(default_factory=list)
    modification_scope: str | None = None
    labeling_strategy: str | None = None
    instrument_families: list[str] = Field(default_factory=list)
    fragmentation_methods: list[str] = Field(default_factory=list)
    diversity_tags: list[str] = Field(default_factory=list)
    metadata_quality: str = "unknown"
    full_status: str = "unknown"
    sample_class: str = "unknown"
    status: str
    ai_ready_outcome: str | None = None
    usable_partial_outputs: bool = False
    task_statuses: dict[str, str] = Field(default_factory=dict)
    rows_out: dict[str, int] = Field(default_factory=dict)
    task_files: dict[str, dict[str, str]] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    recovery_status: str | None = None
    primary_issue: str | None = None
    recommended_next_step: str | None = None
    recovery_report_json: str | None = None
    recovery_report_md: str | None = None
    upstream_recovery_status: str | None = None
    upstream_workflow_outcome: str | None = None
    upstream_usable_partial_outputs: bool = False
    upstream_primary_issue: str | None = None
    upstream_recommended_next_step: str | None = None
    upstream_recovery_report_json: str | None = None
    upstream_recovery_report_md: str | None = None
    recovery_actions: list[dict[str, Any]] = Field(default_factory=list)
    output_size_mb: float = 0.0
    summary_path: str | None = None
    report_path: str | None = None


class MiniE2EBatchResult(JsonModel):
    status: str
    output_dir: str
    run_results: list[MiniE2EBatchRun] = Field(default_factory=list)
    status_counts: dict[str, int] = Field(default_factory=dict)
    ai_ready_outcome_counts: dict[str, int] = Field(default_factory=dict)
    task_status_counts: dict[str, dict[str, int]] = Field(default_factory=dict)
    recovery_issue_counts: dict[str, int] = Field(default_factory=dict)
    upstream_recovery_issue_counts: dict[str, int] = Field(default_factory=dict)
    total_output_size_mb: float = 0.0
    summary_path: str
    csv_path: str
    report_path: str
    benchmark_sample_manifest_json_path: str | None = None
    benchmark_sample_manifest_csv_path: str | None = None
    benchmark_summary_json_path: str | None = None
    benchmark_summary_csv_path: str | None = None
    benchmark_report_path: str | None = None
    benchmark_failure_taxonomy_path: str | None = None


def validate_agent_runs_ai_ready_batch(
    *,
    agent_run_dirs: list[str | Path],
    task_types: list[str] | None,
    output_dir: str | Path,
    peaklists: list[str | Path] | None = None,
    project_accession: str | None = None,
    source_file: str | None = None,
    q_value_threshold: float = 0.01,
    probability_threshold: float = 0.9,
    require_confidence: bool = False,
    search_engine: str | None = None,
    max_input_file_mb: int = 2048,
    allow_large_input: bool = False,
    auto_recover: bool = True,
) -> MiniE2EBatchResult:
    if not agent_run_dirs:
        raise ValueError("At least one --agent-run-dir is required.")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_results: list[MiniE2EBatchRun] = []
    for index, run_dir_value in enumerate(agent_run_dirs, start=1):
        run_dir = Path(run_dir_value)
        run_output_dir = output_dir / f"{index:02d}_{_safe_stem(run_dir.name)}"
        result = validate_agent_run_ai_ready_mini(
            agent_run_dir=run_dir,
            task_types=task_types,
            output_dir=run_output_dir,
            project_accession=project_accession,
            source_file=source_file,
            q_value_threshold=q_value_threshold,
            probability_threshold=probability_threshold,
            require_confidence=require_confidence,
            search_engine=search_engine,
            max_input_file_mb=max_input_file_mb,
            allow_large_input=allow_large_input,
            peaklists=peaklists,
            auto_recover=auto_recover,
        )
        run_results.append(_summarize_run(run_dir=run_dir, result=result, output_dir=run_output_dir))

    status_counts = _status_counts([item.status for item in run_results])
    ai_ready_outcome_counts = _status_counts([item.ai_ready_outcome or "unknown" for item in run_results])
    task_status_counts = _task_status_counts(run_results)
    recovery_issue_counts = _status_counts([item.primary_issue or "none" for item in run_results])
    upstream_recovery_issue_counts = _status_counts([item.upstream_primary_issue or "none" for item in run_results])
    status = "completed" if any(item.status == "completed" for item in run_results) else "blocked"
    summary_path = output_dir / "mini_e2e_batch_summary.json"
    csv_path = output_dir / "mini_e2e_batch_summary.csv"
    report_path = output_dir / "mini_e2e_batch_report.md"
    benchmark_sample_manifest_json_path = output_dir / "benchmark_sample_manifest.json"
    benchmark_sample_manifest_csv_path = output_dir / "benchmark_sample_manifest.csv"
    benchmark_summary_json_path = output_dir / "benchmark_summary.json"
    benchmark_summary_csv_path = output_dir / "benchmark_summary.csv"
    benchmark_report_path = output_dir / "benchmark_report.md"
    benchmark_failure_taxonomy_path = output_dir / "benchmark_failure_taxonomy.json"
    result = MiniE2EBatchResult(
        status=status,
        output_dir=str(output_dir),
        run_results=run_results,
        status_counts=status_counts,
        ai_ready_outcome_counts=ai_ready_outcome_counts,
        task_status_counts=task_status_counts,
        recovery_issue_counts=recovery_issue_counts,
        upstream_recovery_issue_counts=upstream_recovery_issue_counts,
        total_output_size_mb=round(sum(item.output_size_mb for item in run_results), 3),
        summary_path=str(summary_path),
        csv_path=str(csv_path),
        report_path=str(report_path),
        benchmark_sample_manifest_json_path=str(benchmark_sample_manifest_json_path),
        benchmark_sample_manifest_csv_path=str(benchmark_sample_manifest_csv_path),
        benchmark_summary_json_path=str(benchmark_summary_json_path),
        benchmark_summary_csv_path=str(benchmark_summary_csv_path),
        benchmark_report_path=str(benchmark_report_path),
        benchmark_failure_taxonomy_path=str(benchmark_failure_taxonomy_path),
    )
    write_json(summary_path, result.model_dump(mode="json"))
    _write_csv(csv_path, result)
    report_path.write_text(_markdown_report(result), encoding="utf-8")
    _write_benchmark_outputs(
        result,
        sample_manifest_json_path=benchmark_sample_manifest_json_path,
        sample_manifest_csv_path=benchmark_sample_manifest_csv_path,
        summary_json_path=benchmark_summary_json_path,
        summary_csv_path=benchmark_summary_csv_path,
        report_path=benchmark_report_path,
        failure_taxonomy_path=benchmark_failure_taxonomy_path,
    )
    return result


def _summarize_run(*, run_dir: Path, result: MiniE2EResult, output_dir: Path) -> MiniE2EBatchRun:
    metadata = _run_metadata(run_dir)
    task_statuses = {item.task_type: item.status for item in result.task_results}
    rows_out = {item.task_type: item.rows_out for item in result.task_results}
    full_status = result.upstream_workflow_outcome or result.ai_ready_outcome or result.status
    return MiniE2EBatchRun(
        agent_run_dir=str(run_dir),
        output_dir=str(output_dir),
        run_name=run_dir.name,
        repository=metadata.get("repository") or "unknown",
        project_accession=metadata.get("project_accession"),
        source_file=metadata.get("source_file"),
        input_size_mb=metadata.get("input_size_mb"),
        instrument=metadata.get("instrument"),
        fragmentation=metadata.get("fragmentation"),
        species_policy=metadata.get("species_policy") or "open",
        canonical_species=metadata.get("canonical_species") or [],
        organism_taxon_id=metadata.get("organism_taxon_id") or [],
        acquisition_mode=metadata.get("acquisition_mode"),
        ptm_type=metadata.get("ptm_type"),
        ptm_subtype=metadata.get("ptm_subtype"),
        ptm_evidence_terms=metadata.get("ptm_evidence_terms") or [],
        ptm_enrichment_methods=metadata.get("ptm_enrichment_methods") or [],
        semantic_metadata_confidence=float(metadata.get("semantic_metadata_confidence") or 0.0),
        semantic_interpretation_trace=metadata.get("semantic_interpretation_trace") or [],
        modification_scope=metadata.get("modification_scope"),
        labeling_strategy=metadata.get("labeling_strategy"),
        instrument_families=metadata.get("instrument_families") or [],
        fragmentation_methods=metadata.get("fragmentation_methods") or [],
        diversity_tags=metadata.get("diversity_tags") or [],
        metadata_quality=metadata.get("metadata_quality") or "unknown",
        full_status=full_status,
        sample_class=_sample_class(result),
        status=result.status,
        ai_ready_outcome=result.ai_ready_outcome,
        usable_partial_outputs=result.usable_partial_outputs,
        task_statuses=task_statuses,
        rows_out=rows_out,
        task_files={item.task_type: item.files for item in result.task_results},
        blockers=result.blockers,
        warnings=result.warnings,
        recovery_status=result.recovery_status,
        primary_issue=result.primary_issue,
        recommended_next_step=result.recommended_next_step,
        recovery_report_json=result.recovery_report_json,
        recovery_report_md=result.recovery_report_md,
        upstream_recovery_status=result.upstream_recovery_status,
        upstream_workflow_outcome=result.upstream_workflow_outcome,
        upstream_usable_partial_outputs=result.upstream_usable_partial_outputs,
        upstream_primary_issue=result.upstream_primary_issue,
        upstream_recommended_next_step=result.upstream_recommended_next_step,
        upstream_recovery_report_json=result.upstream_recovery_report_json,
        upstream_recovery_report_md=result.upstream_recovery_report_md,
        recovery_actions=[action.model_dump(mode="json") for action in result.recovery_actions],
        output_size_mb=_dir_size_mb(output_dir),
        summary_path=result.summary_path,
        report_path=result.report_path,
    )


def _write_csv(path: Path, result: MiniE2EBatchResult) -> None:
    fieldnames = [
        "agent_run_dir",
        "run_name",
        "repository",
        "project_accession",
        "source_file",
        "input_size_mb",
        "instrument",
        "fragmentation",
        "species_policy",
        "canonical_species",
        "organism_taxon_id",
        "acquisition_mode",
        "ptm_type",
        "ptm_subtype",
        "ptm_evidence_terms",
        "ptm_enrichment_methods",
        "semantic_metadata_confidence",
        "semantic_interpretation_trace",
        "modification_scope",
        "labeling_strategy",
        "instrument_families",
        "fragmentation_methods",
        "diversity_tags",
        "metadata_quality",
        "full_status",
        "sample_class",
        "status",
        "ai_ready_outcome",
        "usable_partial_outputs",
        "task_statuses",
        "rows_out",
        "task_files",
        "blockers",
        "warnings",
        "recovery_status",
        "primary_issue",
        "recommended_next_step",
        "recovery_report_json",
        "recovery_report_md",
        "upstream_recovery_status",
        "upstream_workflow_outcome",
        "upstream_usable_partial_outputs",
        "upstream_primary_issue",
        "upstream_recommended_next_step",
        "upstream_recovery_report_json",
        "upstream_recovery_report_md",
        "recovery_actions",
        "output_size_mb",
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


def _markdown_report(result: MiniE2EBatchResult) -> str:
    lines = [
        "# Mini E2E Batch AI-ready Validation Report",
        "",
        f"- Status: `{result.status}`",
        f"- Runs: {len(result.run_results)}",
        f"- Status counts: `{json.dumps(result.status_counts, ensure_ascii=False, sort_keys=True)}`",
        f"- AI-ready outcome counts: `{json.dumps(result.ai_ready_outcome_counts, ensure_ascii=False, sort_keys=True)}`",
        f"- Recovery issue counts: `{json.dumps(result.recovery_issue_counts, ensure_ascii=False, sort_keys=True)}`",
        f"- Upstream recovery issue counts: `{json.dumps(result.upstream_recovery_issue_counts, ensure_ascii=False, sort_keys=True)}`",
        f"- Total output size MB: {result.total_output_size_mb}",
        "",
        "## Runs",
        "",
    ]
    for item in result.run_results:
        lines.extend(
            [
                f"### {Path(item.agent_run_dir).name}",
                "",
                f"- Status: `{item.status}`",
                f"- Full status: `{item.full_status}`",
                f"- Sample class: `{item.sample_class}`",
                f"- Project: `{item.project_accession or 'unknown'}`",
                f"- Repository: `{item.repository}`",
                f"- Source file: `{item.source_file or 'unknown'}`",
                f"- Metadata quality: `{item.metadata_quality}`",
                f"- AI-ready outcome: `{item.ai_ready_outcome or 'unknown'}`",
                f"- Usable partial outputs: `{item.usable_partial_outputs}`",
                f"- Output size MB: {item.output_size_mb}",
                f"- Task statuses: `{json.dumps(item.task_statuses, ensure_ascii=False, sort_keys=True)}`",
                f"- Rows out: `{json.dumps(item.rows_out, ensure_ascii=False, sort_keys=True)}`",
                f"- Blockers: {', '.join(item.blockers) if item.blockers else 'None'}",
                f"- Warnings: {', '.join(item.warnings) if item.warnings else 'None'}",
                f"- Recovery status: `{item.recovery_status or 'not_run'}`",
                f"- Primary issue: `{item.primary_issue or 'none'}`",
                f"- Recommended next step: {item.recommended_next_step or 'None'}",
                f"- Recovery report: `{item.recovery_report_md or item.recovery_report_json or ''}`",
                f"- Upstream recovery status: `{item.upstream_recovery_status or 'not_run'}`",
                f"- Upstream workflow outcome: `{item.upstream_workflow_outcome or 'unknown'}`",
                f"- Upstream usable partial outputs: `{item.upstream_usable_partial_outputs}`",
                f"- Upstream primary issue: `{item.upstream_primary_issue or 'none'}`",
                f"- Upstream recommended next step: {item.upstream_recommended_next_step or 'None'}",
                f"- Upstream recovery report: `{item.upstream_recovery_report_md or item.upstream_recovery_report_json or ''}`",
                f"- Recovery actions: `{json.dumps(item.recovery_actions, ensure_ascii=False, sort_keys=True)}`",
                "",
            ]
        )
    return "\n".join(lines)


def _write_benchmark_outputs(
    result: MiniE2EBatchResult,
    *,
    sample_manifest_json_path: Path,
    sample_manifest_csv_path: Path,
    summary_json_path: Path,
    summary_csv_path: Path,
    report_path: Path,
    failure_taxonomy_path: Path,
) -> None:
    samples = [_benchmark_sample_row(item) for item in result.run_results]
    sample_class_counts = _status_counts([str(item.get("sample_class") or "unknown") for item in samples])
    full_status_counts = _status_counts([str(item.get("full_status") or "unknown") for item in samples])
    task_rows_total: dict[str, int] = {}
    task_rows_by_repository: dict[str, dict[str, int]] = {}
    for item in result.run_results:
        repository = item.repository or "unknown"
        for task_type, rows in item.rows_out.items():
            row_count = int(rows or 0)
            task_rows_total[task_type] = task_rows_total.get(task_type, 0) + row_count
            task_rows_by_repository.setdefault(repository, {})
            task_rows_by_repository[repository][task_type] = task_rows_by_repository[repository].get(task_type, 0) + row_count
    repository_counts = _status_counts([item.repository or "unknown" for item in result.run_results])
    distinct_projects = sorted({
        str(item.project_accession).strip()
        for item in result.run_results
        if str(item.project_accession or "").strip()
    })
    distinct_source_files = sorted({
        f"{item.project_accession or 'unknown'}::{item.source_file or item.run_name or item.agent_run_dir}"
        for item in result.run_results
        if str(item.source_file or item.run_name or item.agent_run_dir or "").strip()
    })
    distinct_instruments = sorted({
        str(item.instrument).strip()
        for item in result.run_results
        if str(item.instrument or "").strip() and str(item.instrument).strip().casefold() != "unknown"
    })
    distinct_fragmentations = sorted({
        str(item.fragmentation).strip()
        for item in result.run_results
        if str(item.fragmentation or "").strip() and str(item.fragmentation).strip().casefold() != "unknown"
    })
    blocked_reason_by_repository = _blocked_reason_by_repository(result.run_results)
    acceptance = {
        "run_count_between_3_and_5": 3 <= len(result.run_results) <= 5,
        "has_three_distinct_projects": len(distinct_projects) >= 3,
        "has_three_distinct_source_files": len(distinct_source_files) >= 3,
        "has_clean_full_completed": any(item.sample_class == "clean_full_completed" for item in result.run_results),
        "has_partial_output_recovery": any(item.sample_class == "partial_output_recovery" for item in result.run_results),
        "has_blocked_or_review_case": any(item.sample_class == "blocked_or_review_case" for item in result.run_results),
    }
    acceptance["benchmark_complete"] = bool(
        acceptance["run_count_between_3_and_5"]
        and acceptance["has_three_distinct_projects"]
        and acceptance["has_three_distinct_source_files"]
        and acceptance["has_clean_full_completed"]
        and acceptance["has_partial_output_recovery"]
        and acceptance["has_blocked_or_review_case"]
    )
    acceptance["recipe_preview_available"] = bool(
        any(any(int(rows or 0) > 0 for rows in item.rows_out.values()) for item in result.run_results)
    )
    summary = {
        "status": "benchmark_complete"
        if acceptance["benchmark_complete"]
        else ("recipe_preview_available" if acceptance["recipe_preview_available"] else "needs_more_samples"),
        "run_count": len(result.run_results),
        "sample_class_counts": sample_class_counts,
        "full_status_counts": full_status_counts,
        "ai_ready_outcome_counts": result.ai_ready_outcome_counts,
        "repository_counts": repository_counts,
        "diversity_summary": {
            "distinct_project_count": len(distinct_projects),
            "distinct_projects": distinct_projects,
            "distinct_source_file_count": len(distinct_source_files),
            "distinct_source_files": distinct_source_files,
            "distinct_instrument_count": len(distinct_instruments),
            "distinct_instruments": distinct_instruments,
            "distinct_fragmentation_count": len(distinct_fragmentations),
            "distinct_fragmentations": distinct_fragmentations,
        },
        "task_status_counts": result.task_status_counts,
        "task_rows_total": dict(sorted(task_rows_total.items())),
        "task_rows_by_repository": {
            repository: dict(sorted(rows.items()))
            for repository, rows in sorted(task_rows_by_repository.items())
        },
        "blocked_reason_by_repository": blocked_reason_by_repository,
        "total_output_size_mb": result.total_output_size_mb,
        "acceptance": acceptance,
        "files": {
            "mini_e2e_batch_summary_json": result.summary_path,
            "mini_e2e_batch_summary_csv": result.csv_path,
            "mini_e2e_batch_report_md": result.report_path,
            "benchmark_sample_manifest_json": str(sample_manifest_json_path),
            "benchmark_sample_manifest_csv": str(sample_manifest_csv_path),
            "benchmark_summary_json": str(summary_json_path),
            "benchmark_summary_csv": str(summary_csv_path),
            "benchmark_report_md": str(report_path),
            "benchmark_failure_taxonomy_json": str(failure_taxonomy_path),
        },
    }
    failure_taxonomy = _failure_taxonomy(result)
    write_json(sample_manifest_json_path, {"samples": samples, "summary": summary})
    _write_dict_rows_csv(sample_manifest_csv_path, samples)
    write_json(summary_json_path, summary)
    _write_dict_rows_csv(
        summary_csv_path,
        [
            {
                "status": summary["status"],
                "run_count": summary["run_count"],
                "sample_class_counts": sample_class_counts,
                "full_status_counts": full_status_counts,
                "ai_ready_outcome_counts": result.ai_ready_outcome_counts,
                "repository_counts": repository_counts,
                "diversity_summary": summary["diversity_summary"],
                "task_status_counts": result.task_status_counts,
                "task_rows_total": summary["task_rows_total"],
                "task_rows_by_repository": summary["task_rows_by_repository"],
                "blocked_reason_by_repository": blocked_reason_by_repository,
                "acceptance": acceptance,
                "total_output_size_mb": result.total_output_size_mb,
            }
        ],
    )
    write_json(failure_taxonomy_path, failure_taxonomy)
    report_path.write_text(_benchmark_markdown_report(summary, samples, failure_taxonomy), encoding="utf-8")


def _benchmark_sample_row(item: MiniE2EBatchRun) -> dict[str, Any]:
    return {
        "run_name": item.run_name,
        "agent_run_dir": item.agent_run_dir,
        "output_dir": item.output_dir,
        "repository": item.repository,
        "project_accession": item.project_accession,
        "source_file": item.source_file,
        "input_size_mb": item.input_size_mb,
        "instrument": item.instrument,
        "fragmentation": item.fragmentation,
        "species_policy": item.species_policy,
        "canonical_species": item.canonical_species,
        "organism_taxon_id": item.organism_taxon_id,
        "acquisition_mode": item.acquisition_mode,
        "ptm_type": item.ptm_type,
        "ptm_subtype": item.ptm_subtype,
        "ptm_evidence_terms": item.ptm_evidence_terms,
        "ptm_enrichment_methods": item.ptm_enrichment_methods,
        "semantic_metadata_confidence": item.semantic_metadata_confidence,
        "semantic_interpretation_trace": item.semantic_interpretation_trace,
        "modification_scope": item.modification_scope,
        "labeling_strategy": item.labeling_strategy,
        "instrument_families": item.instrument_families,
        "fragmentation_methods": item.fragmentation_methods,
        "diversity_tags": item.diversity_tags,
        "metadata_quality": item.metadata_quality,
        "full_status": item.full_status,
        "sample_class": item.sample_class,
        "ai_ready_outcome": item.ai_ready_outcome,
        "usable_partial_outputs": item.usable_partial_outputs,
        "task_statuses": item.task_statuses,
        "rows_out": item.rows_out,
        "blockers": item.blockers,
        "warnings": item.warnings,
        "primary_issue": item.primary_issue,
        "recommended_next_step": item.recommended_next_step,
        "upstream_primary_issue": item.upstream_primary_issue,
        "upstream_recommended_next_step": item.upstream_recommended_next_step,
        "recovery_actions": item.recovery_actions,
        "output_size_mb": item.output_size_mb,
    }


def _failure_taxonomy(result: MiniE2EBatchResult) -> dict[str, Any]:
    issue_counts: dict[str, int] = {}
    issue_samples: dict[str, list[str]] = {}
    for item in result.run_results:
        labels = _dedupe(
            [
                item.primary_issue or "",
                item.upstream_primary_issue or "",
                *item.blockers,
                *[
                    blocker
                    for action in item.recovery_actions
                    for blocker in action.get("blockers", [])
                    if isinstance(action, dict)
                ],
            ]
        )
        if not labels and item.sample_class not in {"clean_full_completed", "partial_output_recovery"}:
            labels = ["unknown_failure"]
        for label in labels:
            issue_counts[label] = issue_counts.get(label, 0) + 1
            issue_samples.setdefault(label, []).append(item.run_name or Path(item.agent_run_dir).name)
    recommendations = {
        "missing_peaklist": "generate MGF from MSDT/rawspectrum and rerun AI-ready Build",
        "resource_oom": "prepare bounded retry config; do not auto-rerun full without approval",
        "download_slow_or_failed": "choose a smaller cached mzML/mzXML candidate",
        "input_too_large": "skip oversized candidate or require explicit allow-large-input",
        "spectrum_mismatch": "review MGF TITLE/SCANS/nativeID matching strategies",
        "review_gate_blocked": "require file-level metadata evidence or choose another candidate",
        "conversion_failed": "prefer mzML/mzXML mainline; keep RAW conversion as compatibility work",
        "msdt_feature_missing": (
            "use task-specific partial AI-ready export from FragPipe outputs; clean MSDT retry needs "
            "an MSBooster-compatible workflow/config"
        ),
        "zero_psm": "treat as weak/blocked training candidate and choose cleaner sample",
    }
    return {
        "issue_counts": dict(sorted(issue_counts.items())),
        "issue_samples": {key: sorted(values) for key, values in sorted(issue_samples.items())},
        "recovery_recommendations": recommendations,
    }


def _benchmark_markdown_report(
    summary: dict[str, Any],
    samples: list[dict[str, Any]],
    failure_taxonomy: dict[str, Any],
) -> str:
    lines = [
        "# AI-ready Real Benchmark Report",
        "",
        f"- Status: `{summary['status']}`",
        f"- Runs: {summary['run_count']}",
        f"- Sample classes: `{json.dumps(summary['sample_class_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"- Full statuses: `{json.dumps(summary['full_status_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"- Repositories: `{json.dumps(summary.get('repository_counts') or {}, ensure_ascii=False, sort_keys=True)}`",
        f"- Distinct projects/source files: {summary.get('diversity_summary', {}).get('distinct_project_count', 0)} / {summary.get('diversity_summary', {}).get('distinct_source_file_count', 0)}",
        f"- Distinct instruments/fragmentation: {summary.get('diversity_summary', {}).get('distinct_instrument_count', 0)} / {summary.get('diversity_summary', {}).get('distinct_fragmentation_count', 0)}",
        f"- Task rows total: `{json.dumps(summary['task_rows_total'], ensure_ascii=False, sort_keys=True)}`",
        f"- Task rows by repository: `{json.dumps(summary.get('task_rows_by_repository') or {}, ensure_ascii=False, sort_keys=True)}`",
        f"- Acceptance: `{json.dumps(summary['acceptance'], ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Samples",
        "",
    ]
    for item in samples:
        lines.extend(
            [
                f"### {item.get('run_name') or Path(str(item.get('agent_run_dir') or '')).name}",
                "",
                f"- Class: `{item.get('sample_class')}`",
                f"- Full status: `{item.get('full_status')}`",
                f"- AI-ready outcome: `{item.get('ai_ready_outcome') or 'unknown'}`",
                f"- Project/file: `{item.get('project_accession') or 'unknown'}` / `{item.get('source_file') or 'unknown'}`",
                f"- Repository: `{item.get('repository') or 'unknown'}`",
                f"- Metadata quality: `{item.get('metadata_quality')}`",
                f"- Rows out: `{json.dumps(item.get('rows_out') or {}, ensure_ascii=False, sort_keys=True)}`",
                f"- Blockers: {', '.join(item.get('blockers') or []) if item.get('blockers') else 'None'}",
                f"- Recovery: {item.get('recommended_next_step') or item.get('upstream_recommended_next_step') or 'None'}",
                "",
            ]
        )
    lines.extend(
        [
            "## Failure Taxonomy",
            "",
            f"- Issue counts: `{json.dumps(failure_taxonomy.get('issue_counts') or {}, ensure_ascii=False, sort_keys=True)}`",
            "",
        ]
    )
    return "\n".join(lines)


def _write_dict_rows_csv(path: Path, rows: list[dict[str, Any]]) -> None:
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


def _run_metadata(run_dir: Path) -> dict[str, Any]:
    payloads = [
        _read_json(run_dir / "task_state.json"),
        _read_json(run_dir / "run_manifest.json"),
        _read_json(run_dir / "metadata.json"),
        _read_json(run_dir / "attributes.json"),
        _read_json(run_dir / "project_resolution.json"),
    ]
    metadata_text = _metadata_text(payloads)
    project_accession = _first_text(payloads, ["project_accession", "project", "px_accession"])
    repository = _first_text(payloads, ["repository"])
    source_file = _first_text(payloads, ["source_file", "file_name", "source_data_path", "input_value"])
    instrument = _first_nested_text(payloads, ["instrument", "instrument_name", "instrument_family"])
    fragmentation = _first_nested_text(payloads, ["fragmentation", "fragmentation_method", "activation"])
    acquisition_mode = _normalize_acquisition_mode(
        _first_nested_text(payloads, ["acquisition_mode", "acquisition", "data_family"])
        or metadata_text
    )
    species_values = _dedupe([
        *_list_text_values(_find_key({"payloads": payloads}, ["species", "organisms", "organism"])),
        *species_from_text(metadata_text)[0],
    ])
    canonical_species, organism_taxon_id = normalize_species_values(species_values)
    labeling_raw = (
        _first_nested_text(payloads, ["labeling_strategy", "labeling", "quantification_strategy", "quantification"])
        or labeling_from_text(metadata_text)
    )
    labeling_strategy = normalize_labeling_strategy(labeling_raw)
    ptm_semantic = interpret_ptm_metadata(metadata_text)
    ptm_type = ptm_semantic.canonical if ptm_semantic.confidence > 0 else None
    fragmentation_methods = _fragmentation_methods(metadata_text, fragmentation)
    instrument_families = _instrument_families(metadata_text, instrument)
    input_size_mb = _input_size_mb(run_dir, source_file)
    metadata_quality = "unknown"
    if (run_dir / "metadata.json").exists() and (run_dir / "attributes.json").exists():
        metadata_quality = "available"
    elif any(payloads):
        metadata_quality = "minimal"
    diversity_tags = _diversity_tags(
        canonical_species=canonical_species,
        instrument_families=instrument_families,
        fragmentation_methods=fragmentation_methods,
        ptm_type=ptm_type,
        labeling_strategy=labeling_strategy,
    )
    return {
        "project_accession": project_accession,
        "repository": _normalize_repository(repository, project_accession),
        "source_file": Path(source_file).name if source_file else None,
        "input_size_mb": input_size_mb,
        "instrument": instrument,
        "fragmentation": fragmentation or (fragmentation_methods[0] if fragmentation_methods else None),
        "species_policy": "open",
        "canonical_species": canonical_species,
        "organism_taxon_id": organism_taxon_id,
        "acquisition_mode": acquisition_mode,
        "ptm_type": ptm_type,
        "ptm_subtype": ";".join(ptm_semantic.subtypes),
        "ptm_evidence_terms": list(ptm_semantic.evidence_terms),
        "ptm_enrichment_methods": list(ptm_semantic.enrichment_methods),
        "semantic_metadata_confidence": ptm_semantic.confidence,
        "semantic_interpretation_trace": list(ptm_semantic.trace),
        "modification_scope": ptm_type,
        "labeling_strategy": labeling_strategy,
        "instrument_families": instrument_families,
        "fragmentation_methods": fragmentation_methods,
        "diversity_tags": diversity_tags,
        "metadata_quality": metadata_quality,
    }


def _metadata_text(value: Any, *, limit: int = 200_000) -> str:
    parts: list[str] = []

    def visit(item: Any) -> None:
        if len(" ".join(parts)) > limit:
            return
        if item is None:
            return
        if isinstance(item, str):
            text = item.strip()
            if text:
                parts.append(text)
            return
        if isinstance(item, (int, float, bool)):
            parts.append(str(item))
            return
        if isinstance(item, dict):
            for nested in item.values():
                visit(nested)
            return
        if isinstance(item, list):
            for nested in item:
                visit(nested)
            return
        parts.append(str(item))

    visit(value)
    text = " ".join(parts)
    return text[:limit]


def _list_text_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        if "value" in value:
            return _list_text_values(value.get("value"))
        result: list[str] = []
        for nested in value.values():
            result.extend(_list_text_values(nested))
        return _dedupe(result)
    if isinstance(value, list):
        result: list[str] = []
        for nested in value:
            result.extend(_list_text_values(nested))
        return _dedupe(result)
    text = str(value or "").strip()
    return [text] if text else []


def _normalize_acquisition_mode(value: str | None) -> str | None:
    text = str(value or "").casefold()
    if not text:
        return None
    if "data-dependent" in text or re.search(r"(?<![a-z])dda(?![a-z])", text):
        return "dda"
    if "data-independent" in text or re.search(r"(?<![a-z])dia(?![a-z])", text):
        return "dia"
    if re.search(r"(?<![a-z])prm(?![a-z])", text):
        return "prm"
    if re.search(r"(?<![a-z])srm(?![a-z])|(?<![a-z])mrm(?![a-z])", text):
        return "srm"
    return None


def _fragmentation_methods(text: str, fallback: str | None = None) -> list[str]:
    patterns = [
        ("EThcD", r"\bethcd\b|electron\s+transfer/higher[-\s]*energy"),
        ("ETD", r"\betd\b|electron\s+transfer\s+dissociation"),
        ("HCD", r"\bhcd\b|higher[-\s]*energy\s+(?:collisional|collision[-\s]*induced|collision)"),
        ("CID", r"\bcid\b|collision[-\s]*induced"),
        ("ECD", r"\becd\b|electron\s+capture\s+dissociation"),
        ("UVPD", r"\buvpd\b|ultraviolet\s+photodissociation"),
    ]
    methods = [label for label, pattern in patterns if re.search(pattern, text or "", flags=re.IGNORECASE)]
    if fallback:
        folded = fallback.casefold()
        for label, _pattern in patterns:
            if label.casefold() in folded:
                methods.append(label)
    return _dedupe(methods)


def _instrument_families(text: str, instrument: str | None = None) -> list[str]:
    candidates = [instrument or "", text or ""]
    families: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        try:
            inferred = infer_instrument_family_from_name(candidate)
        except Exception:
            inferred = None
        if inferred and inferred.casefold() != "unknown":
            families.append(inferred)
    folded = (text or "").casefold()
    if "orbitrap" in folded or "q exactive" in folded or "fusion lumos" in folded:
        families.append("orbitrap")
    if "tof" in folded or "q-tof" in folded or "qtof" in folded:
        families.append("qtof")
    if "tims" in folded or "tims-tof" in folded or "timstof" in folded:
        families.append("timstof")
    return _dedupe(families)


def _diversity_tags(
    *,
    canonical_species: list[str],
    instrument_families: list[str],
    fragmentation_methods: list[str],
    ptm_type: str | None,
    labeling_strategy: str | None,
) -> list[str]:
    tags: list[str] = []
    tags.extend(f"species:{value}" for value in canonical_species)
    tags.extend(f"instrument:{value}" for value in instrument_families)
    tags.extend(f"fragmentation:{value}" for value in fragmentation_methods)
    if ptm_type:
        tags.append(f"ptm:{ptm_type}")
    if labeling_strategy:
        tags.append(f"labeling:{labeling_strategy}")
    return _dedupe(tags)


def _sample_class(result: MiniE2EResult) -> str:
    issues = " ".join(
        [
            result.primary_issue or "",
            result.upstream_primary_issue or "",
            " ".join(result.blockers),
            " ".join(result.warnings),
        ]
    ).casefold()
    if result.status == "blocked":
        return "blocked_or_review_case"
    if (
        result.upstream_usable_partial_outputs
        or result.usable_partial_outputs
        or result.ai_ready_outcome == "completed_from_usable_partial_outputs"
        or result.upstream_workflow_outcome == "failed_with_usable_partial_outputs"
    ):
        return "partial_output_recovery"
    if (
        result.status == "completed"
        and result.upstream_workflow_outcome == "completed"
        and not result.upstream_usable_partial_outputs
    ):
        return "clean_full_completed"
    if result.status == "completed" and result.ai_ready_outcome in {None, "completed", "completed_from_clean_or_existing_outputs"}:
        return "clean_full_completed"
    if any(
        marker in issues
        for marker in ["review_gate", "conversion_failed", "blocked", "missing", "zero_psm", "download"]
    ):
        return "blocked_or_review_case"
    if result.status == "completed":
        return "completed"
    return "failed"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _first_text(payloads: list[dict[str, Any]], keys: list[str]) -> str | None:
    for payload in payloads:
        value = _find_key(payload, keys)
        if value is not None and str(value).strip():
            return str(value)
    return None


def _first_nested_text(payloads: list[dict[str, Any]], keys: list[str]) -> str | None:
    for payload in payloads:
        value = _find_key(payload, keys)
        if isinstance(value, dict):
            for key in ["value", "name", "label"]:
                if value.get(key):
                    return str(value[key])
        elif value is not None and str(value).strip():
            return str(value)
    return None


def _find_key(value: Any, keys: list[str]) -> Any:
    if isinstance(value, dict):
        lowered = {str(key).casefold(): key for key in value}
        for key in keys:
            original = lowered.get(key.casefold())
            if original is not None:
                return value[original]
        for nested in value.values():
            found = _find_key(nested, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_key(item, keys)
            if found is not None:
                return found
    return None


def _input_size_mb(run_dir: Path, source_file: str | None) -> float | None:
    if source_file:
        candidates = [Path(source_file)]
        candidates.extend(run_dir.rglob(Path(source_file).name))
    else:
        candidates = []
    candidates.extend(run_dir.glob("*.mzML"))
    candidates.extend(run_dir.glob("*.mzXML"))
    candidates.extend(run_dir.glob("*.raw"))
    for path in candidates:
        try:
            if path.exists() and path.is_file():
                return round(path.stat().st_size / 1024 / 1024, 3)
        except OSError:
            continue
    return None


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


def _status_counts(values: list[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items()))


def _task_status_counts(runs: list[MiniE2EBatchRun]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for run in runs:
        for task_type, status in run.task_statuses.items():
            counts.setdefault(task_type, {})
            counts[task_type][status] = counts[task_type].get(status, 0) + 1
    return {task: dict(sorted(statuses.items())) for task, statuses in sorted(counts.items())}


def _blocked_reason_by_repository(runs: list[MiniE2EBatchRun]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for run in runs:
        repository = run.repository or "unknown"
        reasons = _dedupe(
            [
                run.primary_issue or "",
                run.upstream_primary_issue or "",
                *run.blockers,
            ]
        )
        if not reasons and run.status not in {"completed"}:
            reasons = [run.status or "unknown"]
        for reason in reasons:
            result.setdefault(repository, {})
            result[repository][reason] = result[repository].get(reason, 0) + 1
    return {repository: dict(sorted(reasons.items())) for repository, reasons in sorted(result.items())}


def _normalize_repository(repository: str | None, project_accession: str | None = None) -> str:
    value = str(repository or "").strip().lower().replace("-", "_")
    if value in {"pride", "massive", "iprox"}:
        return value
    project = str(project_accession or "").upper()
    if project.startswith("MSV"):
        return "massive"
    if project.startswith("IPX"):
        return "iprox"
    if project.startswith("PXD"):
        return "pride"
    return "unknown"


def _dir_size_mb(path: Path) -> float:
    total = 0
    if path.exists():
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except OSError:
                    continue
    return round(total / 1024 / 1024, 3)


def _safe_stem(value: str) -> str:
    text = "".join(char if char.isalnum() or char in "._-" else "_" for char in str(value or "").strip())
    return text.strip("._-") or "agent_run"
