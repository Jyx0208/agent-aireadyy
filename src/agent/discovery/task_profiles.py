from __future__ import annotations

from typing import Literal

from pydantic import Field

from agent.models import JsonModel


TaskImplementationStatus = Literal["active", "planned"]


class TaskProfile(JsonModel):
    task_type: str
    display_name: str
    implementation_status: TaskImplementationStatus = "planned"
    required_input_files: list[str] = Field(default_factory=list)
    required_labels: list[str] = Field(default_factory=list)
    required_metadata: list[str] = Field(default_factory=list)
    preferred_acquisition: list[str] = Field(default_factory=list)
    preferred_fragmentation: list[str] = Field(default_factory=list)
    positive_evidence: list[str] = Field(default_factory=list)
    negative_evidence: list[str] = Field(default_factory=list)
    readiness_rules: list[str] = Field(default_factory=list)
    next_pipeline_steps: list[str] = Field(default_factory=list)
    ai_ready_target_schema: str
    quality_gate: list[str] = Field(default_factory=list)


TASK_TYPE_ALIASES = {
    "rt": "rt_prediction",
    "retention_time": "rt_prediction",
    "retention_time_prediction": "rt_prediction",
    "fragment_intensity": "fragment_intensity_prediction",
    "fragment_ion_intensity": "fragment_intensity_prediction",
    "fragment_ion_intensity_prediction": "fragment_intensity_prediction",
    "psm": "psm_scoring",
    "psm_score": "psm_scoring",
    "denovo": "denovo",
    "de_novo": "denovo",
    "de_novo_peptide_sequencing": "denovo",
    "ptm_denovo": "ptm_denovo",
    "ptm_de_novo": "ptm_denovo",
    "ptm_aware_denovo": "ptm_denovo",
    "ptm_aware_de_novo": "ptm_denovo",
    "chimeric": "chimeric_interpretation",
    "chimeric_spectrum": "chimeric_interpretation",
    "chimeric_spectrum_interpretation": "chimeric_interpretation",
}


TASK_PROFILES: dict[str, TaskProfile] = {
    "rt_prediction": TaskProfile(
        task_type="rt_prediction",
        display_name="Retention time prediction",
        implementation_status="active",
        required_input_files=["raw_acquisition", "converted_peaklist"],
        required_labels=["retention_time_labels"],
        required_metadata=["species", "dda_acquisition", "instrument", "lc_gradient", "peptide_sequence"],
        preferred_acquisition=["dda"],
        preferred_fragmentation=["HCD", "CID", "ETD"],
        positive_evidence=["RT", "retention time", "iRT", "LC gradient", "DDA"],
        negative_evidence=["DIA-only", "targeted-only", "result/report-only files"],
        readiness_rules=[
            "usable_discovery_candidate",
            "raw_or_peaklist_file",
            "species_known",
            "dda_acquisition",
            "instrument_known",
            "lc_gradient_preferred",
            "retention_time_labels_generated_downstream",
        ],
        next_pipeline_steps=[
            "search",
            "psm_filtering",
            "rt_export",
            "rt_train.parquet",
        ],
        ai_ready_target_schema="rt_train.parquet",
        quality_gate=[
            "high_confidence_psms",
            "consistent_lc_context",
            "decoy_or_qvalue_filtering",
        ],
    ),
    "fragment_intensity_prediction": TaskProfile(
        task_type="fragment_intensity_prediction",
        display_name="Fragment ion intensity prediction",
        implementation_status="active",
        required_input_files=["raw_acquisition", "converted_peaklist"],
        required_labels=["fragment_intensity_labels"],
        required_metadata=["species", "dda_acquisition", "instrument", "fragmentation_method", "charge", "labeling_strategy"],
        preferred_acquisition=["dda"],
        preferred_fragmentation=["HCD", "CID", "ETD"],
        positive_evidence=["fragmentation", "HCD", "CID", "ETD", "MS/MS", "Orbitrap", "Q-TOF"],
        negative_evidence=["MS1-only", "DIA-only", "targeted-only", "result/report-only files"],
        readiness_rules=[
            "usable_discovery_candidate",
            "raw_or_peaklist_file",
            "species_known",
            "dda_acquisition",
            "instrument_known",
            "fragmentation_method_preferred",
            "fragment_labels_generated_downstream",
        ],
        next_pipeline_steps=[
            "search",
            "psm_filtering",
            "fragment_annotation",
            "fragment_intensity_train.parquet",
        ],
        ai_ready_target_schema="fragment_intensity_train.parquet",
        quality_gate=[
            "high_confidence_psms",
            "known_fragmentation_method",
            "charge_state_available",
            "matched_fragment_quality",
        ],
    ),
    "psm_scoring": TaskProfile(
        task_type="psm_scoring",
        display_name="PSM scoring",
        implementation_status="active",
        required_input_files=["raw_acquisition", "converted_peaklist"],
        required_labels=["target_decoy_psm_labels"],
        required_metadata=["species", "dda_acquisition", "search_parameters", "database", "labeling_strategy"],
        preferred_acquisition=["dda"],
        preferred_fragmentation=["HCD", "CID", "ETD"],
        positive_evidence=["DDA", "searchable raw", "mzML", "target-decoy", "FDR"],
        negative_evidence=["result-only without spectra", "missing database context", "DIA-only"],
        readiness_rules=[
            "usable_discovery_candidate",
            "raw_or_peaklist_file",
            "species_known",
            "dda_acquisition",
            "target_decoy_labels_generated_downstream",
        ],
        next_pipeline_steps=[
            "search_with_target_decoy_database",
            "psm_export",
            "score_feature_export",
            "psm_scoring_train.parquet",
        ],
        ai_ready_target_schema="psm_scoring_train.parquet",
        quality_gate=[
            "target_decoy_available",
            "qvalue_or_fdr_available",
            "search_scores_available",
        ],
    ),
    "denovo": TaskProfile(
        task_type="denovo",
        display_name="De novo peptide sequencing",
        implementation_status="active",
        required_input_files=["raw_acquisition", "converted_peaklist"],
        required_labels=["peptide_sequence_labels"],
        required_metadata=["species", "dda_acquisition", "instrument", "fragmentation_method", "charge"],
        preferred_acquisition=["dda"],
        preferred_fragmentation=["HCD", "CID", "ETD"],
        positive_evidence=["high confidence PSM", "DDA", "HCD", "CID", "ETD"],
        negative_evidence=["low confidence labels", "DIA-only", "MS1-only"],
        readiness_rules=["planned_after_search_label_quality_gate"],
        next_pipeline_steps=[
            "search",
            "high_confidence_psm_filtering",
            "spectrum_sequence_pair_export",
            "denovo_train.parquet",
        ],
        ai_ready_target_schema="denovo_train.parquet",
        quality_gate=["very_high_confidence_psms", "sequence_label_quality", "fragmentation_context"],
    ),
    "ptm_denovo": TaskProfile(
        task_type="ptm_denovo",
        display_name="PTM-aware de novo sequencing",
        implementation_status="active",
        required_input_files=["raw_acquisition", "converted_peaklist"],
        required_labels=["modified_peptide_sequence_labels", "ptm_localization_labels"],
        required_metadata=["species", "dda_acquisition", "instrument", "fragmentation_method", "ptm_type", "labeling_strategy"],
        preferred_acquisition=["dda"],
        preferred_fragmentation=["HCD", "ETD", "EThcD", "CID"],
        positive_evidence=["PTM enrichment", "phospho", "acetyl", "glyco", "methyl", "localization probability"],
        negative_evidence=["ambiguous PTM localization", "unmodified-only datasets", "DIA-only"],
        readiness_rules=["planned_after_ptm_localization_quality_gate"],
        next_pipeline_steps=[
            "ptm_search",
            "localization_filtering",
            "modified_sequence_export",
            "ptm_denovo_train.parquet",
        ],
        ai_ready_target_schema="ptm_denovo_train.parquet",
        quality_gate=["ptm_localization_confidence", "modified_sequence_quality", "fragmentation_context"],
    ),
    "chimeric_interpretation": TaskProfile(
        task_type="chimeric_interpretation",
        display_name="Chimeric spectrum interpretation",
        implementation_status="active",
        required_input_files=["raw_acquisition", "converted_peaklist"],
        required_labels=["multi_peptide_spectrum_labels", "component_intensity_labels"],
        required_metadata=["species", "dda_acquisition", "isolation_window", "instrument", "fragmentation_method"],
        preferred_acquisition=["dda"],
        preferred_fragmentation=["HCD", "CID"],
        positive_evidence=["chimeric spectra", "wide isolation window", "multi-peptide assignment"],
        negative_evidence=["single-peptide-only labels", "missing isolation window", "MS1-only"],
        readiness_rules=["active_after_chimeric_label_quality_gate"],
        next_pipeline_steps=[
            "search_with_chimeric_support",
            "multi_component_labeling",
            "chimeric_train.parquet",
        ],
        ai_ready_target_schema="chimeric_train.parquet",
        quality_gate=["multi_peptide_label_confidence", "isolation_window_available", "component_assignment_quality"],
    ),
}


# Grill / UI labels that mean "no modeling task yet" — discovery only.
OPTIONAL_TASK_TYPES = {
    "browse_only",
    "browse",
    "data_only",
    "find_data",
    "find_data_only",
    "none",
    "null",
    "unknown",
    "undetermined",
    "unspecified",
    "other",
    "n_a",
    "na",
    "any",
    "general",
}


def normalize_task_type(value: str | None) -> str | None:
    if value is None:
        return None
    task_type = value.strip().lower().replace("-", "_").replace(" ", "_")
    if not task_type:
        return None
    task_type = TASK_TYPE_ALIASES.get(task_type, task_type)
    if task_type in OPTIONAL_TASK_TYPES:
        # Data discovery without a fixed modeling task (e.g. grill "browse_only").
        return None
    if task_type not in TASK_PROFILES:
        raise ValueError(
            "Unsupported task type. Supported values: "
            + ", ".join(sorted(TASK_PROFILES))
            + ", or empty/browse_only for data-only discovery"
        )
    return task_type


def get_task_profile(task_type: str) -> TaskProfile:
    normalized = normalize_task_type(task_type)
    if normalized is None:
        raise ValueError("Task type is required.")
    return TASK_PROFILES[normalized]


def list_task_profiles() -> list[TaskProfile]:
    return [TASK_PROFILES[key] for key in sorted(TASK_PROFILES)]


def active_task_types() -> list[str]:
    return [key for key, profile in sorted(TASK_PROFILES.items()) if profile.implementation_status == "active"]
