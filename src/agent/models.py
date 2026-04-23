from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


SourceType = Literal["local_path", "url", "file_name"]
TaskStatus = Literal["resolved", "needs_review", "blocked", "completed"]


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
    project_accession: str
    matched_file: str
    match_type: str
    match_score: int
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
    project_accession: str
    file_name: str
    metadata: dict[str, MetadataValue] = Field(default_factory=dict)
    sdrf_rows: list[dict[str, Any]] = Field(default_factory=list)
    project_files: list[dict[str, Any]] = Field(default_factory=list)


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
    raw_data_type: Literal["mzml", "tims"]
    fasta_path: Path
    fasta_selection_mode: Literal["reproduced", "inferred", "defaulted"]
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
