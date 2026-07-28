from __future__ import annotations

from collections import Counter

from agent.discovery.features import UNKNOWN
from agent.discovery.models import DatasetManifest, DiscoveredFile, TaskReadinessStatus
from agent.discovery.task_profiles import (
    TASK_PROFILES,
    TaskProfile,
    active_task_types,
    get_task_profile,
    normalize_task_type as _normalize_task_type,
)
from agent.discovery.value_scoring import annotate_manifest_value_scores


SUPPORTED_TASK_TYPES = set(TASK_PROFILES)
USABLE_VALIDITY = {"valid", "weak_keep"}
SOFT_DOWNSTREAM_REQUIREMENTS = {
    "retention_time_labels",
    "fragment_intensity_labels",
    "target_decoy_psm_labels",
    "peptide_sequence_labels",
    "modified_peptide_sequence_labels",
    "ptm_localization_labels",
    "multi_peptide_spectrum_labels",
    "component_intensity_labels",
    "lc_gradient",
    "fragmentation_method",
    "peptide_sequence",
    "charge",
    "search_parameters",
    "database",
    "labeling_strategy",
}


def normalize_task_type(value: str | None) -> str | None:
    return _normalize_task_type(value)


def annotate_manifest_task_readiness(manifest: DatasetManifest, task_type: str | None) -> DatasetManifest:
    normalized = normalize_task_type(task_type)
    if normalized is None:
        return manifest
    profile = get_task_profile(normalized)
    files = [_annotate_file(file, profile) for file in manifest.files]
    summary = {
        **manifest.summary,
        "task_type": profile.task_type,
        "task_profile": profile.model_dump(mode="json"),
        "task_readiness": _task_readiness_summary(files),
        "active_task_types": active_task_types(),
    }
    annotated = manifest.model_copy(update={"files": files, "summary": summary})
    return annotate_manifest_value_scores(annotated)


def task_ready_files(manifest: DatasetManifest) -> list[DiscoveredFile]:
    """Strict task-ready files only (not weak_ready).

    Use pipeline_eligible_files for L1 batch-parameter handoff which may include
    weak_ready candidates that still need label generation.
    """

    return [
        file
        for file in manifest.files
        if file.task_readiness_status == "ready"
    ]


def pipeline_eligible_files(manifest: DatasetManifest) -> list[DiscoveredFile]:
    """Files eligible for L1 parameter planning (ready + weak_ready)."""

    return [
        file
        for file in manifest.files
        if file.task_readiness_status in {"ready", "weak_ready"}
    ]


def _annotate_file(file: DiscoveredFile, profile: TaskProfile) -> DiscoveredFile:
    if profile.implementation_status != "active":
        return _annotate_planned_profile(file, profile)

    reasons, missing = _base_candidate_checks(file, profile)
    reasons.extend(_metadata_reasons_and_missing(file, profile, missing))
    reasons.extend(_label_reasons(profile, file, missing))
    missing = _dedupe(missing)
    reasons = _dedupe(reasons)

    status = _readiness_status(missing)
    return file.model_copy(
        update={
            "task_type": profile.task_type,
            "task_profile": profile.display_name,
            "task_readiness_status": status,
            "task_readiness_reasons": reasons,
            "missing_task_requirements": missing,
            "label_source_status": _label_source_status(profile, missing),
            "spectra_requirement_status": _spectra_requirement_status(file, profile),
            "metadata_requirement_status": _metadata_requirement_status(missing),
            "next_pipeline_steps": profile.next_pipeline_steps,
            "ai_ready_target_schema": profile.ai_ready_target_schema,
        }
    )


def _annotate_planned_profile(file: DiscoveredFile, profile: TaskProfile) -> DiscoveredFile:
    reasons, missing = _base_candidate_checks(file, profile)
    missing = _dedupe([*missing, *profile.required_labels])
    return file.model_copy(
        update={
            "task_type": profile.task_type,
            "task_profile": profile.display_name,
            "task_readiness_status": "not_ready",
            "task_readiness_reasons": _dedupe([*reasons, "task_profile_not_implemented"]),
            "missing_task_requirements": missing,
            "label_source_status": "not_evaluated",
            "spectra_requirement_status": _spectra_requirement_status(file, profile),
            "metadata_requirement_status": "not_evaluated",
            "next_pipeline_steps": profile.next_pipeline_steps,
            "ai_ready_target_schema": profile.ai_ready_target_schema,
        }
    )


def _base_candidate_checks(file: DiscoveredFile, profile: TaskProfile) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    missing: list[str] = []
    if file.validity_status not in USABLE_VALIDITY:
        reasons.append("not_in_usable_discovery_set")
        missing.append("usable_discovery_candidate")
    if profile.required_input_files and file.file_role not in set(profile.required_input_files):
        reasons.append(f"unsupported_file_role:{file.file_role}")
        missing.append("raw_or_peaklist_file")
    if "species" in profile.required_metadata and not _known_values(file.species):
        if file.species_policy == "include_only":
            reasons.append("species_unknown_for_include_only_policy")
            missing.append("species")
        else:
            reasons.append("species_unknown_open_policy")
    if profile.preferred_acquisition and (file.acquisition_mode or "").casefold() not in {
        item.casefold() for item in profile.preferred_acquisition
    }:
        reasons.append("acquisition_not_confirmed_as_dda")
        missing.append("dda_acquisition")
    if file.evidence_level in {"project", "weak", "unknown"}:
        reasons.append(f"evidence_level_{file.evidence_level}")
    return reasons, missing


def _metadata_reasons_and_missing(
    file: DiscoveredFile,
    profile: TaskProfile,
    missing: list[str],
) -> list[str]:
    reasons: list[str] = []
    required = set(profile.required_metadata)
    if "instrument" in required and not _known_values(file.instrument_families):
        reasons.append("instrument_unknown")
        missing.append("instrument")
    if "lc_gradient" in required and file.lc_gradient_minutes is None:
        reasons.append("lc_gradient_unknown")
        missing.append("lc_gradient")
    if "fragmentation_method" in required and not _known_values(file.fragmentation_methods):
        reasons.append("fragmentation_unknown")
        missing.append("fragmentation_method")
    if "peptide_sequence" in required:
        reasons.append("requires_downstream_peptide_sequence_export")
        missing.append("peptide_sequence")
    if "charge" in required:
        reasons.append("requires_downstream_charge_export")
        missing.append("charge")
    if "search_parameters" in required:
        reasons.append("requires_downstream_search_parameter_generation")
        missing.append("search_parameters")
    if "database" in required:
        reasons.append("requires_downstream_database_selection")
        missing.append("database")
    if "ptm_type" in required and not (file.ptm_type or "").strip():
        reasons.append("ptm_type_unknown")
        missing.append("ptm_type")
    if "labeling_strategy" in required and not (file.labeling_strategy or "").strip():
        reasons.append("labeling_strategy_unknown")
        missing.append("labeling_strategy")
    if profile.task_type in {"fragment_intensity_prediction", "psm_scoring", "ptm_denovo"} and (
        file.labeling_strategy or ""
    ).casefold() in {"tmt", "itraq"}:
        reasons.append(f"isobaric_labeling_requires_downstream_quality_check:{file.labeling_strategy}")
    if file.immunopeptide_scope == "immunopeptidomics":
        reasons.append("immunopeptidomics_context")
        if not file.hla_class:
            reasons.append("hla_class_not_confirmed")
        if not file.hla_alleles:
            reasons.append("hla_allele_not_confirmed")
    if "isolation_window" in required:
        reasons.append("requires_spectrum_level_isolation_window")
        missing.append("isolation_window")
    return reasons


def _label_reasons(profile: TaskProfile, file: DiscoveredFile, missing: list[str]) -> list[str]:
    reasons: list[str] = []
    for label in profile.required_labels:
        missing.append(label)
    if profile.task_type == "rt_prediction":
        reasons.append("requires_downstream_search_export_for_rt_labels")
    elif profile.task_type == "fragment_intensity_prediction":
        reasons.append("requires_downstream_search_export_for_fragment_intensity_labels")
    elif profile.task_type == "psm_scoring":
        if file.file_role == "search_result":
            reasons.append("search_result_candidate_requires_label_schema_check")
        else:
            reasons.append("candidate_raw_requires_search_before_psm_scoring")
    elif profile.task_type == "denovo":
        reasons.append("requires_downstream_search_export_for_peptide_sequence_labels")
    elif profile.task_type == "ptm_denovo":
        reasons.append("requires_downstream_ptm_search_export_for_modified_sequence_labels")
    elif profile.task_type == "chimeric_interpretation":
        reasons.append("requires_downstream_chimeric_search_export_for_multi_peptide_labels")
    return reasons


def _readiness_status(missing: list[str]) -> TaskReadinessStatus:
    if _hard_missing(missing):
        return "not_ready"
    return "weak_ready" if missing else "ready"


def _hard_missing(missing: list[str]) -> list[str]:
    return [item for item in missing if item not in SOFT_DOWNSTREAM_REQUIREMENTS]


def _label_source_status(profile: TaskProfile, missing: list[str]) -> str:
    if any(label in missing for label in profile.required_labels):
        return "requires_downstream_generation"
    return "available"


def _spectra_requirement_status(file: DiscoveredFile, profile: TaskProfile) -> str:
    if file.validity_status not in USABLE_VALIDITY:
        return "missing"
    if profile.required_input_files and file.file_role not in set(profile.required_input_files):
        return "missing"
    return "satisfied"


def _metadata_requirement_status(missing: list[str]) -> str:
    hard = _hard_missing(missing)
    if hard:
        return "missing"
    metadata_missing = set(missing) - {
        "retention_time_labels",
        "fragment_intensity_labels",
        "target_decoy_psm_labels",
        "peptide_sequence_labels",
        "modified_peptide_sequence_labels",
        "ptm_localization_labels",
        "multi_peptide_spectrum_labels",
        "component_intensity_labels",
    }
    return "partial" if metadata_missing else "satisfied"


def _known_values(values: list[str]) -> list[str]:
    return [
        str(value)
        for value in values
        if str(value or "").strip() and str(value).casefold() != UNKNOWN
    ]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _task_readiness_summary(files: list[DiscoveredFile]) -> dict[str, object]:
    status_counts: Counter[str] = Counter(file.task_readiness_status or "not_set" for file in files)
    reason_counts: Counter[str] = Counter()
    missing_counts: Counter[str] = Counter()
    label_status_counts: Counter[str] = Counter()
    spectra_status_counts: Counter[str] = Counter()
    metadata_status_counts: Counter[str] = Counter()
    schemas: Counter[str] = Counter()
    for file in files:
        reason_counts.update(file.task_readiness_reasons)
        missing_counts.update(file.missing_task_requirements)
        if file.label_source_status:
            label_status_counts[file.label_source_status] += 1
        if file.spectra_requirement_status:
            spectra_status_counts[file.spectra_requirement_status] += 1
        if file.metadata_requirement_status:
            metadata_status_counts[file.metadata_requirement_status] += 1
        if file.ai_ready_target_schema:
            schemas[file.ai_ready_target_schema] += 1
    return {
        "status_counts": dict(sorted(status_counts.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
        "missing_requirement_counts": dict(sorted(missing_counts.items())),
        "label_source_status_counts": dict(sorted(label_status_counts.items())),
        "spectra_requirement_status_counts": dict(sorted(spectra_status_counts.items())),
        "metadata_requirement_status_counts": dict(sorted(metadata_status_counts.items())),
        "ai_ready_target_schema_counts": dict(sorted(schemas.items())),
        "task_ready_files": status_counts.get("ready", 0),
        "weak_ready_files": status_counts.get("weak_ready", 0),
        "pipeline_eligible_files": status_counts.get("ready", 0) + status_counts.get("weak_ready", 0),
    }
