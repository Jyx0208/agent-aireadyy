from __future__ import annotations

from collections import Counter
from statistics import mean
from typing import Any

from agent.discovery.features import UNKNOWN
from agent.discovery.models import DatasetManifest, DatasetRequest, DiscoveredFile


USABLE_VALIDITY = {"valid", "weak_keep"}


def annotate_manifest_value_scores(manifest: DatasetManifest) -> DatasetManifest:
    project_counts = Counter(file.project_accession for file in manifest.files if file.project_accession)
    files = [
        _annotate_file_value_scores(file, manifest.request, project_counts=project_counts)
        for file in manifest.files
    ]
    summary = {
        **manifest.summary,
        "task_ai_readiness_v2": _readiness_summary(files),
        "data_value_v1": _data_value_summary(files),
    }
    return manifest.model_copy(update={"files": files, "summary": summary})


def _annotate_file_value_scores(
    file: DiscoveredFile,
    request: DatasetRequest,
    *,
    project_counts: Counter[str],
) -> DiscoveredFile:
    readiness_dimensions = _readiness_dimensions(file)
    penalty = readiness_dimensions["risk_leakage_preliminary_penalty"]
    readiness_score = _clamp(
        0.20 * readiness_dimensions["task_relevance"]
        + 0.18 * readiness_dimensions["expected_label_availability"]
        + 0.17 * readiness_dimensions["metadata_completeness"]
        + 0.16 * readiness_dimensions["acquisition_workflow_fit"]
        + 0.14 * readiness_dimensions["evidence_confidence"]
        + 0.15 * readiness_dimensions["downstream_exporter_feasibility"]
        - 0.25 * penalty
    )
    if file.validity_status == "exclude":
        readiness_score = min(readiness_score, 0.24)
    elif file.validity_status == "needs_review" or file.needs_review:
        readiness_score = min(readiness_score, 0.54)
    memory_action = str((file.memory_feedback or {}).get("recommended_action") or "")
    if memory_action == "skip":
        readiness_score = min(readiness_score, 0.42)
    elif memory_action == "process" and file.validity_status in USABLE_VALIDITY:
        readiness_score = min(1.0, readiness_score + 0.04)
    requested_labeling = str(request.labeling_strategy or "").casefold()
    observed_labeling = str(file.labeling_strategy or "").casefold()
    is_unrequested_isobaric = observed_labeling in {"tmt", "itraq"} and requested_labeling not in {"tmt", "itraq"}
    if is_unrequested_isobaric:
        readiness_score = min(readiness_score, 0.74)
    readiness_score = round(readiness_score, 3)
    readiness_band = _band(readiness_score)
    readiness_reasons, readiness_warnings = _readiness_explanation(file, readiness_dimensions, request)

    value_components = _data_value_components(
        file,
        request,
        readiness_score=readiness_score,
        project_counts=project_counts,
        risk_penalty=penalty,
    )
    data_value_score = _clamp(
        0.32 * value_components["task_readiness"]
        + 0.20 * value_components["estimated_label_yield"]
        + 0.18 * value_components["diversity_gain"]
        + 0.10 * value_components["novelty"]
        + 0.14 * value_components["cost_efficiency"]
        - 0.22 * value_components["risk_penalty"]
    )
    if file.validity_status == "exclude":
        data_value_score = min(data_value_score, 0.2)
    if memory_action == "skip":
        data_value_score = min(data_value_score, 0.22)
    elif memory_action == "process" and file.validity_status in USABLE_VALIDITY:
        data_value_score = min(1.0, data_value_score + 0.06)
    elif memory_action == "review":
        data_value_score = min(data_value_score, 0.64)
    data_value_score = round(data_value_score, 3)
    action, value_reasons = _data_value_action(file, data_value_score, readiness_score, value_components)
    action, value_reasons = _apply_memory_feedback_action(memory_action, action, value_reasons)
    if is_unrequested_isobaric and action == "process":
        action = "review"
        value_reasons.append("isobaric_labeling_not_first_choice_for_task")
    return file.model_copy(
        update={
            "task_ai_readiness_score": readiness_score,
            "task_ai_readiness_band": readiness_band,
            "task_ai_readiness_reasons": readiness_reasons,
            "task_ai_readiness_warnings": readiness_warnings,
            "task_ai_readiness_dimensions": {key: round(value, 3) for key, value in readiness_dimensions.items()},
            "data_value_score": data_value_score,
            "data_value_action": action,
            "data_value_components": {key: round(value, 3) for key, value in value_components.items()},
            "data_value_reasons": value_reasons,
        }
    )


def _readiness_dimensions(file: DiscoveredFile) -> dict[str, float]:
    task_status = file.task_readiness_status or "not_evaluated"
    task_relevance = {
        "ready": 1.0,
        "weak_ready": 0.7,
        "not_ready": 0.18,
        "not_evaluated": 0.35,
    }.get(task_status, 0.3)
    if file.validity_status not in USABLE_VALIDITY:
        task_relevance = min(task_relevance, 0.35)

    label_status = file.label_source_status or ""
    if label_status == "available":
        label_availability = 0.95
    elif label_status == "requires_downstream_generation":
        label_availability = 0.58
    elif file.file_role == "search_result":
        label_availability = 0.72
    else:
        label_availability = 0.35
    hard_missing = [
        item
        for item in file.missing_task_requirements
        if item
        not in {
            "retention_time_labels",
            "fragment_intensity_labels",
            "target_decoy_psm_labels",
            "peptide_sequence_labels",
            "modified_peptide_sequence_labels",
            "ptm_localization_labels",
            "multi_peptide_spectrum_labels",
            "component_intensity_labels",
            "charge",
            "peptide_sequence",
            "lc_gradient",
            "fragmentation_method",
            "search_parameters",
            "database",
            "labeling_strategy",
        }
    ]
    label_availability = max(0.05, label_availability - 0.12 * len(hard_missing))

    known_metadata = [
        _has_known(file.canonical_species) or _has_known(file.species),
        _known_text(file.acquisition_mode),
        _has_known(file.instrument_families),
        _has_known(file.fragmentation_methods),
        file.lc_gradient_minutes is not None or _known_text(file.lc_gradient),
        _known_text(file.ptm_type) or _known_text(file.modification_scope),
        _known_text(file.immunopeptide_scope) or bool(file.immunopeptide_evidence_terms),
        _known_text(file.labeling_strategy),
    ]
    metadata_completeness = sum(1 for item in known_metadata if item) / len(known_metadata)
    if file.evidence_completeness:
        metadata_completeness = max(metadata_completeness, float(file.evidence_completeness))
    if file.semantic_metadata_confidence:
        metadata_completeness = max(metadata_completeness, min(1.0, 0.45 + 0.35 * file.semantic_metadata_confidence))
    if file.immunopeptide_metadata_confidence:
        metadata_completeness = max(metadata_completeness, min(1.0, 0.45 + 0.35 * file.immunopeptide_metadata_confidence))

    acquisition_fit = 0.9 if (file.acquisition_mode or "").casefold() == "dda" else 0.5
    if file.file_role in {"raw_acquisition", "converted_peaklist"}:
        acquisition_fit += 0.08
    elif file.file_role == "search_result":
        acquisition_fit += 0.02
    else:
        acquisition_fit -= 0.2
    if any("acquisition_not_confirmed" in reason for reason in file.task_readiness_reasons + file.validity_reasons):
        acquisition_fit = min(acquisition_fit, 0.45)

    evidence_confidence = max(float(file.trust_score or 0.0), float(file.confidence or 0.0))
    if evidence_confidence <= 0 and file.file_score:
        evidence_confidence = min(1.0, float(file.file_score) / 100.0)

    spectra_status = file.spectra_requirement_status or ""
    metadata_status = file.metadata_requirement_status or ""
    if spectra_status == "satisfied" and metadata_status in {"satisfied", "partial"}:
        exporter_feasibility = 0.82 if metadata_status == "partial" else 0.95
    elif file.file_role == "search_result":
        exporter_feasibility = 0.7
    else:
        exporter_feasibility = 0.35
    if not file.ai_ready_target_schema:
        exporter_feasibility = min(exporter_feasibility, 0.45)

    risk_penalty = 0.0
    if file.validity_status == "weak_keep":
        risk_penalty += 0.12
    if file.validity_status == "needs_review" or file.needs_review:
        risk_penalty += 0.35
    if file.validity_status == "exclude":
        risk_penalty += 0.7
    risk_penalty += min(0.25, 0.04 * len(file.evidence_warnings))
    risk_penalty += min(0.25, 0.03 * len(file.missing_task_requirements))
    if file.file_type.casefold() in {".wiff", ".d"}:
        risk_penalty += 0.15

    return {
        "task_relevance": _clamp(task_relevance),
        "expected_label_availability": _clamp(label_availability),
        "metadata_completeness": _clamp(metadata_completeness),
        "acquisition_workflow_fit": _clamp(acquisition_fit),
        "evidence_confidence": _clamp(evidence_confidence),
        "downstream_exporter_feasibility": _clamp(exporter_feasibility),
        "risk_leakage_preliminary_penalty": _clamp(risk_penalty),
    }


def _data_value_components(
    file: DiscoveredFile,
    request: DatasetRequest,
    *,
    readiness_score: float,
    project_counts: Counter[str],
    risk_penalty: float,
) -> dict[str, float]:
    file_score = min(1.0, max(0.0, float(file.file_score or file.project_score or 0.0) / 100.0))
    estimated_label_yield = _clamp(0.65 * readiness_score + 0.35 * max(file_score, float(file.trust_score or 0.0)))
    diversity_gain = _diversity_gain(file, request)
    project_count = max(1, project_counts.get(file.project_accession, 1))
    novelty = _clamp(1.0 / project_count)
    cost_efficiency = _cost_efficiency(file)
    return {
        "task_readiness": readiness_score,
        "estimated_label_yield": estimated_label_yield,
        "diversity_gain": diversity_gain,
        "novelty": novelty,
        "cost_efficiency": cost_efficiency,
        "risk_penalty": _clamp(risk_penalty),
    }


def _diversity_gain(file: DiscoveredFile, request: DatasetRequest) -> float:
    requested_species = _requested_species(request)
    candidate_species = _candidate_species(file)
    species_matches_request = bool(requested_species and candidate_species & requested_species)
    species_diversity_allowed = not requested_species
    dimensions = [
        (_has_known(file.canonical_species) or _has_known(file.species)) if species_diversity_allowed else species_matches_request,
        _has_known(file.instrument_families),
        _has_known(file.fragmentation_methods),
        _known_text(file.modification_scope or file.ptm_type),
        _known_text(file.immunopeptide_scope) or bool(file.immunopeptide_evidence_terms),
        _known_text(file.labeling_strategy),
    ]
    score = sum(1 for item in dimensions if item) / len(dimensions)
    if requested_species and species_matches_request:
        score += 0.05
    elif requested_species and candidate_species:
        score = min(score, 0.72)
    if file.diversity_tags:
        species_tags = {"species:"}
        non_species_tags = [
            tag
            for tag in file.diversity_tags
            if species_diversity_allowed or not any(str(tag).casefold().startswith(prefix) for prefix in species_tags)
        ]
        score += min(0.15, 0.03 * len(non_species_tags))
    return _clamp(score)


def _cost_efficiency(file: DiscoveredFile) -> float:
    size = file.expected_size_bytes
    if size is None or size <= 0:
        size_score = 0.55
    else:
        mb = size / (1024 * 1024)
        if mb <= 100:
            size_score = 0.95
        elif mb <= 500:
            size_score = 0.82
        elif mb <= 2048:
            size_score = 0.58
        else:
            size_score = 0.28
    suffix = file.file_type.casefold()
    if suffix in {".mzml", ".mzxml", ".mgf"}:
        format_score = 0.95
    elif suffix == ".raw":
        format_score = 0.68
    elif suffix in {".wiff", ".d"}:
        format_score = 0.38
    else:
        format_score = 0.55
    return _clamp(0.65 * size_score + 0.35 * format_score)


def _data_value_action(
    file: DiscoveredFile,
    score: float,
    readiness_score: float,
    components: dict[str, float],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if file.validity_status == "exclude":
        return "skip", ["excluded_by_discovery_validity"]
    if components["risk_penalty"] >= 0.45:
        reasons.append("high_risk_candidate")
    if components["cost_efficiency"] < 0.45:
        reasons.append("low_cost_efficiency")
    if components["diversity_gain"] >= 0.75:
        reasons.append("adds_dataset_diversity")
    if readiness_score >= 0.72:
        reasons.append("strong_task_readiness")
    elif readiness_score >= 0.5:
        reasons.append("usable_but_needs_review")
    else:
        reasons.append("weak_task_readiness")

    if score >= 0.72 and file.validity_status in USABLE_VALIDITY:
        return "process", reasons
    if score >= 0.5:
        return "review", reasons
    if readiness_score >= 0.55 and components["cost_efficiency"] < 0.45:
        return "find_alternative", reasons
    return "skip", reasons


def _apply_memory_feedback_action(memory_action: str, action: str, reasons: list[str]) -> tuple[str, list[str]]:
    if memory_action == "skip":
        return "skip", [*reasons, "discovery_memory_recommends_skip"]
    if memory_action == "process":
        if action in {"skip", "find_alternative"}:
            return "review", [*reasons, "discovery_memory_recommends_process_but_current_evidence_needs_review"]
        return action, [*reasons, "discovery_memory_recommends_process"]
    if memory_action == "review":
        if action == "process":
            return "review", [*reasons, "discovery_memory_recommends_review"]
        return action, [*reasons, "discovery_memory_recommends_review"]
    return action, reasons


def _readiness_explanation(file: DiscoveredFile, dimensions: dict[str, float], request: DatasetRequest) -> tuple[list[str], list[str]]:
    reasons = list(dict.fromkeys([*file.task_readiness_reasons, *file.validity_reasons]))
    warnings: list[str] = []
    if dimensions["metadata_completeness"] < 0.5:
        warnings.append("metadata_completeness_low")
    if dimensions["risk_leakage_preliminary_penalty"] >= 0.35:
        warnings.append("high_preliminary_risk")
    if file.labeling_strategy in {"TMT", "iTRAQ"}:
        warnings.append(f"isobaric_labeling_requires_downstream_validation:{file.labeling_strategy}")
        if str(request.labeling_strategy or "").casefold() not in {"tmt", "itraq"}:
            warnings.append("isobaric_labeling_not_first_choice_for_task")
            reasons.append("labeling_weak_for_task")
    if file.semantic_metadata_confidence:
        reasons.append("semantic_ptm_evidence")
    if file.ptm_enrichment_methods:
        reasons.append("ptm_enrichment_method_evidence")
    if file.immunopeptide_evidence_terms:
        reasons.append("semantic_immunopeptide_evidence")
    if file.immunopeptide_enrichment_methods:
        reasons.append("hla_mhc_enrichment_method_evidence")
    if file.hla_class:
        reasons.append("hla_class_evidence")
    if file.hla_alleles:
        reasons.append("hla_allele_evidence")
    requested_species = _requested_species(request)
    candidate_species = _candidate_species(file)
    if requested_species and candidate_species & requested_species:
        reasons.append("species_preference_match")
    elif requested_species and candidate_species:
        warnings.append("species_preference_not_matched")
    elif file.species_policy == "open" and (_has_known(file.canonical_species) or _has_known(file.species)):
        reasons.append("species_open_diversity_gain")
    memory_action = str((file.memory_feedback or {}).get("recommended_action") or "")
    if memory_action:
        reasons.append(f"discovery_memory_recommends_{memory_action}")
    if not reasons:
        reasons.append("scored_from_discovery_metadata")
    return reasons, warnings


def _readiness_summary(files: list[DiscoveredFile]) -> dict[str, Any]:
    scored = [file for file in files if file.task_ai_readiness_score is not None]
    band_counts = Counter(file.task_ai_readiness_band or "not_scored" for file in files)
    dimension_keys = sorted({key for file in scored for key in file.task_ai_readiness_dimensions})
    return {
        "scored_files": len(scored),
        "band_counts": dict(sorted(band_counts.items())),
        "mean_score": round(mean([float(file.task_ai_readiness_score or 0.0) for file in scored]), 3) if scored else 0.0,
        "dimension_means": {
            key: round(mean([float(file.task_ai_readiness_dimensions.get(key, 0.0)) for file in scored]), 3)
            for key in dimension_keys
        },
    }


def _data_value_summary(files: list[DiscoveredFile]) -> dict[str, Any]:
    scored = [file for file in files if file.data_value_score is not None]
    action_counts = Counter(file.data_value_action or "not_scored" for file in files)
    top = sorted(scored, key=lambda item: float(item.data_value_score or 0.0), reverse=True)[:10]
    return {
        "scored_files": len(scored),
        "action_counts": dict(sorted(action_counts.items())),
        "mean_score": round(mean([float(file.data_value_score or 0.0) for file in scored]), 3) if scored else 0.0,
        "top_candidates": [
            {
                "repository": file.repository,
                "project_accession": file.project_accession,
                "file_name": file.file_name,
                "task_type": file.task_type,
                "data_value_score": file.data_value_score,
                "action": file.data_value_action,
            }
            for file in top
        ],
    }


def _band(score: float) -> str:
    if score >= 0.78:
        return "ready"
    if score >= 0.55:
        return "weak_ready"
    if score >= 0.35:
        return "review"
    return "blocked"


def _has_known(values: list[str]) -> bool:
    return any(_known_text(value) for value in values)


def _requested_species(request: DatasetRequest) -> set[str]:
    return {str(item).casefold() for item in [*request.canonical_species, *request.species] if _known_text(item)}


def _candidate_species(file: DiscoveredFile) -> set[str]:
    return {str(item).casefold() for item in [*file.canonical_species, *file.species] if _known_text(item)}


def _known_text(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and text.casefold() != UNKNOWN


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))
