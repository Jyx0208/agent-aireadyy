"""Create the durable operations plane.

Revision ID: 0001_operations_plane
Revises:
Create Date: 2026-07-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0001_operations_plane"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.String(160), primary_key=True),
        sa.Column("job_type", sa.String(48), nullable=False),
        sa.Column("idempotency_key", sa.String(160), unique=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("repository", sa.String(48), nullable=False),
        sa.Column("species", sa.String(160), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("resumable", sa.Boolean(), nullable=False),
        sa.Column("current_term", sa.String(300), nullable=False),
        sa.Column("term_total", sa.Integer(), nullable=False),
        sa.Column("term_completed", sa.Integer(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("raw_hit_count", sa.Integer(), nullable=False),
        sa.Column("reviewed_count", sa.Integer(), nullable=False),
        sa.Column("pending_review_count", sa.Integer(), nullable=False),
        sa.Column("qualified_count", sa.Integer(), nullable=False),
        sa.Column("file_clue_count", sa.Integer(), nullable=False),
        sa.Column("usable_file_count", sa.Integer(), nullable=False),
        sa.Column("batch_count", sa.Integer(), nullable=False),
        sa.Column("worker_count", sa.Integer(), nullable=False),
        sa.Column("heartbeat_at", sa.String(40)),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("started_at", sa.String(40)),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.Column("finished_at", sa.String(40)),
        sa.Column("archived_at", sa.String(40)),
        sa.Column("deleted_at", sa.String(40)),
        sa.Column("error_code", sa.String(160)),
        sa.Column("error_message", sa.Text()),
        sa.Column("legacy_path", sa.Text()),
    )
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_phase", "jobs", ["phase"])
    op.create_index("ix_jobs_updated_at", "jobs", ["updated_at"])
    op.create_index("ix_jobs_heartbeat_at", "jobs", ["heartbeat_at"])
    op.create_index("ix_jobs_archived_at", "jobs", ["archived_at"])
    op.create_index("ix_jobs_deleted_at", "jobs", ["deleted_at"])
    op.create_index("ix_jobs_type_status_updated", "jobs", ["job_type", "status", "updated_at"])
    op.create_index("ix_jobs_history", "jobs", ["deleted_at", "archived_at", "updated_at"])

    op.create_table(
        "job_terms",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(160), sa.ForeignKey("jobs.job_id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("term", sa.String(300), nullable=False),
        sa.Column("role", sa.String(48), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("cursor", sa.String(500)),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("raw_count", sa.Integer(), nullable=False),
        sa.Column("unique_count", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.String(40)),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.Column("finished_at", sa.String(40)),
        sa.Column("error_code", sa.String(160)),
        sa.Column("error_message", sa.Text()),
        sa.UniqueConstraint("job_id", "position", name="uq_job_terms_position"),
        sa.UniqueConstraint("job_id", "term", name="uq_job_terms_term"),
    )
    op.create_index("ix_job_terms_job_status_position", "job_terms", ["job_id", "status", "position"])

    op.create_table(
        "project_reviews",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(160), sa.ForeignKey("jobs.job_id", ondelete="CASCADE"), nullable=False),
        sa.Column("repository", sa.String(48), nullable=False),
        sa.Column("accession", sa.String(96), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("current_step", sa.String(64), nullable=False),
        sa.Column("worker_slot", sa.Integer()),
        sa.Column("decision", sa.String(48), nullable=False),
        sa.Column("reason_code", sa.String(160)),
        sa.Column("score", sa.Float()),
        sa.Column("confidence", sa.Float()),
        sa.Column("discovered_by_terms", sa.JSON(), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("evidence_summary", sa.JSON(), nullable=False),
        sa.Column("metadata_summary", sa.JSON(), nullable=False),
        sa.Column("file_clue_count", sa.Integer(), nullable=False),
        sa.Column("usable_file_count", sa.Integer(), nullable=False),
        sa.Column("elapsed_ms", sa.Integer()),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("started_at", sa.String(40)),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.Column("finished_at", sa.String(40)),
        sa.UniqueConstraint("job_id", "repository", "accession", name="uq_project_reviews_identity"),
    )
    op.create_index("ix_project_reviews_job_status_position", "project_reviews", ["job_id", "status", "position"])
    op.create_index("ix_project_reviews_job_decision", "project_reviews", ["job_id", "decision"])
    op.create_index("ix_project_reviews_accession", "project_reviews", ["accession"])

    op.create_table(
        "file_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(160), sa.ForeignKey("jobs.job_id", ondelete="CASCADE"), nullable=False),
        sa.Column("repository", sa.String(48), nullable=False),
        sa.Column("project_accession", sa.String(96), nullable=False),
        sa.Column("native_id", sa.String(900), nullable=False),
        sa.Column("file_name", sa.String(900), nullable=False),
        sa.Column("logical_path", sa.Text(), nullable=False),
        sa.Column("download_url", sa.Text(), nullable=False),
        sa.Column("file_format", sa.String(96), nullable=False),
        sa.Column("file_category", sa.String(96), nullable=False),
        sa.Column("file_role", sa.String(96), nullable=False),
        sa.Column("acquisition_mode", sa.String(48), nullable=False),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("reason_code", sa.String(160)),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("job_id", "repository", "project_accession", "native_id", name="uq_file_records_identity"),
    )
    op.create_index("ix_file_records_job_eligible", "file_records", ["job_id", "eligible"])
    op.create_index("ix_file_records_job_project", "file_records", ["job_id", "project_accession"])
    op.create_index("ix_file_records_name", "file_records", ["file_name"])

    op.create_table(
        "job_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(160), sa.ForeignKey("jobs.job_id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("level", sa.String(24), nullable=False),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("job_id", "sequence", name="uq_job_events_sequence"),
    )
    op.create_index("ix_job_events_job_sequence", "job_events", ["job_id", "sequence"])
    op.create_index("ix_job_events_type_created", "job_events", ["event_type", "created_at"])

    op.create_table(
        "batches",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("batch_id", sa.String(180), nullable=False, unique=True),
        sa.Column("job_id", sa.String(160), sa.ForeignKey("jobs.job_id", ondelete="CASCADE"), nullable=False),
        sa.Column("batch_index", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("project_count", sa.Integer(), nullable=False),
        sa.Column("cumulative_file_count", sa.Integer(), nullable=False),
        sa.Column("manifest_path", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(128), nullable=False),
        sa.Column("terminal", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("job_id", "batch_index", name="uq_batches_job_index"),
    )
    op.create_index("ix_batches_job_index", "batches", ["job_id", "batch_index"])

    op.create_table(
        "batch_files",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("batch_id", sa.String(180), sa.ForeignKey("batches.batch_id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_record_id", sa.Integer(), sa.ForeignKey("file_records.id", ondelete="SET NULL")),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("file_identifier", sa.String(1200), nullable=False),
        sa.UniqueConstraint("batch_id", "position", name="uq_batch_files_position"),
        sa.UniqueConstraint("batch_id", "file_identifier", name="uq_batch_files_identity"),
    )
    op.create_index("ix_batch_files_batch_position", "batch_files", ["batch_id", "position"])

    op.create_table(
        "assets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(160), sa.ForeignKey("jobs.job_id", ondelete="CASCADE")),
        sa.Column("owner_kind", sa.String(48), nullable=False),
        sa.Column("owner_id", sa.String(180), nullable=False),
        sa.Column("asset_kind", sa.String(96), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum", sa.String(128), nullable=False),
        sa.Column("managed", sa.Boolean(), nullable=False),
        sa.Column("source_file", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("deleted_at", sa.String(40)),
        sa.UniqueConstraint("owner_kind", "owner_id", "path", name="uq_assets_owner_path"),
    )
    op.create_index("ix_assets_job_kind", "assets", ["job_id", "asset_kind"])

    op.create_table(
        "history_entries",
        sa.Column("history_id", sa.String(220), primary_key=True),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("source_id", sa.String(180), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("status_group", sa.String(32), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("repository", sa.String(48), nullable=False),
        sa.Column("species", sa.String(160), nullable=False),
        sa.Column("project_count", sa.Integer(), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("open_available", sa.Boolean(), nullable=False),
        sa.Column("deletable", sa.Boolean(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.Column("archived_at", sa.String(40)),
        sa.Column("deleted_at", sa.String(40)),
        sa.UniqueConstraint("kind", "source_id", name="uq_history_entries_source"),
    )
    op.create_index("ix_history_entries_kind", "history_entries", ["kind"])
    op.create_index("ix_history_entries_status", "history_entries", ["status"])
    op.create_index("ix_history_entries_status_group", "history_entries", ["status_group"])
    op.create_index("ix_history_entries_updated_at", "history_entries", ["updated_at"])
    op.create_index("ix_history_entries_archived_at", "history_entries", ["archived_at"])
    op.create_index("ix_history_entries_deleted_at", "history_entries", ["deleted_at"])
    op.create_index(
        "ix_history_entries_default",
        "history_entries",
        ["deleted_at", "archived_at", "status_group", "updated_at"],
    )

    op.create_table(
        "deletion_requests",
        sa.Column("request_id", sa.String(180), primary_key=True),
        sa.Column("history_id", sa.String(220), sa.ForeignKey("history_entries.history_id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("include_linked", sa.Boolean(), nullable=False),
        sa.Column("estimated_bytes", sa.BigInteger(), nullable=False),
        sa.Column("released_bytes", sa.BigInteger(), nullable=False),
        sa.Column("targets", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("expires_at", sa.String(40), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("completed_at", sa.String(40)),
    )
    op.create_index("ix_deletion_requests_history_status", "deletion_requests", ["history_id", "status"])


def downgrade() -> None:
    op.drop_table("deletion_requests")
    op.drop_table("history_entries")
    op.drop_table("assets")
    op.drop_table("batch_files")
    op.drop_table("batches")
    op.drop_table("job_events")
    op.drop_table("file_records")
    op.drop_table("project_reviews")
    op.drop_table("job_terms")
    op.drop_table("jobs")
