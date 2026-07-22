from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from agent.discovery.models import DatasetManifest, DiscoveredFile, DiscoveryEvidence
from agent.discovery.task_profiles import active_task_types
from agent.discovery.task_readiness import annotate_manifest_task_readiness, task_ready_files
from agent.utils import write_json


MANIFEST_COLUMNS = [
    "run_id",
    "harvest_run_id",
    "ms_run_id",
    "repository",
    "project_accession",
    "native_accession",
    "px_accession",
    "file_accession_or_path",
    "project_title",
    "file_name",
    "download_url",
    "transfer_method",
    "file_type",
    "file_role",
    "file_role_reasons",
    "sdrf_match_status",
    "evidence_level",
    "file_level_evidence_count",
    "project_level_evidence_count",
    "evidence_warnings",
    "expected_size_bytes",
    "species",
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
    "immunopeptide_scope",
    "hla_class",
    "hla_alleles",
    "immunopeptide_evidence_terms",
    "immunopeptide_enrichment_methods",
    "immunopeptide_metadata_confidence",
    "labeling_strategy",
    "final_grade",
    "judgment_status",
    "hard_gate",
    "judgment_confidence",
    "judgment_decision",
    "judgment_next_action",
    "judgment_evidence_stage",
    "judgment_explanation",
    "judgment_evidence_refs",
    "judgment_constraint_assessments",
    "judgment_limitations",
    "judgment_rubric_version",
    "retrieval_project_score",
    "retrieval_file_score",
    "retrieval_confidence",
    "retrieval_trust_score",
    "evidence_completeness",
    "memory_prior",
    "memory_feedback",
    "validity_status",
    "validity_reasons",
    "needs_review",
    "task_type",
    "task_profile",
    "task_readiness_status",
    "task_readiness_reasons",
    "missing_task_requirements",
    "task_ai_readiness_score",
    "task_ai_readiness_band",
    "task_ai_readiness_reasons",
    "task_ai_readiness_warnings",
    "task_ai_readiness_dimensions",
    "data_value_score",
    "data_value_action",
    "data_value_components",
    "data_value_reasons",
    "label_source_status",
    "spectra_requirement_status",
    "metadata_requirement_status",
    "next_pipeline_steps",
    "ai_ready_target_schema",
    "instrument_names",
    "instrument_families",
    "instrument_generation_score",
    "instrument_generation_label",
    "fragmentation_methods",
    "lc_gradient",
    "lc_gradient_minutes",
    "diversity_tags",
    "review_decision",
    "review_reason",
    "review_note",
    "evidence_count",
    "evidence_preview",
]


def _evidence_payload(evidence: list[DiscoveryEvidence]) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in evidence]


def _join_values(values: list[str]) -> str:
    return ";".join(str(value) for value in values if str(value).strip())


def _ms_run_id(file: DiscoveredFile) -> str:
    name = str(file.file_name or "").strip()
    if not name:
        return str(file.file_accession_or_path or "")
    # Collapse representation variants (raw/mgf/mzml) into one MS run stem.
    stem = Path(name).stem
    lower = stem.casefold()
    for suffix in (".raw", ".mzml", ".mgf", ".d"):
        if lower.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return f"{file.project_accession}:{stem}"


def _judgment_for_file(file: DiscoveredFile, judgments: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    if not judgments:
        return {}
    return judgments.get(str(file.project_accession or "").upper()) or judgments.get(
        str(file.project_accession or "")
    ) or {}


def _csv_row(
    file: DiscoveredFile,
    run_id: str | None,
    *,
    judgments: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    judgment = _judgment_for_file(file, judgments)
    evidence_payload = _evidence_payload(file.evidence)
    return {
        "run_id": run_id or "",
        "harvest_run_id": run_id or "",
        "ms_run_id": _ms_run_id(file),
        "repository": file.repository,
        "project_accession": file.project_accession,
        "native_accession": file.native_accession or "",
        "px_accession": file.px_accession or "",
        "file_accession_or_path": file.file_accession_or_path or file.raw_record.get("file_accession_or_path") or file.raw_record.get("accession") or "",
        "project_title": file.project_title or "",
        "file_name": file.file_name,
        "download_url": file.download_url or "",
        "transfer_method": file.transfer_method or "",
        "file_type": file.file_type,
        "file_role": file.file_role,
        "file_role_reasons": _join_values(file.file_role_reasons),
        "sdrf_match_status": file.sdrf_match_status,
        "evidence_level": file.evidence_level,
        "file_level_evidence_count": file.file_level_evidence_count,
        "project_level_evidence_count": file.project_level_evidence_count,
        "evidence_warnings": _join_values(file.evidence_warnings),
        "expected_size_bytes": file.expected_size_bytes if file.expected_size_bytes is not None else "",
        "species": ";".join(file.species),
        "species_policy": file.species_policy,
        "canonical_species": ";".join(file.canonical_species),
        "organism_taxon_id": ";".join(file.organism_taxon_id),
        "acquisition_mode": file.acquisition_mode or "",
        "ptm_type": file.ptm_type or "",
        "ptm_subtype": file.ptm_subtype or "",
        "ptm_evidence_terms": _join_values(file.ptm_evidence_terms),
        "ptm_enrichment_methods": _join_values(file.ptm_enrichment_methods),
        "semantic_metadata_confidence": file.semantic_metadata_confidence,
        "semantic_interpretation_trace": _join_values(file.semantic_interpretation_trace),
        "modification_scope": file.modification_scope or "",
        "immunopeptide_scope": file.immunopeptide_scope or "",
        "hla_class": _join_values(file.hla_class),
        "hla_alleles": _join_values(file.hla_alleles),
        "immunopeptide_evidence_terms": _join_values(file.immunopeptide_evidence_terms),
        "immunopeptide_enrichment_methods": _join_values(file.immunopeptide_enrichment_methods),
        "immunopeptide_metadata_confidence": file.immunopeptide_metadata_confidence,
        "labeling_strategy": file.labeling_strategy or "",
        "final_grade": judgment.get("grade", ""),
        "judgment_status": judgment.get("status", ""),
        "hard_gate": judgment.get("hard_gate", ""),
        "judgment_confidence": judgment.get("confidence", ""),
        "judgment_decision": judgment.get("decision", ""),
        "judgment_next_action": judgment.get("next_action", ""),
        "judgment_evidence_stage": judgment.get("evidence_stage", ""),
        "judgment_explanation": judgment.get("explanation", ""),
        "judgment_evidence_refs": _join_values(judgment.get("evidence_refs") or []),
        "judgment_constraint_assessments": json.dumps(
            judgment.get("constraint_assessments") or [],
            ensure_ascii=False,
            sort_keys=True,
        ),
        "judgment_limitations": _join_values(judgment.get("limitations") or []),
        "judgment_rubric_version": judgment.get("rubric_version", ""),
        "retrieval_project_score": file.project_score,
        "retrieval_file_score": file.file_score,
        "retrieval_confidence": file.confidence,
        "retrieval_trust_score": file.trust_score,
        "evidence_completeness": file.evidence_completeness,
        "memory_prior": file.memory_prior,
        "memory_feedback": json.dumps(file.memory_feedback, ensure_ascii=False, sort_keys=True),
        "validity_status": file.validity_status,
        "validity_reasons": _join_values(file.validity_reasons),
        "needs_review": file.needs_review,
        "task_type": file.task_type or "",
        "task_profile": file.task_profile or "",
        "task_readiness_status": file.task_readiness_status or "",
        "task_readiness_reasons": _join_values(file.task_readiness_reasons),
        "missing_task_requirements": _join_values(file.missing_task_requirements),
        "task_ai_readiness_score": file.task_ai_readiness_score if file.task_ai_readiness_score is not None else "",
        "task_ai_readiness_band": file.task_ai_readiness_band or "",
        "task_ai_readiness_reasons": _join_values(file.task_ai_readiness_reasons),
        "task_ai_readiness_warnings": _join_values(file.task_ai_readiness_warnings),
        "task_ai_readiness_dimensions": json.dumps(file.task_ai_readiness_dimensions, ensure_ascii=False, sort_keys=True),
        "data_value_score": file.data_value_score if file.data_value_score is not None else "",
        "data_value_action": file.data_value_action or "",
        "data_value_components": json.dumps(file.data_value_components, ensure_ascii=False, sort_keys=True),
        "data_value_reasons": _join_values(file.data_value_reasons),
        "label_source_status": file.label_source_status or "",
        "spectra_requirement_status": file.spectra_requirement_status or "",
        "metadata_requirement_status": file.metadata_requirement_status or "",
        "next_pipeline_steps": _join_values(file.next_pipeline_steps),
        "ai_ready_target_schema": file.ai_ready_target_schema or "",
        "instrument_names": _join_values(file.instrument_names),
        "instrument_families": _join_values(file.instrument_families),
        "instrument_generation_score": (
            file.instrument_generation_score
            if file.instrument_generation_score is not None
            else ""
        ),
        "instrument_generation_label": file.instrument_generation_label or "",
        "fragmentation_methods": _join_values(file.fragmentation_methods),
        "lc_gradient": file.lc_gradient or "",
        "lc_gradient_minutes": file.lc_gradient_minutes if file.lc_gradient_minutes is not None else "",
        "diversity_tags": _join_values(file.diversity_tags),
        "review_decision": file.review_decision or "",
        "review_reason": file.review_reason or "",
        "review_note": file.review_note or "",
        "evidence_count": len(evidence_payload),
        "evidence_preview": json.dumps(evidence_payload[:3], ensure_ascii=False),
    }


def _write_manifest_csv(
    path: Path,
    files: list[DiscoveredFile],
    run_id: str | None,
    *,
    judgments: dict[str, dict[str, Any]] | None = None,
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        for file in files:
            writer.writerow(_csv_row(file, run_id, judgments=judgments))


def _write_batch_inputs(path: Path, files: list[DiscoveredFile]) -> None:
    seen_names: set[str] = set()
    batch_lines: list[str] = []
    for file in files:
        if file.file_name in seen_names:
            continue
        seen_names.add(file.file_name)
        batch_lines.append(file.file_name)
    path.write_text("\n".join(batch_lines) + ("\n" if batch_lines else ""), encoding="utf-8")


def _files_with_statuses(files: list[DiscoveredFile], statuses: set[str]) -> list[DiscoveredFile]:
    return [file for file in files if file.validity_status in statuses]


def _count_values(files: list[DiscoveredFile], field_name: str) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for file in files:
        values = getattr(file, field_name)
        if isinstance(values, list):
            counter.update(value or "unknown" for value in values)
        elif values:
            counter[str(values)] += 1
        else:
            counter["unknown"] += 1
    return dict(counter)


def build_quality_report(manifest: DatasetManifest) -> dict[str, Any]:
    status_counts = Counter(file.validity_status for file in manifest.files)
    reason_counts: Counter[str] = Counter()
    for file in manifest.files:
        reason_counts.update(file.validity_reasons)
    valid_files = _files_with_statuses(manifest.files, {"valid"})
    usable_files = [
        file
        for file in _files_with_statuses(manifest.files, {"valid", "weak_keep"})
        if not file.needs_review
    ]
    needs_review_files = sum(
        file.needs_review or file.validity_status == "needs_review"
        for file in manifest.files
    )
    raw_stems = {
        _ms_run_id(file) for file in manifest.files if file.file_role == "raw_acquisition"
    }
    converted_stems = {
        _ms_run_id(file) for file in manifest.files if file.file_role == "converted_peaklist"
    }
    task_type = manifest.summary.get("task_type")
    recommended_outputs = {
        "strict_manifest_csv": "dataset_manifest_valid.csv",
        "strict_batch_inputs": "batch_inputs_valid.txt",
        "usable_manifest_csv": "dataset_manifest_usable.csv",
        "usable_batch_inputs": "batch_inputs_usable.txt",
        "all_manifest_csv": "dataset_manifest.csv",
        "all_batch_inputs": "batch_inputs.txt",
    }
    if task_type:
        recommended_outputs.update(
            {
                "task_ready_manifest_csv": "dataset_manifest_task_ready.csv",
                "task_ready_batch_inputs": "batch_inputs_task_ready.txt",
            }
        )
    return {
        "run_id": manifest.run_id,
        "request": manifest.request.model_dump(mode="json"),
        "total_projects": len(manifest.projects),
        "total_files": len(manifest.files),
        "valid_files": len(valid_files),
        "usable_files": len(usable_files),
        "needs_review_files": needs_review_files,
        "weak_keep_files": status_counts.get("weak_keep", 0),
        "excluded_files": manifest.summary.get("excluded_files", 0),
        "validity_status_counts": dict(status_counts),
        "validity_reason_counts": dict(reason_counts),
        "species_distribution": _count_values(manifest.files, "species"),
        "canonical_species_distribution": _count_values(manifest.files, "canonical_species"),
        "species_policy_distribution": _count_values(manifest.files, "species_policy"),
        "labeling_strategy_distribution": _count_values(manifest.files, "labeling_strategy"),
        "modification_scope_distribution": _count_values(manifest.files, "modification_scope"),
        "ptm_enrichment_method_distribution": _count_values(manifest.files, "ptm_enrichment_methods"),
        "semantic_metadata_confidence_mean": _mean_semantic_confidence(manifest.files),
        "instrument_family_distribution": _count_values(manifest.files, "instrument_families"),
        "instrument_generation_distribution": _count_values(
            manifest.files, "instrument_generation_label"
        ),
        "fragmentation_method_distribution": _count_values(manifest.files, "fragmentation_methods"),
        "unknown_counts": manifest.summary.get("unknown_counts") or manifest.summary.get("diversity", {}).get("unknown_counts", {}),
        "evidence_level_distribution": _count_values(manifest.files, "evidence_level"),
        "sdrf_match_status_distribution": _count_values(manifest.files, "sdrf_match_status"),
        "evidence_warning_counts": manifest.summary.get("evidence_warning_counts", {}),
        "task_type": task_type,
        "task_readiness_applicability": "applicable" if task_type else "not_applicable_task_undecided",
        "task_readiness": manifest.summary.get("task_readiness", {}),
        "task_ai_readiness_v2": manifest.summary.get("task_ai_readiness_v2", {}),
        "data_value_v1": manifest.summary.get("data_value_v1", {}),
        "memory_feedback_summary": _memory_feedback_summary(manifest.files),
        "run_representation_counts": {
            "repository_files": len(manifest.files),
            "raw_acquisitions": sum(
                file.file_role == "raw_acquisition" for file in manifest.files
            ),
            "converted_peaklists": sum(
                file.file_role == "converted_peaklist" for file in manifest.files
            ),
            "unique_run_stems": len(raw_stems | converted_stems),
            "raw_peaklist_stem_overlap": len(raw_stems & converted_stems),
        },
        "recommended_outputs": recommended_outputs,
        "notes": [
            "strict exports include validity_status=valid only",
            "usable exports include valid/weak_keep files that do not still require review",
            *(
                ["task-ready output is not applicable until a downstream task is chosen"]
                if not task_type
                else []
            ),
            "original manifest and batch_inputs remain unchanged for compatibility",
        ],
    }


def _memory_feedback_summary(files: list[DiscoveredFile]) -> dict[str, Any]:
    feedback_files = [file for file in files if file.memory_feedback]
    action_counts = Counter(
        str(file.memory_feedback.get("recommended_action") or "unknown")
        for file in feedback_files
    )
    curation_type_counts: Counter[str] = Counter()
    repository_strategy_counts: Counter[str] = Counter()
    planned_repository_counts: Counter[str] = Counter()
    for file in feedback_files:
        feedback = file.memory_feedback or {}
        curation_type_counts.update(feedback.get("curation_type_counts") or {})
        strategy = str(feedback.get("repository_strategy") or "").strip()
        if strategy:
            repository_strategy_counts[strategy] += 1
        planned_repository_counts.update(str(item) for item in feedback.get("planned_repositories") or [] if str(item).strip())
    return {
        "files_with_memory_feedback": len(feedback_files),
        "action_counts": dict(sorted(action_counts.items())),
        "curation_type_counts": dict(sorted(curation_type_counts.items())),
        "repository_strategy_counts": dict(sorted(repository_strategy_counts.items())),
        "planned_repository_counts": dict(sorted(planned_repository_counts.items())),
        "top_feedback_candidates": [
            {
                "repository": file.repository,
                "project_accession": file.project_accession,
                "file_name": file.file_name,
                "recommended_action": file.memory_feedback.get("recommended_action"),
                "latest_decision": file.memory_feedback.get("latest_decision"),
                "latest_reason": file.memory_feedback.get("latest_reason"),
            }
            for file in feedback_files[:10]
        ],
    }


REPOSITORY_AUDIT_COLUMNS = [
    "repository",
    "status",
    "support_status",
    "candidate_projects_seen",
    "eligible_projects_seen",
    "selected_projects",
    "selected_files",
    "blocker",
    "next_step",
]


def _repository_audit_rows(manifest: DatasetManifest) -> list[dict[str, Any]]:
    raw_rows = manifest.summary.get("repository_audit") or []
    rows = [row for row in raw_rows if isinstance(row, dict)]
    if rows:
        return [_normalize_repository_audit_row(row) for row in rows]

    file_counts = Counter(file.repository or "unknown" for file in manifest.files)
    if file_counts:
        inferred_rows: list[dict[str, Any]] = []
        for repository, selected_files in sorted(file_counts.items()):
            project_count = len({file.project_accession for file in manifest.files if (file.repository or "unknown") == repository})
            inferred_rows.append(
                _normalize_repository_audit_row(
                    {
                        "repository": repository,
                        "status": "completed",
                        "support_status": manifest.summary.get("repository_support_status") or "not_recorded",
                        "selected_projects": project_count,
                        "selected_files": selected_files,
                        "next_step": "send_selected_to_batch_or_ai_ready_build",
                    }
                )
            )
        return inferred_rows

    repository = str(manifest.summary.get("repository") or manifest.request.repository or "unknown")
    blocker = ""
    failures = manifest.summary.get("failures") or []
    if failures and isinstance(failures[0], dict):
        blocker = str(failures[0].get("error") or "")
    return [
        _normalize_repository_audit_row(
            {
                "repository": repository,
                "status": "blocked" if blocker else "no_selected_files",
                "support_status": manifest.summary.get("repository_support_status") or "not_recorded",
                "candidate_projects_seen": manifest.summary.get("candidate_projects_seen") or 0,
                "eligible_projects_seen": manifest.summary.get("eligible_projects_seen") or 0,
                "selected_projects": manifest.summary.get("selected_projects") or 0,
                "selected_files": manifest.summary.get("selected_files") or 0,
                "blocker": blocker,
                "next_step": manifest.summary.get("next_step") or ("review_repository_discovery_failure" if blocker else "relax_query_or_try_repository_smoke"),
            }
        )
    ]


def _normalize_repository_audit_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = {column: row.get(column, "") for column in REPOSITORY_AUDIT_COLUMNS}
    for column in ["candidate_projects_seen", "eligible_projects_seen", "selected_projects", "selected_files"]:
        normalized[column] = int(normalized.get(column) or 0)
    return normalized


def _repository_audit_payload(manifest: DatasetManifest) -> dict[str, Any]:
    rows = _repository_audit_rows(manifest)
    return {
        "run_id": manifest.run_id,
        "requested_repository": manifest.request.repository,
        "repositories_attempted": manifest.summary.get("repositories_attempted") or [row["repository"] for row in rows],
        "repository_counts": manifest.summary.get("repository_counts") or dict(sorted(Counter(file.repository for file in manifest.files).items())),
        "rows": rows,
    }


def _write_repository_audit(json_path: Path, csv_path: Path, md_path: Path, manifest: DatasetManifest) -> None:
    payload = _repository_audit_payload(manifest)
    rows = payload["rows"]
    write_json(json_path, payload)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPOSITORY_AUDIT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    md_lines = [
        "# Repository Audit",
        "",
        f"- Requested repository: `{payload.get('requested_repository') or 'unknown'}`",
        f"- Repositories attempted: `{', '.join(payload.get('repositories_attempted') or []) or 'not_recorded'}`",
        f"- Candidate rows: {len(rows)}",
        "",
        "| Repository | Status | Selected files | Blocker | Next step |",
        "|---|---:|---:|---|---|",
    ]
    for row in rows:
        md_lines.append(
            "| "
            f"{row.get('repository') or 'unknown'} | "
            f"{row.get('status') or 'unknown'} | "
            f"{row.get('selected_files') or 0} | "
            f"{row.get('blocker') or ''} | "
            f"{row.get('next_step') or ''} |"
        )
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")


def write_dataset_manifest(manifest: DatasetManifest, output_dir: str | Path) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    paths = {
        "dataset_request": output_dir / "dataset_request.json",
        "candidate_projects": output_dir / "candidate_projects.json",
        "dataset_manifest_json": output_dir / "dataset_manifest.json",
        "dataset_manifest_csv": output_dir / "dataset_manifest.csv",
        "dataset_manifest_valid_csv": output_dir / "dataset_manifest_valid.csv",
        "dataset_manifest_usable_csv": output_dir / "dataset_manifest_usable.csv",
        "dataset_manifest_task_ready_csv": output_dir / "dataset_manifest_task_ready.csv",
        "batch_inputs": output_dir / "batch_inputs.txt",
        "batch_inputs_valid": output_dir / "batch_inputs_valid.txt",
        "batch_inputs_usable": output_dir / "batch_inputs_usable.txt",
        "batch_inputs_task_ready": output_dir / "batch_inputs_task_ready.txt",
        "discovery_summary": output_dir / "discovery_summary.json",
        "quality_report": output_dir / "quality_report.json",
        "repository_audit_json": output_dir / "repository_audit.json",
        "repository_audit_csv": output_dir / "repository_audit.csv",
        "repository_audit_md": output_dir / "repository_audit.md",
        "task_ai_readiness_matrix_json": output_dir / "task_ai_readiness_matrix.json",
        "task_ai_readiness_matrix_csv": output_dir / "task_ai_readiness_matrix.csv",
        "data_value_ranking_json": output_dir / "data_value_ranking.json",
        "data_value_ranking_csv": output_dir / "data_value_ranking.csv",
        "data_value_report_md": output_dir / "data_value_report.md",
    }

    write_json(paths["dataset_request"], manifest.request.model_dump(mode="json"))
    write_json(paths["candidate_projects"], [project.model_dump(mode="json") for project in manifest.projects])
    write_json(paths["dataset_manifest_json"], manifest.model_dump(mode="json"))
    write_json(paths["discovery_summary"], manifest.summary)
    write_json(paths["quality_report"], build_quality_report(manifest))
    _write_repository_audit(paths["repository_audit_json"], paths["repository_audit_csv"], paths["repository_audit_md"], manifest)
    _write_task_ai_readiness_matrix(paths["task_ai_readiness_matrix_json"], paths["task_ai_readiness_matrix_csv"], manifest)
    _write_data_value_ranking(paths["data_value_ranking_json"], paths["data_value_ranking_csv"], paths["data_value_report_md"], manifest)

    valid_files = _files_with_statuses(manifest.files, {"valid"})
    usable_files = [
        file
        for file in _files_with_statuses(manifest.files, {"valid", "weak_keep"})
        if not file.needs_review
    ]
    task_files = task_ready_files(manifest)
    judgments = (manifest.summary or {}).get("project_judgments")
    judgments = judgments if isinstance(judgments, dict) else None
    _write_manifest_csv(paths["dataset_manifest_csv"], manifest.files, manifest.run_id, judgments=judgments)
    _write_manifest_csv(paths["dataset_manifest_valid_csv"], valid_files, manifest.run_id, judgments=judgments)
    _write_manifest_csv(paths["dataset_manifest_usable_csv"], usable_files, manifest.run_id, judgments=judgments)
    _write_manifest_csv(paths["dataset_manifest_task_ready_csv"], task_files, manifest.run_id, judgments=judgments)
    _write_batch_inputs(paths["batch_inputs"], manifest.files)
    _write_batch_inputs(paths["batch_inputs_valid"], valid_files)
    _write_batch_inputs(paths["batch_inputs_usable"], usable_files)
    _write_batch_inputs(paths["batch_inputs_task_ready"], task_files)

    return paths


def _matrix_row(file: DiscoveredFile, run_id: str | None) -> dict[str, Any]:
    return {
        "run_id": run_id or "",
        "repository": file.repository,
        "project_accession": file.project_accession,
        "file_name": file.file_name,
        "task_type": file.task_type or "",
        "validity_status": file.validity_status,
        "task_readiness_status": file.task_readiness_status or "",
        "task_ai_readiness_score": file.task_ai_readiness_score if file.task_ai_readiness_score is not None else "",
        "task_ai_readiness_band": file.task_ai_readiness_band or "",
        "metadata_completeness": file.task_ai_readiness_dimensions.get("metadata_completeness", ""),
        "expected_label_availability": file.task_ai_readiness_dimensions.get("expected_label_availability", ""),
        "acquisition_workflow_fit": file.task_ai_readiness_dimensions.get("acquisition_workflow_fit", ""),
        "downstream_exporter_feasibility": file.task_ai_readiness_dimensions.get("downstream_exporter_feasibility", ""),
        "risk_leakage_preliminary_penalty": file.task_ai_readiness_dimensions.get("risk_leakage_preliminary_penalty", ""),
        "warnings": _join_values(file.task_ai_readiness_warnings),
        "reasons": _join_values(file.task_ai_readiness_reasons),
        "memory_recommended_action": (file.memory_feedback or {}).get("recommended_action", ""),
        "memory_latest_decision": (file.memory_feedback or {}).get("latest_decision", ""),
        "semantic_metadata_confidence": file.semantic_metadata_confidence,
        "ptm_evidence_terms": _join_values(file.ptm_evidence_terms),
        "ptm_enrichment_methods": _join_values(file.ptm_enrichment_methods),
        "immunopeptide_scope": file.immunopeptide_scope or "",
        "hla_class": _join_values(file.hla_class),
        "hla_alleles": _join_values(file.hla_alleles),
        "immunopeptide_evidence_terms": _join_values(file.immunopeptide_evidence_terms),
        "immunopeptide_enrichment_methods": _join_values(file.immunopeptide_enrichment_methods),
        "immunopeptide_metadata_confidence": file.immunopeptide_metadata_confidence,
    }


def _write_task_ai_readiness_matrix(json_path: Path, csv_path: Path, manifest: DatasetManifest) -> None:
    requested_task_type = str(manifest.summary.get("task_type") or "").strip()
    task_types = list(active_task_types())
    if requested_task_type and requested_task_type not in task_types:
        task_types.insert(0, requested_task_type)
    rows: list[dict[str, Any]] = []
    task_summaries: dict[str, Any] = {}
    for task_type in task_types:
        annotated = annotate_manifest_task_readiness(manifest, task_type)
        task_summaries[task_type] = {
            "task_readiness": annotated.summary.get("task_readiness", {}),
            "task_ai_readiness_v2": annotated.summary.get("task_ai_readiness_v2", {}),
            "data_value_v1": annotated.summary.get("data_value_v1", {}),
        }
        rows.extend(_matrix_row(file, manifest.run_id) for file in annotated.files)
    write_json(
        json_path,
        {
            "run_id": manifest.run_id,
            "task_type": requested_task_type,
            "matrix_mode": "all_active_tasks",
            "task_types": task_types,
            "summary": task_summaries,
            "rows": rows,
        },
    )
    _write_generic_csv(csv_path, rows)


def _value_row(file: DiscoveredFile, run_id: str | None) -> dict[str, Any]:
    return {
        "run_id": run_id or "",
        "repository": file.repository,
        "project_accession": file.project_accession,
        "file_name": file.file_name,
        "task_type": file.task_type or "",
        "data_value_score": file.data_value_score if file.data_value_score is not None else "",
        "data_value_action": file.data_value_action or "",
        "task_ai_readiness_score": file.task_ai_readiness_score if file.task_ai_readiness_score is not None else "",
        "diversity_gain": file.data_value_components.get("diversity_gain", ""),
        "estimated_label_yield": file.data_value_components.get("estimated_label_yield", ""),
        "cost_efficiency": file.data_value_components.get("cost_efficiency", ""),
        "risk_penalty": file.data_value_components.get("risk_penalty", ""),
        "validity_status": file.validity_status,
        "memory_recommended_action": (file.memory_feedback or {}).get("recommended_action", ""),
        "memory_latest_decision": (file.memory_feedback or {}).get("latest_decision", ""),
        "memory_latest_reason": (file.memory_feedback or {}).get("latest_reason", ""),
        "memory_planned_repositories": _join_values(file.memory_feedback.get("planned_repositories", []) if file.memory_feedback else []),
        "semantic_metadata_confidence": file.semantic_metadata_confidence,
        "ptm_evidence_terms": _join_values(file.ptm_evidence_terms),
        "ptm_enrichment_methods": _join_values(file.ptm_enrichment_methods),
        "immunopeptide_scope": file.immunopeptide_scope or "",
        "hla_class": _join_values(file.hla_class),
        "hla_alleles": _join_values(file.hla_alleles),
        "immunopeptide_evidence_terms": _join_values(file.immunopeptide_evidence_terms),
        "immunopeptide_enrichment_methods": _join_values(file.immunopeptide_enrichment_methods),
        "immunopeptide_metadata_confidence": file.immunopeptide_metadata_confidence,
        "reasons": _join_values(file.data_value_reasons),
    }


def _mean_semantic_confidence(files: list[DiscoveredFile]) -> float:
    values = [float(file.semantic_metadata_confidence or 0.0) for file in files if file.semantic_metadata_confidence]
    return round(sum(values) / len(values), 3) if values else 0.0


def _write_data_value_ranking(json_path: Path, csv_path: Path, md_path: Path, manifest: DatasetManifest) -> None:
    rows = sorted(
        [_value_row(file, manifest.run_id) for file in manifest.files],
        key=lambda row: float(row["data_value_score"] or 0.0),
        reverse=True,
    )
    payload = {
        "run_id": manifest.run_id,
        "task_type": manifest.summary.get("task_type"),
        "summary": manifest.summary.get("data_value_v1", {}),
        "rows": rows,
    }
    write_json(json_path, payload)
    _write_generic_csv(csv_path, rows)
    md_lines = [
        "# Data Value Ranking",
        "",
        f"- Task type: `{payload.get('task_type') or 'not_set'}`",
        f"- Candidates: {len(rows)}",
        f"- Candidates with discovery memory feedback: {sum(1 for row in rows if row.get('memory_recommended_action'))}",
        "",
        "## Top Candidates",
        "",
    ]
    for row in rows[:20]:
        md_lines.append(
            f"- `{row.get('data_value_action') or 'not_scored'}` "
            f"{row.get('data_value_score')}: `{row.get('project_accession')}` / `{row.get('file_name')}`"
        )
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")


def _write_generic_csv(path: Path, rows: list[dict[str, Any]]) -> None:
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
            writer.writerow(row)
