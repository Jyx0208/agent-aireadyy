"""Add file-level review, selection, and reason fields.

Revision ID: 0003_file_level_selection
Revises: 0002_dataset_construction
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0003_file_level_selection"
down_revision: str | None = "0002_dataset_construction"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = (
        sa.Column("file_id", sa.String(80)),
        sa.Column("selection_role", sa.String(48), nullable=False, server_default="primary_input"),
        sa.Column("family_id", sa.String(100)),
        sa.Column("companion_file_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("review_status", sa.String(32), nullable=False, server_default="unreviewed"),
        sa.Column("decision", sa.String(32)),
        sa.Column("reason_status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("reason_scope", sa.String(32), nullable=False, server_default="project_legacy"),
        sa.Column("reason_text", sa.Text()),
        sa.Column("grade", sa.Integer()),
        sa.Column("hard_gate", sa.String(24)),
        sa.Column("confidence", sa.Float()),
        sa.Column("judgment_model_id", sa.String(160)),
        sa.Column("judgment_version", sa.String(80)),
        sa.Column("limitations", sa.JSON(), nullable=False, server_default="[]"),
    )
    for column in columns:
        op.add_column("file_records", column)
    op.create_index(
        "ix_file_records_job_file_id",
        "file_records",
        ["job_id", "file_id"],
        unique=True,
    )
    op.create_index(
        "ix_file_records_job_review_decision_id",
        "file_records",
        ["job_id", "review_status", "decision", "id"],
    )
    op.create_index(
        "ix_file_records_job_family_id",
        "file_records",
        ["job_id", "family_id", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_file_records_job_family_id", table_name="file_records")
    op.drop_index(
        "ix_file_records_job_review_decision_id",
        table_name="file_records",
    )
    op.drop_index("ix_file_records_job_file_id", table_name="file_records")
    for name in (
        "limitations",
        "judgment_version",
        "judgment_model_id",
        "confidence",
        "hard_gate",
        "grade",
        "reason_text",
        "reason_scope",
        "reason_status",
        "decision",
        "review_status",
        "companion_file_ids",
        "family_id",
        "selection_role",
        "file_id",
    ):
        op.drop_column("file_records", name)
