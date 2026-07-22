from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from agent.discovery.models import DatasetRequest, DiscoveredFile, DiscoveredProject, DiscoveryEvidence
from agent.discovery.ontology import SPECIES_TERMS, is_immunopeptidomics_goal, normalize_labeling_strategy, normalize_ptm_type, normalize_species_values


ValidityStatus = Literal["valid", "weak_keep", "needs_review", "exclude"]


@dataclass(frozen=True)
class ValidityDecision:
    status: ValidityStatus
    reasons: list[str]
    needs_review: bool


SPECIES_NAME_TOKENS = {term.canonical: set(term.aliases) for term in SPECIES_TERMS}


def _has_source(evidence: list[DiscoveryEvidence], source: str) -> bool:
    return any(item.source == source for item in evidence)


def _has_mixed_acquisition(evidence: list[DiscoveryEvidence]) -> bool:
    return _has_source(evidence, "mixed_acquisition")


def _has_file_level_acquisition_evidence(evidence: list[DiscoveryEvidence]) -> bool:
    return any(
        item.source in {"file_name", "acquisition"}
        and item.field in {"file_name", "file_record", "sdrf:comment[data file]"}
        for item in evidence
    )


def _has_file_level_immunopeptide_evidence(evidence: list[DiscoveryEvidence]) -> bool:
    return any(
        item.source == "immunopeptidomics"
        and (
            item.field in {"file_name", "file_record"}
            or item.field.startswith("sdrf:")
        )
        for item in evidence
    )


_NON_IMMUNO_ASSAY_FILE_RE = re.compile(
    r"(?:^|[_\-. ])(?:whole[_\-. ]?proteome|total[_\-. ]?proteome|proteasome|"
    r"proteome[_\-. ]?digest|tryptic[_\-. ]?digest|synthetic|prm|srm|mrm)(?:[_\-. ]|$)",
    re.IGNORECASE,
)
_IMMUNO_ASSAY_FILE_RE = re.compile(
    r"(?:^|[_\-. ])(?:hla|mhc|immunopeptid|ligandome|hla[-_]?i{1,2}|"
    r"mhc[-_]?i{1,2})(?:[_\-. ]|$)",
    re.IGNORECASE,
)


def _token_in_text(text: str, token: str) -> bool:
    if re.fullmatch(r"[a-z0-9]+", token.casefold()):
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(token.casefold())}(?![a-z0-9])", text.casefold()))
    return token.casefold() in text.casefold()


def _normalized_requested_species(request: DatasetRequest) -> set[str]:
    canonical, _taxon_ids = normalize_species_values(request.species)
    return {item.casefold() for item in canonical}


def _requested_ptm_types(request: DatasetRequest) -> set[str]:
    values = request.ptm_types or [request.ptm_type]
    return {normalize_ptm_type(value) for value in values if normalize_ptm_type(value) != "unknown_ptm"}


def _ptm_matches_request(value: str | None, request: DatasetRequest) -> bool:
    requested = _requested_ptm_types(request)
    if not requested:
        return normalize_ptm_type(value) == normalize_ptm_type(request.ptm_type)
    return normalize_ptm_type(value) in requested


def _normalized_candidate_species(values: list[str]) -> set[str]:
    canonical, _taxon_ids = normalize_species_values(values)
    return {item.casefold() for item in canonical if item}


def _species_policy_reasons(candidate_species: list[str], request: DatasetRequest) -> tuple[ValidityStatus | None, list[str]]:
    requested = _normalized_requested_species(request)
    candidate = _normalized_candidate_species(candidate_species)
    if request.species_policy == "exclude" and requested and candidate & requested:
        return "exclude", ["species_hard_constraint_conflict"]
    if request.species_policy == "include_only":
        if not candidate:
            return None, ["missing_species_evidence"]
        if requested and not (candidate & requested):
            return "exclude", ["species_hard_constraint_conflict"]
        return None, ["species_include_only_match"]
    if not candidate:
        return None, ["missing_species_evidence"]
    return None, ["species_open_diversity_evidence"]


def _file_name_species_conflict(file_name: str, request: DatasetRequest) -> bool:
    requested = _normalized_requested_species(request)
    if not requested:
        return False
    for species, tokens in SPECIES_NAME_TOKENS.items():
        canonical = {species.casefold()}
        if canonical & requested:
            continue
        if any(_token_in_text(file_name, token) for token in tokens):
            return True
    return False


def _requested_labeling_requires_evidence(request: DatasetRequest) -> bool:
    return (
        request.is_hard_constraint("labeling_strategy")
        and normalize_labeling_strategy(request.labeling_strategy) != "unknown"
    )


def _labeling_matches(candidate: str | None, request: DatasetRequest) -> bool:
    requested = normalize_labeling_strategy(request.labeling_strategy)
    observed = _observed_labeling(candidate)
    if requested == "unknown" or observed == "unknown":
        return False
    return observed == requested


def _observed_labeling(value: str | None) -> str:
    if not str(value or "").strip():
        return "unknown"
    return normalize_labeling_strategy(value)


def normalize_acquisition_mode(value: str | None) -> str:
    text = str(value or "").strip().casefold()
    normalized = re.sub(r"[\s_-]+", " ", text)
    if normalized in {"", "any", "unknown", "not specified", "not reported"}:
        return "unknown"
    aliases = {
        "data dependent": "dda",
        "data dependent acquisition": "dda",
        "data independent": "dia",
        "data independent acquisition": "dia",
        "prm": "targeted",
        "srm": "targeted",
        "mrm": "targeted",
        "targeted acquisition": "targeted",
        "targeted proteomics": "targeted",
    }
    return aliases.get(normalized, normalized.replace(" ", "_"))


def _requested_acquisition_requires_evidence(request: DatasetRequest) -> bool:
    return (
        request.is_hard_constraint("acquisition_mode")
        and normalize_acquisition_mode(request.acquisition_mode) != "unknown"
    )


def _acquisition_matches(candidate: str | None, request: DatasetRequest) -> bool:
    requested = normalize_acquisition_mode(request.acquisition_mode)
    observed = normalize_acquisition_mode(candidate)
    return requested != "unknown" and observed != "unknown" and observed == requested


def assess_project_validity(project: DiscoveredProject, request: DatasetRequest) -> ValidityDecision:
    reasons: list[str] = []
    if _requested_acquisition_requires_evidence(request):
        requested_acquisition = normalize_acquisition_mode(request.acquisition_mode)
        observed_acquisition = normalize_acquisition_mode(project.acquisition_mode)
        if observed_acquisition != "unknown" and observed_acquisition != requested_acquisition:
            return ValidityDecision(
                "exclude",
                ["acquisition_hard_constraint_conflict"],
                True,
            )
    if _requested_labeling_requires_evidence(request):
        requested_labeling = normalize_labeling_strategy(request.labeling_strategy)
        observed_labeling = _observed_labeling(project.labeling_strategy)
        if observed_labeling != "unknown" and observed_labeling != requested_labeling:
            return ValidityDecision(
                "exclude",
                ["labeling_hard_constraint_conflict"],
                True,
            )
    if _has_mixed_acquisition(project.evidence):
        if request.mixed_acquisition_policy == "reject_mixed":
            return ValidityDecision("exclude", ["mixed_acquisition_project"], True)
        if request.mixed_acquisition_policy == "review_mixed":
            reasons.append("mixed_acquisition_project")
    species_status, species_reasons = _species_policy_reasons(project.species, request)
    if species_status == "exclude":
        return ValidityDecision("exclude", species_reasons, True)
    reasons.extend(species_reasons)

    immunopeptidomics_goal = is_immunopeptidomics_goal(request.goal)
    general_goal = str(request.goal or "").casefold() == "general"
    if general_goal:
        reasons.append("general_discovery_target")
    if immunopeptidomics_goal and project.immunopeptide_evidence_terms:
        reasons.append("strong_immunopeptide_evidence")
    elif immunopeptidomics_goal:
        reasons.append("weak_immunopeptide_evidence")
    if not general_goal and not immunopeptidomics_goal and _ptm_matches_request(project.ptm_type, request) and _has_source(project.evidence, "ptm"):
        reasons.append("strong_ptm_evidence")
    elif not general_goal and not immunopeptidomics_goal:
        reasons.append("weak_ptm_evidence")
    if _requested_labeling_requires_evidence(request) and not _labeling_matches(project.labeling_strategy, request):
        reasons.append("missing_labeling_strategy_evidence")

    if _requested_acquisition_requires_evidence(request) and not _acquisition_matches(
        project.acquisition_mode,
        request,
    ):
        reasons.append("missing_acquisition_evidence")
    if not project.instrument_families:
        reasons.append("missing_instrument")
    if not project.fragmentation_methods:
        reasons.append("missing_fragmentation")

    weak_reasons = {
        "weak_ptm_evidence",
        "weak_immunopeptide_evidence",
        "missing_species_evidence",
        "missing_acquisition_evidence",
        "missing_labeling_strategy_evidence",
        "missing_instrument",
        "missing_fragmentation",
        "mixed_acquisition_project",
    }
    weak_count = sum(1 for reason in reasons if reason in weak_reasons)
    if (
        "weak_ptm_evidence" in reasons
        or "weak_immunopeptide_evidence" in reasons
        or (request.species_policy == "include_only" and "missing_species_evidence" in reasons)
        or "species_hard_constraint_conflict" in reasons
        or "missing_acquisition_evidence" in reasons
        or "missing_labeling_strategy_evidence" in reasons
    ):
        status: ValidityStatus = "needs_review"
    elif weak_count:
        status = "weak_keep"
    else:
        status = "valid"
    return ValidityDecision(status, reasons, status == "needs_review")


def assess_file_validity(file: DiscoveredFile, request: DatasetRequest) -> ValidityDecision:
    reasons: list[str] = []
    mixed_acquisition = _has_mixed_acquisition(file.evidence)
    if not file.file_type:
        return ValidityDecision("exclude", ["unsupported_file_type"], True)
    if file.file_role not in {"raw_acquisition", "converted_peaklist", "unknown"}:
        return ValidityDecision("exclude", ["unsupported_file_role"], True)
    requested_labeling = normalize_labeling_strategy(request.labeling_strategy)
    observed_labeling = _observed_labeling(file.labeling_strategy)
    if (
        request.is_hard_constraint("labeling_strategy")
        and requested_labeling != "unknown"
        and observed_labeling != "unknown"
        and observed_labeling != requested_labeling
    ):
        return ValidityDecision("exclude", ["labeling_hard_constraint_conflict"], True)
    requested_acquisition = normalize_acquisition_mode(request.acquisition_mode)
    observed_acquisition = normalize_acquisition_mode(file.acquisition_mode)
    if (
        _requested_acquisition_requires_evidence(request)
        and observed_acquisition != "unknown"
        and observed_acquisition != requested_acquisition
    ):
        return ValidityDecision("exclude", ["acquisition_hard_constraint_conflict"], True)
    if mixed_acquisition and request.mixed_acquisition_policy == "reject_mixed":
        return ValidityDecision("exclude", ["mixed_acquisition_project"], True)

    immunopeptidomics_goal = is_immunopeptidomics_goal(request.goal)
    general_goal = str(request.goal or "").casefold() == "general"
    if general_goal:
        reasons.append("general_discovery_target")
    if (
        immunopeptidomics_goal
        and _NON_IMMUNO_ASSAY_FILE_RE.search(file.file_name)
        and not _IMMUNO_ASSAY_FILE_RE.search(file.file_name)
    ):
        return ValidityDecision(
            "exclude",
            ["file_name_assay_context_conflict"],
            True,
        )
    if immunopeptidomics_goal and _has_source(file.evidence, "sdrf_assay_conflict"):
        reasons.append("conflicting_sdrf_assay_evidence")
    if immunopeptidomics_goal and _has_file_level_immunopeptide_evidence(file.evidence):
        reasons.append("strong_immunopeptide_evidence")
    elif immunopeptidomics_goal and file.immunopeptide_evidence_terms:
        reasons.append("project_level_immunopeptide_evidence")
    elif immunopeptidomics_goal:
        reasons.append("weak_immunopeptide_evidence")
    if not general_goal and not immunopeptidomics_goal and _ptm_matches_request(file.ptm_type, request) and _has_source(file.evidence, "ptm"):
        reasons.append("strong_ptm_evidence")
    elif not general_goal and not immunopeptidomics_goal and _ptm_matches_request(file.ptm_type, request):
        reasons.append("project_level_ptm_evidence")
    elif not general_goal and not immunopeptidomics_goal:
        reasons.append("weak_ptm_evidence")
    if _requested_labeling_requires_evidence(request) and not _labeling_matches(file.labeling_strategy, request):
        reasons.append("missing_labeling_strategy_evidence")
    species_status, species_reasons = _species_policy_reasons(file.species, request)
    if species_status == "exclude":
        return ValidityDecision("exclude", species_reasons, True)
    reasons.extend(species_reasons)

    if not file.download_url:
        reasons.append("missing_download_url")
    if file.expected_size_bytes is None:
        reasons.append("missing_file_size")
    if request.species_policy == "include_only" and _file_name_species_conflict(file.file_name, request):
        reasons.append("file_name_species_conflict")
    if file.file_role == "converted_peaklist":
        reasons.append("converted_peaklist")
    if file.evidence_level == "project":
        reasons.append("project_level_evidence_only")
    if "sdrf_no_file_match" in file.evidence_warnings:
        reasons.append("sdrf_no_file_match")
    if _requested_acquisition_requires_evidence(request) and not _acquisition_matches(
        file.acquisition_mode,
        request,
    ):
        reasons.append("missing_acquisition_evidence")
    if (
        request.mixed_acquisition_policy == "review_mixed"
        and mixed_acquisition
        and not _has_file_level_acquisition_evidence(file.evidence)
    ):
        reasons.append("needs_file_level_acquisition_confirmation")
    if not file.instrument_families:
        reasons.append("missing_instrument")
    if not file.fragmentation_methods:
        reasons.append("missing_fragmentation")

    hard_review = {
        "weak_ptm_evidence",
        "weak_immunopeptide_evidence",
        "missing_acquisition_evidence",
        "file_name_species_conflict",
        "missing_labeling_strategy_evidence",
        "species_hard_constraint_conflict",
        "needs_file_level_acquisition_confirmation",
        "project_level_immunopeptide_evidence",
        "conflicting_sdrf_assay_evidence",
    }
    if immunopeptidomics_goal:
        hard_review.add("sdrf_no_file_match")
    if request.species_policy == "include_only":
        hard_review.add("missing_species_evidence")
    weak = {
        "project_level_ptm_evidence",
        "missing_download_url",
        "missing_file_size",
        "missing_species_evidence",
        "missing_instrument",
        "missing_fragmentation",
        "converted_peaklist",
        "project_level_evidence_only",
        "sdrf_no_file_match",
        "mixed_acquisition_project",
    }
    if any(reason in hard_review for reason in reasons):
        status: ValidityStatus = "needs_review"
    elif any(reason in weak for reason in reasons):
        status = "weak_keep"
    else:
        status = "valid"
    return ValidityDecision(status, reasons, status == "needs_review")
