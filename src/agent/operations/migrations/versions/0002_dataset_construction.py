"""Add leakage-aware dataset release registry.

Revision ID: 0002_dataset_construction
Revises: 0001_operations_plane
Create Date: 2026-08-12
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0002_dataset_construction"
down_revision: str | None = "0001_operations_plane"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dataset_releases",
        sa.Column("release_id", sa.String(160), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("task_spec", sa.JSON(), nullable=False),
        sa.Column("source_batch_dir", sa.Text(), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("release_dir", sa.Text(), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_index("ix_dataset_releases_status", "dataset_releases", ["status"])

    op.create_table(
        "dataset_split_protocols",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "release_id",
            sa.String(160),
            sa.ForeignKey("dataset_releases.release_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("protocol", sa.String(64), nullable=False),
        sa.Column("split_status", sa.String(32), nullable=False),
        sa.Column("audit_status", sa.String(32), nullable=False),
        sa.Column("group_count", sa.Integer(), nullable=False),
        sa.Column("allocation_count", sa.Integer(), nullable=False),
    )
    op.create_index(
        "ix_dataset_protocol_release_protocol",
        "dataset_split_protocols",
        ["release_id", "protocol"],
        unique=True,
    )

    op.create_table(
        "dataset_audit_findings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "release_id",
            sa.String(160),
            sa.ForeignKey("dataset_releases.release_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("protocol", sa.String(64), nullable=False),
        sa.Column("dimension", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("overlap_count", sa.Integer(), nullable=False),
        sa.Column("missing_count", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(24), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_dataset_audit_release_protocol",
        "dataset_audit_findings",
        ["release_id", "protocol"],
    )

    op.create_table(
        "dataset_split_allocations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "release_id",
            sa.String(160),
            sa.ForeignKey("dataset_releases.release_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("protocol", sa.String(64), nullable=False),
        sa.Column("observation_id", sa.String(160), nullable=False),
        sa.Column("component_id", sa.String(160), nullable=False),
        sa.Column("split", sa.String(24), nullable=False),
    )
    op.create_index(
        "ix_dataset_allocation_release_protocol_split",
        "dataset_split_allocations",
        ["release_id", "protocol", "split"],
    )
    op.create_index(
        "ix_dataset_allocation_unique",
        "dataset_split_allocations",
        ["release_id", "protocol", "observation_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_dataset_allocation_unique", table_name="dataset_split_allocations")
    op.drop_index(
        "ix_dataset_allocation_release_protocol_split",
        table_name="dataset_split_allocations",
    )
    op.drop_table("dataset_split_allocations")
    op.drop_index(
        "ix_dataset_audit_release_protocol",
        table_name="dataset_audit_findings",
    )
    op.drop_table("dataset_audit_findings")
    op.drop_index(
        "ix_dataset_protocol_release_protocol",
        table_name="dataset_split_protocols",
    )
    op.drop_table("dataset_split_protocols")
    op.drop_index("ix_dataset_releases_status", table_name="dataset_releases")
    op.drop_table("dataset_releases")
