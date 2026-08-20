from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.operations.repository import OperationsRepository


_ACTIVE_HISTORY_STATUSES = {
    "queued",
    "running",
    "searching",
    "reviewing",
    "finalizing",
}
_RESERVED_HISTORY_IDENTITIES = {
    "_batches",
    "_history",
    "_operations",
    "batches",
}


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _identity_text(*values: Any) -> str:
    for value in values:
        candidate = str(value or "").strip()
        if candidate and candidate.casefold() not in {"none", "null", "undefined"}:
            return candidate
    return ""


def _clean_legacy_text(value: Any) -> str:
    return str(value or "").strip().replace("Â·", "·")


def is_material_legacy_history_record(
    item: dict[str, Any],
    *,
    source_id: str,
) -> bool:
    """Return whether a compatibility history row belongs in the live index."""

    normalized_id = _identity_text(source_id).casefold()
    if not normalized_id or normalized_id in _RESERVED_HISTORY_IDENTITIES:
        return False
    material_counts = bool(
        _safe_int(item.get("project_count"))
        or _safe_int(item.get("file_count"))
    )
    status = _identity_text(item.get("status")).lower() or "interrupted"
    return material_counts or status in _ACTIVE_HISTORY_STATUSES


def _read_small_json(path: Path, *, byte_limit: int = 1_000_000) -> dict[str, Any]:
    try:
        if not path.is_file() or path.stat().st_size > byte_limit:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def import_legacy_discovery_summaries(
    repository: OperationsRepository,
    runs_root: Path,
) -> dict[str, int]:
    """Index compact discovery sidecars without loading legacy giant job JSON.

    Legacy artifacts stay read-only. The importer is idempotent through job_id
    and does not delete stale files; lifecycle cleanup remains an explicit user
    action with a preview.
    """

    summaries_root = runs_root / "discovery_jobs" / "_history"
    stats = {"seen": 0, "imported": 0, "skipped": 0, "failed": 0}
    if not summaries_root.is_dir():
        return stats
    for path in sorted(summaries_root.glob("*.json")):
        stats["seen"] += 1
        summary = _read_small_json(path)
        job_id = str(summary.get("job_id") or "").strip()
        if not job_id:
            stats["skipped"] += 1
            continue
        legacy_path = runs_root / "discovery_jobs" / f"{job_id}.json"
        has_material_result = bool(
            _safe_int(summary.get("project_count"))
            or _safe_int(summary.get("file_count"))
            or legacy_path.is_file()
        )
        if not has_material_result:
            stats["skipped"] += 1
            continue
        try:
            existing = repository.get_job(job_id)
            repository.sync_legacy_job(
                {
                    "job_id": job_id,
                    "status": summary.get("status") or "interrupted",
                    "created_at": summary.get("created_at")
                    or summary.get("history_time")
                    or summary.get("updated_at"),
                    "started_at": summary.get("started_at"),
                    "finished_at": summary.get("finished_at"),
                    "updated_at": summary.get("updated_at")
                    or summary.get("history_time"),
                    "resumable": summary.get("resumable"),
                    "error": summary.get("error"),
                    "body": {
                        "objective": summary.get("objective")
                        or summary.get("display_name")
                        or summary.get("input_value")
                        or job_id,
                        "repository": summary.get("repository") or "pride",
                        "species": summary.get("species") or [],
                    },
                    "execution_state": {
                        "phase": summary.get("phase"),
                        "candidate_count": summary.get("candidate_count")
                        or summary.get("project_count")
                        or 0,
                        "reviewed_project_count": summary.get("reviewed_project_count")
                        or summary.get("project_count")
                        or 0,
                        "pending_review_count": summary.get("pending_review_count")
                        or 0,
                        "usable_file_count": summary.get("file_count") or 0,
                    },
                    "record": {
                        "project_count": summary.get("project_count") or 0,
                        "file_count": summary.get("file_count") or 0,
                    },
                },
                legacy_path=str(legacy_path) if legacy_path.is_file() else None,
                append_sync_event=existing is None,
            )
            stats["imported"] += 1
        except Exception:
            stats["failed"] += 1
    return stats


def import_legacy_history_index(
    repository: OperationsRepository,
    runs_root: Path,
) -> dict[str, int]:
    """Import only material legacy history rows into the indexed history table."""

    stats = {"seen": 0, "imported": 0, "skipped": 0, "failed": 0}
    path = runs_root / "project_history.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    except (OSError, UnicodeError, json.JSONDecodeError):
        raw = []
    records = raw if isinstance(raw, list) else []
    pending: list[
        tuple[dict[str, Any], str, str, str | None, int]
    ] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        stats["seen"] += 1
        kind = str(item.get("kind") or "").strip().lower()
        if not kind:
            kind = (
                "batch"
                if item.get("batch_id")
                else "discovery"
                if item.get("discovery_id") or item.get("job_id")
                else "task"
            )
        source_id = (
            _identity_text(
                item.get("job_id"),
                item.get("source_discovery_job_id"),
                item.get("discovery_id"),
                item.get("run_id"),
                item.get("result_id"),
            )
            if kind == "discovery"
            else _identity_text(item.get("batch_id"), item.get("run_id"))
            if kind == "batch"
            else _identity_text(item.get("task_id"), item.get("result_id"))
        )
        status = _identity_text(item.get("status")).lower() or "interrupted"
        if not is_material_legacy_history_record(item, source_id=source_id):
            stats["skipped"] += 1
            continue
        normalized = dict(item)
        normalized["display_name"] = _clean_legacy_text(
            item.get("display_name")
            or item.get("input_value")
            or item.get("name")
            or source_id
        )
        if kind == "discovery":
            try:
                existing = repository.get_job(source_id)
                repository.sync_legacy_job(
                    {
                        "job_id": source_id,
                        "status": status,
                        "created_at": item.get("created_at")
                        or item.get("history_time")
                        or item.get("updated_at"),
                        "finished_at": item.get("finished_at"),
                        "updated_at": item.get("updated_at")
                        or item.get("history_time"),
                        "resumable": item.get("resumable"),
                        "error": item.get("error") or item.get("error_summary"),
                        "body": {
                            "objective": item.get("objective")
                            or item.get("input_value")
                            or normalized["display_name"],
                            "repository": item.get("repository") or "pride",
                            "species": item.get("species") or [],
                        },
                        "execution_state": {
                            "phase": item.get("phase") or status,
                            "candidate_count": item.get("candidate_count")
                            or item.get("project_count")
                            or 0,
                            "reviewed_project_count": item.get(
                                "reviewed_project_count"
                            )
                            or item.get("project_count")
                            or 0,
                            "pending_review_count": item.get(
                                "pending_review_count"
                            )
                            or 0,
                            "usable_file_count": item.get("file_count") or 0,
                        },
                        "record": {
                            "project_count": item.get("project_count") or 0,
                            "file_count": item.get("file_count") or 0,
                        },
                    },
                    append_sync_event=existing is None,
                )
            except Exception:
                stats["failed"] += 1
                continue
        pending.append(
            (
                normalized,
                kind,
                source_id,
                f"{kind}:{source_id}",
                _safe_int(item.get("size_bytes")),
            )
        )
    try:
        stats["imported"] = repository.upsert_history_records_bulk(pending)
    except Exception:
        stats["failed"] += len(pending)
    return stats
