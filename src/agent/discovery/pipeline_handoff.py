from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import Field

from agent.discovery.models import DatasetManifest, DiscoveredFile
from agent.models import JsonModel
from agent.oneclick.preflight import run_preflight
from agent.utils import write_json


HandoffSelection = Literal["auto", "task_ready", "usable", "valid", "review", "all"]
HandoffStatus = Literal["ready_for_batch_parameters", "needs_review", "not_ready"]

USABLE_VALIDITY = {"valid", "weak_keep"}
TASK_READY_STATUSES = {"ready"}
PIPELINE_ELIGIBLE_STATUSES = {"ready", "weak_ready"}
HANDOFF_FILE_ROLES = {"raw_acquisition", "converted_peaklist"}


HANDOFF_COLUMNS = [
    "run_id",
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
    "species_policy",
    "canonical_species",
    "organism_taxon_id",
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
    "validity_status",
    "task_type",
    "task_readiness_status",
    "task_ai_readiness_score",
    "task_ai_readiness_band",
    "data_value_score",
    "data_value_action",
    "label_source_status",
    "spectra_requirement_status",
    "metadata_requirement_status",
    "missing_task_requirements",
    "next_pipeline_steps",
    "ai_ready_target_schema",
    "handoff_status",
    "recommended_entrypoint",
    "recommended_run_mode",
    "handoff_reasons",
    "trust_score",
    "file_score",
    "evidence_level",
    "sdrf_match_status",
    "instrument_families",
    "fragmentation_methods",
    "lc_gradient_minutes",
]


class PipelineHandoffRow(JsonModel):
    run_id: str | None = None
    repository: str = "pride"
    project_accession: str
    native_accession: str | None = None
    px_accession: str | None = None
    file_accession_or_path: str | None = None
    project_title: str | None = None
    file_name: str
    download_url: str | None = None
    transfer_method: str | None = None
    file_type: str
    file_role: str = "unknown"
    species_policy: str = "open"
    canonical_species: list[str] = Field(default_factory=list)
    organism_taxon_id: list[str] = Field(default_factory=list)
    ptm_type: str | None = None
    ptm_subtype: str | None = None
    ptm_evidence_terms: list[str] = Field(default_factory=list)
    ptm_enrichment_methods: list[str] = Field(default_factory=list)
    semantic_metadata_confidence: float = 0.0
    semantic_interpretation_trace: list[str] = Field(default_factory=list)
    modification_scope: str | None = None
    immunopeptide_scope: str | None = None
    hla_class: list[str] = Field(default_factory=list)
    hla_alleles: list[str] = Field(default_factory=list)
    immunopeptide_evidence_terms: list[str] = Field(default_factory=list)
    immunopeptide_enrichment_methods: list[str] = Field(default_factory=list)
    immunopeptide_metadata_confidence: float = 0.0
    labeling_strategy: str | None = None
    validity_status: str
    task_type: str | None = None
    task_readiness_status: str | None = None
    task_ai_readiness_score: float | None = None
    task_ai_readiness_band: str | None = None
    data_value_score: float | None = None
    data_value_action: str | None = None
    label_source_status: str | None = None
    spectra_requirement_status: str | None = None
    metadata_requirement_status: str | None = None
    missing_task_requirements: list[str] = Field(default_factory=list)
    next_pipeline_steps: list[str] = Field(default_factory=list)
    ai_ready_target_schema: str | None = None
    handoff_status: HandoffStatus
    recommended_entrypoint: str
    recommended_run_mode: str
    handoff_reasons: list[str] = Field(default_factory=list)
    trust_score: float = 0.0
    file_score: float = 0.0
    evidence_level: str = "unknown"
    sdrf_match_status: str = "not_checked"
    instrument_families: list[str] = Field(default_factory=list)
    fragmentation_methods: list[str] = Field(default_factory=list)
    lc_gradient_minutes: float | None = None


class PipelineHandoff(JsonModel):
    run_id: str | None = None
    selection: HandoffSelection
    resolved_selection: HandoffSelection
    summary: dict[str, Any] = Field(default_factory=dict)
    files: list[PipelineHandoffRow] = Field(default_factory=list)


class PipelineHandoffPreflight(JsonModel):
    run_id: str | None = None
    status: str
    batch_request_path: str
    input_count: int = 0
    ready_input_count: int = 0
    skipped_count: int = 0
    run_mode: str = "parameters"
    repository: str = "pride"
    resource_policy: str = "balanced"
    summary: dict[str, Any] = Field(default_factory=dict)
    preflight: dict[str, Any] = Field(default_factory=dict)
    skipped_files: list[dict[str, Any]] = Field(default_factory=list)


def _join(values: list[str]) -> str:
    return ";".join(str(value) for value in values if str(value).strip())


def _selection_has_task_readiness(files: list[DiscoveredFile]) -> bool:
    return any(file.task_readiness_status for file in files)


def resolve_handoff_selection(manifest: DatasetManifest, selection: HandoffSelection) -> HandoffSelection:
    if selection != "auto":
        return selection
    if _selection_has_task_readiness(manifest.files):
        return "task_ready"
    return "usable"


def select_handoff_files(
    manifest: DatasetManifest,
    *,
    selection: HandoffSelection = "auto",
    max_files: int | None = None,
) -> list[DiscoveredFile]:
    resolved = resolve_handoff_selection(manifest, selection)
    if resolved == "task_ready":
        files = [file for file in manifest.files if file.task_readiness_status in PIPELINE_ELIGIBLE_STATUSES]
    elif resolved == "usable":
        files = [file for file in manifest.files if file.validity_status in USABLE_VALIDITY]
    elif resolved == "valid":
        files = [file for file in manifest.files if file.validity_status == "valid"]
    elif resolved == "review":
        files = [file for file in manifest.files if file.validity_status == "needs_review" or file.needs_review]
    elif resolved == "all":
        files = list(manifest.files)
    else:
        raise ValueError(f"Unsupported handoff selection: {selection}")

    files = sorted(
        files,
        key=lambda file: (
            -float(file.data_value_score or 0.0),
            -float(file.task_ai_readiness_score or 0.0),
            -float(file.trust_score or 0.0),
            -float(file.file_score or 0.0),
            file.project_accession,
            file.file_name,
        ),
    )
    if max_files is not None:
        files = files[:max_files]
    return files


def _handoff_decision(file: DiscoveredFile, *, require_task_ready: bool) -> tuple[HandoffStatus, str, str, list[str]]:
    reasons: list[str] = []
    if file.validity_status == "exclude":
        reasons.append("excluded_by_discovery_validity")
        return "not_ready", "not_available", "skip", reasons
    if file.validity_status == "needs_review" or file.needs_review:
        reasons.append("discovery_needs_review")
        return "needs_review", "manual_review", "review", reasons
    if file.validity_status not in USABLE_VALIDITY:
        reasons.append(f"validity_not_usable:{file.validity_status}")
        return "not_ready", "not_available", "skip", reasons

    if file.file_role not in HANDOFF_FILE_ROLES:
        if file.file_role in {"search_result", "metadata", "report_table"}:
            reasons.append(f"not_raw_or_peaklist:{file.file_role}")
            return "not_ready", "not_available", "skip", reasons
        reasons.append("file_role_unknown_or_unconfirmed")
        return "needs_review", "manual_review", "review", reasons

    if require_task_ready and file.task_readiness_status not in PIPELINE_ELIGIBLE_STATUSES:
        reasons.append(f"task_not_ready:{file.task_readiness_status or 'not_evaluated'}")
        return "not_ready", "not_available", "skip", reasons

    if not file.download_url:
        reasons.append("download_url_missing_for_later_prepare")
    if file.validity_status == "weak_keep":
        reasons.append("weak_keep_requires_light_review_before_large_scale")
    if file.task_readiness_status == "weak_ready":
        reasons.append("task_weak_ready_requires_downstream_label_generation")

    return "ready_for_batch_parameters", "batch_parameters", "parameters", reasons


def _row_from_file(
    file: DiscoveredFile,
    *,
    run_id: str | None,
    require_task_ready: bool,
) -> PipelineHandoffRow:
    status, entrypoint, run_mode, reasons = _handoff_decision(file, require_task_ready=require_task_ready)
    return PipelineHandoffRow(
        run_id=run_id,
        repository=file.repository,
        project_accession=file.project_accession,
        native_accession=file.native_accession,
        px_accession=file.px_accession,
        file_accession_or_path=file.file_accession_or_path,
        project_title=file.project_title,
        file_name=file.file_name,
        download_url=file.download_url,
        transfer_method=file.transfer_method,
        file_type=file.file_type,
        file_role=file.file_role,
        species_policy=file.species_policy,
        canonical_species=file.canonical_species,
        organism_taxon_id=file.organism_taxon_id,
        ptm_type=file.ptm_type,
        ptm_subtype=file.ptm_subtype,
        ptm_evidence_terms=file.ptm_evidence_terms,
        ptm_enrichment_methods=file.ptm_enrichment_methods,
        semantic_metadata_confidence=file.semantic_metadata_confidence,
        semantic_interpretation_trace=file.semantic_interpretation_trace,
        modification_scope=file.modification_scope,
        immunopeptide_scope=file.immunopeptide_scope,
        hla_class=file.hla_class,
        hla_alleles=file.hla_alleles,
        immunopeptide_evidence_terms=file.immunopeptide_evidence_terms,
        immunopeptide_enrichment_methods=file.immunopeptide_enrichment_methods,
        immunopeptide_metadata_confidence=file.immunopeptide_metadata_confidence,
        labeling_strategy=file.labeling_strategy,
        validity_status=file.validity_status,
        task_type=file.task_type,
        task_readiness_status=file.task_readiness_status,
        task_ai_readiness_score=file.task_ai_readiness_score,
        task_ai_readiness_band=file.task_ai_readiness_band,
        data_value_score=file.data_value_score,
        data_value_action=file.data_value_action,
        label_source_status=file.label_source_status,
        spectra_requirement_status=file.spectra_requirement_status,
        metadata_requirement_status=file.metadata_requirement_status,
        missing_task_requirements=file.missing_task_requirements,
        next_pipeline_steps=file.next_pipeline_steps,
        ai_ready_target_schema=file.ai_ready_target_schema,
        handoff_status=status,
        recommended_entrypoint=entrypoint,
        recommended_run_mode=run_mode,
        handoff_reasons=reasons,
        trust_score=file.trust_score,
        file_score=file.file_score,
        evidence_level=file.evidence_level,
        sdrf_match_status=file.sdrf_match_status,
        instrument_families=file.instrument_families,
        fragmentation_methods=file.fragmentation_methods,
        lc_gradient_minutes=file.lc_gradient_minutes,
    )


def build_pipeline_handoff(
    manifest: DatasetManifest,
    *,
    selection: HandoffSelection = "auto",
    max_files: int | None = None,
) -> PipelineHandoff:
    resolved = resolve_handoff_selection(manifest, selection)
    selected_files = select_handoff_files(manifest, selection=resolved, max_files=max_files)
    require_task_ready = resolved == "task_ready"
    rows = [
        _row_from_file(file, run_id=manifest.run_id, require_task_ready=require_task_ready)
        for file in selected_files
    ]

    status_counts = Counter(row.handoff_status for row in rows)
    entrypoint_counts = Counter(row.recommended_entrypoint for row in rows)
    reason_counts: Counter[str] = Counter()
    for row in rows:
        reason_counts.update(row.handoff_reasons)
    ready_rows = [row for row in rows if row.handoff_status == "ready_for_batch_parameters"]
    summary: dict[str, Any] = {
        "run_id": manifest.run_id,
        "selection": selection,
        "resolved_selection": resolved,
        "input_files": len(manifest.files),
        "selected_files": len(rows),
        "ready_for_batch_parameters": len(ready_rows),
        "needs_review": status_counts.get("needs_review", 0),
        "not_ready": status_counts.get("not_ready", 0),
        "handoff_status_counts": dict(sorted(status_counts.items())),
        "recommended_entrypoint_counts": dict(sorted(entrypoint_counts.items())),
        "handoff_reason_counts": dict(sorted(reason_counts.items())),
        "task_type_counts": dict(sorted(Counter(row.task_type or "none" for row in rows).items())),
        "notes": [
            "handoff outputs do not download files or run prepare/full",
            "ready_for_batch_parameters means safe to feed file names into the existing parameters-only batch entrypoint",
            "weak_ready files still need downstream label generation before final AI-ready training data",
        ],
    }
    return PipelineHandoff(
        run_id=manifest.run_id,
        selection=selection,
        resolved_selection=resolved,
        summary=summary,
        files=rows,
    )


def _row_to_csv(row: PipelineHandoffRow) -> dict[str, Any]:
    return {
        "run_id": row.run_id or "",
        "repository": row.repository,
        "project_accession": row.project_accession,
        "native_accession": row.native_accession or "",
        "px_accession": row.px_accession or "",
        "file_accession_or_path": row.file_accession_or_path or "",
        "project_title": row.project_title or "",
        "file_name": row.file_name,
        "download_url": row.download_url or "",
        "transfer_method": row.transfer_method or "",
        "file_type": row.file_type,
        "file_role": row.file_role,
        "species_policy": row.species_policy,
        "canonical_species": _join(row.canonical_species),
        "organism_taxon_id": _join(row.organism_taxon_id),
        "ptm_type": row.ptm_type or "",
        "ptm_subtype": row.ptm_subtype or "",
        "ptm_evidence_terms": _join(row.ptm_evidence_terms),
        "ptm_enrichment_methods": _join(row.ptm_enrichment_methods),
        "semantic_metadata_confidence": row.semantic_metadata_confidence,
        "semantic_interpretation_trace": _join(row.semantic_interpretation_trace),
        "modification_scope": row.modification_scope or "",
        "immunopeptide_scope": row.immunopeptide_scope or "",
        "hla_class": _join(row.hla_class),
        "hla_alleles": _join(row.hla_alleles),
        "immunopeptide_evidence_terms": _join(row.immunopeptide_evidence_terms),
        "immunopeptide_enrichment_methods": _join(row.immunopeptide_enrichment_methods),
        "immunopeptide_metadata_confidence": row.immunopeptide_metadata_confidence,
        "labeling_strategy": row.labeling_strategy or "",
        "validity_status": row.validity_status,
        "task_type": row.task_type or "",
        "task_readiness_status": row.task_readiness_status or "",
        "label_source_status": row.label_source_status or "",
        "spectra_requirement_status": row.spectra_requirement_status or "",
        "metadata_requirement_status": row.metadata_requirement_status or "",
        "missing_task_requirements": _join(row.missing_task_requirements),
        "next_pipeline_steps": _join(row.next_pipeline_steps),
        "ai_ready_target_schema": row.ai_ready_target_schema or "",
        "handoff_status": row.handoff_status,
        "recommended_entrypoint": row.recommended_entrypoint,
        "recommended_run_mode": row.recommended_run_mode,
        "handoff_reasons": _join(row.handoff_reasons),
        "trust_score": row.trust_score,
        "file_score": row.file_score,
        "evidence_level": row.evidence_level,
        "sdrf_match_status": row.sdrf_match_status,
        "instrument_families": _join(row.instrument_families),
        "fragmentation_methods": _join(row.fragmentation_methods),
        "lc_gradient_minutes": row.lc_gradient_minutes if row.lc_gradient_minutes is not None else "",
    }


def _write_handoff_csv(path: Path, rows: list[PipelineHandoffRow]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HANDOFF_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(_row_to_csv(row))


def _write_unique_file_names(path: Path, rows: list[PipelineHandoffRow]) -> None:
    seen: set[str] = set()
    lines: list[str] = []
    for row in rows:
        if row.file_name in seen:
            continue
        seen.add(row.file_name)
        lines.append(row.file_name)
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def load_pipeline_handoff(path: str | Path) -> PipelineHandoff:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return PipelineHandoff.model_validate(payload)


def ready_handoff_rows(handoff: PipelineHandoff) -> list[PipelineHandoffRow]:
    return [row for row in handoff.files if row.handoff_status == "ready_for_batch_parameters"]


def batch_parameters_request(
    handoff: PipelineHandoff,
    *,
    submitter: str = "discovery_handoff",
    jobs: int = 1,
    repository: str = "pride",
    resource_policy: str = "balanced",
    prefer_project_fasta: bool = False,
) -> dict[str, Any]:
    ready_rows = ready_handoff_rows(handoff)
    inputs = [row.file_name for row in ready_rows]
    input_records = [
        {
            "input": row.file_name,
            "repository": row.repository,
            "project_accession": row.project_accession,
            "project_title": row.project_title,
            "file_name": row.file_name,
            "download_url": row.download_url,
            "file_type": row.file_type,
            "file_role": row.file_role,
            "species_policy": row.species_policy,
            "canonical_species": row.canonical_species,
            "organism_taxon_id": row.organism_taxon_id,
            "ptm_type": row.ptm_type,
            "ptm_subtype": row.ptm_subtype,
            "ptm_evidence_terms": row.ptm_evidence_terms,
            "ptm_enrichment_methods": row.ptm_enrichment_methods,
            "semantic_metadata_confidence": row.semantic_metadata_confidence,
            "modification_scope": row.modification_scope,
            "immunopeptide_scope": row.immunopeptide_scope,
            "hla_class": row.hla_class,
            "hla_alleles": row.hla_alleles,
            "immunopeptide_evidence_terms": row.immunopeptide_evidence_terms,
            "immunopeptide_enrichment_methods": row.immunopeptide_enrichment_methods,
            "immunopeptide_metadata_confidence": row.immunopeptide_metadata_confidence,
            "labeling_strategy": row.labeling_strategy,
            "validity_status": row.validity_status,
            "task_type": row.task_type,
            "task_readiness_status": row.task_readiness_status,
            "evidence_level": row.evidence_level,
            "sdrf_match_status": row.sdrf_match_status,
            "instrument_families": row.instrument_families,
            "fragmentation_methods": row.fragmentation_methods,
            "lc_gradient_minutes": row.lc_gradient_minutes,
        }
        for row in ready_rows
    ]
    return {
        "inputs": inputs,
        "input_records": input_records,
        "input_record_mode": "discovery_handoff_v1",
        "submitter": submitter,
        "repository": repository,
        "run_mode": "parameters",
        "resource_policy": resource_policy,
        "jobs": jobs,
        "prefer_project_fasta": prefer_project_fasta,
        "source_run_id": handoff.run_id,
        "source_selection": handoff.resolved_selection,
        "source": "discovery_pipeline_handoff",
    }


def build_handoff_preflight(
    handoff: PipelineHandoff,
    *,
    output_root: str | Path,
    submitter: str = "discovery_handoff",
    jobs: int = 1,
    repository: str = "pride",
    resource_policy: str = "balanced",
    prefer_project_fasta: bool = False,
    preflight_runner: Callable[..., dict[str, Any]] = run_preflight,
) -> tuple[PipelineHandoffPreflight, dict[str, Any]]:
    request = batch_parameters_request(
        handoff,
        submitter=submitter,
        jobs=jobs,
        repository=repository,
        resource_policy=resource_policy,
        prefer_project_fasta=prefer_project_fasta,
    )
    ready_rows = ready_handoff_rows(handoff)
    skipped_rows = [row for row in handoff.files if row.handoff_status != "ready_for_batch_parameters"]
    skipped_files = [
        {
            "project_accession": row.project_accession,
            "file_name": row.file_name,
            "handoff_status": row.handoff_status,
            "handoff_reasons": row.handoff_reasons,
            "file_role": row.file_role,
            "validity_status": row.validity_status,
            "task_readiness_status": row.task_readiness_status,
        }
        for row in skipped_rows
    ]
    if not request["inputs"]:
        preflight = {
            "status": "blocked",
            "run_mode": "parameters",
            "resource_policy": resource_policy,
            "repository": repository,
            "input_count": 0,
            "checks": [],
            "blocking_issues": ["No ready_for_batch_parameters files in handoff."],
            "warnings": [],
            "required_disk_bytes": 0,
        }
    else:
        preflight = preflight_runner(
            inputs=request["inputs"],
            run_mode="parameters",
            repository=repository,
            output_root=output_root,
            resource_policy=resource_policy,
        )
    reason_counts: Counter[str] = Counter()
    for row in handoff.files:
        reason_counts.update(row.handoff_reasons)
    summary = {
        "run_id": handoff.run_id,
        "selection": handoff.selection,
        "resolved_selection": handoff.resolved_selection,
        "input_count": len(handoff.files),
        "ready_input_count": len(ready_rows),
        "skipped_count": len(skipped_rows),
        "preflight_status": preflight.get("status"),
        "handoff_status_counts": dict(sorted(Counter(row.handoff_status for row in handoff.files).items())),
        "handoff_reason_counts": dict(sorted(reason_counts.items())),
        "next_step": (
            "submit_batch_parameters_request"
            if preflight.get("status") in {"ok", "warning"} and request["inputs"]
            else "fix_or_review_handoff_before_batch_parameters"
        ),
    }
    report = PipelineHandoffPreflight(
        run_id=handoff.run_id,
        status=str(preflight.get("status") or "blocked"),
        batch_request_path="",
        input_count=len(handoff.files),
        ready_input_count=len(ready_rows),
        skipped_count=len(skipped_rows),
        run_mode="parameters",
        repository=repository,
        resource_policy=resource_policy,
        summary=summary,
        preflight=preflight,
        skipped_files=skipped_files,
    )
    return report, request


def write_handoff_batch_preflight(
    handoff: PipelineHandoff,
    output_dir: str | Path,
    *,
    submitter: str = "discovery_handoff",
    jobs: int = 1,
    repository: str = "pride",
    resource_policy: str = "balanced",
    prefer_project_fasta: bool = False,
    preflight_runner: Callable[..., dict[str, Any]] = run_preflight,
) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "batch_parameters_request": output_dir / "batch_parameters_request.json",
        "batch_parameters_inputs": output_dir / "batch_parameters_inputs.txt",
        "batch_preflight_report": output_dir / "batch_preflight_report.json",
        "skipped_files_csv": output_dir / "batch_preflight_skipped_files.csv",
    }
    report, request = build_handoff_preflight(
        handoff,
        output_root=output_dir,
        submitter=submitter,
        jobs=jobs,
        repository=repository,
        resource_policy=resource_policy,
        prefer_project_fasta=prefer_project_fasta,
        preflight_runner=preflight_runner,
    )
    report = report.model_copy(update={"batch_request_path": str(paths["batch_parameters_request"])})
    write_json(paths["batch_parameters_request"], request)
    write_json(paths["batch_preflight_report"], report.model_dump(mode="json"))
    paths["batch_parameters_inputs"].write_text(
        "\n".join(request["inputs"]) + ("\n" if request["inputs"] else ""),
        encoding="utf-8",
    )
    with paths["skipped_files_csv"].open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "project_accession",
            "file_name",
            "handoff_status",
            "handoff_reasons",
            "file_role",
            "validity_status",
            "task_readiness_status",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in report.skipped_files:
            writer.writerow({**row, "handoff_reasons": _join(row.get("handoff_reasons", []))})
    return paths


def write_pipeline_handoff(
    manifest: DatasetManifest,
    output_dir: str | Path,
    *,
    selection: HandoffSelection = "auto",
    max_files: int | None = None,
) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    handoff = build_pipeline_handoff(manifest, selection=selection, max_files=max_files)
    ready_rows = [row for row in handoff.files if row.handoff_status == "ready_for_batch_parameters"]
    prepare_rows = [
        row
        for row in ready_rows
        if row.file_role in HANDOFF_FILE_ROLES and row.recommended_entrypoint == "batch_parameters"
    ]

    paths = {
        "pipeline_handoff_json": output_dir / "discovery_pipeline_handoff.json",
        "pipeline_handoff_csv": output_dir / "discovery_pipeline_handoff.csv",
        "batch_parameters_inputs": output_dir / "batch_parameters_inputs.txt",
        "prepare_candidates": output_dir / "prepare_candidates.txt",
        "pipeline_handoff_summary": output_dir / "pipeline_handoff_summary.json",
    }
    write_json(paths["pipeline_handoff_json"], handoff.model_dump(mode="json"))
    write_json(paths["pipeline_handoff_summary"], handoff.summary)
    _write_handoff_csv(paths["pipeline_handoff_csv"], handoff.files)
    _write_unique_file_names(paths["batch_parameters_inputs"], ready_rows)
    _write_unique_file_names(paths["prepare_candidates"], prepare_rows)
    return paths
