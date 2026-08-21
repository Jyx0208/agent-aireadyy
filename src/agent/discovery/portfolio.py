"""Agent-guided portfolio construction for discovery manifests.

This module deliberately keeps the scientific gate deterministic.  An Agent may
decide which evidence to search for next, but it cannot make a missing diversity
dimension look satisfied by writing a nicer explanation.  The resulting state is
small enough to persist in the control-plane run record and rich enough for the
UI to explain assumptions, gaps, and recovery options.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any, Iterable, Literal, Mapping

from pydantic import Field, model_validator

from agent.discovery.models import DatasetRequest, DiscoveredFile
from agent.models import JsonModel


PortfolioDimension = Literal[
    "projects",
    "files",
    "files_per_project",
    "labs",
    "instruments",
    "organisms",
    "acquisition_modes",
    "fragmentation_methods",
    "acquisition_or_fragmentation",
    "modifications",
    "file_extensions",
    "evidence",
]
PortfolioDetailLevel = Literal["guided", "benchmark", "strict"]
PortfolioStateStatus = Literal[
    "planning",
    "searching",
    "needs_recovery",
    "ready",
    "frozen",
    "blocked",
]
RecoveryActionKind = Literal[
    "search_dimension",
    "inspect_metadata",
    "select_best_effort",
    "relax_soft_requirement",
    "relax_hard_requirement",
    "stop_with_limitations",
]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _unique(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = _text(value)
        if not item:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


class PortfolioSpec(JsonModel):
    """User-facing portfolio intent, with explicit requirements separated from defaults."""

    schema_version: str = "portfolio-spec/v1"
    target_projects: int | None = Field(default=None, ge=1, le=5000)
    target_files: int | None = Field(default=None, ge=1, le=200000)
    min_files_per_project: int | None = Field(default=None, ge=1, le=5000)
    max_files_per_project: int | None = Field(default=None, ge=1, le=5000)
    min_distinct_labs: int | None = Field(default=None, ge=1, le=500)
    min_distinct_instruments: int | None = Field(default=None, ge=1, le=500)
    min_distinct_organisms: int | None = Field(default=None, ge=1, le=500)
    min_distinct_acquisition_modes: int | None = Field(default=None, ge=1, le=50)
    min_distinct_fragmentation_methods: int | None = Field(default=None, ge=1, le=100)
    min_distinct_acquisition_or_fragmentation: int | None = Field(default=None, ge=1, le=100)
    min_distinct_modifications: int | None = Field(default=None, ge=1, le=500)
    allowed_file_extensions: list[str] = Field(default_factory=list)
    preferred_file_extensions: list[str] = Field(default_factory=list)
    detail_level: PortfolioDetailLevel = "guided"
    hard_dimensions: list[PortfolioDimension] = Field(default_factory=list)
    max_recovery_rounds: int = Field(default=4, ge=0, le=20)
    max_recovery_actions: int = Field(default=8, ge=1, le=50)
    approval_required_for_hard_relaxation: bool = True

    @model_validator(mode="after")
    def normalize(self) -> "PortfolioSpec":
        self.allowed_file_extensions = _normalize_extensions(self.allowed_file_extensions)
        self.preferred_file_extensions = _normalize_extensions(self.preferred_file_extensions)
        self.hard_dimensions = list(dict.fromkeys(self.hard_dimensions))
        if self.detail_level == "strict" and not self.hard_dimensions:
            self.hard_dimensions = [
                "projects",
                "files",
                "files_per_project",
                "labs",
                "instruments",
                "organisms",
                "acquisition_modes",
                "fragmentation_methods",
                "acquisition_or_fragmentation",
                "file_extensions",
                "evidence",
            ]
        return self

    @classmethod
    def from_request(cls, request: DatasetRequest) -> "PortfolioSpec":
        raw = request.portfolio_spec if isinstance(request.portfolio_spec, dict) else {}
        payload = dict(raw)
        # Existing first-class request fields remain authoritative when a portfolio
        # field was not explicitly supplied.  They are soft unless the request says
        # the corresponding field is hard.
        payload.setdefault("max_files_per_project", request.max_files_per_project)
        if request.per_project_min_files is not None:
            payload.setdefault("min_files_per_project", request.per_project_min_files)
        if request.max_files_per_project is not None:
            payload.setdefault("max_files_per_project", request.max_files_per_project)
        if request.quota_flexibility == "fixed":
            payload.setdefault("target_projects", request.max_projects)
            payload.setdefault("detail_level", "benchmark")
        explicit = set(request.hard_constraint_fields or [])
        hard = list(payload.get("hard_dimensions") or [])
        aliases = {
            "max_projects": "projects",
            "target_projects": "projects",
            "max_files": "files",
            "target_files": "files",
            "per_project_min_files": "files_per_project",
            "laboratory": "labs",
            "lab": "labs",
            "instrument": "instruments",
            "species": "organisms",
            "organism": "organisms",
            "acquisition": "acquisition_modes",
            "fragmentation": "fragmentation_methods",
            "modification": "modifications",
            "extension": "file_extensions",
        }
        for field in explicit:
            dimension = aliases.get(field)
            if dimension and dimension not in hard:
                hard.append(dimension)
        if payload.get("max_files_per_project") is not None:
            hard.append("files_per_project")
        payload["hard_dimensions"] = hard
        return cls.model_validate(payload)


class AssumptionLedgerEntry(JsonModel):
    id: str
    label: str
    value: Any = None
    source: Literal["explicit", "inferred", "open"] = "open"
    status: Literal["accepted", "needs_confirmation"] = "needs_confirmation"
    impact: Literal["hard", "soft", "informational"] = "informational"
    rationale: str = ""


class PortfolioGap(JsonModel):
    dimension: PortfolioDimension
    required: int
    observed: int
    missing: int = Field(ge=0)
    severity: Literal["hard", "soft", "unknown"] = "soft"
    recoverable: bool = True
    message: str
    evidence_refs: list[str] = Field(default_factory=list)


class PortfolioCoverage(JsonModel):
    schema_version: str = "portfolio-coverage/v1"
    candidate_files: int = 0
    selected_files: int = 0
    distinct_projects: int = 0
    files_per_project: dict[str, int] = Field(default_factory=dict)
    dimension_counts: dict[str, int] = Field(default_factory=dict)
    distributions: dict[str, dict[str, int]] = Field(default_factory=dict)
    unknown_counts: dict[str, int] = Field(default_factory=dict)
    hard_gap_count: int = 0
    soft_gap_count: int = 0
    evidence_complete_files: int = 0
    evidence_unknown_files: int = 0
    invalid_file_extensions: int = 0
    gaps: list[PortfolioGap] = Field(default_factory=list)
    ready: bool = False


class RecoveryAction(JsonModel):
    id: str
    kind: RecoveryActionKind
    dimension: PortfolioDimension | None = None
    priority: int = Field(default=1, ge=1, le=100)
    rationale: str
    expected_gain: str = ""
    query_hints: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    status: Literal["proposed", "accepted", "executed", "skipped"] = "proposed"


class RecoveryAttempt(JsonModel):
    attempt_id: str
    action_id: str
    started_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    status: Literal["planned", "running", "completed", "failed", "stopped"] = "planned"
    observation: str = ""
    gain: dict[str, int] = Field(default_factory=dict)


class PortfolioState(JsonModel):
    schema_version: str = "portfolio-state/v1"
    status: PortfolioStateStatus = "planning"
    spec: PortfolioSpec
    assumptions: list[AssumptionLedgerEntry] = Field(default_factory=list)
    coverage: PortfolioCoverage | None = None
    gaps: list[PortfolioGap] = Field(default_factory=list)
    recovery_actions: list[RecoveryAction] = Field(default_factory=list)
    recovery_attempts: list[RecoveryAttempt] = Field(default_factory=list)
    selected_file_identifiers: list[str] = Field(default_factory=list)
    selected_project_accessions: list[str] = Field(default_factory=list)
    frozen_rationale: str | None = None
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


def _normalize_extensions(values: Iterable[Any]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        text = _text(value).lower()
        if not text:
            continue
        if not text.startswith("."):
            text = "." + text
        if text not in normalized:
            normalized.append(text)
    return normalized


def _metadata_values(item: DiscoveredFile, dimension: str) -> list[str]:
    if dimension == "labs":
        names = (
            "laboratory_names",
            "laboratories",
            "laboratory",
            "laboratory_name",
            "lab",
            "lab_name",
            "institution",
            "institution_name",
            "institute",
            "center",
            "centre",
        )
        tags = ("lab:", "laboratory:", "institution:")
        raw_keys = names
    elif dimension == "instruments":
        names = ("instrument_families", "instrument_names", "instrument")
        tags = ("instrument:",)
        raw_keys = names
    elif dimension == "organisms":
        # Canonical species is the identity key; do not count "human" and
        # "Homo sapiens" as two organisms when both are present.
        canonical = list(getattr(item, "canonical_species", []) or [])
        if canonical:
            return _unique(canonical)
        names = ("species", "organism", "organisms")
        tags = ("species:", "organism:")
        raw_keys = names
    elif dimension == "acquisition_modes":
        names = ("acquisition_mode",)
        tags = ("acquisition:", "acquisition_mode:")
        raw_keys = names
    elif dimension == "fragmentation_methods":
        names = ("fragmentation_methods", "fragmentation_method")
        tags = ("fragmentation:",)
        raw_keys = names
    elif dimension == "acquisition_or_fragmentation":
        return _unique([
            *_metadata_values(item, "acquisition_modes"),
            *_metadata_values(item, "fragmentation_methods"),
        ])
    elif dimension == "modifications":
        names = ("modification_scope", "ptm_type", "ptm_subtype", "ptm_evidence_terms")
        tags = ("modification:", "ptm:")
        raw_keys = names
    else:
        names, tags, raw_keys = (), (), ()
    values: list[Any] = []
    for name in names:
        values.extend(getattr(item, name, []) if isinstance(getattr(item, name, []), list) else [getattr(item, name, None)])
    for tag in getattr(item, "diversity_tags", []):
        text = _text(tag)
        if any(text.casefold().startswith(prefix) for prefix in tags):
            values.append(text.split(":", 1)[1] if ":" in text else text)
    raw = getattr(item, "raw_record", {}) or {}
    if isinstance(raw, Mapping):
        for key in raw_keys:
            value = raw.get(key)
            values.extend(value if isinstance(value, list) else [value])
    cleaned = [value for value in _unique(values) if value.casefold() not in {"unknown", "na", "n/a", "none", "null"}]
    return cleaned


def _file_extension(item: DiscoveredFile) -> str | None:
    name = _text(item.file_name).lower()
    for extension in (".raw.zip", ".mzml.gz", ".mzxml.gz", ".mzml", ".mzxml", ".raw"):
        if name.endswith(extension):
            return extension
    file_type = _text(item.file_type).lower()
    return "." + file_type.lstrip(".") if file_type else None


def _dimension_values(item: DiscoveredFile, dimension: str) -> list[str]:
    if dimension == "file_extensions":
        extension = _file_extension(item)
        return [extension] if extension else []
    if dimension == "projects":
        return [_text(item.project_accession)] if _text(item.project_accession) else []
    if dimension == "files":
        return [_text(item.file_accession_or_path or item.file_name)]
    if dimension == "files_per_project":
        return []
    return _metadata_values(item, dimension)


def _file_identifier(item: DiscoveredFile) -> str:
    return f"{item.repository}:{item.project_accession}:{item.file_accession_or_path or item.file_name}"


def _has_complete_evidence(item: DiscoveredFile) -> bool:
    """Whether this row can be reproduced from an evidence-backed manifest."""

    return bool(
        _text(item.project_accession)
        and _text(item.file_name)
        and _text(item.download_url)
        and item.expected_size_bytes is not None
        and bool(item.evidence)
    )


def _is_hard(dimension: PortfolioDimension, spec: PortfolioSpec) -> bool:
    return dimension in set(spec.hard_dimensions)


def assess_portfolio_coverage(
    files: Iterable[DiscoveredFile],
    spec: PortfolioSpec,
    *,
    selected: bool = True,
) -> PortfolioCoverage:
    rows = list(files)
    projects = sorted({row.project_accession for row in rows if _text(row.project_accession)})
    distributions: dict[str, dict[str, int]] = {}
    unknown_counts: dict[str, int] = {}
    dimensions = (
        "labs",
        "instruments",
        "organisms",
        "acquisition_modes",
        "fragmentation_methods",
        "acquisition_or_fragmentation",
        "modifications",
        "file_extensions",
    )
    for dimension in dimensions:
        counter: Counter[str] = Counter()
        unknown = 0
        for row in rows:
            values = _dimension_values(row, dimension)
            if not values:
                unknown += 1
            counter.update(value.casefold() for value in values)
        distributions[dimension] = dict(counter)
        unknown_counts[dimension] = unknown

    per_project: dict[str, int] = defaultdict(int)
    for row in rows:
        per_project[_text(row.project_accession)] += 1
    per_project = {key: value for key, value in sorted(per_project.items()) if key}
    dimension_counts = {
        "projects": len(projects),
        "files": len(rows),
        "labs": len(distributions["labs"]),
        "instruments": len(distributions["instruments"]),
        "organisms": len(distributions["organisms"]),
        "acquisition_modes": len(distributions["acquisition_modes"]),
        "fragmentation_methods": len(distributions["fragmentation_methods"]),
        "acquisition_or_fragmentation": len(distributions["acquisition_or_fragmentation"]),
        "modifications": len(distributions["modifications"]),
        "file_extensions": len(distributions["file_extensions"]),
    }
    requirements: list[tuple[PortfolioDimension, int | None]] = [
        ("projects", spec.target_projects),
        ("files", spec.target_files),
        ("files_per_project", spec.min_files_per_project),
        ("labs", spec.min_distinct_labs),
        ("instruments", spec.min_distinct_instruments),
        ("organisms", spec.min_distinct_organisms),
        ("acquisition_modes", spec.min_distinct_acquisition_modes),
        ("fragmentation_methods", spec.min_distinct_fragmentation_methods),
        ("acquisition_or_fragmentation", spec.min_distinct_acquisition_or_fragmentation),
        ("modifications", spec.min_distinct_modifications),
        ("evidence", spec.target_files if (spec.detail_level == "strict" or "evidence" in spec.hard_dimensions) else None),
    ]
    gaps: list[PortfolioGap] = []
    for dimension, required in requirements:
        if required is None:
            continue
        if dimension == "files_per_project":
            observed = min(per_project.values(), default=0)
        elif dimension == "evidence":
            observed = sum(1 for row in rows if _has_complete_evidence(row))
        else:
            observed = dimension_counts[dimension]
        missing = max(0, required - observed)
        if missing <= 0:
            continue
        severity: Literal["hard", "soft", "unknown"] = "hard" if _is_hard(dimension, spec) else "soft"
        if dimension in {"labs", "instruments", "organisms", "acquisition_modes", "fragmentation_methods", "modifications"} and unknown_counts.get(dimension, 0):
            severity = "unknown" if not _is_hard(dimension, spec) else "hard"
        gaps.append(
            PortfolioGap(
                dimension=dimension,
                required=required,
                observed=observed,
                missing=missing,
                severity=severity,
                recoverable=dimension != "file_extensions" or bool(spec.allowed_file_extensions),
                message=f"Need {required} {dimension.replace('_', ' ')}, observed {observed}.",
                evidence_refs=[f"distribution:{dimension}"],
            )
        )
    invalid_extensions = 0
    if spec.allowed_file_extensions:
        allowed = set(spec.allowed_file_extensions)
        invalid_extensions = sum(1 for row in rows if _file_extension(row) not in allowed)
        if invalid_extensions:
            gaps.append(
                PortfolioGap(
                    dimension="file_extensions",
                    required=0,
                    observed=invalid_extensions,
                    missing=invalid_extensions,
                    severity="hard",
                    recoverable=True,
                    message=f"{invalid_extensions} selected file(s) are outside the allowed extension set.",
                    evidence_refs=["distribution:file_extensions"],
                )
            )
    if spec.max_files_per_project is not None:
        oversized = {project: count for project, count in per_project.items() if count > spec.max_files_per_project}
        if oversized:
            gaps.append(
                PortfolioGap(
                    dimension="files_per_project",
                    required=spec.max_files_per_project,
                    observed=max(oversized.values()),
                    missing=max(oversized.values()) - spec.max_files_per_project,
                    severity="hard",
                    recoverable=True,
                    message=(
                        f"Some projects exceed the maximum of {spec.max_files_per_project} file(s): "
                        + ", ".join(f"{project}={count}" for project, count in sorted(oversized.items()))
                    ),
                    evidence_refs=["files_per_project"],
                )
            )
    hard_gap_count = sum(1 for gap in gaps if gap.severity == "hard")
    soft_gap_count = sum(1 for gap in gaps if gap.severity != "hard")
    return PortfolioCoverage(
        candidate_files=len(rows),
        selected_files=len(rows) if selected else 0,
        distinct_projects=len(projects),
        files_per_project=per_project,
        dimension_counts=dimension_counts,
        distributions=distributions,
        unknown_counts=unknown_counts,
        hard_gap_count=hard_gap_count,
        soft_gap_count=soft_gap_count,
        evidence_complete_files=sum(1 for row in rows if _has_complete_evidence(row)),
        evidence_unknown_files=sum(1 for row in rows if not _has_complete_evidence(row)),
        invalid_file_extensions=invalid_extensions,
        gaps=gaps,
        ready=hard_gap_count == 0,
    )


def select_portfolio_files(files: Iterable[DiscoveredFile], spec: PortfolioSpec) -> list[DiscoveredFile]:
    """Select a diverse, evidence-ranked subset without pretending it is final publication."""

    candidates = list(files)
    seen: set[str] = set()
    deduped: list[DiscoveredFile] = []
    for row in candidates:
        identity = _file_identifier(row).casefold()
        if identity in seen or row.validity_status == "exclude":
            continue
        seen.add(identity)
        deduped.append(row)
    if not deduped:
        return []
    if spec.allowed_file_extensions:
        allowed = set(spec.allowed_file_extensions)
        # Allowed extensions are eligibility constraints, never a best-effort
        # preference. Falling back to a disallowed extension would make a frozen
        # portfolio impossible to reproduce.
        deduped = [row for row in deduped if _file_extension(row) in allowed]
    if spec.detail_level == "strict" or "evidence" in spec.hard_dimensions:
        deduped = [row for row in deduped if _has_complete_evidence(row)]
    target_files = min(spec.target_files or len(deduped), len(deduped))
    target_projects = min(
        spec.target_projects or len({row.project_accession for row in deduped}),
        len({row.project_accession for row in deduped}),
    )
    max_per_project = spec.max_files_per_project or target_files
    selected: list[DiscoveredFile] = []
    selected_projects: set[str] = set()
    covered: dict[str, set[str]] = defaultdict(set)

    def score(row: DiscoveredFile) -> float:
        base = float(row.file_score or 0) + float(row.confidence or 0) * 10 + float(row.trust_score or 0) * 10
        novelty = 0.0
        for dimension in ("labs", "instruments", "organisms", "acquisition_modes", "fragmentation_methods", "modifications", "file_extensions"):
            novelty += 8.0 * sum(1 for value in _dimension_values(row, dimension) if value.casefold() not in covered[dimension])
        if spec.preferred_file_extensions and _file_extension(row) in set(spec.preferred_file_extensions):
            base += 12.0
        return base + novelty

    by_project: dict[str, list[DiscoveredFile]] = defaultdict(list)
    for row in deduped:
        by_project[row.project_accession].append(row)
    for rows in by_project.values():
        rows.sort(key=score, reverse=True)
    # Choose the project universe before filling files. A pure score ordering
    # can select eight near-identical Orbitrap projects and make a hard
    # instrument/lab/species requirement impossible even when qualifying
    # alternatives are present. Greedily seed the universe with hard-dimension
    # novelty, then use score as the tie-breaker.
    hard_requirements = {
        "labs": spec.min_distinct_labs,
        "instruments": spec.min_distinct_instruments,
        "organisms": spec.min_distinct_organisms,
        "acquisition_modes": spec.min_distinct_acquisition_modes,
        "fragmentation_methods": spec.min_distinct_fragmentation_methods,
        "acquisition_or_fragmentation": spec.min_distinct_acquisition_or_fragmentation,
        "modifications": spec.min_distinct_modifications,
    }
    hard_requirements = {
        dimension: required
        for dimension, required in hard_requirements.items()
        if required is not None and _is_hard(dimension, spec)
    }
    project_values = {
        project: {
            dimension: {
                value.casefold()
                for row in rows
                for value in _dimension_values(row, dimension)
            }
            for dimension in hard_requirements
        }
        for project, rows in by_project.items()
    }
    project_order: list[str] = []
    covered_project_values: dict[str, set[str]] = defaultdict(set)
    remaining_projects = set(by_project)
    while remaining_projects and len(project_order) < target_projects:
        def project_priority(project: str) -> tuple[float, str]:
            gain = sum(
                250.0 * len(
                    project_values[project][dimension] - covered_project_values[dimension]
                )
                for dimension in hard_requirements
            )
            capacity_bonus = 100.0 * min(len(by_project[project]), max_per_project)
            return score(by_project[project][0]) + gain + capacity_bonus, project

        chosen = max(remaining_projects, key=project_priority)
        project_order.append(chosen)
        remaining_projects.remove(chosen)
        for dimension in hard_requirements:
            covered_project_values[dimension].update(project_values[chosen][dimension])
    selected_rows_by_project: dict[str, int] = defaultdict(int)

    def add(row: DiscoveredFile) -> None:
        selected.append(row)
        selected_projects.add(row.project_accession)
        selected_rows_by_project[row.project_accession] += 1
        for dimension in covered:
            covered[dimension].update(value.casefold() for value in _dimension_values(row, dimension))

    remaining = [row for project in project_order for row in by_project[project]]
    minimum = spec.min_files_per_project or 0
    for project in project_order:
        for row in by_project[project][:minimum]:
            if len(selected) >= target_files or selected_rows_by_project[project] >= max_per_project:
                break
            add(row)

    # Seed any still-missing hard dimension before score-only filling. This is
    # deterministic and bounded by the requested file/project caps; if the
    # candidate pool cannot supply a value, the later audit reports the gap.
    def missing_hard_dimensions() -> list[str]:
        missing: list[str] = []
        for dimension, required in hard_requirements.items():
            observed = {
                value.casefold()
                for row in selected
                for value in _dimension_values(row, dimension)
            }
            if len(observed) < int(required):
                missing.append(dimension)
        return missing

    while remaining and len(selected) < target_files:
        missing_dimensions = missing_hard_dimensions()
        if not missing_dimensions:
            break
        ranked_gap: list[tuple[float, str, str, DiscoveredFile]] = []
        for row in remaining:
            if row in selected or row.project_accession not in project_order:
                continue
            if selected_rows_by_project[row.project_accession] >= max_per_project:
                continue
            gain = 0.0
            for dimension in missing_dimensions:
                values = {value.casefold() for value in _dimension_values(row, dimension)}
                observed = {
                    value.casefold()
                    for item in selected
                    for value in _dimension_values(item, dimension)
                }
                gain += 1000.0 * len(values - observed)
            if gain:
                ranked_gap.append(
                    (
                        gain + score(row),
                        row.project_accession,
                        row.file_name.casefold(),
                        row,
                    )
                )
        if not ranked_gap:
            break
        _, _, _, row = max(ranked_gap)
        remaining.remove(row)
        add(row)
    while remaining and len(selected) < target_files:
        remaining.sort(key=score, reverse=True)
        row = remaining.pop(0)
        if row in selected or row.project_accession not in project_order:
            continue
        if selected_rows_by_project[row.project_accession] >= max_per_project:
            continue
        add(row)
    return selected[:target_files]


def suggest_recovery_actions(coverage: PortfolioCoverage, spec: PortfolioSpec) -> list[RecoveryAction]:
    actions: list[RecoveryAction] = []
    safe_limit = max(1, spec.max_recovery_actions - (1 if coverage.hard_gap_count else 0))
    for index, gap in enumerate(coverage.gaps[:safe_limit], start=1):
        if gap.dimension in {
            "labs",
            "instruments",
            "organisms",
            "acquisition_modes",
            "fragmentation_methods",
            "acquisition_or_fragmentation",
            "modifications",
            "evidence",
        }:
            kind: RecoveryActionKind = "inspect_metadata" if coverage.unknown_counts.get(gap.dimension, 0) else "search_dimension"
            hints = {
                "labs": ["laboratory", "institution", "institute"],
                "instruments": ["instrument", "mass spectrometer"],
                "organisms": ["organism", "species", "taxon"],
                "acquisition_modes": ["DDA", "DIA", "acquisition"],
                "fragmentation_methods": ["HCD", "CID", "ETD", "fragmentation"],
                "acquisition_or_fragmentation": ["DDA", "DIA", "HCD", "CID", "ETD"],
                "modifications": ["PTM", "modified peptide", "modification"],
                "evidence": ["download URL", "file size", "source evidence", "SDRF"],
            }.get(gap.dimension, [])
        else:
            kind = "search_dimension"
            hints = list(spec.allowed_file_extensions or spec.preferred_file_extensions)
        actions.append(
            RecoveryAction(
                id=f"recovery-{index}-{gap.dimension}",
                kind=kind,
                dimension=gap.dimension,
                priority=100 if gap.severity == "hard" else 50,
                rationale=gap.message,
                expected_gain=f"Add at least {gap.missing} distinct {gap.dimension.replace('_', ' ')}.",
                query_hints=hints,
                # Searching/inspecting does not relax a scientific condition.
                # Approval is reserved for the explicit relaxation action below.
                requires_approval=False,
            )
        )
    if coverage.hard_gap_count:
        actions.append(
            RecoveryAction(
                id="relax-hard-requirement",
                kind="relax_hard_requirement",
                priority=1,
                rationale="A hard portfolio gap remains after evidence-backed recovery options.",
                expected_gain="Proceed only after the user explicitly approves which hard requirement may be relaxed.",
                requires_approval=spec.approval_required_for_hard_relaxation,
            )
        )
    if not coverage.gaps:
        actions.append(
            RecoveryAction(
                id="select-best-effort",
                kind="select_best_effort",
                priority=10,
                rationale="All explicit hard portfolio requirements are currently covered.",
                expected_gain="Freeze the evidence-backed portfolio candidate.",
            )
        )
    elif coverage.hard_gap_count == 0:
        actions.append(
            RecoveryAction(
                id="accept-soft-gaps",
                kind="select_best_effort",
                priority=20,
                rationale="Only soft or unresolved preferences remain; they will be visible in the limitations.",
                expected_gain="Deliver the best covered portfolio without silently changing requirements.",
            )
        )
    return actions[: spec.max_recovery_actions]


def build_assumption_ledger(request: DatasetRequest, spec: PortfolioSpec) -> list[AssumptionLedgerEntry]:
    explicit = bool(request.portfolio_spec)
    entries = [
        AssumptionLedgerEntry(
            id="portfolio-detail-level",
            label="Portfolio detail level",
            value=spec.detail_level,
            source="explicit" if explicit else "inferred",
            status="accepted" if explicit else "needs_confirmation",
            impact="hard" if spec.detail_level == "strict" else "informational",
            rationale="Detailed diversity quotas are enforced only when the user asks for them or accepts the benchmark profile.",
        ),
        AssumptionLedgerEntry(
            id="unknown-metadata",
            label="Unknown metadata",
            value="never counted as a distinct category",
            source="inferred",
            status="accepted",
            impact="hard",
            rationale="Missing laboratory, instrument, organism, or acquisition evidence remains unknown and is never invented.",
        ),
    ]
    return entries


def initialize_portfolio_state(request: DatasetRequest) -> PortfolioState:
    spec = PortfolioSpec.from_request(request)
    return PortfolioState(
        spec=spec,
        assumptions=build_assumption_ledger(request, spec),
        status="planning",
    )


def update_portfolio_state(
    state: PortfolioState,
    files: Iterable[DiscoveredFile],
    *,
    selected_file_identifiers: list[str] | None = None,
) -> PortfolioState:
    rows = list(files)
    selected_ids = selected_file_identifiers or []
    if selected_ids:
        wanted = {value.casefold() for value in selected_ids}
        selected_rows = [
            row for row in rows
            if f"{row.repository}:{row.project_accession}:{row.file_accession_or_path or row.file_name}".casefold() in wanted
            or _text(row.file_accession_or_path or row.file_name).casefold() in wanted
        ]
    else:
        selected_rows = select_portfolio_files(rows, state.spec)
    coverage = assess_portfolio_coverage(selected_rows, state.spec)
    coverage = coverage.model_copy(update={"candidate_files": len(rows)})
    actions = suggest_recovery_actions(coverage, state.spec)
    status: PortfolioStateStatus = "ready" if coverage.ready else "needs_recovery"
    if not selected_rows and rows:
        status = "blocked"
    return state.model_copy(
        update={
            "status": status,
            "coverage": coverage,
            "gaps": coverage.gaps,
            "recovery_actions": actions,
            "selected_file_identifiers": [
                f"{row.repository}:{row.project_accession}:{row.file_accession_or_path or row.file_name}"
                for row in selected_rows
            ],
            "selected_project_accessions": sorted({row.project_accession for row in selected_rows}),
            "updated_at": datetime.now(UTC).isoformat(),
        }
    )


def infer_portfolio_spec(value: Any, explicit: Any = None) -> dict[str, Any]:
    """Extract only unmistakable numeric portfolio requirements from a prompt.

    This is intentionally conservative.  An LLM can propose richer assumptions
    later, but the parser must not turn vague language into a hard requirement.
    """

    payload = dict(explicit) if isinstance(explicit, Mapping) else {}
    text = _text(value)
    range_match = re.search(
        r"(\d+)\s*[-–—到]\s*(\d+)\s*(?:files?|个文件)\s*(?:per|每个|/)\s*(?:project|项目)",
        text,
        flags=re.IGNORECASE,
    )
    search_text = text
    # Current Chinese prompts should be parsed directly; older clients may
    # still send the mojibake forms handled by the legacy expressions below.
    unicode_range_match = re.search(
        r"(\d+)\s*[-–—]\s*(\d+)\s*(?:个)?\s*文件\s*(?:每个|/)\s*项目",
        text,
        flags=re.IGNORECASE,
    )
    if unicode_range_match:
        payload.setdefault("min_files_per_project", int(unicode_range_match.group(1)))
        payload.setdefault("max_files_per_project", int(unicode_range_match.group(2)))
        search_text = text[: unicode_range_match.start()] + text[unicode_range_match.end() :]
    if unicode_range_match is None:
        unicode_range_match = re.search(
            r"每个项目[^。；;，,]{0,12}?(\d+)\s*[-–—]\s*(\d+)\s*(?:个)?\s*文件",
            text,
            flags=re.IGNORECASE,
        )
        if unicode_range_match:
            payload.setdefault("min_files_per_project", int(unicode_range_match.group(1)))
            payload.setdefault("max_files_per_project", int(unicode_range_match.group(2)))
            search_text = text[: unicode_range_match.start()] + text[unicode_range_match.end() :]
    if range_match:
        payload.setdefault("min_files_per_project", int(range_match.group(1)))
        payload.setdefault("max_files_per_project", int(range_match.group(2)))
        search_text = text[: range_match.start()] + text[range_match.end() :]
    patterns: dict[str, tuple[str, ...]] = {
        "target_projects": (r"(?:at\s+least\s+|至少\s*|约\s*)?(\d+)\s*(?:个(?:左右)?\s*)?(?:PRIDE\s*)?(?:projects?|项目)",),
        "target_files": (r"(?:at\s+least\s+|至少\s*|约\s*)?(\d+)\s*(?:个(?:左右)?\s*)?(?:files?|文件)",),
        "min_distinct_labs": (r"(?:at\s+least\s+|至少\s*)?(\d+)\s*(?:个|种)?\s*(?:labs?|laborator(?:y|ies)|实验室)",),
        "min_distinct_instruments": (r"(?:at\s+least\s+|至少\s*)?(\d+)\s*(?:个|种)?\s*(?:instruments?|仪器)",),
        "min_distinct_organisms": (r"(?:at\s+least\s+|至少\s*)?(\d+)\s*(?:个|种)?\s*(?:organisms?|species|物种)",),
        "min_distinct_acquisition_modes": (r"(?:at\s+least\s+|至少\s*)?(\d+)\s*(?:种\s*)?(?:acquisition\s+conditions?|采集条件)",),
        "min_distinct_fragmentation_methods": (r"(?:at\s+least\s+|至少\s*|约\s*)?(\d+)\s*(?:种\s*)?(?:fragmentation\s+(?:conditions?|methods?)|碎裂条件)",),
        "min_distinct_acquisition_or_fragmentation": (r"(?:at\s+least\s+|至少\s*|约\s*)?(\d+)\s*(?:种\s*)?(?:acquisition\s*/\s*fragmentation\s+conditions?|采集\s*(?:或|/|或/)\s*碎裂条件)",),
        "min_distinct_modifications": (r"(?:at\s+least\s+|至少\s*|约\s*)?(\d+)\s*(?:种\s*)?(?:modifications?|PTMs?|修饰)",),
    }
    for field, expressions in patterns.items():
        if field in payload:
            continue
        for expression in expressions:
            match = re.search(expression, search_text, flags=re.IGNORECASE)
            if match:
                payload[field] = int(match.group(1))
                break
    unicode_patterns: dict[str, str] = {
        "target_projects": r"(?:约|大约|大概|around|about|approximately)?\s*(\d+)\s*(?:个|项)?\s*(?:PRIDE\s*)?(?:项目|projects?)",
        "target_files": r"(?:约|大约|大概|around|about|approximately)?\s*(\d+)\s*(?:个|项)?\s*(?:文件|files?)",
        "min_distinct_labs": r"(?:至少|不少于|at\s+least)\s*(?:覆盖\s*)?(\d+)\s*(?:个|种)?\s*(?:不同的?\s*)?(?:实验室|机构|labs?|laborator(?:y|ies))",
        "min_distinct_instruments": r"(?:至少|不少于|at\s+least)\s*(?:覆盖\s*)?(\d+)\s*(?:个|种)?\s*(?:不同的?\s*)?(?:仪器家族|仪器|instruments?)",
        "min_distinct_organisms": r"(?:至少|不少于|at\s+least)\s*(?:覆盖\s*)?(\d+)\s*(?:个|种)?\s*(?:不同的?\s*)?(?:物种|species|organisms?)",
        "min_distinct_acquisition_or_fragmentation": r"(?:至少|不少于|at\s+least)\s*(?:覆盖\s*)?(\d+)\s*(?:种|个)?\s*(?:不同的?\s*)?(?:采集或碎裂条件|采集/碎裂条件|acquisition\s*(?:or|/)\s*fragmentation\s+conditions?)",
    }
    for field, expression in unicode_patterns.items():
        if field in payload:
            continue
        match = re.search(expression, search_text, flags=re.IGNORECASE)
        if match:
            payload[field] = int(match.group(1))

    per_project = re.search(
        r"(\d+)\s*(?:files?|个文件)\s*(?:per|每个|/)\s*(?:project|项目)",
        search_text,
        flags=re.IGNORECASE,
    )
    if per_project and "min_files_per_project" not in payload:
        payload["min_files_per_project"] = int(per_project.group(1))
    extensions = re.findall(r"\.(?:raw(?:\.zip)?|mzml(?:\.gz)?|mzxml(?:\.gz)?)\b", text, flags=re.IGNORECASE)
    if extensions and "preferred_file_extensions" not in payload:
        payload["preferred_file_extensions"] = _normalize_extensions(extensions)
    parsed_keys = (
        "target_projects", "target_files", "min_distinct_labs", "min_distinct_instruments",
        "min_distinct_organisms", "min_distinct_acquisition_modes", "min_distinct_fragmentation_methods",
        "min_distinct_acquisition_or_fragmentation", "min_distinct_modifications",
        "min_files_per_project", "max_files_per_project", "allowed_file_extensions", "preferred_file_extensions",
    )
    if any(key in payload for key in parsed_keys):
        payload.setdefault("detail_level", "strict")
        hard_dimensions = list(payload.get("hard_dimensions") or [])
        hard_dimensions.extend(
            [
                "projects", "files", "files_per_project", "labs", "instruments", "organisms",
                "acquisition_modes", "fragmentation_methods", "acquisition_or_fragmentation",
                "modifications", "evidence",
            ]
        )
        # “Prefer .raw/.mzML” is a ranking preference, not an exclusion rule.
        # Only an explicit allowed_file_extensions contract turns the extension
        # dimension into a hard gate.
        if payload.get("allowed_file_extensions"):
            hard_dimensions.append("file_extensions")
        # Approximate counts guide planning but are not hard scientific gates.
        if re.search(
            r"(?:约\s*\d+\s*(?:个)?(?:项目|文件)|\d+\s*个左右\s*(?:项目|文件)|about\s+\d+\s+(?:projects?|files?)|around\s+\d+\s+(?:projects?|files?))",
            text,
            flags=re.IGNORECASE,
        ):
            for dimension in ("projects", "files"):
                while dimension in hard_dimensions:
                    hard_dimensions.remove(dimension)
        if re.search(r"(?:约|大约|大概|around|about|approximately)", text, flags=re.IGNORECASE):
            for dimension in ("projects", "files"):
                while dimension in hard_dimensions:
                    hard_dimensions.remove(dimension)
        payload["hard_dimensions"] = list(dict.fromkeys(hard_dimensions))
    return payload
