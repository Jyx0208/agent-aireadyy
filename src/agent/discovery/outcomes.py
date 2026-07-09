from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from pydantic import Field

from agent.discovery.models import DatasetManifest, DiscoveredFile
from agent.models import JsonModel
from agent.utils import write_json


OUTCOME_COLUMNS = [
    "run_id",
    "repository",
    "project_accession",
    "project_title",
    "file_name",
    "download_url",
    "file_type",
    "file_role",
    "validity_status",
    "task_type",
    "task_readiness_status",
    "evidence_level",
    "sdrf_match_status",
    "batch_id",
    "batch_index",
    "batch_input",
    "batch_status",
    "batch_error",
    "batch_output_dir",
    "batch_started_at",
    "batch_finished_at",
    "matched_by",
]


class DiscoveryBatchOutcomeRow(JsonModel):
    run_id: str | None = None
    repository: str = "pride"
    project_accession: str
    project_title: str | None = None
    file_name: str
    download_url: str | None = None
    file_type: str
    file_role: str
    validity_status: str
    task_type: str | None = None
    task_readiness_status: str | None = None
    evidence_level: str = "unknown"
    sdrf_match_status: str = "not_checked"
    batch_id: str | None = None
    batch_index: int | None = None
    batch_input: str | None = None
    batch_status: str = "not_submitted"
    batch_error: str = ""
    batch_output_dir: str | None = None
    batch_started_at: str | None = None
    batch_finished_at: str | None = None
    matched_by: str = "not_matched"


class DiscoveryBatchOutcomeReport(JsonModel):
    run_id: str | None = None
    batch_id: str
    batch_status: str
    manifest_file_count: int = 0
    submitted_files: int = 0
    matched_submitted_files: int = 0
    unmatched_batch_items: int = 0
    completed_items: int = 0
    failed_items: int = 0
    needs_review_items: int = 0
    queued_or_running_items: int = 0
    submitted_success_rate: float = 0.0
    submitted_failure_rate: float = 0.0
    submitted_needs_review_rate: float = 0.0
    manifest_completion_rate: float = 0.0
    batch_status_counts: dict[str, int] = Field(default_factory=dict)
    by_validity_status: dict[str, dict[str, Any]] = Field(default_factory=dict)
    by_task_readiness_status: dict[str, dict[str, Any]] = Field(default_factory=dict)
    by_file_role: dict[str, dict[str, Any]] = Field(default_factory=dict)
    by_project_accession: dict[str, dict[str, Any]] = Field(default_factory=dict)
    unmatched_items: list[dict[str, Any]] = Field(default_factory=list)
    rows: list[DiscoveryBatchOutcomeRow] = Field(default_factory=list)


def _batch_context(item: dict[str, Any]) -> dict[str, Any]:
    context = item.get("discovery_context")
    return dict(context) if isinstance(context, dict) else {}


def _item_file_name(item: dict[str, Any]) -> str:
    context = _batch_context(item)
    return str(context.get("file_name") or item.get("input") or "").strip()


def _item_project_accession(item: dict[str, Any]) -> str:
    context = _batch_context(item)
    return str(context.get("project_accession") or "").strip()


def _manifest_indexes(files: list[DiscoveredFile]) -> tuple[dict[tuple[str, str], DiscoveredFile], dict[str, DiscoveredFile]]:
    by_project_file = {(file.project_accession, file.file_name): file for file in files}
    by_name: dict[str, DiscoveredFile] = {}
    name_counts = Counter(file.file_name for file in files)
    for file in files:
        if name_counts[file.file_name] == 1:
            by_name[file.file_name] = file
    return by_project_file, by_name


def _match_batch_items(
    manifest: DatasetManifest,
    batch_manifest: dict[str, Any],
) -> tuple[dict[tuple[str, str], tuple[dict[str, Any], str]], list[dict[str, Any]]]:
    by_project_file, by_name = _manifest_indexes(manifest.files)
    matched: dict[tuple[str, str], tuple[dict[str, Any], str]] = {}
    unmatched: list[dict[str, Any]] = []
    for raw in batch_manifest.get("items") or []:
        if not isinstance(raw, dict):
            continue
        file_name = _item_file_name(raw)
        project_accession = _item_project_accession(raw)
        key: tuple[str, str] | None = None
        matched_by = ""
        if project_accession and file_name and (project_accession, file_name) in by_project_file:
            key = (project_accession, file_name)
            matched_by = "project_accession+file_name"
        elif file_name in by_name:
            file = by_name[file_name]
            key = (file.project_accession, file.file_name)
            matched_by = "unique_file_name"
        if key is None:
            unmatched.append(raw)
            continue
        matched[key] = (raw, matched_by)
    return matched, unmatched


def _row_from_file(
    file: DiscoveredFile,
    *,
    run_id: str | None,
    batch_id: str,
    item: dict[str, Any] | None,
    matched_by: str,
) -> DiscoveryBatchOutcomeRow:
    if item is None:
        return DiscoveryBatchOutcomeRow(
            run_id=run_id,
            repository=file.repository,
            project_accession=file.project_accession,
            project_title=file.project_title,
            file_name=file.file_name,
            download_url=file.download_url,
            file_type=file.file_type,
            file_role=file.file_role,
            validity_status=file.validity_status,
            task_type=file.task_type,
            task_readiness_status=file.task_readiness_status,
            evidence_level=file.evidence_level,
            sdrf_match_status=file.sdrf_match_status,
        )
    return DiscoveryBatchOutcomeRow(
        run_id=run_id,
        repository=file.repository,
        project_accession=file.project_accession,
        project_title=file.project_title,
        file_name=file.file_name,
        download_url=file.download_url,
        file_type=file.file_type,
        file_role=file.file_role,
        validity_status=file.validity_status,
        task_type=file.task_type,
        task_readiness_status=file.task_readiness_status,
        evidence_level=file.evidence_level,
        sdrf_match_status=file.sdrf_match_status,
        batch_id=batch_id,
        batch_index=int(item["index"]) if str(item.get("index") or "").isdigit() else None,
        batch_input=str(item.get("input") or ""),
        batch_status=str(item.get("status") or "unknown"),
        batch_error=str(item.get("error") or ""),
        batch_output_dir=str(item.get("output_dir") or "") or None,
        batch_started_at=item.get("started_at"),
        batch_finished_at=item.get("finished_at"),
        matched_by=matched_by,
    )


def _group_metrics(rows: list[DiscoveryBatchOutcomeRow], field_name: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[DiscoveryBatchOutcomeRow]] = defaultdict(list)
    for row in rows:
        value = getattr(row, field_name) or "unknown"
        grouped[str(value)].append(row)
    metrics: dict[str, dict[str, Any]] = {}
    for value, group in grouped.items():
        submitted = [row for row in group if row.batch_status != "not_submitted"]
        completed = sum(1 for row in submitted if row.batch_status == "completed")
        failed = sum(1 for row in submitted if row.batch_status == "failed")
        needs_review = sum(1 for row in submitted if row.batch_status == "needs_review")
        metrics[value] = {
            "manifest_files": len(group),
            "submitted_files": len(submitted),
            "completed_items": completed,
            "failed_items": failed,
            "needs_review_items": needs_review,
            "submitted_success_rate": round(completed / len(submitted), 6) if submitted else 0.0,
            "submitted_failure_rate": round(failed / len(submitted), 6) if submitted else 0.0,
            "submitted_needs_review_rate": round(needs_review / len(submitted), 6) if submitted else 0.0,
        }
    return dict(sorted(metrics.items()))


def build_discovery_batch_outcome_report(
    manifest: DatasetManifest,
    batch_manifest: dict[str, Any],
) -> DiscoveryBatchOutcomeReport:
    batch_id = str(batch_manifest.get("batch_id") or "")
    matched, unmatched = _match_batch_items(manifest, batch_manifest)
    rows = [
        _row_from_file(
            file,
            run_id=manifest.run_id,
            batch_id=batch_id,
            item=matched.get((file.project_accession, file.file_name), (None, "not_matched"))[0],
            matched_by=matched.get((file.project_accession, file.file_name), (None, "not_matched"))[1],
        )
        for file in manifest.files
    ]
    submitted = [row for row in rows if row.batch_status != "not_submitted"]
    status_counts = Counter(row.batch_status for row in submitted)
    completed = status_counts.get("completed", 0)
    failed = status_counts.get("failed", 0)
    needs_review = status_counts.get("needs_review", 0)
    queued_or_running = sum(
        count
        for status, count in status_counts.items()
        if status in {"queued", "running"} or status not in {"completed", "failed", "needs_review"}
    )
    return DiscoveryBatchOutcomeReport(
        run_id=manifest.run_id,
        batch_id=batch_id,
        batch_status=str(batch_manifest.get("status") or "unknown"),
        manifest_file_count=len(manifest.files),
        submitted_files=len(submitted),
        matched_submitted_files=len(submitted),
        unmatched_batch_items=len(unmatched),
        completed_items=completed,
        failed_items=failed,
        needs_review_items=needs_review,
        queued_or_running_items=queued_or_running,
        submitted_success_rate=round(completed / len(submitted), 6) if submitted else 0.0,
        submitted_failure_rate=round(failed / len(submitted), 6) if submitted else 0.0,
        submitted_needs_review_rate=round(needs_review / len(submitted), 6) if submitted else 0.0,
        manifest_completion_rate=round(completed / len(manifest.files), 6) if manifest.files else 0.0,
        batch_status_counts=dict(sorted(status_counts.items())),
        by_validity_status=_group_metrics(rows, "validity_status"),
        by_task_readiness_status=_group_metrics(rows, "task_readiness_status"),
        by_file_role=_group_metrics(rows, "file_role"),
        by_project_accession=_group_metrics(rows, "project_accession"),
        unmatched_items=unmatched,
        rows=rows,
    )


def _row_to_csv(row: DiscoveryBatchOutcomeRow) -> dict[str, Any]:
    payload = row.model_dump(mode="json")
    return {column: payload.get(column, "") for column in OUTCOME_COLUMNS}


def write_discovery_batch_outcome_report(
    manifest: DatasetManifest,
    batch_manifest: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report = build_discovery_batch_outcome_report(manifest, batch_manifest)
    paths = {
        "discovery_batch_outcome_report": output_dir / "discovery_batch_outcome_report.json",
        "discovery_batch_outcomes": output_dir / "discovery_batch_outcomes.csv",
        "unmatched_batch_items": output_dir / "unmatched_batch_items.json",
    }
    write_json(paths["discovery_batch_outcome_report"], report.model_dump(mode="json"))
    write_json(paths["unmatched_batch_items"], report.unmatched_items)
    with paths["discovery_batch_outcomes"].open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTCOME_COLUMNS)
        writer.writeheader()
        for row in report.rows:
            writer.writerow(_row_to_csv(row))
    return paths
