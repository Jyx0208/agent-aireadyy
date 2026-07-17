from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agent.models import JsonModel


class DiscoveryEvidence(JsonModel):
    field: str
    source: str
    text: str
    weight: float = 0.0


FileRole = Literal[
    "raw_acquisition",
    "converted_peaklist",
    "search_result",
    "metadata",
    "report_table",
    "unknown",
]
SdrfMatchStatus = Literal["matched", "no_sdrf", "no_file_match", "not_checked"]
EvidenceLevel = Literal["file", "mixed", "project", "weak", "unknown"]
TaskReadinessStatus = Literal["ready", "weak_ready", "not_ready"]
DiscoveryRepository = Literal["pride", "massive", "iprox", "auto", "local"]
SpeciesPolicy = Literal["open", "include_only", "exclude"]


class DatasetRequest(JsonModel):
    repository: Literal["pride", "massive", "iprox", "auto"] = "pride"
    goal: str = "general"
    ptm_type: str = "unknown_ptm"
    ptm_types: list[str] = Field(default_factory=list)
    query_terms: list[str] = Field(default_factory=list)
    species: list[str] = Field(default_factory=list)
    species_policy: SpeciesPolicy = "open"
    acquisition_mode: str = "dda"
    labeling_strategy: str = "label_free"
    canonical_species: list[str] = Field(default_factory=list)
    organism_taxon_id: list[str] = Field(default_factory=list)
    modification_scope: str | None = None
    immunopeptide_scope: str | None = None
    hla_class: list[str] = Field(default_factory=list)
    hla_alleles: list[str] = Field(default_factory=list)
    immunopeptide_evidence_terms: list[str] = Field(default_factory=list)
    immunopeptide_enrichment_methods: list[str] = Field(default_factory=list)
    immunopeptide_metadata_confidence: float = 0.0
    max_projects: int = Field(default=100, ge=1)
    max_files: int = Field(default=2000, ge=1)
    max_candidate_projects: int = Field(default=300, ge=1)
    max_files_per_project: int = Field(default=50, ge=1)
    quantity_scope: Literal["unspecified", "portfolio", "per_project"] = "unspecified"
    portfolio_size_preference: str | None = None
    per_project_min_files: int | None = Field(default=None, ge=1)
    per_project_min_samples: int | None = Field(default=None, ge=1)
    hard_constraint_fields: list[str] = Field(
        default_factory=lambda: [
            "repository",
            "goal",
            "ptm_type",
            "ptm_types",
            "species",
            "species_policy",
            "acquisition_mode",
            "labeling_strategy",
        ]
    )
    constraint_provenance: dict[str, str] = Field(default_factory=dict)

    def is_hard_constraint(self, field_name: str) -> bool:
        return str(field_name) in set(self.hard_constraint_fields)


class DiscoveredProject(JsonModel):
    repository: DiscoveryRepository = "pride"
    project_accession: str
    native_accession: str | None = None
    px_accession: str | None = None
    project_title: str | None = None
    project_description: str | None = None
    species: list[str] = Field(default_factory=list)
    species_policy: SpeciesPolicy = "open"
    canonical_species: list[str] = Field(default_factory=list)
    organism_taxon_id: list[str] = Field(default_factory=list)
    acquisition_mode: str | None = None
    ptm_type: str | None = None
    ptm_subtype: str | None = None
    ptm_evidence_terms: list[str] = Field(default_factory=list)
    ptm_enrichment_methods: list[str] = Field(default_factory=list)
    semantic_metadata_confidence: float = 0.0
    semantic_interpretation_trace: list[str] = Field(default_factory=list)
    modification_scope: str | None = None
    immunopeptide_scope: str | None = None
    hla_class: list[str] = Field(default_factory=list)
    hla_alleles: list[str] = Field(default_factory=list)
    immunopeptide_evidence_terms: list[str] = Field(default_factory=list)
    immunopeptide_enrichment_methods: list[str] = Field(default_factory=list)
    immunopeptide_metadata_confidence: float = 0.0
    labeling_strategy: str | None = None
    project_score: float = 0.0
    calibrated_project_score: float | None = None
    calibration_version: str | None = None
    calibration_components: dict[str, float] = Field(default_factory=dict)
    confidence: float = 0.0
    trust_score: float = 0.0
    evidence_completeness: float = 0.0
    memory_prior: float = 0.0
    memory_feedback: dict[str, Any] = Field(default_factory=dict)
    validity_status: Literal["valid", "weak_keep", "needs_review", "exclude"] = "weak_keep"
    validity_reasons: list[str] = Field(default_factory=list)
    needs_review: bool = False
    evidence: list[DiscoveryEvidence] = Field(default_factory=list)
    instrument_names: list[str] = Field(default_factory=list)
    instrument_families: list[str] = Field(default_factory=list)
    fragmentation_methods: list[str] = Field(default_factory=list)
    lc_gradient: str | None = None
    lc_gradient_minutes: float | None = None
    diversity_tags: list[str] = Field(default_factory=list)
    file_count: int = 0
    selected_file_count: int = 0
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class DiscoveredFile(JsonModel):
    repository: DiscoveryRepository = "pride"
    project_accession: str
    native_accession: str | None = None
    px_accession: str | None = None
    file_accession_or_path: str | None = None
    project_title: str | None = None
    file_name: str
    download_url: str | None = None
    transfer_method: str | None = None
    file_type: str
    file_role: FileRole = "unknown"
    file_role_reasons: list[str] = Field(default_factory=list)
    sdrf_match_status: SdrfMatchStatus = "not_checked"
    evidence_level: EvidenceLevel = "unknown"
    file_level_evidence_count: int = 0
    project_level_evidence_count: int = 0
    evidence_warnings: list[str] = Field(default_factory=list)
    expected_size_bytes: int | None = None
    species: list[str] = Field(default_factory=list)
    species_policy: SpeciesPolicy = "open"
    canonical_species: list[str] = Field(default_factory=list)
    organism_taxon_id: list[str] = Field(default_factory=list)
    acquisition_mode: str | None = None
    ptm_type: str | None = None
    ptm_subtype: str | None = None
    ptm_evidence_terms: list[str] = Field(default_factory=list)
    ptm_enrichment_methods: list[str] = Field(default_factory=list)
    semantic_metadata_confidence: float = 0.0
    semantic_interpretation_trace: list[str] = Field(default_factory=list)
    modification_scope: str | None = None
    immunopeptide_scope: str | None = None
    hla_class: list[str] = Field(default_factory=list)
    hla_alleles: list[str] = Field(default_factory=list)
    immunopeptide_evidence_terms: list[str] = Field(default_factory=list)
    immunopeptide_enrichment_methods: list[str] = Field(default_factory=list)
    immunopeptide_metadata_confidence: float = 0.0
    labeling_strategy: str | None = None
    project_score: float = 0.0
    file_score: float = 0.0
    confidence: float = 0.0
    trust_score: float = 0.0
    evidence_completeness: float = 0.0
    memory_prior: float = 0.0
    memory_feedback: dict[str, Any] = Field(default_factory=dict)
    validity_status: Literal["valid", "weak_keep", "needs_review", "exclude"] = "weak_keep"
    validity_reasons: list[str] = Field(default_factory=list)
    needs_review: bool = False
    task_type: str | None = None
    task_profile: str | None = None
    task_readiness_status: TaskReadinessStatus | None = None
    task_readiness_reasons: list[str] = Field(default_factory=list)
    missing_task_requirements: list[str] = Field(default_factory=list)
    task_ai_readiness_score: float | None = None
    task_ai_readiness_band: str | None = None
    task_ai_readiness_reasons: list[str] = Field(default_factory=list)
    task_ai_readiness_warnings: list[str] = Field(default_factory=list)
    task_ai_readiness_dimensions: dict[str, float] = Field(default_factory=dict)
    data_value_score: float | None = None
    data_value_action: str | None = None
    data_value_components: dict[str, float] = Field(default_factory=dict)
    data_value_reasons: list[str] = Field(default_factory=list)
    label_source_status: str | None = None
    spectra_requirement_status: str | None = None
    metadata_requirement_status: str | None = None
    next_pipeline_steps: list[str] = Field(default_factory=list)
    ai_ready_target_schema: str | None = None
    review_decision: str | None = None
    review_reason: str | None = None
    review_note: str | None = None
    evidence: list[DiscoveryEvidence] = Field(default_factory=list)
    instrument_names: list[str] = Field(default_factory=list)
    instrument_families: list[str] = Field(default_factory=list)
    fragmentation_methods: list[str] = Field(default_factory=list)
    lc_gradient: str | None = None
    lc_gradient_minutes: float | None = None
    diversity_tags: list[str] = Field(default_factory=list)
    raw_record: dict[str, Any] = Field(default_factory=dict)


class DatasetManifest(JsonModel):
    run_id: str | None = None
    request: DatasetRequest
    projects: list[DiscoveredProject] = Field(default_factory=list)
    files: list[DiscoveredFile] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
