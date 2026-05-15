from __future__ import annotations

from typing import Any

from pydantic import Field

from agent.models import JsonModel, RepositoryName


class CanonicalMetadataValue(JsonModel):
    value: Any
    source: str
    confidence: float = 0.8
    raw_keys: list[str] = Field(default_factory=list)
    evidence: str | None = None


class CanonicalProject(JsonModel):
    repository: RepositoryName
    primary_accession: str
    native_accession: str | None = None
    px_accession: str | None = None
    title: str | None = None
    description: str | None = None
    organisms: list[CanonicalMetadataValue] = Field(default_factory=list)
    instruments: list[CanonicalMetadataValue] = Field(default_factory=list)
    experiment_types: list[CanonicalMetadataValue] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    sample_processing_protocol: CanonicalMetadataValue | None = None
    data_processing_protocol: CanonicalMetadataValue | None = None
    submission_date: str | None = None
    publication_date: str | None = None
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class CanonicalFile(JsonModel):
    repository: RepositoryName
    project_accession: str
    file_name: str
    logical_path: str | None = None
    file_category: str | None = None
    file_format: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None
    download_urls: list[str] = Field(default_factory=list)
    transfer_method: str = "unknown"
    raw_record: dict[str, Any] = Field(default_factory=dict)
