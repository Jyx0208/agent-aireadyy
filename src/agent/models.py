from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


SourceType = Literal["local_path", "url", "file_name"]
TaskStatus = Literal["resolved", "needs_review", "blocked", "completed", "failed"]
RepositoryName = Literal["pride", "massive", "iprox"]


class JsonModel(BaseModel):
    model_config = {
        "arbitrary_types_allowed": True,
        "populate_by_name": True,
    }


class InputTask(JsonModel):
    task_id: str
    original_input: str
    source_type: SourceType
    file_name: str
    normalized_name: str
    stem: str
    extension: str
    checksum: str | None = None


class MetadataValue(JsonModel):
    value: Any
    source: str
    source_level: str
    completeness: float


class ProjectCandidate(JsonModel):
    repository: RepositoryName = "pride"
    project_accession: str
    matched_file: str
    match_type: str
    match_score: int
    native_accession: str | None = None
    px_accession: str | None = None
    publication_date: date | None = None
    submission_date: date | None = None
    evidence: list[str] = Field(default_factory=list)
    metadata_consistency: float = 0.0


class ProjectResolution(JsonModel):
    primary_project: ProjectCandidate | None = None
    alternative_projects: list[ProjectCandidate] = Field(default_factory=list)
    resolution_reason: str = ""
    resolution_confidence: float = 0.0
    needs_review: bool = False

    @classmethod
    def empty(cls) -> "ProjectResolution":
        return cls(
            primary_project=None,
            alternative_projects=[],
            resolution_reason="No project resolved.",
            resolution_confidence=0.0,
            needs_review=True,
        )


class ProjectContext(JsonModel):
    repository: RepositoryName = "pride"
    project_accession: str
    native_accession: str | None = None
    px_accession: str | None = None
    file_name: str
    metadata: dict[str, MetadataValue] = Field(default_factory=dict)
    sdrf_rows: list[dict[str, Any]] = Field(default_factory=list)
    project_files: list[dict[str, Any]] = Field(default_factory=list)
    evidence_documents: list[dict[str, Any]] = Field(default_factory=list)
    raw_project_metadata: dict[str, Any] = Field(default_factory=dict)


class FileAsset(JsonModel):
    repository: RepositoryName = "pride"
    original_file_name: str
    resolved_asset_type: Literal["mzml", "mzxml", "tims", "raw", "wiff", "mgf", "mzid", "unknown"]
    project_accession: str | None = None
    native_project_accession: str | None = None
    matched_project_file: str | None = None
    logical_path: str | None = None
    file_category: str | None = None
    file_format: str | None = None
    download_url: str | None = None
    download_urls: list[str] = Field(default_factory=list)
    transfer_method: Literal["https", "ftp", "aspera", "webdav", "unknown"] = "unknown"
    local_path: Path | None = None
    prepared_path: Path | None = None
    expected_size_bytes: int | None = None
    checksum: str | None = None
    sidecar_files: list[dict[str, Any]] = Field(default_factory=list)
    requires_conversion: bool = False
    asset_confidence: float = 0.0
    match_type: str = "unresolved"


class AttributeValue(JsonModel):
    value: Any
    confidence: float
    source: str
    evidence_excerpt: str
    conflict_flag: bool = False


class AttributeSet(JsonModel):
    acquisition_mode: AttributeValue
    species: AttributeValue
    instrument_name: AttributeValue
    instrument_family: AttributeValue
    enzyme: AttributeValue
    labeling_strategy: AttributeValue
    fixed_mods: AttributeValue
    variable_mods: AttributeValue
    fractionation_hint: AttributeValue
    search_parameter_hints: AttributeValue


class DdaExecutionPlan(JsonModel):
    task_id: str
    source_file_name: str
    source_data_path: Path
    raw_data_type: Literal["mzml", "tims", "wiff2mzml", "mgf", "mzid"]
    fasta_path: Path
    fasta_selection_mode: Literal["reproduced", "inferred", "defaulted", "reviewed"]
    fasta_download_url: str | None = None
    fragpipe_workflow_path: Path
    manifest_path: Path
    converter_config_path: Path
    rawspectrum_output_path: Path
    fragpipe_workdir: Path
    expected_pin_path: Path
    expected_pin_glob: str
    output_paths: dict[str, Path]
    thread_num: int = 10
    needs_review: bool = False
    blocking_issues: list[str] = Field(default_factory=list)


class RunManifest(JsonModel):
    task_id: str
    created_at: datetime
    status: TaskStatus
    project_accession: str | None = None
    source_file: str
    source_data_path: str | None = None
    outputs: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class ReviewItem(JsonModel):
    task_id: str
    source_file: str
    project_accession: str | None = None
    stage: str
    reasons: list[str] = Field(default_factory=list)
    created_at: datetime


class TaskStateSnapshot(JsonModel):
    task_id: str
    status: TaskStatus
    stage: str
    source_file: str
    project_accession: str | None = None
    updated_at: datetime
    notes: list[str] = Field(default_factory=list)


class ToolchainReport(JsonModel):
    docker_cli_available: bool
    docker_daemon_available: bool
    docker_client_version: str | None = None
    docker_server_version: str | None = None
    docker_pwiz_image_available: bool = False
    docker_msdt_image_available: bool = False
    git_available: bool
    java_available: bool
    msconvert_available: bool = False
    fragpipe_root: str | None = None
    msdt_converter_root: str | None = None
    notes: list[str] = Field(default_factory=list)


class MaterializedTaskBundle(JsonModel):
    plan: DdaExecutionPlan
    converter_config_path: Path
    materialized_workflow_path: Path
    materialized_fasta_path: Path
    task_root: Path


class PridePlanResult(JsonModel):
    resolution: ProjectResolution
    context: ProjectContext
    asset: FileAsset
    attributes: AttributeSet
    plan: DdaExecutionPlan
