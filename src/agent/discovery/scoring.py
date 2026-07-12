from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from agent.discovery.features import DiscoveryFeatureSummary, diversity_tags
from agent.discovery.models import (
    DatasetRequest,
    DiscoveredFile,
    DiscoveredProject,
    DiscoveryEvidence,
    EvidenceLevel,
    FileRole,
    SdrfMatchStatus,
)
from agent.discovery.ontology import (
    interpret_immunopeptide_metadata,
    labeling_aliases,
    labeling_from_text,
    interpret_ptm_metadata,
    is_immunopeptidomics_goal,
    normalize_labeling_strategy,
    normalize_ptm_type,
    normalize_species_values,
    species_aliases,
    species_from_text,
)
from agent.discovery.validity import assess_file_validity, assess_project_validity
from agent.pride.client import PrideClient


DDA_TERMS = ("dda", "data dependent", "data-dependent", "shotgun")
DIA_TARGETED_TERMS = (
    "dia",
    "swath",
    "data independent",
    "data-independent",
    "prm",
    "srm",
    "mrm",
)
RAW_FILE_SUFFIXES = (".raw", ".mzml", ".mzxml", ".mgf", ".wiff", ".d")
RAW_DIRECTORY_SUFFIXES = (".d.zip", ".d.tar", ".d.tar.gz")
RESULT_FILE_SUFFIXES = (
    ".tsv",
    ".txt",
    ".csv",
    ".xlsx",
    ".xls",
    ".pdf",
    ".mzid",
    ".html",
    ".json",
)
RESULT_NAME_TOKENS = (
    "search result",
    "search_result",
    "protein report",
    "peptide report",
    "psm",
    "summary",
    "result",
)
DERIVED_RAW_NAME_TOKENS = (
    ".mzid",
    ".mzidentml",
    ".pep.xml",
    ".pepxml",
    "protein_groups",
    "protein-group",
    "peptide_report",
    "protein_report",
    "psm_report",
    "search_result",
    "search result",
)

@dataclass(frozen=True)
class ProjectScore:
    project_score: float
    confidence: float
    needs_review: bool
    excluded: bool
    species: list[str]
    canonical_species: list[str]
    organism_taxon_id: list[str]
    acquisition_mode: str | None
    ptm_type: str | None
    ptm_subtype: str | None
    ptm_evidence_terms: list[str]
    ptm_enrichment_methods: list[str]
    semantic_metadata_confidence: float
    semantic_interpretation_trace: list[str]
    modification_scope: str | None
    immunopeptide_scope: str | None
    hla_class: list[str]
    hla_alleles: list[str]
    immunopeptide_evidence_terms: list[str]
    immunopeptide_enrichment_methods: list[str]
    immunopeptide_metadata_confidence: float
    labeling_strategy: str | None
    evidence: list[DiscoveryEvidence]
    exclusion_reason: str | None = None


@dataclass(frozen=True)
class FileRoleDecision:
    role: FileRole
    file_type: str | None
    reasons: list[str]


def _project_accession(project: dict[str, Any]) -> str:
    return str(project.get("accession") or project.get("projectAccession") or "")


def _project_title(project: dict[str, Any]) -> str | None:
    value = project.get("title")
    return str(value) if value else None


def _entry_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts = []
        for key in ("name", "value", "accession", "description"):
            if value.get(key):
                parts.append(str(value[key]))
        return " ".join(parts)
    if isinstance(value, list):
        return " ".join(_entry_text(item) for item in value)
    return str(value)


def _project_fields(project: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        ("title", _entry_text(project.get("title"))),
        ("description", _entry_text(project.get("projectDescription"))),
        ("sampleProcessing", _entry_text(project.get("sampleProcessingProtocol"))),
        ("dataProcessing", _entry_text(project.get("dataProcessingProtocol"))),
        ("keywords", _entry_text(project.get("keywords"))),
        ("experimentTypes", _entry_text(project.get("experimentTypes"))),
        ("organisms", _entry_text(project.get("organisms"))),
    ]


def _combined_project_text(project: dict[str, Any]) -> str:
    return " ".join(text for _, text in _project_fields(project) if text)


def _term_pattern(term: str) -> re.Pattern[str]:
    if re.fullmatch(r"[a-z0-9]+", term.casefold()):
        return re.compile(rf"(?<![a-z0-9]){re.escape(term.casefold())}(?![a-z0-9])")
    return re.compile(re.escape(term.casefold()))


def _matches(text: str, term: str) -> bool:
    return bool(_term_pattern(term).search(text.casefold()))


def _has_source(evidence: list[DiscoveryEvidence], source: str) -> bool:
    return any(item.source == source for item in evidence)


def _keyword_evidence(fields: list[tuple[str, str]], terms: tuple[str, ...], source: str, weight: float) -> list[DiscoveryEvidence]:
    evidence: list[DiscoveryEvidence] = []
    seen: set[tuple[str, str]] = set()
    for field, text in fields:
        if not text:
            continue
        for term in terms:
            if not _matches(text, term):
                continue
            key = (field, term.casefold())
            if key in seen:
                continue
            seen.add(key)
            evidence.append(DiscoveryEvidence(field=field, source=source, text=term, weight=weight))
    return evidence


def _requested_species_aliases(request: DatasetRequest) -> dict[str, tuple[str, ...]]:
    aliases: dict[str, tuple[str, ...]] = {}
    for species in request.species:
        aliases[species] = species_aliases(species)
    return aliases


def _matched_species(project: dict[str, Any], request: DatasetRequest) -> tuple[list[str], list[DiscoveryEvidence]]:
    fields = _project_fields(project)
    matches: list[str] = []
    evidence: list[DiscoveryEvidence] = []
    if request.species_policy == "open":
        detected_species, _detected_taxa = species_from_text(_combined_project_text(project))
        for species in detected_species:
            aliases = species_aliases(species)
            alias = next((item for item in aliases if _matches(_combined_project_text(project), item)), species)
            matches.append(species)
            evidence.append(DiscoveryEvidence(field="semantic_metadata", source="species", text=alias, weight=12))
    for requested, aliases in _requested_species_aliases(request).items():
        for field, text in fields:
            if not text:
                continue
            alias = next((alias for alias in aliases if _matches(text, alias)), None)
            if alias is None:
                continue
            matches.append(requested)
            evidence.append(DiscoveryEvidence(field=field, source="species", text=alias, weight=20))
            break
    return sorted(set(matches)), evidence


def score_project(project: dict[str, Any], request: DatasetRequest) -> ProjectScore:
    fields = _project_fields(project)
    combined_text = _combined_project_text(project)
    requested_ptm = normalize_ptm_type(request.ptm_type)
    requested_labeling = normalize_labeling_strategy(request.labeling_strategy)
    requested_immunopeptidomics = is_immunopeptidomics_goal(request.goal)
    general_goal = str(request.goal or "").casefold() == "general"
    ptm_semantic = interpret_ptm_metadata(combined_text, requested_ptm)
    immuno_semantic = interpret_immunopeptide_metadata(combined_text)
    general_evidence = _keyword_evidence(fields, tuple(request.query_terms), "general_query", 12) if general_goal else []
    ptm_evidence = [
        DiscoveryEvidence(field="semantic_metadata", source="ptm", text=term, weight=16)
        for term in ptm_semantic.evidence_terms
    ]
    immuno_evidence = [
        DiscoveryEvidence(field="semantic_metadata", source="immunopeptidomics", text=term, weight=18)
        for term in immuno_semantic.evidence_terms
    ]
    dda_evidence = _keyword_evidence(fields, DDA_TERMS, "acquisition", 8)
    raw_negative_evidence = _keyword_evidence(fields, DIA_TARGETED_TERMS, "unsupported_acquisition", -100)
    mixed_acquisition_evidence = [
        DiscoveryEvidence(field=item.field, source="mixed_acquisition", text=item.text, weight=-20)
        for item in raw_negative_evidence
    ] if dda_evidence and raw_negative_evidence else []
    negative_evidence = [] if mixed_acquisition_evidence else raw_negative_evidence
    labeling_evidence = _keyword_evidence(fields, labeling_aliases(requested_labeling), "labeling", 8)
    detected_labeling = labeling_from_text(combined_text)
    species, species_evidence = _matched_species(project, request)
    canonical_species, taxon_ids = normalize_species_values(species)

    score = 0.0
    if general_evidence:
        score += min(45.0, 18.0 + 5.0 * len({item.text.casefold() for item in general_evidence}))
    if immuno_evidence:
        score += min(52.0, 24.0 + 6.0 * len({item.text.casefold() for item in immuno_evidence}))
    if ptm_evidence:
        score += min(50.0, 20.0 + 6.0 * len({item.text.casefold() for item in ptm_evidence}))
    if species:
        score += 20.0
    if dda_evidence:
        score += 10.0
    if requested_labeling in {"TMT", "iTRAQ"} and labeling_evidence:
        score += 8.0
    elif requested_labeling == "label_free":
        score += 2.0

    populated_fields = sum(1 for _, text in fields if text.strip())
    score += min(10.0, populated_fields * 1.5)

    excluded = (
        request.is_hard_constraint("acquisition_mode")
        and request.acquisition_mode == "dda"
        and bool(negative_evidence)
    )
    labeling_missing = requested_labeling in {"TMT", "iTRAQ"} and not labeling_evidence
    species_required = request.species_policy == "include_only"
    ptm_required = not general_goal and not requested_immunopeptidomics and requested_ptm != "unknown_ptm"
    immuno_missing = requested_immunopeptidomics and not immuno_evidence
    needs_review = (
        (ptm_required and not ptm_evidence)
        or immuno_missing
        or (species_required and not species)
        or (
            request.is_hard_constraint("acquisition_mode")
            and request.acquisition_mode == "dda"
            and not dda_evidence
        )
        or bool(mixed_acquisition_evidence)
        or labeling_missing
    )
    confidence = max(0.0, min(1.0, score / 90.0))
    evidence = (
        general_evidence
        + immuno_evidence
        + ptm_evidence
        + species_evidence
        + dda_evidence
        + labeling_evidence
        + mixed_acquisition_evidence
        + negative_evidence
    )
    return ProjectScore(
        project_score=round(score, 2),
        confidence=round(confidence, 3),
        needs_review=needs_review or excluded,
        excluded=excluded,
        species=species,
        canonical_species=canonical_species,
        organism_taxon_id=taxon_ids,
        acquisition_mode="dda" if dda_evidence else None,
        ptm_type=ptm_semantic.canonical if ptm_evidence else None,
        ptm_subtype=";".join(ptm_semantic.subtypes) or None,
        ptm_evidence_terms=list(ptm_semantic.evidence_terms),
        ptm_enrichment_methods=list(ptm_semantic.enrichment_methods),
        semantic_metadata_confidence=ptm_semantic.confidence,
        semantic_interpretation_trace=list(ptm_semantic.trace),
        modification_scope=ptm_semantic.canonical if ptm_evidence else None,
        immunopeptide_scope=immuno_semantic.scope if immuno_evidence else None,
        hla_class=list(immuno_semantic.hla_classes),
        hla_alleles=list(immuno_semantic.hla_alleles),
        immunopeptide_evidence_terms=list(immuno_semantic.evidence_terms),
        immunopeptide_enrichment_methods=list(immuno_semantic.enrichment_methods),
        immunopeptide_metadata_confidence=immuno_semantic.confidence,
        labeling_strategy=detected_labeling or (requested_labeling if labeling_evidence else None),
        evidence=evidence,
        exclusion_reason="DIA/targeted evidence conflicts with DDA request." if excluded else None,
    )


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def _project_completeness(score: ProjectScore, features: DiscoveryFeatureSummary) -> float:
    checks = [
        bool(score.ptm_type) or bool(score.immunopeptide_scope) or _has_source(score.evidence, "general_query"),
        bool(score.species),
        bool(score.acquisition_mode),
        bool(features.instrument_families),
        bool(features.fragmentation_methods),
    ]
    return round(sum(1 for item in checks if item) / len(checks), 3)


def _file_completeness(
    *,
    file: DiscoveredFile | None = None,
    download_url: str | None = None,
    expected_size_bytes: int | None = None,
    project: DiscoveredProject,
    features: DiscoveryFeatureSummary,
) -> float:
    checks = [
        bool(download_url if file is None else file.download_url),
        (expected_size_bytes if file is None else file.expected_size_bytes) is not None,
        bool(project.species),
        bool(project.acquisition_mode),
        bool(project.ptm_type) or bool(project.immunopeptide_scope) or _has_source(project.evidence, "general_query"),
        bool(features.instrument_families),
        bool(features.fragmentation_methods),
    ]
    return round(sum(1 for item in checks if item) / len(checks), 3)


def _trust_score(confidence: float, completeness: float, memory_prior: float, needs_review: bool) -> float:
    penalty = 0.05 if needs_review else 0.0
    return round(_clamp(confidence * 0.8 + completeness * 0.2 + memory_prior - penalty), 3)


def _trust_score_after_validity(trust_score: float, status: str, reasons: list[str]) -> float:
    if status == "exclude":
        return 0.0

    penalty = 0.0
    if status == "needs_review":
        penalty += 0.18
    elif status == "weak_keep":
        penalty += 0.06

    reason_penalties = {
        "project_level_ptm_evidence": 0.04,
        "missing_fragmentation": 0.03,
        "missing_instrument": 0.03,
        "missing_download_url": 0.08,
        "missing_file_size": 0.03,
        "file_name_species_conflict": 0.25,
        "converted_peaklist": 0.04,
        "project_level_evidence_only": 0.06,
        "sdrf_no_file_match": 0.04,
        "mixed_acquisition_project": 0.08,
        "needs_file_level_acquisition_confirmation": 0.12,
    }
    penalty += sum(reason_penalties.get(reason, 0.0) for reason in reasons)
    return round(_clamp(trust_score - penalty), 3)


def _file_context_from_evidence(
    *,
    project_evidence: list[DiscoveryEvidence],
    file_evidence: list[DiscoveryEvidence],
    sdrf_match_status: SdrfMatchStatus,
) -> tuple[EvidenceLevel, int, int, list[str]]:
    file_count = len(file_evidence)
    project_count = len(project_evidence)
    warnings: list[str] = []

    if sdrf_match_status == "no_sdrf":
        warnings.append("sdrf_missing")
    elif sdrf_match_status == "no_file_match":
        warnings.append("sdrf_no_file_match")

    if file_count and project_count:
        level: EvidenceLevel = "mixed"
    elif file_count:
        level = "file"
    elif project_count:
        level = "project"
        warnings.append("project_level_evidence_only")
    else:
        level = "unknown"
        warnings.append("no_evidence")

    if level == "mixed" and not any(item.field.startswith("sdrf:") for item in file_evidence):
        warnings.append("file_name_or_record_evidence_only")

    return level, file_count, project_count, sorted(set(warnings))


def build_discovered_project(
    project: dict[str, Any],
    request: DatasetRequest,
    score: ProjectScore,
    *,
    features: DiscoveryFeatureSummary | None = None,
    memory_prior: float = 0.0,
    memory_feedback: dict[str, Any] | None = None,
) -> DiscoveredProject:
    feature_summary = features or DiscoveryFeatureSummary()
    completeness = _project_completeness(score, feature_summary)
    trust = _trust_score(score.confidence, completeness, memory_prior, score.needs_review)
    general_goal = str(request.goal or "").casefold() == "general"
    request_ptm = None if general_goal else request.ptm_type
    request_modification_scope = None if general_goal else (request.modification_scope or normalize_ptm_type(request.ptm_type))
    project_model = DiscoveredProject(
        repository=request.repository,
        project_accession=_project_accession(project),
        native_accession=str(project.get("accession") or project.get("projectAccession") or "") or None,
        px_accession=str(project.get("projectAccession") or project.get("accession") or "") or None,
        project_title=_project_title(project),
        project_description=_entry_text(project.get("projectDescription")) or None,
        species=score.species,
        species_policy=request.species_policy,
        canonical_species=score.canonical_species,
        organism_taxon_id=score.organism_taxon_id,
        acquisition_mode=score.acquisition_mode or request.acquisition_mode,
        ptm_type=score.ptm_type or request_ptm,
        modification_scope=score.modification_scope or request_modification_scope,
        ptm_subtype=score.ptm_subtype,
        ptm_evidence_terms=score.ptm_evidence_terms,
        ptm_enrichment_methods=score.ptm_enrichment_methods,
        semantic_metadata_confidence=score.semantic_metadata_confidence,
        semantic_interpretation_trace=score.semantic_interpretation_trace,
        immunopeptide_scope=score.immunopeptide_scope or request.immunopeptide_scope,
        hla_class=score.hla_class or request.hla_class,
        hla_alleles=score.hla_alleles or request.hla_alleles,
        immunopeptide_evidence_terms=score.immunopeptide_evidence_terms or request.immunopeptide_evidence_terms,
        immunopeptide_enrichment_methods=score.immunopeptide_enrichment_methods or request.immunopeptide_enrichment_methods,
        immunopeptide_metadata_confidence=max(score.immunopeptide_metadata_confidence, request.immunopeptide_metadata_confidence),
        labeling_strategy=score.labeling_strategy or request.labeling_strategy,
        project_score=score.project_score,
        confidence=score.confidence,
        trust_score=trust,
        evidence_completeness=completeness,
        memory_prior=round(memory_prior, 3),
        memory_feedback=memory_feedback or {},
        needs_review=score.needs_review,
        evidence=score.evidence + feature_summary.evidence,
        instrument_names=feature_summary.instrument_names,
        instrument_families=feature_summary.instrument_families,
        fragmentation_methods=feature_summary.fragmentation_methods,
        lc_gradient=feature_summary.lc_gradient,
        lc_gradient_minutes=feature_summary.lc_gradient_minutes,
        diversity_tags=diversity_tags(
            species=score.species,
            instrument_families=feature_summary.instrument_families,
            fragmentation_methods=feature_summary.fragmentation_methods,
            lc_gradient_minutes=feature_summary.lc_gradient_minutes,
            modification_scope=score.modification_scope or request_modification_scope,
            immunopeptide_scope=score.immunopeptide_scope or request.immunopeptide_scope,
            labeling_strategy=score.labeling_strategy or request.labeling_strategy,
        ),
        raw_metadata=project,
    )
    validity = assess_project_validity(project_model, request)
    return project_model.model_copy(
        update={
            "validity_status": validity.status,
            "validity_reasons": validity.reasons,
            "needs_review": project_model.needs_review or validity.needs_review,
        }
    )


def file_type_for_name(file_name: str) -> str | None:
    lower = file_name.casefold()
    for suffix in RAW_DIRECTORY_SUFFIXES:
        if lower.endswith(suffix):
            return ".d"
    for suffix in RAW_FILE_SUFFIXES:
        if lower.endswith(suffix):
            return suffix
    return None


def classify_file_role(file_name: str) -> FileRoleDecision:
    lower = file_name.casefold()
    file_type = file_type_for_name(file_name)
    reasons: list[str] = []

    if any(token in lower for token in DERIVED_RAW_NAME_TOKENS) or lower.endswith(".mzid"):
        return FileRoleDecision("search_result", file_type or ".mzid", ["derived_identification_token"])

    if lower.endswith((".sdrf.tsv", ".sdrf.txt")) or "sdrf" in lower:
        return FileRoleDecision("metadata", file_type, ["sdrf_metadata"])

    if lower.endswith(RESULT_FILE_SUFFIXES):
        table_suffixes = (".tsv", ".txt", ".csv", ".xlsx", ".xls", ".pdf", ".html", ".json")
        role: FileRole = "report_table" if lower.endswith(table_suffixes) else "search_result"
        return FileRoleDecision(role, file_type, ["result_or_report_suffix"])

    if file_type == ".mgf":
        return FileRoleDecision("converted_peaklist", file_type, ["peaklist_suffix"])

    if file_type is not None:
        if file_type == ".d" and any(lower.endswith(suffix) for suffix in RAW_DIRECTORY_SUFFIXES):
            reasons.append("vendor_directory_archive")
        else:
            reasons.append("raw_acquisition_suffix")
        return FileRoleDecision("raw_acquisition", file_type, reasons)

    return FileRoleDecision("unknown", None, ["unsupported_or_unknown_suffix"])


def is_supported_raw_file(file_name: str) -> bool:
    return classify_file_role(file_name).role in {"raw_acquisition", "converted_peaklist"}


def is_result_or_report_file(file_name: str) -> bool:
    role = classify_file_role(file_name).role
    if role in {"search_result", "metadata", "report_table"}:
        return True
    lower = file_name.casefold()
    return role == "unknown" and any(token in lower for token in RESULT_NAME_TOKENS)


def _file_name(file_record: dict[str, Any]) -> str:
    return str(file_record.get("fileName") or file_record.get("name") or "")


def _file_size(file_record: dict[str, Any]) -> int | None:
    for key in ("fileSizeBytes", "fileSize", "size"):
        value = file_record.get(key)
        if value in (None, ""):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _transfer_method(download_url: str | None) -> str | None:
    if not download_url:
        return None
    lower = download_url.casefold()
    if lower.startswith("ftp://"):
        return "ftp"
    if lower.startswith(("http://", "https://")):
        return "https"
    return "unknown"


def score_file(
    file_record: dict[str, Any],
    project: DiscoveredProject,
    request: DatasetRequest,
    *,
    features: DiscoveryFeatureSummary | None = None,
    memory_prior: float = 0.0,
    memory_feedback: dict[str, Any] | None = None,
    sdrf_match_status: SdrfMatchStatus = "not_checked",
) -> DiscoveredFile | None:
    file_name = _file_name(file_record)
    role = classify_file_role(file_name)
    if not file_name or role.file_type is None or role.role not in {"raw_acquisition", "converted_peaklist"}:
        return None

    fields = [("file_name", file_name), ("file_record", _entry_text(file_record))]
    project_evidence = list(project.evidence)
    requested_ptm = normalize_ptm_type(project.ptm_type or request.ptm_type)
    general_goal = str(request.goal or "").casefold() == "general"
    file_semantic = interpret_ptm_metadata(" ".join(text for _, text in fields), requested_ptm)
    file_immuno = interpret_immunopeptide_metadata(" ".join(text for _, text in fields))
    general_file_evidence = _keyword_evidence(fields, tuple(request.query_terms), "general_query", 6) if general_goal else []
    file_evidence = [
        DiscoveryEvidence(field="file_name", source="ptm", text=term, weight=8)
        for term in file_semantic.evidence_terms
    ]
    immuno_file_evidence = [
        DiscoveryEvidence(field="file_name", source="immunopeptidomics", text=term, weight=9)
        for term in file_immuno.evidence_terms
    ]
    dda_evidence = _keyword_evidence(fields, DDA_TERMS, "file_name", 4)
    negative_evidence = _keyword_evidence(fields, DIA_TARGETED_TERMS, "unsupported_acquisition", -100)
    requested_labeling = normalize_labeling_strategy(request.labeling_strategy)
    labeling_evidence = _keyword_evidence(fields, labeling_aliases(requested_labeling), "labeling", 4)
    detected_labeling = labeling_from_text(" ".join(text for _, text in fields))
    if (
        request.is_hard_constraint("acquisition_mode")
        and request.acquisition_mode == "dda"
        and negative_evidence
    ):
        return None

    score = 40.0
    if role.file_type in {".raw", ".mzml"}:
        score += 10.0
    if role.role == "converted_peaklist":
        score -= 6.0
    if general_file_evidence:
        score += min(16.0, 6.0 + 3.0 * len(general_file_evidence))
    if file_evidence:
        score += min(20.0, 8.0 + 4.0 * len(file_evidence))
    if immuno_file_evidence:
        score += min(22.0, 10.0 + 4.0 * len(immuno_file_evidence))
    if dda_evidence:
        score += 5.0
    if requested_labeling in {"TMT", "iTRAQ"} and labeling_evidence:
        score += 5.0

    download_url = PrideClient.first_download_url(file_record)
    expected_size_bytes = _file_size(file_record)
    needs_review = project.needs_review or not download_url or expected_size_bytes is None
    confidence = max(0.0, min(1.0, (project.project_score + score) / 150.0))
    feature_summary = features or DiscoveryFeatureSummary()
    completeness = _file_completeness(
        download_url=download_url,
        expected_size_bytes=expected_size_bytes,
        project=project,
        features=feature_summary,
    )
    trust = _trust_score(confidence, completeness, memory_prior, needs_review)
    file_context_evidence = general_file_evidence + immuno_file_evidence + file_evidence + dda_evidence + labeling_evidence + feature_summary.evidence
    evidence_level, file_level_count, project_level_count, evidence_warnings = _file_context_from_evidence(
        project_evidence=project_evidence,
        file_evidence=file_context_evidence,
        sdrf_match_status=sdrf_match_status,
    )

    file_model = DiscoveredFile(
        repository=request.repository,
        project_accession=project.project_accession,
        native_accession=project.native_accession,
        px_accession=project.px_accession,
        file_accession_or_path=str(
            file_record.get("fileAccession")
            or file_record.get("accession")
            or file_record.get("path")
            or file_record.get("filePath")
            or file_name
        ),
        project_title=project.project_title,
        file_name=file_name,
        download_url=download_url,
        transfer_method=_transfer_method(download_url),
        file_type=role.file_type,
        file_role=role.role,
        file_role_reasons=role.reasons,
        sdrf_match_status=sdrf_match_status,
        evidence_level=evidence_level,
        file_level_evidence_count=file_level_count,
        project_level_evidence_count=project_level_count,
        evidence_warnings=evidence_warnings,
        expected_size_bytes=expected_size_bytes,
        species=project.species,
        species_policy=request.species_policy,
        canonical_species=project.canonical_species,
        organism_taxon_id=project.organism_taxon_id,
        acquisition_mode=project.acquisition_mode,
        ptm_type=project.ptm_type,
        ptm_subtype=file_semantic.subtypes[0] if file_semantic.subtypes else project.ptm_subtype,
        ptm_evidence_terms=list(dict.fromkeys([*project.ptm_evidence_terms, *file_semantic.evidence_terms])),
        ptm_enrichment_methods=list(dict.fromkeys([*project.ptm_enrichment_methods, *file_semantic.enrichment_methods])),
        semantic_metadata_confidence=max(project.semantic_metadata_confidence, file_semantic.confidence),
        semantic_interpretation_trace=list(dict.fromkeys([*project.semantic_interpretation_trace, *file_semantic.trace])),
        modification_scope=project.modification_scope,
        immunopeptide_scope=file_immuno.scope if immuno_file_evidence else project.immunopeptide_scope,
        hla_class=list(dict.fromkeys([*project.hla_class, *file_immuno.hla_classes])),
        hla_alleles=list(dict.fromkeys([*project.hla_alleles, *file_immuno.hla_alleles])),
        immunopeptide_evidence_terms=list(dict.fromkeys([*project.immunopeptide_evidence_terms, *file_immuno.evidence_terms])),
        immunopeptide_enrichment_methods=list(dict.fromkeys([*project.immunopeptide_enrichment_methods, *file_immuno.enrichment_methods])),
        immunopeptide_metadata_confidence=max(project.immunopeptide_metadata_confidence, file_immuno.confidence),
        labeling_strategy=detected_labeling or project.labeling_strategy,
        project_score=project.project_score,
        file_score=round(score, 2),
        confidence=round(confidence, 3),
        trust_score=trust,
        evidence_completeness=completeness,
        memory_prior=round(memory_prior, 3),
        memory_feedback=memory_feedback or {},
        needs_review=needs_review,
        evidence=project_evidence + file_context_evidence,
        instrument_names=feature_summary.instrument_names,
        instrument_families=feature_summary.instrument_families,
        fragmentation_methods=feature_summary.fragmentation_methods,
        lc_gradient=feature_summary.lc_gradient,
        lc_gradient_minutes=feature_summary.lc_gradient_minutes,
        diversity_tags=diversity_tags(
            species=project.species,
            instrument_families=feature_summary.instrument_families,
            fragmentation_methods=feature_summary.fragmentation_methods,
            lc_gradient_minutes=feature_summary.lc_gradient_minutes,
            modification_scope=project.modification_scope,
            immunopeptide_scope=file_immuno.scope if immuno_file_evidence else project.immunopeptide_scope,
            labeling_strategy=detected_labeling or project.labeling_strategy,
        ),
        raw_record=file_record,
    )
    validity = assess_file_validity(file_model, request)
    return file_model.model_copy(
        update={
            "validity_status": validity.status,
            "validity_reasons": validity.reasons,
            "needs_review": file_model.needs_review or validity.needs_review,
            "trust_score": _trust_score_after_validity(file_model.trust_score, validity.status, validity.reasons),
        }
    )
