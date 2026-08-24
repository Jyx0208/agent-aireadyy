from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(48), nullable=False, default="discovery")
    idempotency_key: Mapped[str | None] = mapped_column(String(160), unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    phase: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    objective: Mapped[str] = mapped_column(Text, nullable=False, default="")
    repository: Mapped[str] = mapped_column(String(48), nullable=False, default="pride")
    species: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resumable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    current_term: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    term_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    term_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    raw_hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reviewed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pending_review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    qualified_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    file_clue_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    usable_file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    batch_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    worker_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    heartbeat_at: Mapped[str | None] = mapped_column(String(40), index=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_iso)
    started_at: Mapped[str | None] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default=utc_now_iso,
        index=True,
    )
    finished_at: Mapped[str | None] = mapped_column(String(40))
    archived_at: Mapped[str | None] = mapped_column(String(40), index=True)
    deleted_at: Mapped[str | None] = mapped_column(String(40), index=True)
    error_code: Mapped[str | None] = mapped_column(String(160))
    error_message: Mapped[str | None] = mapped_column(Text)
    legacy_path: Mapped[str | None] = mapped_column(Text)

    terms: Mapped[list["JobTerm"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
    )
    reviews: Mapped[list["ProjectReview"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
    )
    files: Mapped[list["FileRecord"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
    )
    events: Mapped[list["JobEvent"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_jobs_type_status_updated", "job_type", "status", "updated_at"),
        Index("ix_jobs_history", "deleted_at", "archived_at", "updated_at"),
    )


class JobTerm(Base):
    __tablename__ = "job_terms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.job_id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    term: Mapped[str] = mapped_column(String(300), nullable=False)
    role: Mapped[str] = mapped_column(String(48), nullable=False, default="theme_synonym")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    cursor: Mapped[str | None] = mapped_column(String(500))
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    raw_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unique_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[str | None] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_iso)
    finished_at: Mapped[str | None] = mapped_column(String(40))
    error_code: Mapped[str | None] = mapped_column(String(160))
    error_message: Mapped[str | None] = mapped_column(Text)

    job: Mapped[Job] = relationship(back_populates="terms")

    __table_args__ = (
        UniqueConstraint("job_id", "position", name="uq_job_terms_position"),
        UniqueConstraint("job_id", "term", name="uq_job_terms_term"),
        Index("ix_job_terms_job_status_position", "job_id", "status", "position"),
    )


class ProjectReview(Base):
    __tablename__ = "project_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.job_id", ondelete="CASCADE"),
        nullable=False,
    )
    repository: Mapped[str] = mapped_column(String(48), nullable=False, default="pride")
    accession: Mapped[str] = mapped_column(String(96), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    current_step: Mapped[str] = mapped_column(String(64), nullable=False, default="queued")
    worker_slot: Mapped[int | None] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(48), nullable=False, default="pending")
    reason_code: Mapped[str | None] = mapped_column(String(160))
    score: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    discovered_by_terms: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    evidence_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    metadata_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    file_clue_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    usable_file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_iso)
    started_at: Mapped[str | None] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_iso)
    finished_at: Mapped[str | None] = mapped_column(String(40))

    job: Mapped[Job] = relationship(back_populates="reviews")

    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "repository",
            "accession",
            name="uq_project_reviews_identity",
        ),
        Index("ix_project_reviews_job_status_position", "job_id", "status", "position"),
        Index("ix_project_reviews_job_decision", "job_id", "decision"),
        Index("ix_project_reviews_accession", "accession"),
    )


class FileRecord(Base):
    __tablename__ = "file_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.job_id", ondelete="CASCADE"),
        nullable=False,
    )
    repository: Mapped[str] = mapped_column(String(48), nullable=False, default="pride")
    project_accession: Mapped[str] = mapped_column(String(96), nullable=False)
    native_id: Mapped[str] = mapped_column(String(900), nullable=False)
    file_id: Mapped[str | None] = mapped_column(String(80))
    file_name: Mapped[str] = mapped_column(String(900), nullable=False)
    logical_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    download_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    file_format: Mapped[str] = mapped_column(String(96), nullable=False, default="")
    file_category: Mapped[str] = mapped_column(String(96), nullable=False, default="")
    file_role: Mapped[str] = mapped_column(String(96), nullable=False, default="")
    selection_role: Mapped[str] = mapped_column(
        String(48), nullable=False, default="primary_input"
    )
    family_id: Mapped[str | None] = mapped_column(String(100))
    companion_file_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    acquisition_mode: Mapped[str] = mapped_column(String(48), nullable=False, default="unknown")
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="candidate")
    review_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unreviewed"
    )
    decision: Mapped[str | None] = mapped_column(String(32))
    reason_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )
    reason_scope: Mapped[str] = mapped_column(
        String(32), nullable=False, default="project_legacy"
    )
    reason_text: Mapped[str | None] = mapped_column(Text)
    grade: Mapped[int | None] = mapped_column(Integer)
    hard_gate: Mapped[str | None] = mapped_column(String(24))
    confidence: Mapped[float | None] = mapped_column(Float)
    judgment_model_id: Mapped[str | None] = mapped_column(String(160))
    judgment_version: Mapped[str | None] = mapped_column(String(80))
    limitations: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason_code: Mapped[str | None] = mapped_column(String(160))
    reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_iso)

    job: Mapped[Job] = relationship(back_populates="files")

    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "repository",
            "project_accession",
            "native_id",
            name="uq_file_records_identity",
        ),
        Index("ix_file_records_job_eligible", "job_id", "eligible"),
        Index("ix_file_records_job_project", "job_id", "project_accession"),
        Index("ix_file_records_job_file_id", "job_id", "file_id", unique=True),
        Index(
            "ix_file_records_job_review_decision_id",
            "job_id",
            "review_status",
            "decision",
            "id",
        ),
        Index("ix_file_records_job_family_id", "job_id", "family_id", "id"),
        Index("ix_file_records_name", "file_name"),
    )


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.job_id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    level: Mapped[str] = mapped_column(String(24), nullable=False, default="info")
    actor: Mapped[str] = mapped_column(String(120), nullable=False, default="system")
    phase: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_iso)

    job: Mapped[Job] = relationship(back_populates="events")

    __table_args__ = (
        UniqueConstraint("job_id", "sequence", name="uq_job_events_sequence"),
        Index("ix_job_events_job_sequence", "job_id", "sequence"),
        Index("ix_job_events_type_created", "event_type", "created_at"),
    )


class Batch(Base):
    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(String(180), nullable=False, unique=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.job_id", ondelete="CASCADE"),
        nullable=False,
    )
    batch_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ready")
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    project_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cumulative_file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    manifest_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    checksum: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    terminal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_iso)

    __table_args__ = (
        UniqueConstraint("job_id", "batch_index", name="uq_batches_job_index"),
        Index("ix_batches_job_index", "job_id", "batch_index"),
    )


class BatchFile(Base):
    __tablename__ = "batch_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("batches.batch_id", ondelete="CASCADE"),
        nullable=False,
    )
    file_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("file_records.id", ondelete="SET NULL"),
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    file_identifier: Mapped[str] = mapped_column(String(1200), nullable=False)

    __table_args__ = (
        UniqueConstraint("batch_id", "position", name="uq_batch_files_position"),
        UniqueConstraint("batch_id", "file_identifier", name="uq_batch_files_identity"),
        Index("ix_batch_files_batch_position", "batch_id", "position"),
    )


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.job_id", ondelete="CASCADE"),
    )
    owner_kind: Mapped[str] = mapped_column(String(48), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(180), nullable=False)
    asset_kind: Mapped[str] = mapped_column(String(96), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    managed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source_file: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_iso)
    deleted_at: Mapped[str | None] = mapped_column(String(40))

    __table_args__ = (
        UniqueConstraint("owner_kind", "owner_id", "path", name="uq_assets_owner_path"),
        Index("ix_assets_job_kind", "job_id", "asset_kind"),
    )


class HistoryEntry(Base):
    __tablename__ = "history_entries"

    history_id: Mapped[str] = mapped_column(String(220), primary_key=True)
    kind: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(180), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status_group: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False, default="")
    repository: Mapped[str] = mapped_column(String(48), nullable=False, default="")
    species: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    project_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    open_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deletable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    archived_at: Mapped[str | None] = mapped_column(String(40), index=True)
    deleted_at: Mapped[str | None] = mapped_column(String(40), index=True)

    __table_args__ = (
        UniqueConstraint("kind", "source_id", name="uq_history_entries_source"),
        Index(
            "ix_history_entries_default",
            "deleted_at",
            "archived_at",
            "status_group",
            "updated_at",
        ),
    )


class DeletionRequest(Base):
    __tablename__ = "deletion_requests"

    request_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    history_id: Mapped[str] = mapped_column(
        ForeignKey("history_entries.history_id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="preview")
    include_linked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    estimated_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    released_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    targets: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    error_message: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_iso)
    completed_at: Mapped[str | None] = mapped_column(String(40))

    __table_args__ = (
        Index("ix_deletion_requests_history_status", "history_id", "status"),
    )
