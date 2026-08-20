from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class DatasetConstructionBase(DeclarativeBase):
    pass


class DatasetReleaseRow(DatasetConstructionBase):
    __tablename__ = "dataset_releases"

    release_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    task_spec: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_batch_dir: Mapped[str] = mapped_column(Text, nullable=False)
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    release_dir: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_iso)


class DatasetProtocolRow(DatasetConstructionBase):
    __tablename__ = "dataset_split_protocols"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    release_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_releases.release_id", ondelete="CASCADE"),
        nullable=False,
    )
    protocol: Mapped[str] = mapped_column(String(64), nullable=False)
    split_status: Mapped[str] = mapped_column(String(32), nullable=False)
    audit_status: Mapped[str] = mapped_column(String(32), nullable=False)
    group_count: Mapped[int] = mapped_column(Integer, nullable=False)
    allocation_count: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("ix_dataset_protocol_release_protocol", "release_id", "protocol", unique=True),
    )


class DatasetAuditFindingRow(DatasetConstructionBase):
    __tablename__ = "dataset_audit_findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    release_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_releases.release_id", ondelete="CASCADE"),
        nullable=False,
    )
    protocol: Mapped[str] = mapped_column(String(64), nullable=False)
    dimension: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    overlap_count: Mapped[int] = mapped_column(Integer, nullable=False)
    missing_count: Mapped[int] = mapped_column(Integer, nullable=False)
    severity: Mapped[str] = mapped_column(String(24), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    __table_args__ = (
        Index("ix_dataset_audit_release_protocol", "release_id", "protocol"),
    )


class DatasetSplitAllocationRow(DatasetConstructionBase):
    __tablename__ = "dataset_split_allocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    release_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_releases.release_id", ondelete="CASCADE"),
        nullable=False,
    )
    protocol: Mapped[str] = mapped_column(String(64), nullable=False)
    observation_id: Mapped[str] = mapped_column(String(160), nullable=False)
    component_id: Mapped[str] = mapped_column(String(160), nullable=False)
    split: Mapped[str] = mapped_column(String(24), nullable=False)

    __table_args__ = (
        Index(
            "ix_dataset_allocation_release_protocol_split",
            "release_id",
            "protocol",
            "split",
        ),
        Index(
            "ix_dataset_allocation_unique",
            "release_id",
            "protocol",
            "observation_id",
            unique=True,
        ),
    )
