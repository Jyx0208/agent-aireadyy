from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import case, exists, func, or_, select, text
from sqlalchemy.orm import Session, aliased

from agent.discovery.file_judgment import stable_file_id
from agent.operations.config import OperationsSettings
from agent.operations.database import OperationsDatabase
from agent.operations.models import (
    Batch,
    BatchFile,
    DeletionRequest,
    FileRecord,
    HistoryEntry,
    Job,
    JobEvent,
    JobTerm,
    ProjectReview,
    utc_now_iso,
)
from agent.operations.state import (
    ACTIVE_STATUSES,
    JobStatus,
    TERMINAL_STATUSES,
    coerce_status,
    status_group,
    validate_transition,
)


_MAX_EVENT_MESSAGE = 2_000
_MAX_EVENT_PAYLOAD_BYTES = 16_384
_DEFAULT_MAX_PAGE_SIZE = 200
_EXECUTION_DISCOVERY_PREFIX = "agents_job_"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed


def _float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _json_value(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return fallback


def _bounded_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    clean = _json_value(dict(payload or {}), {})
    encoded = json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) <= _MAX_EVENT_PAYLOAD_BYTES:
        return clean
    summary: dict[str, Any] = {}
    for key in (
        "term",
        "query",
        "status",
        "phase",
        "repository",
        "project_accession",
        "accession",
        "batch_index",
        "candidate_count",
        "reviewed_project_count",
        "pending_review_count",
        "qualified_count",
        "usable_file_count",
        "raw_count",
        "unique_count",
        "error_code",
        "reason",
    ):
        if key in clean:
            summary[key] = clean[key]
    summary["_payload_truncated"] = True
    summary["_original_bytes"] = len(encoded.encode("utf-8"))
    return summary


def _legacy_status(job: Mapping[str, Any]) -> tuple[JobStatus, str]:
    raw = _text(job.get("status")).lower() or "queued"
    execution = job.get("execution_state")
    execution = execution if isinstance(execution, Mapping) else {}
    phase = _text(execution.get("phase")).lower()
    if raw in {"completed", "failed", "blocked", "cancelled", "interrupted"}:
        return coerce_status(raw), phase or raw
    if raw == "durability_failed":
        return JobStatus.FAILED, phase or "finalizing"
    if raw == "queued":
        return JobStatus.QUEUED, "queued"
    if phase in {"searching", "reviewing", "finalizing"}:
        return coerce_status(phase), phase
    reviewed = _int(execution.get("reviewed_project_count"))
    candidates = _int(execution.get("candidate_count"))
    if reviewed > 0 or candidates > 0:
        return JobStatus.REVIEWING, "reviewing"
    return JobStatus.SEARCHING, "searching"


def _history_status_group(status: str) -> str:
    try:
        return status_group(status)
    except Exception:
        return "unknown"


def _visible_history_entry_clause():
    """Hide an execution-artifact alias when its canonical job is indexed."""

    canonical = aliased(HistoryEntry)
    canonical_exists = (
        exists(
            select(1)
            .select_from(canonical)
            .where(
                canonical.kind == HistoryEntry.kind,
                canonical.source_id
                == func.substr(
                    HistoryEntry.source_id,
                    len(_EXECUTION_DISCOVERY_PREFIX) + 1,
                ),
            )
        )
        .correlate(HistoryEntry)
    )
    return or_(
        HistoryEntry.kind != "discovery",
        ~HistoryEntry.source_id.startswith(_EXECUTION_DISCOVERY_PREFIX),
        ~canonical_exists,
    )


@dataclass(frozen=True, slots=True)
class Page:
    items: list[dict[str, Any]]
    page: int
    page_size: int
    total: int
    next_cursor: int | None = None
    summary: dict[str, int] | None = None
    has_more: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        pages = max(1, math.ceil(self.total / self.page_size))
        result = {
            "items": self.items,
            "page": self.page,
            "page_size": self.page_size,
            "total": self.total,
            "pages": pages,
            "has_previous": self.page > 1,
            "has_next": self.has_more if self.has_more is not None else self.page < pages,
        }
        if self.next_cursor is not None:
            result["next_cursor"] = self.next_cursor
        if self.summary is not None:
            result["summary"] = self.summary
        return result


class OperationsRepository:
    """Transactional authority for job state and indexed operational evidence."""

    def __init__(
        self,
        settings: OperationsSettings | None = None,
        *,
        migrate: bool = True,
    ) -> None:
        self.settings = settings or OperationsSettings.from_environment()
        self.database = OperationsDatabase(self.settings)
        if migrate:
            self.database.migrate()

    def close(self) -> None:
        self.database.dispose()

    def create_job(
        self,
        *,
        job_id: str,
        payload: Mapping[str, Any],
        terms: Sequence[Mapping[str, Any] | str] = (),
        idempotency_key: str | None = None,
        job_type: str = "discovery",
        created_at: str | None = None,
        legacy_path: str | None = None,
    ) -> dict[str, Any]:
        now = created_at or utc_now_iso()
        body = _json_value(dict(payload), {})
        objective = _text(
            body.get("objective")
            or body.get("scientific_goal")
            or body.get("prompt")
            or body.get("goal")
        )
        species_raw = body.get("species")
        species = ", ".join(str(item) for item in species_raw) if isinstance(species_raw, list) else _text(species_raw)
        repository = _text(body.get("repository")) or "pride"
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            if idempotency_key:
                existing = session.scalar(
                    select(Job).where(Job.idempotency_key == idempotency_key)
                )
                if existing is not None:
                    session.rollback()
                    return self._job_snapshot(existing)
            existing_by_id = session.get(Job, job_id)
            if existing_by_id is not None:
                session.rollback()
                return self._job_snapshot(existing_by_id)
            job = Job(
                job_id=job_id,
                job_type=job_type,
                idempotency_key=idempotency_key or None,
                status=JobStatus.QUEUED.value,
                phase="queued",
                objective=objective,
                repository=repository,
                species=species,
                payload=body,
                summary={},
                created_at=now,
                updated_at=now,
                heartbeat_at=now,
                term_total=len(terms),
                worker_count=self.settings.worker_count,
                legacy_path=legacy_path,
            )
            session.add(job)
            for position, item in enumerate(terms, start=1):
                data = item if isinstance(item, Mapping) else {"term": item}
                term = _text(data.get("term") or data.get("query") or item)
                if not term:
                    continue
                session.add(
                    JobTerm(
                        job_id=job_id,
                        position=position,
                        term=term,
                        role=_text(data.get("role")) or "theme_synonym",
                        status="pending",
                        updated_at=now,
                    )
                )
            sequence = self._append_event_locked(
                session,
                job,
                event_type="job_queued",
                level="info",
                actor="operations",
                phase="queued",
                message="任务已进入持久队列。",
                payload={"term_total": len(terms), "job_type": job_type},
                created_at=now,
            )
            job.summary = {"last_event_sequence": sequence}
            self._upsert_history_locked(session, job)
            session.commit()
            return self._job_snapshot(job)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            job = session.get(Job, job_id)
            return self._job_snapshot(job) if job is not None else None

    def get_job_by_idempotency_key(self, key: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            job = session.scalar(select(Job).where(Job.idempotency_key == key))
            return self._job_snapshot(job) if job is not None else None

    def get_job_payload(self, job_id: str) -> dict[str, Any]:
        """Return the durable execution input for a worker, never for an API response."""

        with self.database.session() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise KeyError(job_id)
            return dict(job.payload or {})

    def set_job_result(
        self,
        job_id: str,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist a bounded terminal projection without embedding large artifacts."""

        now = utc_now_iso()
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            job = session.get(Job, job_id)
            if job is None:
                raise KeyError(job_id)
            job.summary = {
                **dict(job.summary or {}),
                "result": _bounded_payload(result),
            }
            job.updated_at = now
            job.version += 1
            self._upsert_history_locked(session, job)
            session.commit()
            return self._job_snapshot(job)

    def sync_legacy_job(
        self,
        job: Mapping[str, Any],
        *,
        legacy_path: str | None = None,
        append_sync_event: bool = False,
    ) -> dict[str, Any]:
        job_id = _text(job.get("job_id"))
        if not job_id:
            raise ValueError("job_id_required")
        body = job.get("body")
        body = body if isinstance(body, Mapping) else {}
        status, phase = _legacy_status(job)
        execution = job.get("execution_state")
        execution = execution if isinstance(execution, Mapping) else {}
        record = job.get("record")
        record = record if isinstance(record, Mapping) else {}
        error = _text(job.get("error")) or None
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = session.get(Job, job_id)
            if row is None:
                session.rollback()
                snapshot = self.create_job(
                    job_id=job_id,
                    payload=body,
                    terms=self._legacy_terms(execution, body),
                    idempotency_key=_text(job.get("idempotency_key")) or None,
                    created_at=_text(job.get("created_at")) or None,
                    legacy_path=legacy_path,
                )
                with self.database.session() as retry_session:
                    retry_session.execute(text("BEGIN IMMEDIATE"))
                    row = retry_session.get(Job, job_id)
                    if row is None:
                        raise RuntimeError("operations_job_create_failed")
                    self._apply_legacy_projection(row, job, status=status, phase=phase)
                    self._sync_legacy_entities_locked(retry_session, row, job)
                    if append_sync_event:
                        self._append_event_locked(
                            retry_session,
                            row,
                            event_type="legacy_job_synchronized",
                            level="info",
                            actor="migration",
                            phase=phase,
                            message="旧任务状态已同步到 operations 数据库。",
                            payload={},
                            created_at=_text(job.get("updated_at")) or utc_now_iso(),
                        )
                    self._upsert_history_locked(retry_session, row)
                    retry_session.commit()
                    return self._job_snapshot(row)
            self._apply_legacy_projection(row, job, status=status, phase=phase)
            self._sync_legacy_entities_locked(session, row, job)
            if append_sync_event:
                self._append_event_locked(
                    session,
                    row,
                    event_type="legacy_job_synchronized",
                    level="info",
                    actor="migration",
                    phase=phase,
                    message="旧任务状态已同步到 operations 数据库。",
                    payload={},
                    created_at=_text(job.get("updated_at")) or utc_now_iso(),
                )
            self._upsert_history_locked(session, row)
            session.commit()
            return self._job_snapshot(row)

    def _apply_legacy_projection(
        self,
        row: Job,
        job: Mapping[str, Any],
        *,
        status: JobStatus,
        phase: str,
    ) -> None:
        body = job.get("body")
        body = body if isinstance(body, Mapping) else {}
        execution = job.get("execution_state")
        execution = execution if isinstance(execution, Mapping) else {}
        record = job.get("record")
        record = record if isinstance(record, Mapping) else {}
        now = _text(execution.get("updated_at") or job.get("updated_at")) or utc_now_iso()
        incoming_status = status.value
        current_status = row.status
        stale_queued_projection = (
            incoming_status == JobStatus.QUEUED.value
            and current_status != JobStatus.QUEUED.value
        )
        stale_active_projection = (
            status in ACTIVE_STATUSES
            and current_status in {
                terminal.value for terminal in TERMINAL_STATUSES
            }
        )
        if not stale_queued_projection and not stale_active_projection:
            row.status = incoming_status
            row.phase = phase or incoming_status
        row.payload = _json_value(dict(body), {})
        row.objective = _text(
            body.get("objective")
            or body.get("scientific_goal")
            or body.get("prompt")
            or row.objective
        )
        row.repository = _text(body.get("repository")) or row.repository or "pride"
        row.current_term = _text(
            execution.get("current_term")
            or execution.get("current_query")
            or execution.get("active_term")
        )
        terms = execution.get("terms")
        row.term_total = len(terms) if isinstance(terms, list) else row.term_total
        row.term_completed = sum(
            1
            for item in (terms if isinstance(terms, list) else [])
            if isinstance(item, Mapping)
            and _text(item.get("status")).lower() in {"completed", "failed", "skipped", "exhausted"}
        )
        row.candidate_count = max(
            row.candidate_count,
            _int(execution.get("candidate_count")),
            _int(record.get("project_count")),
        )
        row.raw_hit_count = max(
            row.raw_hit_count,
            _int(execution.get("raw_hit_count")),
        )
        row.reviewed_count = max(
            row.reviewed_count,
            _int(execution.get("reviewed_project_count")),
        )
        row.pending_review_count = max(
            0,
            _int(execution.get("pending_review_count")),
        )
        row.qualified_count = max(
            row.qualified_count,
            _int(execution.get("qualified_project_count")),
            _int(record.get("project_count")),
        )
        row.file_clue_count = max(
            row.file_clue_count,
            _int(execution.get("candidate_file_count")),
            _int(record.get("candidate_file_count")),
        )
        row.usable_file_count = max(
            row.usable_file_count,
            _int(execution.get("usable_file_count")),
            _int(record.get("file_count")),
        )
        row.batch_count = max(
            row.batch_count,
            len(job.get("result_batches") or []),
        )
        row.cancel_requested = row.cancel_requested or bool(
            job.get("cancel_requested")
        )
        row.resumable = row.resumable or bool(job.get("resumable"))
        row.started_at = _text(job.get("started_at")) or row.started_at
        row.finished_at = _text(job.get("finished_at")) or row.finished_at
        row.updated_at = now
        row.heartbeat_at = now if status in ACTIVE_STATUSES else row.heartbeat_at
        if status in ACTIVE_STATUSES or incoming_status == JobStatus.COMPLETED.value:
            # A resumed/active operations record must not surface a stale error
            # copied from compatibility JSON. The original failure remains in
            # the immutable event stream, while the live snapshot describes the
            # current attempt. A completed record is likewise authoritative over
            # an earlier failed resume attempt.
            row.error_message = None
            row.error_code = None
        else:
            row.error_message = _text(job.get("error")) or None
            row.error_code = (
                "legacy_durability_failed"
                if _text(job.get("status")).lower() == "durability_failed"
                else row.error_code
            )
        row.summary = {
            **dict(row.summary or {}),
            "legacy_record_available": bool(record),
            "last_event_sequence": row.event_sequence,
        }
        row.version += 1

    def _sync_legacy_entities_locked(
        self,
        session: Session,
        job_row: Job,
        job: Mapping[str, Any],
    ) -> None:
        record = job.get("record")
        record = record if isinstance(record, Mapping) else {}
        projects = record.get("projects")
        existing_reviews = {
            (row.repository, row.accession): row
            for row in session.scalars(
                select(ProjectReview).where(
                    ProjectReview.job_id == job_row.job_id
                )
            ).all()
        }
        if isinstance(projects, Sequence) and not isinstance(projects, str):
            base_position = (
                session.scalar(
                    select(func.coalesce(func.max(ProjectReview.position), 0)).where(
                        ProjectReview.job_id == job_row.job_id
                    )
                )
                or 0
            )
            for offset, item in enumerate(projects, start=1):
                if not isinstance(item, Mapping):
                    continue
                accession = _text(
                    item.get("project_accession")
                    or item.get("accession")
                    or item.get("primary_accession")
                )
                if not accession:
                    continue
                repository = _text(item.get("repository")) or job_row.repository or "pride"
                review = existing_reviews.get((repository, accession))
                if review is None:
                    review = ProjectReview(
                        job_id=job_row.job_id,
                        repository=repository,
                        accession=accession,
                        position=base_position + offset,
                        created_at=job_row.created_at,
                    )
                    session.add(review)
                    existing_reviews[(repository, accession)] = review
                review.title = _text(item.get("title") or item.get("project_title"))
                review.status = "completed"
                review.current_step = "completed"
                review.decision = _text(
                    item.get("decision")
                    or item.get("judgment")
                    or item.get("review_status")
                ) or "qualified"
                review.reason_code = _text(
                    item.get("reason_code")
                    or item.get("primary_reason")
                ) or review.reason_code
                review.score = _float(item.get("score")) or review.score
                review.confidence = _float(item.get("confidence")) or review.confidence
                review.reasons = _json_value(item.get("reasons") or review.reasons, [])
                review.evidence_summary = _bounded_payload(
                    item.get("evidence")
                    if isinstance(item.get("evidence"), Mapping)
                    else {
                        "evidence_refs": item.get("evidence_refs") or [],
                        "selection_reason": item.get("selection_reason"),
                    }
                )
                review.metadata_summary = _bounded_payload(
                    {
                        key: item.get(key)
                        for key in (
                            "species",
                            "acquisition_mode",
                            "hla_class",
                            "publication_date",
                        )
                        if item.get(key) is not None
                    }
                )
                review.updated_at = job_row.updated_at
                review.finished_at = job_row.finished_at or job_row.updated_at
        files = record.get("files")
        existing_files = {
            (row.repository, row.project_accession, row.native_id): row
            for row in session.scalars(
                select(FileRecord).where(FileRecord.job_id == job_row.job_id)
            ).all()
        }
        existing_files_by_id = {
            str(row.file_id): row
            for row in existing_files.values()
            if row.file_id
        }
        if isinstance(files, Sequence) and not isinstance(files, str):
            for item in files:
                if not isinstance(item, Mapping):
                    continue
                project_accession = _text(
                    item.get("project_accession")
                    or item.get("accession")
                )
                native_id = _text(
                    item.get("file_accession_or_path")
                    or item.get("native_id")
                    or item.get("file_name")
                    or item.get("logical_path")
                )
                if not native_id:
                    continue
                repository = _text(item.get("repository")) or job_row.repository or "pride"
                file_id = _text(item.get("file_id")) or stable_file_id(
                    repository,
                    project_accession,
                    native_id,
                )
                file_key = (repository, project_accession, native_id)
                file_row = existing_files.get(file_key) or existing_files_by_id.get(file_id)
                if file_row is None:
                    file_row = FileRecord(
                        job_id=job_row.job_id,
                        repository=repository,
                        project_accession=project_accession,
                        native_id=native_id,
                        file_id=file_id,
                        file_name=_text(item.get("file_name")) or native_id,
                        created_at=job_row.created_at,
                    )
                    session.add(file_row)
                    existing_files[file_key] = file_row
                    existing_files_by_id[file_id] = file_row
                file_row.repository = repository
                file_row.project_accession = project_accession
                file_row.native_id = native_id
                file_row.file_id = file_id
                file_row.file_name = _text(item.get("file_name")) or file_row.file_name
                file_row.logical_path = _text(
                    item.get("logical_path")
                    or item.get("file_accession_or_path")
                )
                file_row.download_url = _text(item.get("download_url"))
                file_row.file_format = _text(
                    item.get("file_format")
                    or item.get("file_type")
                )
                file_row.file_category = _text(item.get("file_category"))
                file_row.file_role = _text(item.get("file_role"))
                file_row.selection_role = _text(item.get("selection_role")) or "primary_input"
                file_row.family_id = _text(item.get("family_id")) or None
                file_row.companion_file_ids = _json_value(
                    item.get("companion_file_ids") or [],
                    [],
                )
                file_row.acquisition_mode = _text(item.get("acquisition_mode")) or "unknown"
                file_row.size_bytes = _int(item.get("size_bytes"), 0) or None
                file_row.status = _text(
                    item.get("status")
                    or item.get("validity_status")
                ) or "usable"
                file_row.review_status = _text(item.get("review_status")) or "unreviewed"
                file_row.decision = _text(
                    item.get("decision") or item.get("judgment_decision")
                ) or None
                file_row.reason_status = _text(item.get("reason_status")) or "pending"
                file_row.reason_scope = _text(item.get("reason_scope")) or "project_legacy"
                file_row.reason_text = _text(
                    item.get("reason_text")
                    or item.get("judgment_explanation")
                ) or None
                file_row.grade = _int(
                    item.get("grade") if item.get("grade") is not None else item.get("final_grade"),
                    -1,
                )
                if file_row.grade < 0:
                    file_row.grade = None
                file_row.hard_gate = _text(item.get("hard_gate")) or None
                file_row.confidence = _float(
                    item.get("judgment_confidence")
                    if item.get("judgment_confidence") is not None
                    else item.get("confidence")
                )
                file_row.judgment_model_id = _text(
                    item.get("judgment_model_id") or item.get("model_id")
                ) or None
                file_row.judgment_version = _text(
                    item.get("judgment_version") or item.get("judgment_rubric_version")
                ) or None
                file_row.limitations = _json_value(
                    item.get("limitations") or item.get("judgment_limitations") or [],
                    [],
                )
                explicit_eligible = item.get("eligible")
                if explicit_eligible is None:
                    explicit_eligible = item.get("usable")
                if file_row.decision is not None:
                    file_row.eligible = file_row.decision == "include"
                else:
                    file_row.eligible = (
                        bool(explicit_eligible)
                        if explicit_eligible is not None
                        else file_row.status
                        not in {"excluded", "invalid", "unusable", "missing"}
                    )
                file_row.reason_code = _text(
                    item.get("reason_code")
                    or item.get("primary_reason")
                ) or None
                file_row.reasons = _json_value(item.get("reasons") or [], [])
                file_row.evidence = _bounded_payload(
                    item.get("evidence")
                    if isinstance(item.get("evidence"), Mapping)
                    else {
                        "evidence_refs": item.get("evidence_refs") or [],
                        "file_role": item.get("file_role"),
                    }
                )
                file_row.updated_at = job_row.updated_at
        for batch_payload in job.get("result_batches") or []:
            if isinstance(batch_payload, Mapping):
                self._upsert_batch_locked(
                    session,
                    job_row,
                    batch_payload,
                    created_at=_text(batch_payload.get("published_at"))
                    or job_row.updated_at,
                )
        session.flush()
        if projects:
            job_row.reviewed_count = max(
                job_row.reviewed_count,
                self._completed_review_count(session, job_row.job_id),
            )
        if files:
            eligible_count = _int(
                session.scalar(
                    select(func.count(FileRecord.id)).where(
                        FileRecord.job_id == job_row.job_id,
                        FileRecord.eligible.is_(True),
                    )
                )
            )
            job_row.usable_file_count = max(job_row.usable_file_count, eligible_count)

    def transition_job(
        self,
        job_id: str,
        requested: str | JobStatus,
        *,
        phase: str,
        reason: str,
        event_type: str = "job_status_changed",
        level: str = "info",
        actor: str = "worker",
        payload: Mapping[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        resumable: bool | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            job = session.get(Job, job_id)
            if job is None:
                raise KeyError(job_id)
            transition = validate_transition(
                job.status,
                requested,
                phase=phase,
                reason=reason,
                event_type=event_type,
            )
            job.status = transition.current.value
            job.phase = phase
            job.updated_at = now
            job.heartbeat_at = now
            job.version += 1
            if job.started_at is None and transition.current in ACTIVE_STATUSES:
                job.started_at = now
            if transition.current in TERMINAL_STATUSES:
                job.finished_at = now
            job.error_code = error_code
            job.error_message = error_message
            if transition.current == JobStatus.QUEUED:
                job.cancel_requested = False
                job.finished_at = None
            if resumable is not None:
                job.resumable = resumable
            self._append_event_locked(
                session,
                job,
                event_type=event_type,
                level=level,
                actor=actor,
                phase=phase,
                message=reason,
                payload={
                    "previous_status": transition.previous.value,
                    "status": transition.current.value,
                    **dict(payload or {}),
                },
                created_at=now,
            )
            self._upsert_history_locked(session, job)
            session.commit()
            return self._job_snapshot(job)

    def heartbeat(
        self,
        job_id: str,
        *,
        phase: str | None = None,
        current_term: str | None = None,
        worker_count: int | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            job = session.get(Job, job_id)
            if job is None:
                raise KeyError(job_id)
            job.heartbeat_at = now
            job.updated_at = now
            if phase:
                job.phase = phase
            if current_term is not None:
                job.current_term = current_term
            if worker_count is not None:
                job.worker_count = max(0, worker_count)
            job.version += 1
            session.commit()
            return self._job_snapshot(job)

    def request_cancel(self, job_id: str) -> dict[str, Any]:
        now = utc_now_iso()
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            job = session.get(Job, job_id)
            if job is None:
                raise KeyError(job_id)
            if job.status in {status.value for status in TERMINAL_STATUSES}:
                session.rollback()
                return self._job_snapshot(job)
            job.cancel_requested = True
            job.updated_at = now
            job.version += 1
            if job.status == JobStatus.QUEUED.value:
                job.status = JobStatus.CANCELLED.value
                job.phase = JobStatus.CANCELLED.value
                job.finished_at = now
                job.resumable = True
            self._append_event_locked(
                session,
                job,
                event_type="job_cancel_requested",
                level="warning",
                actor="user",
                phase=job.phase,
                message="用户已请求停止；当前外部调用完成后安全退出。",
                payload={},
                created_at=now,
            )
            self._upsert_history_locked(session, job)
            session.commit()
            return self._job_snapshot(job)

    def cancel_requested(self, job_id: str) -> bool:
        with self.database.session() as session:
            value = session.scalar(
                select(Job.cancel_requested).where(Job.job_id == job_id)
            )
            return bool(value)

    def append_event(
        self,
        job_id: str,
        *,
        event_type: str,
        level: str = "info",
        actor: str = "worker",
        phase: str = "",
        message: str = "",
        payload: Mapping[str, Any] | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        now = created_at or utc_now_iso()
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            job = session.get(Job, job_id)
            if job is None:
                raise KeyError(job_id)
            sequence = self._append_event_locked(
                session,
                job,
                event_type=event_type,
                level=level,
                actor=actor,
                phase=phase or job.phase,
                message=message,
                payload=payload,
                created_at=now,
            )
            self._project_event_locked(
                session,
                job,
                event_type=event_type,
                phase=phase,
                payload=payload or {},
                created_at=now,
            )
            job.updated_at = now
            job.heartbeat_at = now
            job.version += 1
            job.summary = {
                **dict(job.summary or {}),
                "last_event_sequence": sequence,
            }
            self._upsert_history_locked(session, job)
            session.commit()
            return {
                "job_id": job_id,
                "sequence": sequence,
                "snapshot": self._job_snapshot(job),
            }

    def _append_event_locked(
        self,
        session: Session,
        job: Job,
        *,
        event_type: str,
        level: str,
        actor: str,
        phase: str,
        message: str,
        payload: Mapping[str, Any] | None,
        created_at: str,
    ) -> int:
        # SQLAlchemy column defaults are applied on INSERT, while the initial
        # queued event is appended before the first flush.
        job.event_sequence = int(job.event_sequence or 0) + 1
        event = JobEvent(
            job_id=job.job_id,
            sequence=job.event_sequence,
            event_type=_text(event_type) or "job_event",
            level=_text(level) or "info",
            actor=_text(actor) or "system",
            phase=_text(phase),
            message=_text(message)[:_MAX_EVENT_MESSAGE],
            payload=_bounded_payload(payload),
            created_at=created_at,
        )
        session.add(event)
        return job.event_sequence

    def _project_event_locked(
        self,
        session: Session,
        job: Job,
        *,
        event_type: str,
        phase: str,
        payload: Mapping[str, Any],
        created_at: str,
    ) -> None:
        event_name = _text(event_type)
        terminal_values = {status.value for status in TERMINAL_STATUSES}
        if phase and (
            job.status not in terminal_values or phase in terminal_values
        ):
            job.phase = phase
        self._apply_count_projection(job, payload)
        if event_name == "job_enqueued":
            queue_task_id = _text(payload.get("queue_task_id"))
            if queue_task_id:
                job.summary = {
                    **dict(job.summary or {}),
                    "queue_task_id": queue_task_id,
                }
        elif event_name == "confirmed_theme_pipeline_started":
            raw_terms = payload.get("terms")
            if isinstance(raw_terms, Sequence) and not isinstance(raw_terms, str):
                for index, term in enumerate(raw_terms, start=1):
                    self._upsert_term_locked(
                        session,
                        job,
                        {
                            "term": term,
                            "role": "primary_theme"
                            if index == 1
                            else "theme_synonym",
                        },
                        status="pending",
                        created_at=created_at,
                    )
                job.term_total = max(job.term_total, len(raw_terms))
        elif event_name == "repository_query_started":
            query_payload = dict(payload)
            query_payload["term"] = _text(
                payload.get("term")
                or payload.get("query")
                or payload.get("executed_query")
            )
            self._upsert_term_locked(
                session,
                job,
                query_payload,
                status="running",
                created_at=created_at,
            )
            job.current_term = query_payload["term"]
            if job.status == JobStatus.QUEUED.value:
                job.status = JobStatus.SEARCHING.value
                job.started_at = job.started_at or created_at
        elif event_name == "repository_query_page_completed":
            query_payload = dict(payload)
            query_payload["term"] = _text(
                payload.get("term")
                or payload.get("query")
                or payload.get("executed_query")
            )
            query_payload["page_count"] = _int(
                payload.get("pages_completed"),
                max(1, _int(payload.get("page_number"))),
            )
            query_payload["raw_count"] = _int(payload.get("cumulative_count"))
            self._upsert_term_locked(
                session,
                job,
                query_payload,
                status="running",
                created_at=created_at,
            )
        elif event_name == "repository_query_completed":
            query_payload = dict(payload)
            query_payload["term"] = _text(
                payload.get("term")
                or payload.get("query")
                or payload.get("executed_query")
            )
            query_payload["page_count"] = _int(payload.get("pages_completed"))
            query_payload["raw_count"] = _int(
                payload.get("raw_result_count") or payload.get("raw_count")
            )
            query_payload["unique_count"] = _int(
                payload.get("new_candidate_count") or payload.get("unique_count")
            )
            self._upsert_term_locked(
                session,
                job,
                query_payload,
                status="completed",
                created_at=created_at,
            )
        elif event_name == "repository_query_failed":
            query_payload = dict(payload)
            query_payload["term"] = _text(
                payload.get("term")
                or payload.get("query")
                or payload.get("executed_query")
            )
            query_payload["page_count"] = _int(payload.get("pages_completed"))
            self._upsert_term_locked(
                session,
                job,
                query_payload,
                status="failed",
                created_at=created_at,
            )
        elif event_name == "confirmed_theme_pipeline_completed":
            complete = (
                _text(payload.get("status")) == "completed"
                and (
                    payload.get("target_reached") is True
                    or payload.get("all_terms_exhausted") is True
                )
                and _int(payload.get("pending_review_count")) == 0
            )
            job.phase = "finalizing" if complete else "failed"
        elif event_name in {
            "repository_term_started",
            "repository_term_search_started",
            "repository_term_task_started",
            "confirmed_theme_term_started",
        }:
            self._upsert_term_locked(
                session,
                job,
                payload,
                status="running",
                created_at=created_at,
            )
            job.current_term = _text(payload.get("term") or payload.get("query"))
            if job.status == JobStatus.QUEUED.value:
                job.status = JobStatus.SEARCHING.value
                job.started_at = job.started_at or created_at
        elif event_name in {
            "repository_term_completed",
            "repository_term_search_completed",
            "repository_term_task_completed",
            "confirmed_theme_term_completed",
            "repository_seed_exhausted",
        }:
            self._upsert_term_locked(
                session,
                job,
                payload,
                status="completed",
                created_at=created_at,
            )
        elif event_name in {
            "repository_term_failed",
            "repository_term_search_failed",
            "repository_term_task_failed",
            "confirmed_theme_term_failed",
        }:
            self._upsert_term_locked(
                session,
                job,
                payload,
                status="failed",
                created_at=created_at,
            )
        elif event_name in {
            "candidate_review_queue_batch_started",
            "candidate_inspection_started",
            "candidate_pipeline_review_started",
            "project_review_started",
            "project_inspection_started",
        }:
            if job.status in {
                JobStatus.SEARCHING.value,
                JobStatus.REVIEWING.value,
            }:
                job.status = JobStatus.REVIEWING.value
                job.phase = "reviewing"
            self._project_reviews_started_locked(
                session,
                job,
                payload,
                created_at=created_at,
            )
        elif event_name == "project_review_step":
            self._project_review_step_locked(
                session,
                job,
                payload,
                created_at=created_at,
            )
        elif event_name in {
            "candidate_review_queue_batch_completed",
            "candidate_inspection_completed",
            "candidate_pipeline_review_completed",
            "project_review_completed",
            "project_inspection_completed",
        }:
            if job.status in {
                JobStatus.SEARCHING.value,
                JobStatus.REVIEWING.value,
            }:
                job.status = JobStatus.REVIEWING.value
                job.phase = "reviewing"
            self._project_reviews_completed_locked(
                session,
                job,
                payload,
                created_at=created_at,
            )
        elif event_name == "project_judgments_recorded":
            if job.status in {
                JobStatus.SEARCHING.value,
                JobStatus.REVIEWING.value,
            }:
                job.status = JobStatus.REVIEWING.value
                job.phase = "reviewing"
            self._project_judgments_recorded_locked(
                session,
                job,
                payload,
                created_at=created_at,
            )
        elif event_name == "verified_project_batch_published":
            self._upsert_batch_locked(session, job, payload, created_at=created_at)
        elif event_name == "repository_term_chunk_completed":
            self._upsert_term_locked(
                session,
                job,
                payload,
                status="running",
                created_at=created_at,
            )
        elif event_name in {"finalization_started", "discovery_finalizing"}:
            if job.status in {
                JobStatus.SEARCHING.value,
                JobStatus.REVIEWING.value,
                JobStatus.FINALIZING.value,
            }:
                job.status = JobStatus.FINALIZING.value
                job.phase = "finalizing"

    def _apply_count_projection(self, job: Job, payload: Mapping[str, Any]) -> None:
        containers: list[Mapping[str, Any]] = [payload]
        metrics = payload.get("metrics")
        if isinstance(metrics, Mapping):
            containers.append(metrics)
        observation = payload.get("observation")
        if isinstance(observation, Mapping):
            containers.append(observation)
            nested_metrics = observation.get("metrics")
            if isinstance(nested_metrics, Mapping):
                containers.append(nested_metrics)

        def maximum(*keys: str, current: int) -> int:
            values = [current]
            for container in containers:
                values.extend(_int(container.get(key)) for key in keys if key in container)
            return max(values)

        job.candidate_count = maximum(
            "candidate_count",
            "candidate_projects",
            "deduped_candidate_count",
            current=job.candidate_count,
        )
        job.raw_hit_count = maximum(
            "raw_hit_count",
            "raw_count",
            "returned_count",
            current=job.raw_hit_count,
        )
        job.reviewed_count = maximum(
            "reviewed_project_count",
            "reviewed_projects",
            "inspected_project_count",
            current=job.reviewed_count,
        )
        pending_values = [
            _int(container.get(key), -1)
            for container in containers
            for key in ("pending_review_count", "pending_projects")
            if key in container
        ]
        if pending_values:
            job.pending_review_count = max(0, pending_values[-1])
        job.qualified_count = maximum(
            "qualified_count",
            "qualified_projects",
            "qualified_project_count",
            "judgment_qualified_projects",
            current=job.qualified_count,
        )
        job.file_clue_count = maximum(
            "candidate_file_count",
            "candidate_files",
            "file_clue_count",
            current=job.file_clue_count,
        )
        job.usable_file_count = maximum(
            "usable_file_count",
            "build_ready_files",
            "selected_file_count",
            current=job.usable_file_count,
        )

    def _upsert_term_locked(
        self,
        session: Session,
        job: Job,
        payload: Mapping[str, Any],
        *,
        status: str,
        created_at: str,
    ) -> None:
        term = _text(
            payload.get("term")
            or payload.get("query")
            or payload.get("search_term")
            or payload.get("theme")
        )
        if not term:
            return
        row = session.scalar(
            select(JobTerm).where(
                JobTerm.job_id == job.job_id,
                func.lower(JobTerm.term) == term.lower(),
            )
        )
        if row is None:
            position = (
                session.scalar(
                    select(func.coalesce(func.max(JobTerm.position), 0)).where(
                        JobTerm.job_id == job.job_id
                    )
                )
                or 0
            ) + 1
            row = JobTerm(
                job_id=job.job_id,
                position=position,
                term=term,
                role=_text(payload.get("role")) or "theme_synonym",
                status=status,
                updated_at=created_at,
            )
            session.add(row)
            session.flush()
            job.term_total = max(job.term_total, position)
        row.status = status
        row.role = _text(payload.get("role")) or row.role
        row.cursor = _text(payload.get("cursor")) or row.cursor
        row.page_count = max(
            row.page_count,
            _int(payload.get("page_count")),
            _int(payload.get("chunk_index")),
            _int(payload.get("chunks_completed")),
        )
        row.raw_count = max(
            row.raw_count,
            _int(payload.get("raw_count")),
            _int(payload.get("returned_count")),
            _int(payload.get("raw_result_count")),
        )
        row.unique_count = max(
            row.unique_count,
            _int(payload.get("unique_count")),
            _int(payload.get("new_candidate_count")),
        )
        row.attempt_count = max(row.attempt_count, _int(payload.get("attempt_count")))
        row.started_at = row.started_at or created_at
        row.updated_at = created_at
        if status in {"completed", "failed", "skipped"}:
            row.finished_at = created_at
        row.error_code = _text(payload.get("error_code")) or None
        row.error_message = _text(payload.get("error") or payload.get("reason")) or None
        session.flush()
        job.raw_hit_count = _int(
            session.scalar(
                select(func.coalesce(func.sum(JobTerm.raw_count), 0)).where(
                    JobTerm.job_id == job.job_id
                )
            )
        )
        job.term_completed = _int(
            session.scalar(
                select(func.count(JobTerm.id)).where(
                    JobTerm.job_id == job.job_id,
                    JobTerm.status.in_(("completed", "failed", "skipped")),
                )
            )
        )

    def _project_reviews_started_locked(
        self,
        session: Session,
        job: Job,
        payload: Mapping[str, Any],
        *,
        created_at: str,
    ) -> None:
        raw_accessions = payload.get("accessions") or payload.get("project_accessions")
        action = payload.get("action")
        if not raw_accessions and isinstance(action, Mapping):
            raw_accessions = action.get("accessions")
        if isinstance(raw_accessions, str):
            accessions = [raw_accessions]
        elif isinstance(raw_accessions, Sequence):
            accessions = [_text(value) for value in raw_accessions if _text(value)]
        else:
            single = _text(payload.get("project_accession") or payload.get("accession"))
            accessions = [single] if single else []
        base_position = (
            session.scalar(
                select(func.coalesce(func.max(ProjectReview.position), 0)).where(
                    ProjectReview.job_id == job.job_id
                )
            )
            or 0
        )
        for offset, accession in enumerate(accessions, start=1):
            row = session.scalar(
                select(ProjectReview).where(
                    ProjectReview.job_id == job.job_id,
                    ProjectReview.repository == (_text(payload.get("repository")) or "pride"),
                    ProjectReview.accession == accession,
                )
            )
            if row is None:
                row = ProjectReview(
                    job_id=job.job_id,
                    repository=_text(payload.get("repository")) or "pride",
                    accession=accession,
                    position=base_position + offset,
                    created_at=created_at,
                    updated_at=created_at,
                )
                session.add(row)
            row.status = "running"
            row.current_step = _text(payload.get("step")) or "metadata"
            requested_slot = _int(payload.get("worker_slot"), 0)
            worker_count = max(1, job.worker_count)
            row.worker_slot = requested_slot or (
                ((base_position + offset - 1) % worker_count) + 1
            )
            row.started_at = row.started_at or created_at
            row.updated_at = created_at

    def _project_reviews_completed_locked(
        self,
        session: Session,
        job: Job,
        payload: Mapping[str, Any],
        *,
        created_at: str,
    ) -> None:
        observation = payload.get("observation")
        observation = observation if isinstance(observation, Mapping) else {}
        raw_assessments = (
            observation.get("project_assessments")
            or payload.get("project_assessments")
            or []
        )
        raw_outcomes = (
            observation.get("inspection_outcomes")
            or payload.get("inspection_outcomes")
            or []
        )
        if isinstance(raw_assessments, Mapping):
            raw_assessments = [raw_assessments]
        if isinstance(raw_outcomes, Mapping):
            raw_outcomes = [raw_outcomes]
        if not isinstance(raw_assessments, Sequence):
            raw_assessments = []
        if not isinstance(raw_outcomes, Sequence):
            raw_outcomes = []
        merged: dict[str, dict[str, Any]] = {}
        for item in [*raw_assessments, *raw_outcomes]:
            if not isinstance(item, Mapping):
                continue
            accession = _text(
                item.get("project_accession")
                or item.get("accession")
            )
            if not accession:
                continue
            merged.setdefault(accession, {}).update(dict(item))
        assessments = list(merged.values())
        if not assessments:
            return
        base_position = (
            session.scalar(
                select(func.coalesce(func.max(ProjectReview.position), 0)).where(
                    ProjectReview.job_id == job.job_id
                )
            )
            or 0
        )
        completed_now = 0
        for offset, assessment in enumerate(assessments, start=1):
            if not isinstance(assessment, Mapping):
                continue
            accession = _text(
                assessment.get("project_accession")
                or assessment.get("accession")
            )
            if not accession:
                continue
            repository = _text(assessment.get("repository")) or "pride"
            row = session.scalar(
                select(ProjectReview).where(
                    ProjectReview.job_id == job.job_id,
                    ProjectReview.repository == repository,
                    ProjectReview.accession == accession,
                )
            )
            if row is None:
                row = ProjectReview(
                    job_id=job.job_id,
                    repository=repository,
                    accession=accession,
                    position=base_position + offset,
                    created_at=created_at,
                )
                session.add(row)
            category = _text(assessment.get("category")).lower()
            decision = _text(
                assessment.get("decision")
                or assessment.get("judgment")
                or assessment.get("status")
            ).lower()
            if not decision:
                decision = {
                    "usable_files": "usable",
                    "scientific_exclusion": "excluded",
                    "no_usable_files": "excluded",
                    "inspection_failure": "failed",
                }.get(category, "investigate")
            qualified = decision in {
                "qualified",
                "keep",
                "selected",
                "usable",
                "accepted",
                "include",
            }
            row.title = _text(assessment.get("project_title") or assessment.get("title"))
            row.status = "completed"
            row.current_step = "completed"
            row.decision = decision or ("qualified" if qualified else "excluded")
            row.reason_code = _text(
                assessment.get("reason_code")
                or assessment.get("primary_reason")
                or assessment.get("reason")
                or assessment.get("error")
                or category
            ) or None
            row.score = _float(assessment.get("score") or assessment.get("grade"))
            row.confidence = _float(assessment.get("confidence"))
            row.discovered_by_terms = _json_value(
                assessment.get("discovered_by_terms") or [],
                [],
            )
            row.reasons = _json_value(
                assessment.get("reasons")
                or assessment.get("reasoning")
                or [value for value in (
                    assessment.get("reason"),
                    assessment.get("error"),
                ) if value]
                or [],
                [],
            )
            final_evidence = _json_value(
                assessment.get("evidence")
                or assessment.get("evidence_summary")
                or {
                    "available_evidence_refs": assessment.get(
                        "available_evidence_refs"
                    )
                    or [],
                    "selected_file_examples": assessment.get(
                        "selected_file_examples"
                    )
                    or [],
                    "file_role_counts": assessment.get("file_role_counts") or {},
                    "filter_reason_counts": assessment.get(
                        "filter_reason_counts"
                    )
                    or {},
                },
                {},
            )
            if not isinstance(final_evidence, Mapping):
                final_evidence = {"evidence": final_evidence}
            row.evidence_summary = {
                **dict(row.evidence_summary or {}),
                **dict(final_evidence),
            }
            final_metadata = _json_value(
                {
                    key: assessment.get(key)
                    for key in (
                        "species",
                        "acquisition_mode",
                        "hla_class",
                        "labeling_strategy",
                        "instrument_names",
                        "validity_status",
                        "sdrf",
                        "sample_processing_excerpt",
                        "data_processing_excerpt",
                    )
                    if assessment.get(key) is not None
                },
                {},
            )
            row.metadata_summary = {
                **dict(row.metadata_summary or {}),
                **dict(final_metadata),
            }
            row.file_clue_count = _int(
                assessment.get("candidate_file_count")
                or assessment.get("file_clue_count")
                or assessment.get("raw_file_count")
            )
            row.usable_file_count = _int(
                assessment.get("usable_file_count")
                or assessment.get("selected_file_count")
            )
            row.elapsed_ms = _int(assessment.get("elapsed_ms"), 0) or None
            row.started_at = row.started_at or created_at
            row.updated_at = created_at
            row.finished_at = created_at
            row.worker_slot = None
            completed_now += 1
        session.flush()
        job.reviewed_count = max(job.reviewed_count, self._completed_review_count(session, job.job_id))
        job.pending_review_count = max(0, job.candidate_count - job.reviewed_count)
        job.qualified_count = max(
            job.qualified_count,
            _int(
                session.scalar(
                    select(func.count(ProjectReview.id)).where(
                        ProjectReview.job_id == job.job_id,
                        ProjectReview.decision.in_(
                            ("qualified", "keep", "selected", "usable", "accepted", "include")
                        ),
                    )
                )
            ),
        )

    def _project_review_step_locked(
        self,
        session: Session,
        job: Job,
        payload: Mapping[str, Any],
        *,
        created_at: str,
    ) -> None:
        accession = _text(
            payload.get("project_accession") or payload.get("accession")
        )
        if not accession:
            return
        row = session.scalar(
            select(ProjectReview).where(
                ProjectReview.job_id == job.job_id,
                ProjectReview.accession == accession,
            )
        )
        if row is None:
            self._project_reviews_started_locked(
                session,
                job,
                payload,
                created_at=created_at,
            )
            row = session.scalar(
                select(ProjectReview).where(
                    ProjectReview.job_id == job.job_id,
                    ProjectReview.accession == accession,
                )
            )
        if row is None:
            return
        step = _text(payload.get("step")) or row.current_step
        step_status = _text(payload.get("status")).lower() or "running"
        row.status = "running"
        row.current_step = step
        row.worker_slot = _int(payload.get("worker_slot"), 0) or row.worker_slot
        row.elapsed_ms = _int(payload.get("elapsed_ms"), 0) or row.elapsed_ms
        row.file_clue_count = max(
            row.file_clue_count,
            _int(payload.get("raw_file_count")),
        )
        row.usable_file_count = max(
            row.usable_file_count,
            _int(payload.get("usable_file_count")),
        )
        if payload.get("confidence") is not None:
            row.confidence = _float(payload.get("confidence"))
        if payload.get("retrieval_score") is not None:
            row.score = _float(payload.get("retrieval_score"))
        metadata = dict(row.metadata_summary or {})
        for key in (
            "species",
            "acquisition_mode",
            "sdrf_status",
            "sdrf_row_count",
            "evidence_fields",
        ):
            if payload.get(key) is not None:
                metadata[key] = _json_value(payload.get(key), payload.get(key))
        row.metadata_summary = _bounded_payload(metadata)
        evidence = dict(row.evidence_summary or {})
        steps = list(evidence.get("steps") or [])
        steps.append(
            {
                "step": step,
                "status": step_status,
                "elapsed_ms": _int(payload.get("elapsed_ms")),
                "reason": _text(payload.get("reason")) or None,
                "error": _text(payload.get("error")) or None,
                "raw_file_count": _int(payload.get("raw_file_count")),
                "usable_file_count": _int(payload.get("usable_file_count")),
                "excluded_file_count": _int(
                    payload.get("excluded_file_count")
                ),
                "filter_reason_counts": _json_value(
                    payload.get("filter_reason_counts") or {},
                    {},
                ),
            }
        )
        evidence["steps"] = steps[-24:]
        row.evidence_summary = _bounded_payload(evidence)
        if payload.get("decision"):
            row.decision = _text(payload.get("decision")).lower()
        if payload.get("reason") or payload.get("error"):
            row.reason_code = _text(
                payload.get("reason") or payload.get("error")
            )
        row.updated_at = created_at

    def _project_judgments_recorded_locked(
        self,
        session: Session,
        job: Job,
        payload: Mapping[str, Any],
        *,
        created_at: str,
    ) -> None:
        judgments = payload.get("judgments")
        if isinstance(judgments, Mapping):
            judgments = [judgments]
        if not isinstance(judgments, Sequence):
            judgments = []
        for judgment in judgments:
            if not isinstance(judgment, Mapping):
                continue
            accession = _text(judgment.get("project_accession"))
            if not accession:
                continue
            row = session.scalar(
                select(ProjectReview).where(
                    ProjectReview.job_id == job.job_id,
                    ProjectReview.accession == accession,
                )
            )
            if row is None:
                position = (
                    session.scalar(
                        select(func.coalesce(func.max(ProjectReview.position), 0)).where(
                            ProjectReview.job_id == job.job_id
                        )
                    )
                    or 0
                ) + 1
                row = ProjectReview(
                    job_id=job.job_id,
                    repository=job.repository or "pride",
                    accession=accession,
                    position=position,
                    created_at=created_at,
                )
                session.add(row)
            decision = _text(judgment.get("decision")).lower() or "investigate"
            row.status = "completed"
            row.current_step = "completed"
            row.worker_slot = None
            row.decision = decision
            row.score = _float(judgment.get("grade"))
            row.confidence = _float(judgment.get("confidence"))
            row.reason_code = _text(judgment.get("status")) or None
            row.reasons = _json_value(
                [judgment.get("explanation"), *(judgment.get("limitations") or [])],
                [],
            )
            row.evidence_summary = _json_value(
                {
                    "evidence_refs": judgment.get("evidence_refs") or [],
                    "hard_gate": judgment.get("hard_gate"),
                    "constraint_assessments": judgment.get(
                        "constraint_assessments"
                    )
                    or [],
                    "missing_information": judgment.get(
                        "missing_information"
                    )
                    or [],
                },
                {},
            )
            row.usable_file_count = max(
                row.usable_file_count,
                _int(judgment.get("target_file_count")),
            )
            row.updated_at = created_at
            row.finished_at = created_at
        session.flush()
        job.reviewed_count = max(
            job.reviewed_count,
            self._completed_review_count(session, job.job_id),
        )
        job.pending_review_count = max(0, job.candidate_count - job.reviewed_count)
        job.qualified_count = max(
            job.qualified_count,
            _int(payload.get("qualified_project_count")),
            _int(
                session.scalar(
                    select(func.count(ProjectReview.id)).where(
                        ProjectReview.job_id == job.job_id,
                        ProjectReview.decision == "include",
                    )
                )
            ),
        )

    @staticmethod
    def _completed_review_count(session: Session, job_id: str) -> int:
        return _int(
            session.scalar(
                select(func.count(ProjectReview.id)).where(
                    ProjectReview.job_id == job_id,
                    ProjectReview.status == "completed",
                )
            )
        )

    def _upsert_batch_locked(
        self,
        session: Session,
        job: Job,
        payload: Mapping[str, Any],
        *,
        created_at: str,
    ) -> None:
        batch_index = _int(payload.get("batch_index"))
        if batch_index <= 0:
            return
        row = session.scalar(
            select(Batch).where(
                Batch.job_id == job.job_id,
                Batch.batch_index == batch_index,
            )
        )
        batch_id = _text(payload.get("batch_id")) or f"{job.job_id}:batch:{batch_index:04d}"
        if row is None:
            row = Batch(
                batch_id=batch_id,
                job_id=job.job_id,
                batch_index=batch_index,
                created_at=created_at,
            )
            session.add(row)
        row.status = _text(payload.get("status")) or "ready"
        row.file_count = _int(payload.get("file_count"))
        row.project_count = _int(payload.get("project_count"))
        row.cumulative_file_count = _int(
            payload.get("cumulative_verified_file_count")
            or payload.get("cumulative_file_count")
        )
        row.manifest_path = _text(payload.get("manifest_path"))
        row.checksum = _text(payload.get("checksum"))
        row.terminal = bool(payload.get("terminal"))
        file_identifiers = payload.get("file_identifiers")
        if isinstance(file_identifiers, Sequence) and not isinstance(
            file_identifiers, str
        ):
            existing = {
                item.file_identifier
                for item in session.scalars(
                    select(BatchFile).where(BatchFile.batch_id == row.batch_id)
                ).all()
            }
            for position, identifier_value in enumerate(file_identifiers, start=1):
                identifier = _text(identifier_value)
                if not identifier or identifier in existing:
                    continue
                session.add(
                    BatchFile(
                        batch_id=row.batch_id,
                        position=position,
                        file_identifier=identifier,
                    )
                )
                existing.add(identifier)
        job.batch_count = max(job.batch_count, batch_index)
        job.usable_file_count = max(
            job.usable_file_count,
            row.cumulative_file_count,
            row.file_count,
        )

    def events_after(
        self,
        job_id: str,
        *,
        after: int = 0,
        limit: int | None = None,
        event_types: Sequence[str] = (),
    ) -> list[dict[str, Any]]:
        page_size = min(
            max(1, limit or self.settings.event_page_size),
            _DEFAULT_MAX_PAGE_SIZE,
        )
        with self.database.session() as session:
            query = select(JobEvent).where(
                JobEvent.job_id == job_id,
                JobEvent.sequence > max(0, after),
            )
            if event_types:
                query = query.where(JobEvent.event_type.in_(tuple(event_types)))
            rows = session.scalars(
                query.order_by(JobEvent.sequence.asc()).limit(page_size)
            ).all()
            return [self._event_dict(row) for row in rows]

    def list_terms(self, job_id: str) -> list[dict[str, Any]]:
        with self.database.session() as session:
            rows = session.scalars(
                select(JobTerm)
                .where(JobTerm.job_id == job_id)
                .order_by(JobTerm.position.asc())
            ).all()
            return [self._term_dict(row) for row in rows]

    def list_reviews(
        self,
        job_id: str,
        *,
        page: int = 1,
        page_size: int = 25,
        status: str = "",
        decision: str = "",
        query: str = "",
        sort: str = "position",
        direction: str = "asc",
    ) -> Page:
        return self._paginated_reviews(
            job_id,
            page=page,
            page_size=page_size,
            status=status,
            decision=decision,
            query=query,
            sort=sort,
            direction=direction,
        )

    def _paginated_reviews(
        self,
        job_id: str,
        *,
        page: int,
        page_size: int,
        status: str,
        decision: str,
        query: str,
        sort: str,
        direction: str,
    ) -> Page:
        page = max(1, page)
        page_size = min(max(1, page_size), 100)
        filters = [ProjectReview.job_id == job_id]
        if status:
            filters.append(ProjectReview.status == status)
        if decision:
            filters.append(ProjectReview.decision == decision)
        if query:
            token = f"%{query.strip()}%"
            filters.append(
                or_(
                    ProjectReview.accession.ilike(token),
                    ProjectReview.title.ilike(token),
                    ProjectReview.reason_code.ilike(token),
                )
            )
        sort_columns = {
            "position": ProjectReview.position,
            "accession": ProjectReview.accession,
            "status": ProjectReview.status,
            "decision": ProjectReview.decision,
            "score": ProjectReview.score,
            "updated_at": ProjectReview.updated_at,
        }
        order_column = sort_columns.get(sort, ProjectReview.position)
        order = order_column.desc() if direction.lower() == "desc" else order_column.asc()
        with self.database.session() as session:
            total = _int(
                session.scalar(
                    select(func.count(ProjectReview.id)).where(*filters)
                )
            )
            rows = session.scalars(
                select(ProjectReview)
                .where(*filters)
                .order_by(order, ProjectReview.id.asc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
            return Page(
                [self._review_dict(row) for row in rows],
                page,
                page_size,
                total,
            )

    def list_files(
        self,
        job_id: str,
        *,
        page: int = 1,
        page_size: int = 25,
        eligible: bool | None = None,
        project_accession: str = "",
        query: str = "",
        sort: str = "project_accession",
        direction: str = "asc",
        cursor: int | None = None,
        review_status: str = "",
        decision: str = "",
        reason_status: str = "",
    ) -> Page:
        page = max(1, page)
        page_size = min(max(1, page_size), 100)
        filters = [FileRecord.job_id == job_id]
        if eligible is not None:
            filters.append(FileRecord.eligible.is_(eligible))
        if project_accession:
            filters.append(FileRecord.project_accession == project_accession)
        if review_status:
            filters.append(FileRecord.review_status == review_status)
        if decision:
            filters.append(FileRecord.decision == decision)
        if reason_status:
            filters.append(FileRecord.reason_status == reason_status)
        if query:
            token = f"%{query.strip()}%"
            filters.append(
                or_(
                    FileRecord.file_name.ilike(token),
                    FileRecord.project_accession.ilike(token),
                    FileRecord.file_format.ilike(token),
                )
            )
        sort_columns = {
            "project_accession": FileRecord.project_accession,
            "file_name": FileRecord.file_name,
            "size_bytes": FileRecord.size_bytes,
            "status": FileRecord.status,
            "updated_at": FileRecord.updated_at,
        }
        order_column = sort_columns.get(sort, FileRecord.project_accession)
        order = order_column.desc() if direction.lower() == "desc" else order_column.asc()
        with self.database.session() as session:
            total = _int(
                session.scalar(select(func.count(FileRecord.id)).where(*filters))
            )
            query_statement = select(FileRecord).where(*filters)
            if cursor is not None:
                if direction.lower() == "desc":
                    query_statement = query_statement.where(FileRecord.id < cursor)
                    id_order = FileRecord.id.desc()
                else:
                    query_statement = query_statement.where(FileRecord.id > cursor)
                    id_order = FileRecord.id.asc()
                fetched = session.scalars(
                    query_statement.order_by(id_order).limit(page_size + 1)
                ).all()
                has_more = len(fetched) > page_size
                rows = fetched[:page_size]
                next_cursor = rows[-1].id if has_more and rows else None
            else:
                rows = session.scalars(
                    query_statement
                    .order_by(order, FileRecord.id.asc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                ).all()
                has_more = None
                next_cursor = None
            return Page(
                [self._file_dict(row, include_detail=False) for row in rows],
                page,
                page_size,
                total,
                next_cursor=next_cursor,
                summary=self._file_summary_locked(session, job_id),
                has_more=has_more,
            )

    def get_file(self, job_id: str, file_id: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            row = session.scalar(
                select(FileRecord).where(
                    FileRecord.job_id == job_id,
                    FileRecord.file_id == file_id,
                )
            )
            return self._file_dict(row) if row is not None else None

    def sync_file_review_candidates(
        self,
        job_id: str,
        items: Sequence[Mapping[str, Any]],
    ) -> None:
        """Register the complete candidate pool before the model reviews batches."""

        with self.database.session() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            existing_rows = session.scalars(
                select(FileRecord).where(FileRecord.job_id == job_id)
            ).all()
            existing_by_id = {
                str(row.file_id): row for row in existing_rows if row.file_id
            }
            existing_by_identity = {
                (row.repository, row.project_accession, row.native_id): row
                for row in existing_rows
            }
            now = utc_now_iso()
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                repository = _text(item.get("repository")) or job.repository or "pride"
                project_accession = _text(item.get("project_accession"))
                native_id = _text(
                    item.get("file_accession_or_path")
                    or item.get("native_id")
                    or item.get("file_name")
                )
                if not project_accession or not native_id:
                    continue
                file_id = _text(item.get("file_id")) or stable_file_id(
                    repository,
                    project_accession,
                    native_id,
                )
                row = existing_by_id.get(file_id) or existing_by_identity.get(
                    (repository, project_accession, native_id)
                )
                if row is None:
                    row = FileRecord(
                        job_id=job_id,
                        repository=repository,
                        project_accession=project_accession,
                        native_id=native_id,
                        file_id=file_id,
                        file_name=_text(item.get("file_name")) or native_id,
                        review_status="unreviewed",
                        reason_status="pending",
                        created_at=now,
                    )
                    session.add(row)
                    existing_by_id[file_id] = row
                    existing_by_identity[(repository, project_accession, native_id)] = row
                row.repository = repository
                row.project_accession = project_accession
                row.native_id = native_id
                row.file_id = file_id
                row.file_name = _text(item.get("file_name")) or row.file_name
                row.logical_path = _text(item.get("logical_path") or native_id)
                row.download_url = _text(item.get("download_url"))
                row.file_format = _text(item.get("file_type") or item.get("file_format"))
                row.file_category = _text(item.get("file_category"))
                row.file_role = _text(item.get("file_role"))
                row.selection_role = _text(item.get("selection_role")) or "primary_input"
                row.family_id = _text(item.get("family_id")) or None
                row.companion_file_ids = _json_value(
                    item.get("companion_file_ids") or [],
                    [],
                )
                row.acquisition_mode = _text(item.get("acquisition_mode")) or "unknown"
                row.size_bytes = _int(
                    item.get("expected_size_bytes") or item.get("size_bytes"),
                    0,
                ) or None
                row.status = _text(item.get("validity_status")) or "candidate"
                if row.review_status not in {"reviewing", "reviewed", "error"}:
                    row.review_status = "unreviewed"
                row.updated_at = now
            session.commit()

    def project_file_review_event(
        self,
        job_id: str,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        """Project bounded agent file-review events into the live file table."""

        if event_type not in {"file_review_batch_started", "file_review_batch_completed"}:
            return
        key = "items" if event_type.endswith("started") else "judgments"
        items = payload.get(key)
        if not isinstance(items, Sequence) or isinstance(items, str):
            return
        with self.database.session() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            file_ids = [_text(item.get("file_id")) for item in items if isinstance(item, Mapping)]
            existing = {
                str(row.file_id): row
                for row in session.scalars(
                    select(FileRecord).where(
                        FileRecord.job_id == job_id,
                        FileRecord.file_id.in_(file_ids),
                    )
                ).all()
            }
            now = utc_now_iso()
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                file_id = _text(item.get("file_id"))
                if not file_id:
                    continue
                row = existing.get(file_id)
                if row is None:
                    row = FileRecord(
                        job_id=job_id,
                        repository=job.repository or "pride",
                        project_accession=_text(item.get("project_accession")),
                        native_id=file_id,
                        file_id=file_id,
                        file_name=_text(item.get("file_name")) or file_id,
                        created_at=now,
                    )
                    session.add(row)
                    existing[file_id] = row
                row.project_accession = _text(item.get("project_accession")) or row.project_accession
                row.file_name = _text(item.get("file_name")) or row.file_name
                row.file_format = _text(item.get("file_type")) or row.file_format
                row.file_role = _text(item.get("file_role")) or row.file_role
                row.selection_role = _text(item.get("selection_role")) or row.selection_role
                row.family_id = _text(item.get("family_id")) or row.family_id
                row.companion_file_ids = _json_value(item.get("companion_file_ids") or row.companion_file_ids, [])
                if event_type.endswith("started"):
                    row.review_status = "reviewing"
                else:
                    row.review_status = _text(item.get("review_status")) or "reviewed"
                    row.decision = _text(item.get("decision")) or None
                    row.reason_status = _text(item.get("reason_status")) or "pending"
                    row.reason_scope = _text(item.get("reason_scope")) or "file"
                    row.reason_text = _text(item.get("reason_text")) or None
                    row.grade = _int(item.get("grade"), -1)
                    if row.grade < 0:
                        row.grade = None
                    row.hard_gate = _text(item.get("hard_gate")) or None
                    row.confidence = _float(item.get("confidence"))
                    row.judgment_model_id = _text(item.get("model_id")) or None
                    row.judgment_version = _text(item.get("judgment_version")) or None
                    row.limitations = _json_value(item.get("limitations") or [], [])
                    row.eligible = row.decision == "include"
                    row.evidence = {"evidence_refs": item.get("evidence_refs") or []}
                row.updated_at = now
            session.commit()

    @staticmethod
    def _file_summary_locked(session: Session, job_id: str) -> dict[str, int]:
        row = session.execute(
            select(
                func.count(FileRecord.id),
                func.sum(case((FileRecord.review_status == "unreviewed", 1), else_=0)),
                func.sum(case((FileRecord.review_status == "queued", 1), else_=0)),
                func.sum(case((FileRecord.review_status == "reviewing", 1), else_=0)),
                func.sum(case((FileRecord.review_status == "reviewed", 1), else_=0)),
                func.sum(case((FileRecord.decision == "include", 1), else_=0)),
                func.sum(case((FileRecord.decision == "investigate", 1), else_=0)),
                func.sum(case((FileRecord.decision == "exclude", 1), else_=0)),
                func.sum(case((FileRecord.review_status == "error", 1), else_=0)),
                func.sum(case((FileRecord.reason_status == "ready", 1), else_=0)),
            ).where(FileRecord.job_id == job_id)
        ).one()
        labels = (
            "total",
            "unreviewed",
            "queued",
            "reviewing",
            "reviewed",
            "selected",
            "investigate",
            "excluded",
            "errors",
            "reasons_ready",
        )
        return {label: _int(value) for label, value in zip(labels, row, strict=True)}

    def list_batches(self, job_id: str) -> list[dict[str, Any]]:
        with self.database.session() as session:
            rows = session.scalars(
                select(Batch)
                .where(Batch.job_id == job_id)
                .order_by(Batch.batch_index.asc())
            ).all()
            return [self._batch_dict(row) for row in rows]

    def get_batch_delivery(
        self,
        job_id: str,
        batch_index: int,
    ) -> dict[str, Any] | None:
        """Return an ordered batch from the operations database alone.

        Batch delivery must survive web-process restarts and removal of legacy
        discovery JSON. BatchFile is the frozen order/identity authority while
        FileRecord supplies the processing metadata.
        """

        with self.database.session() as session:
            batch = session.scalar(
                select(Batch).where(
                    Batch.job_id == job_id,
                    Batch.batch_index == batch_index,
                )
            )
            if batch is None:
                return None
            links = session.scalars(
                select(BatchFile)
                .where(BatchFile.batch_id == batch.batch_id)
                .order_by(BatchFile.position.asc())
            ).all()
            file_rows = session.scalars(
                select(FileRecord).where(
                    FileRecord.job_id == job_id,
                    FileRecord.eligible.is_(True),
                )
            ).all()
            by_identifier: dict[str, FileRecord] = {}
            for row in file_rows:
                repository = _text(row.repository).casefold() or "pride"
                accession = _text(row.project_accession).upper()
                for native in (
                    row.native_id,
                    row.logical_path,
                    row.file_name,
                    row.download_url,
                ):
                    token = _text(native)
                    if token:
                        by_identifier.setdefault(
                            f"{repository}:{accession}:{token}",
                            row,
                        )
            ordered_files: list[dict[str, Any]] = []
            missing_identifiers: list[str] = []
            for link in links:
                row = by_identifier.get(_text(link.file_identifier))
                if row is None:
                    missing_identifiers.append(_text(link.file_identifier))
                    continue
                item = self._file_dict(row)
                item["file_identifier"] = _text(link.file_identifier)
                item["position"] = link.position
                ordered_files.append(item)
            return {
                **self._batch_dict(batch),
                "files": ordered_files,
                "missing_file_identifiers": missing_identifiers,
            }

    def list_history(
        self,
        *,
        page: int = 1,
        page_size: int | None = None,
        status_group_filter: str = "",
        kind: str = "",
        query: str = "",
        archived: bool = False,
        trash: bool = False,
        sort: str = "updated_at",
        direction: str = "desc",
    ) -> Page:
        page = max(1, page)
        page_size = min(
            max(1, page_size or self.settings.history_page_size),
            100,
        )
        filters = [_visible_history_entry_clause()]
        if trash:
            filters.append(HistoryEntry.deleted_at.is_not(None))
        else:
            filters.append(HistoryEntry.deleted_at.is_(None))
        if archived:
            filters.append(HistoryEntry.archived_at.is_not(None))
        elif not trash:
            filters.append(HistoryEntry.archived_at.is_(None))
        if status_group_filter:
            filters.append(HistoryEntry.status_group == status_group_filter)
        if kind:
            filters.append(HistoryEntry.kind == kind)
        if query:
            token = f"%{query.strip()}%"
            filters.append(
                or_(
                    HistoryEntry.display_name.ilike(token),
                    HistoryEntry.objective.ilike(token),
                    HistoryEntry.source_id.ilike(token),
                )
            )
        sort_columns = {
            "updated_at": HistoryEntry.updated_at,
            "created_at": HistoryEntry.created_at,
            "size_bytes": HistoryEntry.size_bytes,
            "project_count": HistoryEntry.project_count,
            "file_count": HistoryEntry.file_count,
            "status": HistoryEntry.status,
        }
        order_column = sort_columns.get(sort, HistoryEntry.updated_at)
        order = order_column.desc() if direction.lower() == "desc" else order_column.asc()
        with self.database.session() as session:
            total = _int(
                session.scalar(select(func.count(HistoryEntry.history_id)).where(*filters))
            )
            rows = session.scalars(
                select(HistoryEntry)
                .where(*filters)
                .order_by(order, HistoryEntry.history_id.asc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
            return Page(
                [self._history_dict(row) for row in rows],
                page,
                page_size,
                total,
            )

    def history_summary(self) -> dict[str, Any]:
        with self.database.session() as session:
            rows = session.execute(
                select(
                    HistoryEntry.status_group,
                    func.count(HistoryEntry.history_id),
                    func.coalesce(func.sum(HistoryEntry.size_bytes), 0),
                )
                .where(
                    HistoryEntry.deleted_at.is_(None),
                    _visible_history_entry_clause(),
                )
                .group_by(HistoryEntry.status_group)
            ).all()
            summary = {
                "total": 0,
                "storage_bytes": 0,
                "active": 0,
                "completed": 0,
                "needs_attention": 0,
            }
            for group, count, size in rows:
                summary["total"] += _int(count)
                summary["storage_bytes"] += _int(size)
                summary[str(group)] = _int(count)
            summary["archived"] = _int(
                session.scalar(
                    select(func.count(HistoryEntry.history_id)).where(
                        HistoryEntry.deleted_at.is_(None),
                        HistoryEntry.archived_at.is_not(None),
                        _visible_history_entry_clause(),
                    )
                )
            )
            summary["trash"] = _int(
                session.scalar(
                    select(func.count(HistoryEntry.history_id)).where(
                        HistoryEntry.deleted_at.is_not(None),
                        _visible_history_entry_clause(),
                    )
                )
            )
            return summary

    def upsert_history_record(
        self,
        record: Mapping[str, Any],
        *,
        kind: str,
        source_id: str,
        history_id: str | None = None,
        size_bytes: int = 0,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = self._upsert_history_record_locked(
                session,
                record,
                kind=kind,
                source_id=source_id,
                history_id=history_id,
                size_bytes=size_bytes,
            )
            session.commit()
            return self._history_dict(row)

    def upsert_history_records_bulk(
        self,
        records: Sequence[
            tuple[Mapping[str, Any], str, str, str | None, int]
        ],
    ) -> int:
        """Import validated history rows in one SQLite write transaction."""

        if not records:
            return 0
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            for record, kind, source_id, history_id, size_bytes in records:
                self._upsert_history_record_locked(
                    session,
                    record,
                    kind=kind,
                    source_id=source_id,
                    history_id=history_id,
                    size_bytes=size_bytes,
                )
            session.commit()
            return len(records)

    def _upsert_history_record_locked(
        self,
        session: Session,
        record: Mapping[str, Any],
        *,
        kind: str,
        source_id: str,
        history_id: str | None,
        size_bytes: int,
    ) -> HistoryEntry:
        now = _text(
            record.get("updated_at")
            or record.get("finished_at")
            or record.get("history_time")
            or record.get("created_at")
        ) or utc_now_iso()
        created_at = _text(record.get("created_at")) or now
        status = _text(record.get("status")).lower() or "interrupted"
        identity = history_id or f"{kind}:{source_id}"
        row = session.get(HistoryEntry, identity)
        if row is None:
            row = HistoryEntry(
                history_id=identity,
                kind=kind,
                source_id=source_id,
                status=status,
                status_group=_history_status_group(status),
                display_name=_text(
                    record.get("display_name")
                    or record.get("input_value")
                    or record.get("name")
                    or source_id
                ),
                objective=_text(
                    record.get("objective")
                    or record.get("input_value")
                ),
                repository=_text(record.get("repository")),
                species=_text(record.get("species")),
                created_at=created_at,
                updated_at=now,
            )
            session.add(row)
            session.flush()
        row.status = status
        row.status_group = _history_status_group(status)
        row.display_name = _text(
            record.get("display_name")
            or record.get("input_value")
            or record.get("name")
            or source_id
        )
        row.objective = _text(
            record.get("objective")
            or record.get("input_value")
            or row.objective
        )
        row.repository = _text(record.get("repository") or row.repository)
        row.species = _text(record.get("species") or row.species)
        row.project_count = max(
            row.project_count,
            _int(record.get("project_count")),
        )
        row.file_count = max(
            row.file_count,
            _int(record.get("file_count")),
        )
        row.size_bytes = max(row.size_bytes, max(0, size_bytes))
        row.open_available = record.get("open_available") is not False
        row.deletable = (
            record.get("deletable") is not False
            and row.status_group != "active"
        )
        row.metadata_json = _bounded_payload(
            {
                key: record.get(key)
                for key in (
                    "task_id",
                    "batch_id",
                    "discovery_id",
                    "job_id",
                    "run_id",
                    "output_dir",
                    "result_id",
                    "source_discovery_job_id",
                    "source_discovery_id",
                    "source_batch_index",
                    "run_mode",
                    "requested_run_mode",
                    "item_count",
                    "completed_items",
                    "failed_items",
                    "needs_review_items",
                    "cancelled_items",
                    "running_items",
                    "queued_items",
                    "progress_percent",
                    "cancel_requested",
                    "can_download",
                    "error_summary",
                )
                if record.get(key) is not None
            }
        )
        row.updated_at = now
        return row

    def archive_history(self, history_id: str, archived: bool = True) -> dict[str, Any]:
        now = utc_now_iso()
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = session.get(HistoryEntry, history_id)
            if row is None:
                raise KeyError(history_id)
            row.archived_at = now if archived else None
            row.updated_at = now
            session.commit()
            return self._history_dict(row)

    def mark_history_deleted(
        self,
        history_id: str,
        *,
        released_bytes: int = 0,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = session.get(HistoryEntry, history_id)
            if row is None:
                raise KeyError(history_id)
            if row.status_group == "active":
                raise ValueError("active_history_cannot_be_deleted")
            row.deleted_at = now
            row.updated_at = now
            row.open_available = False
            row.deletable = False
            metadata = dict(row.metadata_json or {})
            metadata["released_bytes"] = max(0, released_bytes)
            metadata["deleted_at"] = now
            row.metadata_json = metadata
            session.add(
                DeletionRequest(
                    request_id=f"delete_{uuid4().hex}",
                    history_id=history_id,
                    status="completed",
                    include_linked=False,
                    estimated_bytes=max(0, row.size_bytes),
                    released_bytes=max(0, released_bytes),
                    targets=[
                        {
                            "kind": row.kind,
                            "source_id": row.source_id,
                            "display_name": row.display_name,
                        }
                    ],
                    expires_at=now,
                    created_at=now,
                    completed_at=now,
                )
            )
            session.commit()
            return self._history_dict(row)

    def _upsert_history_locked(self, session: Session, job: Job) -> None:
        history_id = f"{job.job_type}:{job.job_id}"
        row = session.get(HistoryEntry, history_id)
        if row is None:
            row = HistoryEntry(
                history_id=history_id,
                kind=job.job_type,
                source_id=job.job_id,
                status=job.status,
                status_group=_history_status_group(job.status),
                display_name=job.objective or job.job_id,
                objective=job.objective,
                repository=job.repository,
                species=job.species,
                created_at=job.created_at,
                updated_at=job.updated_at,
            )
            session.add(row)
        row.status = job.status
        row.status_group = _history_status_group(job.status)
        row.display_name = job.objective or job.job_id
        row.objective = job.objective
        row.repository = job.repository
        row.species = job.species
        row.project_count = job.qualified_count or job.candidate_count
        row.file_count = job.usable_file_count
        row.open_available = True
        row.deletable = job.status not in {status.value for status in ACTIVE_STATUSES}
        row.metadata_json = {
            "job_id": job.job_id,
            "phase": job.phase,
            "candidate_count": job.candidate_count,
            "reviewed_count": job.reviewed_count,
            "batch_count": job.batch_count,
            "resumable": job.resumable,
            "error_code": job.error_code,
        }
        row.updated_at = job.updated_at

    @staticmethod
    def _legacy_terms(
        execution: Mapping[str, Any],
        body: Mapping[str, Any],
    ) -> list[Mapping[str, Any] | str]:
        terms = execution.get("terms")
        if isinstance(terms, list) and terms:
            return [item for item in terms if isinstance(item, (Mapping, str))]
        for key in ("confirmed_theme_terms", "search_terms", "query_terms"):
            value = body.get(key)
            if isinstance(value, list):
                return [str(item) for item in value if _text(item)]
        return []

    @staticmethod
    def _job_snapshot(job: Job) -> dict[str, Any]:
        return {
            "job_id": job.job_id,
            "job_type": job.job_type,
            "status": job.status,
            "phase": job.phase,
            "objective": job.objective,
            "repository": job.repository,
            "species": job.species,
            "version": job.version,
            "last_event_sequence": job.event_sequence,
            "cancel_requested": job.cancel_requested,
            "resumable": job.resumable,
            "queue_task_id": _text((job.summary or {}).get("queue_task_id")),
            "result": dict((job.summary or {}).get("result") or {}),
            "progress": {
                "current_term": job.current_term,
                "term_total": job.term_total,
                "term_completed": job.term_completed,
                "raw_hit_count": job.raw_hit_count,
                "candidate_count": job.candidate_count,
                "reviewed_count": job.reviewed_count,
                "pending_review_count": job.pending_review_count,
                "qualified_count": job.qualified_count,
                "file_clue_count": job.file_clue_count,
                "usable_file_count": job.usable_file_count,
                "batch_count": job.batch_count,
                "worker_count": job.worker_count,
            },
            "heartbeat_at": job.heartbeat_at,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "updated_at": job.updated_at,
            "finished_at": job.finished_at,
            "archived_at": job.archived_at,
            "error": (
                {
                    "code": job.error_code,
                    "message": job.error_message,
                }
                if job.error_code or job.error_message
                else None
            ),
        }

    @staticmethod
    def _event_dict(row: JobEvent) -> dict[str, Any]:
        return {
            "id": row.sequence,
            "sequence": row.sequence,
            "job_id": row.job_id,
            "type": row.event_type,
            "level": row.level,
            "actor": row.actor,
            "phase": row.phase,
            "message": row.message,
            "payload": row.payload or {},
            "created_at": row.created_at,
        }

    @staticmethod
    def _term_dict(row: JobTerm) -> dict[str, Any]:
        return {
            "position": row.position,
            "term": row.term,
            "role": row.role,
            "status": row.status,
            "cursor": row.cursor,
            "page_count": row.page_count,
            "raw_count": row.raw_count,
            "unique_count": row.unique_count,
            "attempt_count": row.attempt_count,
            "started_at": row.started_at,
            "updated_at": row.updated_at,
            "finished_at": row.finished_at,
            "error": (
                {"code": row.error_code, "message": row.error_message}
                if row.error_code or row.error_message
                else None
            ),
        }

    @staticmethod
    def _review_dict(row: ProjectReview) -> dict[str, Any]:
        return {
            "id": row.id,
            "repository": row.repository,
            "accession": row.accession,
            "title": row.title,
            "position": row.position,
            "status": row.status,
            "current_step": row.current_step,
            "worker_slot": row.worker_slot,
            "decision": row.decision,
            "reason_code": row.reason_code,
            "score": row.score,
            "confidence": row.confidence,
            "discovered_by_terms": row.discovered_by_terms or [],
            "reasons": row.reasons or [],
            "evidence_summary": row.evidence_summary or {},
            "metadata_summary": row.metadata_summary or {},
            "file_clue_count": row.file_clue_count,
            "usable_file_count": row.usable_file_count,
            "elapsed_ms": row.elapsed_ms,
            "started_at": row.started_at,
            "updated_at": row.updated_at,
            "finished_at": row.finished_at,
        }

    @staticmethod
    def _file_dict(
        row: FileRecord,
        *,
        include_detail: bool = True,
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "file_id": row.file_id,
            "repository": row.repository,
            "project_accession": row.project_accession,
            "native_id": row.native_id,
            "file_name": row.file_name,
            "logical_path": row.logical_path,
            "download_url": row.download_url,
            "file_format": row.file_format,
            "file_category": row.file_category,
            "file_role": row.file_role,
            "selection_role": row.selection_role,
            "family_id": row.family_id,
            "companion_file_ids": row.companion_file_ids or [],
            "acquisition_mode": row.acquisition_mode,
            "size_bytes": row.size_bytes,
            "status": row.status,
            "review_status": row.review_status,
            "decision": row.decision,
            "reason_status": row.reason_status,
            "reason_scope": row.reason_scope,
            "reason_text": row.reason_text if include_detail else None,
            "reason_preview": (
                str(row.reason_text or "")[:180]
                if row.reason_text
                else None
            ),
            "grade": row.grade,
            "hard_gate": row.hard_gate,
            "confidence": row.confidence,
            "judgment_model_id": row.judgment_model_id,
            "judgment_version": row.judgment_version,
            "limitations": (row.limitations or []) if include_detail else [],
            "eligible": row.eligible,
            "reason_code": row.reason_code,
            "reasons": row.reasons or [],
            "evidence": (row.evidence or {}) if include_detail else {},
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _batch_dict(row: Batch) -> dict[str, Any]:
        return {
            "batch_id": row.batch_id,
            "job_id": row.job_id,
            "batch_index": row.batch_index,
            "status": row.status,
            "file_count": row.file_count,
            "project_count": row.project_count,
            "cumulative_file_count": row.cumulative_file_count,
            "manifest_path": row.manifest_path,
            "checksum": row.checksum,
            "terminal": row.terminal,
            "created_at": row.created_at,
        }

    @staticmethod
    def _history_dict(row: HistoryEntry) -> dict[str, Any]:
        return {
            "history_id": row.history_id,
            "kind": row.kind,
            "source_id": row.source_id,
            "job_id": row.source_id if row.kind == "discovery" else None,
            "status": row.status,
            "status_group": row.status_group,
            "display_name": row.display_name,
            "objective": row.objective,
            "repository": row.repository,
            "species": row.species,
            "project_count": row.project_count,
            "file_count": row.file_count,
            "size_bytes": row.size_bytes,
            "open_available": row.open_available,
            "deletable": row.deletable,
            "metadata": row.metadata_json or {},
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "archived_at": row.archived_at,
            "deleted_at": row.deleted_at,
        }
