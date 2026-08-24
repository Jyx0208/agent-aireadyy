from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol

from pydantic import Field, model_validator

from agent.discovery.memory import DiscoveryMemory
from agent.discovery.models import DatasetManifest, DatasetRequest
from agent.discovery.pride_discovery import InspectionOutcomeCategory, discover_pride_dataset
from agent.discovery.query_builder import prepare_pride_search_queries
from agent.discovery.query_portfolio import (
    MAX_REPOSITORY_QUERY_DEPTH,
    classify_atomic_seed_budget_role,
    QueryPortfolio,
    build_query_portfolio_units,
    expand_query_unit_seeds,
    is_filter_budget_role,
    resolve_budget_role,
)
from agent.discovery.candidate_evidence_matrix import build_provisional_cem
from agent.discovery.scoring import score_project
from agent.models import JsonModel
from agent.pride.client import PrideClient, search_projects_paginated_with_state
from agent.utils import write_json


_INTENT_STOPWORDS = {
    "and",
    "assets",
    "about",
    "acquired",
    "against",
    "candidate",
    "candidates",
    "class",
    "data",
    "dataset",
    "datasets",
    "develop",
    "development",
    "evidence",
    "explain",
    "find",
    "file",
    "files",
    "from",
    # "human" is domain-critical (species); never stopword it (WP-A).
    "inspect",
    "judgment",
    "level",
    "matched",
    "model",
    "need",
    "only",
    "project",
    "projects",
    "proteome",
    "proteomic",
    "proteomics",
    "sample",
    "samples",
    "retain",
    "study",
    "the",
    "to",
    "using",
    "verify",
    "with",
}


def _merge_parallel_inspection_manifests(
    request: DatasetRequest,
    manifests: list[DatasetManifest],
    *,
    workers: int,
) -> DatasetManifest:
    """Merge independently inspected projects while retaining terminal outcomes."""
    projects: dict[tuple[str, str], Any] = {}
    files: dict[tuple[str, str, str], Any] = {}
    outcomes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    summary_counts: Counter[str] = Counter()
    distribution_keys = (
        "validity_status_counts",
        "validity_reason_counts",
        "evidence_level_distribution",
        "sdrf_match_status_distribution",
        "evidence_warning_counts",
        "species_distribution",
        "instrument_family_distribution",
        "fragmentation_method_distribution",
        "lc_gradient_distribution",
        "unknown_counts",
    )
    distributions = {key: Counter() for key in distribution_keys}
    for manifest in manifests:
        for project in manifest.projects:
            projects.setdefault(
                (str(project.repository), project.project_accession.upper()),
                project,
            )
        for file in manifest.files:
            files.setdefault(
                (
                    str(file.repository),
                    file.project_accession.upper(),
                    str(file.file_accession_or_path),
                ),
                file,
            )
        outcomes.extend(
            item
            for item in (manifest.summary.get("inspection_outcomes") or [])
            if isinstance(item, dict)
        )
        failures.extend(
            item
            for item in (manifest.summary.get("failures") or [])
            if isinstance(item, dict)
        )
        for key in ("excluded_projects", "excluded_files", "eligible_projects_seen"):
            summary_counts[key] += int(manifest.summary.get(key) or 0)
        for key in distribution_keys:
            values = manifest.summary.get(key)
            if isinstance(values, dict):
                distributions[key].update(
                    {
                        str(name): int(count or 0)
                        for name, count in values.items()
                    }
                )

    selected_files = list(files.values())[: request.max_files]
    selected_project_keys = {
        (str(file.repository), file.project_accession.upper())
        for file in selected_files
    }
    selected_projects = [
        project
        for key, project in projects.items()
        if key in selected_project_keys
    ][: request.max_projects]
    outcome_counts = dict(
        sorted(Counter(str(item.get("category") or "unknown") for item in outcomes).items())
    )
    base_summary = dict(manifests[0].summary) if manifests else {}
    summary = {
        **base_summary,
        "candidate_projects_seen": len(manifests),
        "eligible_projects_seen": summary_counts["eligible_projects_seen"],
        "excluded_projects": summary_counts["excluded_projects"],
        "excluded_files": summary_counts["excluded_files"],
        "inspection_outcomes": outcomes,
        "inspection_outcome_counts": outcome_counts,
        "selected_projects": len(selected_projects),
        "selected_files": len(selected_files),
        "failures": failures,
        **{
            key: dict(sorted(counts.items()))
            for key, counts in distributions.items()
        },
        "inspection_parallelism": {
            "mode": "bounded_parallel",
            "workers": workers,
        },
    }
    return DatasetManifest(
        request=request,
        projects=selected_projects,
        files=selected_files,
        summary=summary,
    )

_EXACT_PRIDE_ACCESSION_RE = re.compile(r"PXD\d+", flags=re.IGNORECASE)


class RepositoryQuery(JsonModel):
    query: str = Field(min_length=1, max_length=240)
    depth: int = Field(default=20, ge=1, le=MAX_REPOSITORY_QUERY_DEPTH)
    intent_dimension: str = Field(default="general", min_length=1, max_length=120)
    expected_gain: str = Field(default="", max_length=500)
    # PTS: primary_theme | secondary_theme | filter_only | exact_accession | general | unknown
    # Default general = legacy agent query (searchable); filter_only never deep-pages.
    budget_role: str = Field(default="general", min_length=1, max_length=64)


class CandidateSearchAction(JsonModel):
    queries: list[RepositoryQuery] = Field(min_length=1, max_length=40)
    candidate_limit: int = Field(default=50, ge=1, le=1000)
    preview_offset: int = Field(default=0, ge=0)
    rationale: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_queries(self) -> "CandidateSearchAction":
        keys = [" ".join(item.query.casefold().split()) for item in self.queries]
        if len(keys) != len(set(keys)):
            raise ValueError("candidate search queries must be unique")
        return self


class QueryYield(JsonModel):
    query: str
    executed_query: str
    intent_dimension: str
    requested_depth: int
    raw_result_count: int = Field(ge=0)
    new_candidate_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    top_accessions: list[str] = Field(default_factory=list)
    error: str | None = None
    skipped_reason: str | None = None
    seeds_planned: list[str] = Field(default_factory=list)
    portfolio_unit_status: str | None = None


class RepositorySearchFailure(JsonModel):
    query: str
    executed_query: str
    intent_dimension: str
    requested_depth: int
    error_type: str
    message: str


class CandidatePreview(JsonModel):
    project_accession: str
    title: str = ""
    description_excerpt: str = ""
    project_score: float = 0.0
    confidence: float = 0.0
    needs_review: bool = False
    excluded: bool = False
    species: list[str] = Field(default_factory=list)
    acquisition_mode: str | None = None
    matched_intent_terms: list[str] = Field(default_factory=list)
    query_hits: list[str] = Field(default_factory=list)


class CandidateSearchObservation(JsonModel):
    status: str = "completed"
    search_id: str
    query_yields: list[QueryYield] = Field(default_factory=list)
    raw_result_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    new_candidate_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    duplicate_rate: float = Field(ge=0.0, le=1.0)
    previews: list[CandidatePreview] = Field(default_factory=list)
    preview_offset: int = Field(default=0, ge=0)
    next_preview_offset: int | None = Field(default=None, ge=0)
    has_more_candidates: bool = False
    intent_terms: list[str] = Field(default_factory=list)
    covered_intent_terms: list[str] = Field(default_factory=list)
    unresolved_intent_terms: list[str] = Field(default_factory=list)
    # Diagnostic corpus OR-coverage only (WP-A rename). semantic_coverage kept as alias.
    corpus_term_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    semantic_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    high_relevance_candidate_count: int = Field(default=0, ge=0)
    new_high_relevance_candidate_count: int = Field(default=0, ge=0)
    corpus_term_coverage_gain: float = Field(default=0.0, ge=0.0, le=1.0)
    semantic_coverage_gain: float = Field(default=0.0, ge=0.0, le=1.0)
    hard_constraint_evidence_gap: float = Field(default=1.0, ge=0.0, le=1.0)
    n_hard_conjunction_pass: int = Field(default=0, ge=0)
    n_hard_pass_inspected: int = Field(default=0, ge=0)
    unknown_hard_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    candidate_level_conjunction_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    query_portfolio: dict[str, Any] = Field(default_factory=dict)
    cem_summary: dict[str, Any] = Field(default_factory=dict)
    recommended_action: str = "review_candidate_previews"
    failures: list[str] = Field(default_factory=list)
    operational_failures: list[RepositorySearchFailure] = Field(default_factory=list)
    stop_reason: str | None = None
    rationale: str = ""


class CandidateInspectionAction(JsonModel):
    search_id: str = Field(min_length=1)
    # Keep inspection batches small so tool results fit model context (context-overflow fail).
    # Prefer multiple inspect rounds over one giant batch.
    accessions: list[str] = Field(min_length=1, max_length=40)
    rationale: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_accessions(self) -> "CandidateInspectionAction":
        normalized = [item.strip().upper() for item in self.accessions if item.strip()]
        if not normalized:
            raise ValueError("candidate inspection requires accessions")
        if len(normalized) != len(set(normalized)):
            raise ValueError("candidate inspection accessions must be unique")
        self.accessions = normalized
        return self


class CandidateInspectionOutcome(JsonModel):
    project_accession: str
    category: InspectionOutcomeCategory
    stage: str | None = None
    reason: str | None = None
    error: str | None = None
    raw_file_count: int = Field(default=0, ge=0)
    usable_file_count: int = Field(default=0, ge=0)
    excluded_file_count: int = Field(default=0, ge=0)
    file_role_counts: dict[str, int] = Field(default_factory=dict)
    filter_reason_counts: dict[str, int] = Field(default_factory=dict)


class CandidateInspectionResult(JsonModel):
    search_id: str
    requested_accessions: list[str] = Field(default_factory=list)
    inspected_accessions: list[str]
    eligible_accessions: list[str] = Field(default_factory=list)
    failed_accessions: list[str] = Field(default_factory=list)
    excluded_accessions: list[str] = Field(default_factory=list)
    no_usable_files_accessions: list[str] = Field(default_factory=list)
    inspection_outcomes: list[CandidateInspectionOutcome] = Field(default_factory=list)
    manifest: DatasetManifest
    usable_files: int = Field(ge=0)
    valid_files: int = Field(ge=0)
    rationale: str


class DiscoverySearchEnvironment(Protocol):
    def search(self, action: CandidateSearchAction) -> CandidateSearchObservation: ...

    def inspect(self, action: CandidateInspectionAction) -> CandidateInspectionResult: ...

    def is_query_exhausted(self, query: str) -> bool: ...

    def reviewable_accessions(self, *, limit: int | None = None) -> list[str]: ...

    def close(self) -> None: ...


class PrideDiscoverySearchEnvironment:
    def __init__(
        self,
        *,
        request: DatasetRequest,
        prompt: str,
        state_path: str | Path,
        client: PrideClient | None = None,
        memory: DiscoveryMemory | None = None,
        report: Callable[[str], None] | None = None,
        search_event: Callable[[str, dict[str, Any]], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> None:
        self.request = request
        self.prompt = " ".join(str(prompt or "").split()).strip()
        self.state_path = Path(state_path)
        self.client = client or PrideClient()
        self._owns_client = client is None
        self.memory = memory
        self.report = report
        self.search_event = search_event
        self.should_cancel = should_cancel
        self.intent_terms = _extract_intent_terms(self.prompt, request)
        self._records: dict[str, dict[str, Any]] = {}
        self._query_hits: dict[str, set[str]] = defaultdict(set)
        self._pinned_accessions: set[str] = set()
        self._seed_depths: dict[str, int] = {}
        # Absolute result offsets let continuous discovery resume inside a
        # repository page without dropping the unconsumed tail.
        self._seed_offsets: dict[str, int] = {}
        self._exhausted_seeds: set[str] = set()
        self._search_counter = 0
        self._latest_search_id: str | None = None
        self._load_state()

    def _emit_search_event(self, event_type: str, **payload: Any) -> None:
        if self.search_event is not None:
            self.search_event(event_type, payload)

    @property
    def candidate_accessions(self) -> list[str]:
        return list(self._records)

    @property
    def latest_search_id(self) -> str | None:
        return self._latest_search_id

    def high_relevance_accessions(self, *, limit: int | None = None) -> list[str]:
        """Return ranked non-excluded high-relevance accessions for maximize inspection.

        WP-A: if none meet the relevance floor, return [] — never fall back to all
        non-excluded candidates (that inflated inspection minima and false ready).
        """
        ranked = self._ranked_records()
        floor = min(2, max(1, len(self.intent_terms)))
        accessions = [
            accession
            for accession, _record, preview in ranked
            if not preview.excluded and len(preview.matched_intent_terms) >= floor
        ]
        if limit is not None:
            return accessions[: max(0, int(limit))]
        return accessions

    def reviewable_accessions(self, *, limit: int | None = None) -> list[str]:
        """Return every ranked candidate that did not fail metadata hard filters.

        High-relevance scoring remains useful for ordering, but exhaustive
        discovery must not silently turn that heuristic into a candidate-pool
        cap.  Project inspection is the authority for the final conclusion.
        """

        accessions = [
            accession
            for accession, _record, preview in self._ranked_records()
            if not preview.excluded
        ]
        if limit is not None:
            return accessions[: max(0, int(limit))]
        return accessions

    def is_query_exhausted(self, query: str) -> bool:
        """Report whether every repository seed for one confirmed phrase ended."""

        # Use the exact same phrase-preserving planner as repository execution.
        # The legacy query builder atomizes phrases such as "HLA ligandome",
        # which would make the exhaustion probe check different seed keys from
        # the ones persisted by ``build_query_portfolio_units``.
        prepared = expand_query_unit_seeds(query) or [query]
        seed_keys = {
            " ".join(str(seed).casefold().split())
            for seed in prepared
            if str(seed).strip()
        }
        return bool(seed_keys) and seed_keys.issubset(self._exhausted_seeds)

    def search(self, action: CandidateSearchAction) -> CandidateSearchObservation:
        return self._search(action, request_budget=None)

    def search_with_request_budget(
        self,
        action: CandidateSearchAction,
        *,
        request_budget: int,
    ) -> CandidateSearchObservation:
        """Search while sharing a bounded page budget fairly across new seeds."""
        return self._search(action, request_budget=max(0, int(request_budget)))

    def _search(
        self,
        action: CandidateSearchAction,
        *,
        request_budget: int | None,
    ) -> CandidateSearchObservation:
        self._check_cancel()
        continuous_search = (
            bool(self.request.continuous_discovery)
            or bool(self.request.harvest_all_qualified)
            or (
                self.request.quota_flexibility == "fixed"
                and bool(self.request.query_terms)
            )
        )
        confirmed_themes = [
            (
                " ".join(str(term).casefold().split()),
                " ".join(str(term).split()),
            )
            for term in self.request.query_terms or []
            if str(term).strip()
        ]
        confirmed_theme_order = [key for key, _display in confirmed_themes]
        active_confirmed_theme = next(
            (
                term
                for term in confirmed_theme_order
                if term not in self._exhausted_seeds
            ),
            None,
        )
        submitted_theme_queries = [
            " ".join(query.query.casefold().split())
            for query in action.queries
            if not _EXACT_PRIDE_ACCESSION_RE.fullmatch(query.query.strip())
        ]
        submitted_queries_are_already_exhausted = bool(submitted_theme_queries) and all(
            query in self._exhausted_seeds for query in submitted_theme_queries
        )
        if (
            continuous_search
            and self.request.quota_flexibility in {"fixed", "open_ended"}
            and active_confirmed_theme is not None
            and bool(submitted_theme_queries)
            and not submitted_queries_are_already_exhausted
            and submitted_theme_queries != [active_confirmed_theme]
        ):
            active_theme_display = next(
                display
                for key, display in confirmed_themes
                if key == active_confirmed_theme
            )
            requested_queries = [item.query for item in action.queries]
            theme_depth = max(
                200,
                *(
                    item.depth
                    for item in action.queries
                    if not _EXACT_PRIDE_ACCESSION_RE.fullmatch(item.query.strip())
                ),
            )
            active_rank = confirmed_theme_order.index(active_confirmed_theme)
            exact_accessions = [
                item
                for item in action.queries
                if _EXACT_PRIDE_ACCESSION_RE.fullmatch(item.query.strip())
            ]
            action = action.model_copy(
                update={
                    "queries": [
                        RepositoryQuery(
                            query=active_theme_display,
                            depth=min(MAX_REPOSITORY_QUERY_DEPTH, theme_depth),
                            intent_dimension="confirmed theme order",
                            expected_gain=(
                                "Continue the active confirmed theme from its saved "
                                "repository offset until exhaustion."
                            ),
                            budget_role=(
                                "primary_theme" if active_rank == 0 else "theme_synonym"
                            ),
                        ),
                        *exact_accessions,
                    ],
                    "rationale": (
                        "Deterministic scheduler corrected an out-of-order Agent "
                        f"request to the active confirmed theme {active_theme_display}. "
                        f"{action.rationale}"
                    )[:2000],
                }
            )
            self._emit_search_event(
                "repository_theme_order_corrected",
                requested_queries=requested_queries,
                executed_query=active_theme_display,
                requested_depth=theme_depth,
                reason="active_confirmed_theme_not_exhausted",
            )
            if self.report is not None:
                self.report(
                    "Corrected out-of-order Agent search "
                    f"({'; '.join(requested_queries)}) to active confirmed theme "
                    f"{active_theme_display}."
                )
        if len(self.intent_terms) <= 1:
            translated_terms = _extract_candidate_terms(
                " ".join(
                    value
                    for query in action.queries
                    for value in (query.intent_dimension, query.query)
                )
            )
            self.intent_terms = list(
                dict.fromkeys([*self.intent_terms, *translated_terms])
            )[:24]
        before = set(self._records)
        raw_total = 0
        duplicate_total = 0
        failures: list[str] = []
        operational_failures: list[RepositorySearchFailure] = []
        successful_repository_attempts = 0
        query_yields: list[QueryYield] = []
        portfolio_units = build_query_portfolio_units(action.queries, max_seeds_per_query=8)
        # Tokens that must never consume deep-search page budget (species / acq / labeling).
        _filter_seed_tokens: set[str] = set()
        try:
            from agent.discovery.ontology import species_aliases_for_query  # type: ignore
        except Exception:
            species_aliases_for_query = None  # type: ignore
        try:
            from agent.discovery.query_builder import species_aliases_for_query as _sa  # type: ignore
            species_aliases_for_query = _sa
        except Exception:
            pass
        for sp in list(getattr(self.request, "species", None) or []):
            _filter_seed_tokens.add(str(sp).casefold())
            if callable(species_aliases_for_query):
                try:
                    for alias in species_aliases_for_query(str(sp)):
                        _filter_seed_tokens.add(str(alias).casefold())
                except Exception:
                    pass
        acq = str(getattr(self.request, "acquisition_mode", "") or "").casefold()
        if acq and acq not in {"", "unknown", "any"}:
            _filter_seed_tokens.add(acq)
            if acq == "dda":
                _filter_seed_tokens.update({"dda", "data dependent", "data-dependent"})
            if acq == "dia":
                _filter_seed_tokens.update({"dia", "data independent", "data-independent"})
        lab = str(getattr(self.request, "labeling_strategy", "") or "").casefold()
        if lab and lab not in {"", "unknown", "any"}:
            _filter_seed_tokens.add(lab.replace("_", "-"))
            _filter_seed_tokens.add(lab.replace("_", " "))
        confirmed_theme_keys = {
            " ".join(str(term).casefold().split()): index
            for index, term in enumerate(self.request.query_terms or [])
            if str(term).strip()
        }
        # Align unit roles with action queries (portfolio already resolves budget_role).
        for unit, query_spec in zip(portfolio_units, action.queries, strict=False):
            resolved = resolve_budget_role(
                budget_role=getattr(query_spec, "budget_role", None),
                intent_dimension=query_spec.intent_dimension,
                query_text=query_spec.query,
            )
            confirmed_rank = confirmed_theme_keys.get(
                " ".join(str(query_spec.query).casefold().split())
            )
            if confirmed_rank is not None and resolved in {"general", "unknown"}:
                resolved = "primary_theme" if confirmed_rank == 0 else "theme_synonym"
            unit.budget_role = resolved
            # Keep query_spec in sync for downstream yield reporting.
            try:
                query_spec.budget_role = resolved
            except Exception:
                pass
        pending_seed_keys: set[str] = set()
        primary_pending = 0
        for unit in portfolio_units:
            if is_filter_budget_role(unit.budget_role):
                continue  # filters are never deep-search page peers
            for seed in unit.seeds_planned:
                pending_key = " ".join(seed.casefold().split())
                is_pending = (
                    pending_key not in self._exhausted_seeds
                    if continuous_search
                    else self._seed_depths.get(pending_key, 0) < unit.depth
                )
                if is_pending:
                    if pending_key not in pending_seed_keys:
                        pending_seed_keys.add(pending_key)
                        if unit.budget_role == "primary_theme":
                            primary_pending += 1
        pending_seed_count = len(pending_seed_keys)
        page_requests_remaining = request_budget
        for unit, query_spec in zip(portfolio_units, action.queries, strict=False):
            self._check_cancel()
            unit_role = resolve_budget_role(
                budget_role=unit.budget_role or getattr(query_spec, "budget_role", None),
                intent_dimension=query_spec.intent_dimension,
                query_text=query_spec.query,
            )
            prepared = list(unit.seeds_planned) or prepare_pride_search_queries([query_spec.query])
            if not prepared:
                prepared = [query_spec.query]
            unit_executed: list[str] = []
            unit_new = 0
            unit_dup = 0
            unit_raw = 0
            unit_errors: list[str] = []
            unit_skipped: list[str] = []
            unit_top: list[str] = []
            if is_filter_budget_role(unit_role):
                self._emit_search_event(
                    "repository_query_skipped",
                    query=query_spec.query,
                    executed_query=query_spec.query,
                    depth=query_spec.depth,
                    role=unit_role,
                    reason="filter_only_not_deep_searched",
                )
                unit_skipped.append("filter_only_not_deep_searched")
                query_yields.append(
                    QueryYield(
                        query=query_spec.query,
                        executed_query=query_spec.query,
                        intent_dimension=query_spec.intent_dimension,
                        requested_depth=query_spec.depth,
                        raw_result_count=0,
                        new_candidate_count=0,
                        duplicate_count=0,
                        skipped_reason="filter_only_not_deep_searched",
                        seeds_planned=prepared,
                        portfolio_unit_status="skipped_filter_role",
                    )
                )
                unit.status = "skipped_budget"
                unit.not_executed_reason = "filter_only_not_deep_searched"
                continue
            for executed_query in prepared:
                self._check_cancel()
                seed_key = " ".join(executed_query.casefold().split())
                seed_role = classify_atomic_seed_budget_role(
                    executed_query,
                    parent_role=unit_role,
                    filter_tokens=_filter_seed_tokens,
                )
                if is_filter_budget_role(seed_role):
                    self._emit_search_event(
                        "repository_query_skipped",
                        query=query_spec.query,
                        executed_query=executed_query,
                        depth=query_spec.depth,
                        role=seed_role,
                        reason="atomic_filter_seed_not_deep_searched",
                    )
                    unit_skipped.append("atomic_filter_seed_not_deep_searched")
                    query_yields.append(
                        QueryYield(
                            query=query_spec.query,
                            executed_query=executed_query,
                            intent_dimension=query_spec.intent_dimension,
                            requested_depth=query_spec.depth,
                            raw_result_count=0,
                            new_candidate_count=0,
                            duplicate_count=0,
                            skipped_reason="atomic_filter_seed_not_deep_searched",
                            seeds_planned=prepared,
                            portfolio_unit_status="skipped_filter_role",
                        )
                    )
                    continue
                exact_accession = (
                    executed_query.strip().upper()
                    if _EXACT_PRIDE_ACCESSION_RE.fullmatch(executed_query.strip())
                    else None
                )
                previous_depth = self._seed_depths.get(seed_key, 0)
                if continuous_search and seed_key in self._exhausted_seeds:
                    self._emit_search_event(
                        "repository_query_skipped",
                        query=query_spec.query,
                        executed_query=executed_query,
                        depth=query_spec.depth,
                        role=unit_role,
                        reason="repository_seed_exhausted",
                    )
                    unit_skipped.append("repository_seed_exhausted")
                    query_yields.append(
                        QueryYield(
                            query=query_spec.query,
                            executed_query=executed_query,
                            intent_dimension=query_spec.intent_dimension,
                            requested_depth=query_spec.depth,
                            raw_result_count=0,
                            new_candidate_count=0,
                            duplicate_count=0,
                            skipped_reason="repository_seed_exhausted",
                            seeds_planned=prepared,
                            portfolio_unit_status="skipped_depth",
                        )
                    )
                    continue
                if not continuous_search and previous_depth >= query_spec.depth:
                    self._emit_search_event(
                        "repository_query_skipped",
                        query=query_spec.query,
                        executed_query=executed_query,
                        depth=query_spec.depth,
                        role=unit_role,
                        reason="repository_seed_already_searched_at_equal_or_greater_depth",
                    )
                    unit_skipped.append("repository_seed_already_searched_at_equal_or_greater_depth")
                    query_yields.append(
                        QueryYield(
                            query=query_spec.query,
                            executed_query=executed_query,
                            intent_dimension=query_spec.intent_dimension,
                            requested_depth=query_spec.depth,
                            raw_result_count=0,
                            new_candidate_count=0,
                            duplicate_count=0,
                            skipped_reason="repository_seed_already_searched_at_equal_or_greater_depth",
                            seeds_planned=prepared,
                            portfolio_unit_status="skipped_depth",
                        )
                    )
                    continue
                self._report(
                    f"Searching PRIDE projects: {query_spec.query} -> {executed_query} "
                    f"(depth {query_spec.depth}, role {unit_role})."
                )
                new_for_query = 0
                duplicate_for_query = 0
                try:
                    # Depth is the size of this query's retrieval chunk, not a
                    # candidate-pool target. Continuous discovery may request
                    # later chunks; it must not inflate one chunk from a global
                    # pool configuration.
                    target_per_query = int(query_spec.depth)
                    page_size = 100
                    absolute_offset = (
                        max(0, self._seed_offsets.get(seed_key, 0))
                        if continuous_search
                        else 0
                    )
                    start_page, page_prefix_to_skip = divmod(
                        absolute_offset, page_size
                    )
                    raw_target = page_prefix_to_skip + target_per_query
                    max_pages = max(
                        1,
                        min(20, (raw_target + page_size - 1) // page_size),
                    )
                    if page_requests_remaining is not None:
                        if page_requests_remaining <= 0:
                            self._emit_search_event(
                                "repository_query_skipped",
                                query=query_spec.query,
                                executed_query=executed_query,
                                depth=query_spec.depth,
                                role=unit_role,
                                reason="search_request_budget_reserved_for_inspection",
                            )
                            operational_failures.append(
                                RepositorySearchFailure(
                                    query=query_spec.query,
                                    executed_query=executed_query,
                                    intent_dimension=query_spec.intent_dimension,
                                    requested_depth=query_spec.depth,
                                    error_type="RequestBudgetExhausted",
                                    message="search_request_budget_reserved_for_inspection",
                                )
                            )
                            query_yields.append(
                                QueryYield(
                                    query=query_spec.query,
                                    executed_query=executed_query,
                                    intent_dimension=query_spec.intent_dimension,
                                    requested_depth=query_spec.depth,
                                    raw_result_count=0,
                                    new_candidate_count=0,
                                    duplicate_count=0,
                                    skipped_reason="search_request_budget_reserved_for_inspection",
                                    seeds_planned=prepared,
                                    portfolio_unit_status="skipped_budget",
                                )
                            )
                            unit_skipped.append("search_request_budget_reserved_for_inspection")
                            continue
                        # PTS-3: role-weighted allocation among *executable* seeds.
                        # Equal fair-share only across non-filter peers; primary may take
                        # majority when mixed with non-primary roles.
                        equal_share = max(
                            1,
                            page_requests_remaining // max(1, pending_seed_count),
                        )
                        if unit_role == "primary_theme" and primary_pending > 0:
                            # Primary pool ~75% of remaining, split among remaining primaries.
                            primary_pool = max(
                                equal_share,
                                int(page_requests_remaining * 0.75) // max(1, primary_pending),
                            )
                            share = max(equal_share, primary_pool)
                            # Single primary (or last primary) may consume remaining budget.
                            if primary_pending <= 1 and pending_seed_count <= 1:
                                share = page_requests_remaining
                            elif primary_pending <= 1:
                                share = max(share, int(page_requests_remaining * 0.75))
                            max_pages = min(50, max(max_pages, share))
                            primary_pending = max(0, primary_pending - 1)
                        elif unit_role == "exact_accession":
                            share = 1
                            max_pages = 1
                        else:
                            share = equal_share
                            max_pages = min(max_pages, share)
                        max_pages = min(max_pages, share, page_requests_remaining)
                        page_requests_remaining -= max_pages
                        pending_seed_count = max(0, pending_seed_count - 1)
                    max_results = min(
                        target_per_query,
                        max(1, page_size * max_pages - page_prefix_to_skip),
                    )
                    self._emit_search_event(
                        "repository_query_started",
                        query=query_spec.query,
                        executed_query=executed_query,
                        depth=query_spec.depth,
                        role=unit_role,
                        page_size=page_size,
                        max_pages=max_pages,
                        max_results=max_results,
                        start_page=start_page,
                        start_offset=absolute_offset,
                        page_number=start_page + 1,
                    )
                    last_page_count: int | None = None
                    actual_pages_requested = 0

                    def report_page(
                        page: int,
                        page_count: int,
                        cumulative_count: int,
                    ) -> None:
                        nonlocal last_page_count, actual_pages_requested
                        last_page_count = page_count
                        actual_pages_requested += 1
                        self._emit_search_event(
                            "repository_query_page_completed",
                            query=query_spec.query,
                            executed_query=executed_query,
                            depth=query_spec.depth,
                            role=unit_role,
                            page=page,
                            page_number=page + 1,
                            page_result_count=page_count,
                            # Keep the legacy field for old event consumers.
                            page_count=page_count,
                            pages_completed=actual_pages_requested,
                            cumulative_count=min(
                                target_per_query,
                                max(0, cumulative_count - page_prefix_to_skip),
                            ),
                            max_pages=max_pages,
                            start_page=start_page,
                            start_offset=absolute_offset,
                        )

                    search_result = search_projects_paginated_with_state(
                        self.client,
                        executed_query,
                        mode="budgeted",
                        page_size=page_size,
                        max_pages=max_pages,
                        max_results=max_results,
                        start_page=start_page,
                        start_page_offset=page_prefix_to_skip,
                        on_page=report_page,
                    )
                    actual_pages_requested = search_result.state.pages_completed
                    if page_requests_remaining is not None:
                        page_requests_remaining += max(
                            0, max_pages - actual_pages_requested
                        )
                    rows = search_result.records
                    if continuous_search:
                        self._seed_offsets[seed_key] = absolute_offset + len(rows)
                        if search_result.state.exhausted:
                            self._exhausted_seeds.add(seed_key)
                    self._seed_depths[seed_key] = max(
                        previous_depth, query_spec.depth
                    )
                except Exception as exc:  # pragma: no cover - network boundary
                    self._emit_search_event(
                        "repository_query_failed",
                        query=query_spec.query,
                        executed_query=executed_query,
                        depth=query_spec.depth,
                        role=unit_role,
                        error=str(exc),
                        page_number=start_page + actual_pages_requested + 1,
                        pages_completed=actual_pages_requested,
                    )
                    failure = f"{query_spec.query}: {exc}"
                    failures.append(failure)
                    unit_errors.append(str(exc))
                    operational_failures.append(
                        RepositorySearchFailure(
                            query=query_spec.query,
                            executed_query=executed_query,
                            intent_dimension=query_spec.intent_dimension,
                            requested_depth=query_spec.depth,
                            error_type=type(exc).__name__,
                            message=str(exc),
                        )
                    )
                    query_yields.append(
                        QueryYield(
                            query=query_spec.query,
                            executed_query=executed_query,
                            intent_dimension=query_spec.intent_dimension,
                            requested_depth=query_spec.depth,
                            raw_result_count=0,
                            new_candidate_count=0,
                            duplicate_count=0,
                            error=str(exc),
                            seeds_planned=prepared,
                            portfolio_unit_status="failed",
                        )
                    )
                    if "search_request_budget_reserved_for_inspection" in str(exc):
                        break
                    continue
                successful_repository_attempts += 1
                unit_executed.append(executed_query)
                unit_raw += len(rows)
                raw_total += len(rows)
                top_accessions: list[str] = []
                for row in rows:
                    accession = _project_accession(row)
                    if not accession:
                        continue
                    top_accessions.append(accession)
                    if exact_accession and accession == exact_accession:
                        # A user- or Agent-requested exact accession is not merely another
                        # ranked preview. Keep it inspectable even when broad results score
                        # higher and fill the bounded candidate pool.
                        self._pinned_accessions.add(accession)
                    if accession in self._records:
                        duplicate_for_query += 1
                    else:
                        enriched = dict(row)
                        enriched["_discovery_query"] = query_spec.query
                        enriched["_discovery_depth"] = query_spec.depth
                        enriched["_discovery_intent"] = query_spec.intent_dimension
                        enriched["_discovery_seed"] = executed_query
                        self._records[accession] = enriched
                        new_for_query += 1
                    self._query_hits[accession].add(executed_query)
                duplicate_total += duplicate_for_query
                unit_new += new_for_query
                unit_dup += duplicate_for_query
                unit_top.extend(top_accessions[:20])
                query_yields.append(
                    QueryYield(
                        query=query_spec.query,
                        executed_query=executed_query,
                        intent_dimension=query_spec.intent_dimension,
                        requested_depth=query_spec.depth,
                        raw_result_count=len(rows),
                        new_candidate_count=new_for_query,
                        duplicate_count=duplicate_for_query,
                        top_accessions=top_accessions[:20],
                        seeds_planned=prepared,
                        portfolio_unit_status="executed",
                    )
                )
                self._emit_search_event(
                    "repository_query_completed",
                    query=query_spec.query,
                    executed_query=executed_query,
                    depth=query_spec.depth,
                    role=unit_role,
                    raw_result_count=len(rows),
                    new_candidate_count=new_for_query,
                    duplicate_count=duplicate_for_query,
                    pages_completed=actual_pages_requested,
                    last_page_result_count=last_page_count or 0,
                    pagination=search_result.state.to_dict(),
                    exhausted=search_result.state.exhausted,
                    truncated=search_result.state.truncated,
                    stop_reason=search_result.state.stop_reason,
                    next_page=search_result.state.next_page,
                    next_page_offset=search_result.state.next_page_offset,
                )
            # Portfolio unit audit status.
            if unit_executed:
                unit.seeds_executed = list(dict.fromkeys(unit_executed))
                unit.status = "executed"
                unit.yield_counts = {
                    "raw_result_count": unit_raw,
                    "new_candidate_count": unit_new,
                    "duplicate_count": unit_dup,
                }
            elif unit_errors and not unit_skipped:
                unit.status = "failed"
                unit.not_executed_reason = unit_errors[0]
            elif any("budget" in reason for reason in unit_skipped):
                unit.status = "skipped_budget"
                unit.not_executed_reason = unit_skipped[0]
            elif unit_skipped:
                unit.status = "skipped_depth"
                unit.not_executed_reason = unit_skipped[0]
            else:
                unit.status = "failed"
                unit.not_executed_reason = "no_seeds_executed"


        portfolio = QueryPortfolio(
            units=portfolio_units,
            executed_seed_count=sum(len(unit.seeds_executed) for unit in portfolio_units),
            skipped_seed_count=sum(
                1
                for unit in portfolio_units
                if unit.status.startswith("skipped")
            ),
            failed_seed_count=sum(1 for unit in portfolio_units if unit.status == "failed"),
        )


        ranked = self._ranked_records()
        ranked = sorted(
            ranked,
            key=lambda item: item[0] not in self._pinned_accessions,
        )
        unlimited_pool = (
            bool(self.request.continuous_discovery)
            or bool(self.request.harvest_all_qualified)
            or (
                self.request.quota_flexibility == "fixed"
                and bool(self.request.query_terms)
            )
        )
        retention_limit = max(1, int(self.request.max_candidate_projects))
        if not unlimited_pool and len(ranked) > retention_limit:
            retained = {
                accession
                for accession, _record, _preview in ranked[:retention_limit]
            }
            self._records = {
                accession: record
                for accession, record in self._records.items()
                if accession in retained
            }
            self._query_hits = defaultdict(
                set,
                {
                    accession: hits
                    for accession, hits in self._query_hits.items()
                    if accession in retained
                },
            )
            self._pinned_accessions.intersection_update(retained)
            ranked = ranked[:retention_limit]
        self._search_counter += 1
        self._latest_search_id = f"search_{self._search_counter:04d}"
        preview_end = action.preview_offset + action.candidate_limit
        previews = [
            preview
            for _accession, _record, preview in ranked[action.preview_offset : preview_end]
        ]
        covered = sorted(
            {
                term
                for preview in previews
                if not preview.excluded
                for term in preview.matched_intent_terms
            }
        )
        unresolved = [term for term in self.intent_terms if term not in set(covered)]
        high_relevance_floor = min(2, max(1, len(self.intent_terms)))
        high_relevance = sum(
            not preview.excluded and len(preview.matched_intent_terms) >= high_relevance_floor
            for preview in previews
        )
        corpus_term_coverage = len(covered) / max(1, len(self.intent_terms))
        cem = build_provisional_cem(
            request=self.request,
            previews=previews,
            target_projects=self.request.max_projects,
        )
        all_current_attempts_failed = (
            bool(operational_failures) and successful_repository_attempts == 0
        )
        budget_blocked = all_current_attempts_failed and all(
            failure.message == "search_request_budget_reserved_for_inspection"
            for failure in operational_failures
        )
        total_repository_outage = all_current_attempts_failed and not self._records
        status = "completed"
        stop_reason: str | None = None
        recommended_action = "review_candidate_previews"
        if budget_blocked:
            status = "blocked"
            stop_reason = "search_request_budget_reserved_for_inspection"
            recommended_action = (
                "inspect_existing_candidates_or_stop"
                if self._records
                else "retry_repository_or_stop"
            )
        elif total_repository_outage:
            status = "failed"
            stop_reason = "all_repository_search_attempts_failed"
            recommended_action = "retry_repository_or_stop"
        self._save_state()
        return CandidateSearchObservation(
            status=status,
            search_id=self._latest_search_id,
            query_yields=query_yields,
            raw_result_count=raw_total,
            candidate_count=len(self._records),
            new_candidate_count=len(set(self._records) - before),
            duplicate_count=duplicate_total,
            duplicate_rate=duplicate_total / max(1, raw_total),
            previews=previews,
            preview_offset=action.preview_offset,
            next_preview_offset=preview_end if preview_end < len(ranked) else None,
            has_more_candidates=preview_end < len(ranked),
            intent_terms=self.intent_terms,
            covered_intent_terms=covered,
            unresolved_intent_terms=unresolved,
            corpus_term_coverage=corpus_term_coverage,
            semantic_coverage=corpus_term_coverage,
            high_relevance_candidate_count=high_relevance,
            hard_constraint_evidence_gap=cem.hard_constraint_evidence_gap,
            n_hard_conjunction_pass=cem.n_hard_conjunction_pass,
            n_hard_pass_inspected=cem.n_hard_pass_inspected,
            unknown_hard_rate=cem.unknown_hard_rate,
            candidate_level_conjunction_coverage=cem.candidate_level_conjunction_coverage,
            query_portfolio=portfolio.record_summary(),
            cem_summary=cem.summary(),
            recommended_action=recommended_action,
            failures=failures,
            operational_failures=operational_failures,
            stop_reason=stop_reason,
            rationale=action.rationale,
        )

    def inspect(self, action: CandidateInspectionAction) -> CandidateInspectionResult:
        self._check_cancel()
        if action.search_id != self._latest_search_id:
            raise ValueError("candidate inspection search_id is not the latest persisted search")
        # Soft-skip accessions not in the latest pool. Agents sometimes request
        # IDs from an older ranking; failing the whole batch zeroed progress (UI
        # "candidate inspection failed" with 0 reviewed).
        original_requested = [str(item).strip().upper() for item in action.accessions if str(item).strip()]
        missing = [accession for accession in original_requested if accession not in self._records]
        present = [accession for accession in original_requested if accession in self._records]
        if missing:
            self._report(
                "Skipping "
                + str(len(missing))
                + " accession(s) outside the latest candidate pool: "
                + ", ".join(missing[:12])
                + ("..." if len(missing) > 12 else "")
            )
        if not present:
            raise ValueError(
                "candidate inspection accession is outside the persisted candidate pool: "
                + ", ".join(missing[:20])
            )
        records = [self._records[accession] for accession in present]
        skipped_outside_pool = list(missing)
        # Inspect only pool members; record skips for the rest.
        action = action.model_copy(update={"accessions": present})
        inspection_request = self.request.model_copy(
            update={
                "max_projects": min(self.request.max_projects, len(records)),
                "max_candidate_projects": len(records),
            }
        )
        inspection_workers = min(4, len(records))
        def _inspect_one(
            record: dict[str, Any],
            worker_slot: int,
        ) -> DatasetManifest:
            accession = _project_accession(record)
            started = perf_counter()
            self._emit_search_event(
                "project_review_started",
                project_accession=accession,
                worker_slot=worker_slot,
                step="metadata_read",
                status="running",
            )
            single_request = inspection_request.model_copy(
                update={
                    "max_projects": 1,
                    "max_candidate_projects": 1,
                }
            )

            def project_event(
                project_accession: str,
                step: str,
                status: str,
                payload: dict[str, Any],
            ) -> None:
                self._emit_search_event(
                    "project_review_step",
                    project_accession=project_accession,
                    worker_slot=worker_slot,
                    step=step,
                    status=status,
                    elapsed_ms=int((perf_counter() - started) * 1_000),
                    **payload,
                )

            try:
                result = discover_pride_dataset(
                    single_request,
                    client=self.client,
                    memory=self.memory,
                    queries=[],
                    candidate_records=[record],
                    report=self.report,
                    project_event=project_event,
                    should_cancel=self.should_cancel,
                    early_stop_on_limits=False,
                )
            except Exception as exc:
                self._emit_search_event(
                    "project_review_completed",
                    project_accession=accession,
                    worker_slot=worker_slot,
                    status="failed",
                    step="failed",
                    elapsed_ms=int((perf_counter() - started) * 1_000),
                    inspection_outcomes=[
                        {
                            "project_accession": accession,
                            "category": "inspection_failure",
                            "stage": "inspection",
                            "reason": "project_review_failed",
                            "error": str(exc),
                        }
                    ],
                )
                raise
            outcomes = [
                {
                    **dict(item),
                    "elapsed_ms": int((perf_counter() - started) * 1_000),
                }
                for item in result.summary.get("inspection_outcomes") or []
                if isinstance(item, dict)
            ]
            self._emit_search_event(
                "project_review_completed",
                project_accession=accession,
                worker_slot=worker_slot,
                status="completed",
                step="completed",
                elapsed_ms=int((perf_counter() - started) * 1_000),
                inspection_outcomes=outcomes,
            )
            return result

        if inspection_workers <= 1:
            manifest = _inspect_one(records[0], 1)
        else:
            self._report(
                f"Reviewing {len(records)} deduplicated projects with "
                f"{inspection_workers} concurrent workers."
            )
            ordered_manifests: list[DatasetManifest | None] = [None] * len(records)

            with ThreadPoolExecutor(
                max_workers=inspection_workers,
                thread_name_prefix="pride-review",
            ) as executor:
                futures = {
                    executor.submit(
                        copy_context().run,
                        _inspect_one,
                        record,
                        (index % inspection_workers) + 1,
                    ): index
                    for index, record in enumerate(records)
                }
                for future in as_completed(futures):
                    ordered_manifests[futures[future]] = future.result()
            manifest = _merge_parallel_inspection_manifests(
                inspection_request,
                [item for item in ordered_manifests if item is not None],
                workers=inspection_workers,
            )
        selected_accessions = {
            project.project_accession.upper()
            for project in manifest.projects
            if project.project_accession
        }
        outcomes_by_accession: dict[str, CandidateInspectionOutcome] = {}
        for payload in manifest.summary.get("inspection_outcomes") or []:
            try:
                outcome = CandidateInspectionOutcome.model_validate(payload)
            except (TypeError, ValueError):
                continue
            outcomes_by_accession[outcome.project_accession.upper()] = outcome.model_copy(
                update={"project_accession": outcome.project_accession.upper()}
            )
        for accession in action.accessions:
            if accession not in outcomes_by_accession:
                if accession in selected_accessions:
                    outcomes_by_accession[accession] = CandidateInspectionOutcome(
                        project_accession=accession,
                        category="usable_files",
                    )
                else:
                    outcomes_by_accession[accession] = CandidateInspectionOutcome(
                        project_accession=accession,
                        category="inspection_failure",
                        stage="inspection",
                        reason="inspection produced no terminal outcome",
                    )
        for accession in skipped_outside_pool:
            outcomes_by_accession.setdefault(
                accession,
                CandidateInspectionOutcome(
                    project_accession=accession,
                    category="not_inspected",
                    stage="inspection",
                    reason="outside_latest_candidate_pool",
                ),
            )
        ordered_accessions = list(action.accessions) + [
            item for item in skipped_outside_pool if item not in action.accessions
        ]
        inspection_outcomes = [outcomes_by_accession[item] for item in ordered_accessions if item in outcomes_by_accession]
        eligible_accessions = [
            item.project_accession
            for item in inspection_outcomes
            if item.category == "usable_files"
        ]
        excluded_accessions = [
            item.project_accession
            for item in inspection_outcomes
            if item.category == "scientific_exclusion"
        ]
        no_usable_files_accessions = [
            item.project_accession
            for item in inspection_outcomes
            if item.category == "no_usable_files"
        ]
        failed_accessions = [
            item.project_accession
            for item in inspection_outcomes
            if item.category in {"inspection_failure", "not_inspected"}
        ]
        inspected_accessions = [
            item.project_accession
            for item in inspection_outcomes
            if item.category
            in {"usable_files", "scientific_exclusion", "no_usable_files"}
        ]
        inspection_outcome_counts = dict(
            sorted(
                Counter(item.category for item in inspection_outcomes).items()
            )
        )
        summary = {
            **manifest.summary,
            "inspection_outcomes": [
                item.model_dump(mode="json") for item in inspection_outcomes
            ],
            "inspection_outcome_counts": inspection_outcome_counts,
            "search_environment": {
                "search_id": action.search_id,
                "requested_accessions": action.accessions,
                "inspected_accessions": inspected_accessions,
                "eligible_accessions": eligible_accessions,
                "failed_accessions": failed_accessions,
                "excluded_accessions": excluded_accessions,
                "no_usable_files_accessions": no_usable_files_accessions,
                "inspection_outcome_counts": inspection_outcome_counts,
                "inspection_rationale": action.rationale,
            },
        }
        manifest = manifest.model_copy(update={"summary": summary})
        usable = sum(file.validity_status in {"valid", "weak_keep"} for file in manifest.files)
        valid = sum(file.validity_status == "valid" for file in manifest.files)
        return CandidateInspectionResult(
            search_id=action.search_id,
            requested_accessions=original_requested,
            inspected_accessions=inspected_accessions,
            eligible_accessions=eligible_accessions,
            failed_accessions=failed_accessions,
            excluded_accessions=excluded_accessions,
            no_usable_files_accessions=no_usable_files_accessions,
            inspection_outcomes=inspection_outcomes,
            manifest=manifest,
            usable_files=usable,
            valid_files=valid,
            rationale=action.rationale,
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _ranked_records(self) -> list[tuple[str, dict[str, Any], CandidatePreview]]:
        ranked: list[tuple[str, dict[str, Any], CandidatePreview]] = []
        for accession, record in self._records.items():
            score = score_project(record, self.request)
            text = _project_text(record)
            matched = [term for term in self.intent_terms if _contains_term(text, term)]
            preview = CandidatePreview(
                project_accession=accession,
                title=str(record.get("title") or ""),
                description_excerpt=_excerpt(record.get("projectDescription")),
                project_score=score.project_score,
                confidence=score.confidence,
                needs_review=score.needs_review,
                excluded=score.excluded,
                species=score.species,
                acquisition_mode=score.acquisition_mode,
                matched_intent_terms=matched,
                query_hits=sorted(self._query_hits.get(accession, set())),
            )
            ranked.append((accession, record, preview))
        return sorted(
            ranked,
            key=lambda item: (
                item[2].excluded,
                -len(item[2].matched_intent_terms),
                item[2].needs_review,
                -item[2].project_score,
                item[0],
            ),
        )

    def _load_state(self) -> None:
        if not self.state_path.is_file():
            return
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        records = payload.get("records") if isinstance(payload, dict) else None
        if isinstance(records, list):
            self._records = {
                _project_accession(record): record
                for record in records
                if isinstance(record, dict) and _project_accession(record)
            }
        raw_hits = payload.get("query_hits") if isinstance(payload, dict) else None
        if isinstance(raw_hits, dict):
            self._query_hits = defaultdict(
                set,
                {
                    str(accession): {str(query) for query in queries}
                    for accession, queries in raw_hits.items()
                    if isinstance(queries, list)
                },
            )
        raw_pinned = payload.get("pinned_accessions") if isinstance(payload, dict) else None
        if isinstance(raw_pinned, list):
            self._pinned_accessions = {
                str(accession).strip().upper()
                for accession in raw_pinned
                if str(accession).strip().upper() in self._records
            }
        raw_seed_depths = payload.get("seed_depths") if isinstance(payload, dict) else None
        if isinstance(raw_seed_depths, dict):
            self._seed_depths = {
                str(seed): max(0, int(depth))
                for seed, depth in raw_seed_depths.items()
            }
        raw_seed_offsets = payload.get("seed_offsets") if isinstance(payload, dict) else None
        if isinstance(raw_seed_offsets, dict):
            self._seed_offsets = {
                str(seed): max(0, int(offset))
                for seed, offset in raw_seed_offsets.items()
            }
        raw_exhausted = payload.get("exhausted_seeds") if isinstance(payload, dict) else None
        if isinstance(raw_exhausted, list):
            self._exhausted_seeds = {
                " ".join(str(seed).casefold().split())
                for seed in raw_exhausted
                if str(seed).strip()
            }
        self._search_counter = max(0, int(payload.get("search_counter") or 0))
        latest = str(payload.get("latest_search_id") or "").strip()
        self._latest_search_id = latest or None
        persisted_intent = payload.get("intent_terms")
        if isinstance(persisted_intent, list):
            self.intent_terms = [str(term) for term in persisted_intent if str(term).strip()][:24]

    def _save_state(self) -> None:
        write_json(
            self.state_path,
            {
                "schema_version": "discovery-candidate-state/v3",
                "search_counter": self._search_counter,
                "latest_search_id": self._latest_search_id,
                "intent_terms": self.intent_terms,
                "records": list(self._records.values()),
                "query_hits": {
                    accession: sorted(queries)
                    for accession, queries in self._query_hits.items()
                },
                "pinned_accessions": sorted(self._pinned_accessions),
                "seed_depths": self._seed_depths,
                "seed_offsets": self._seed_offsets,
                "exhausted_seeds": sorted(self._exhausted_seeds),
            },
        )

    def _report(self, message: str) -> None:
        if self.report is not None:
            self.report(message)

    def _check_cancel(self) -> None:
        if self.should_cancel is not None and self.should_cancel():
            raise InterruptedError("Discovery cancelled.")


def _extract_intent_terms(prompt: str, request: DatasetRequest) -> list[str]:
    """Extract intent terms from structured request fields first, then free text.

    WP-A: species / PTM / acquisition / labeling are domain-critical and must not be
    dropped by free-text stopwords.
    """
    structured: list[str] = []
    for species in request.species or []:
        token = str(species or "").strip()
        if token:
            structured.append(token)
            # Common alias so bag-of-terms matching works for "human" vs Homo sapiens.
            if token.casefold() in {"homo sapiens", "h. sapiens"}:
                structured.append("human")
            elif token.casefold() == "human":
                structured.append("Homo sapiens")
    if request.acquisition_mode and str(request.acquisition_mode).casefold() not in {
        "",
        "unknown",
        "any",
    }:
        structured.append(str(request.acquisition_mode))
        if str(request.acquisition_mode).casefold() == "dda":
            structured.append("data-dependent")
    if request.ptm_type and str(request.ptm_type).casefold() not in {
        "",
        "unknown",
        "unknown_ptm",
        "any",
    }:
        structured.append(str(request.ptm_type))
    for ptm in request.ptm_types or []:
        if str(ptm or "").strip():
            structured.append(str(ptm))
    if request.labeling_strategy and str(request.labeling_strategy).casefold() not in {
        "",
        "unknown",
        "any",
    }:
        structured.append(str(request.labeling_strategy))
    for constraint in request.scientific_constraints or []:
        if str(getattr(constraint, "strength", "soft")).casefold() != "hard":
            continue
        label = str(getattr(constraint, "label", "") or getattr(constraint, "dimension", "") or "")
        if label.strip():
            structured.append(label)
    source = " ".join([prompt, *request.query_terms, *structured])
    return _extract_candidate_terms(source)


def _extract_candidate_terms(source: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", source):
        normalized = token.casefold().strip("-")
        if (
            normalized in _INTENT_STOPWORDS
            or normalized in seen
            or _EXACT_PRIDE_ACCESSION_RE.fullmatch(normalized)
        ):
            continue
        seen.add(normalized)
        terms.append(normalized)
        if len(terms) >= 24:
            break
    return terms


def _project_accession(record: dict[str, Any]) -> str:
    return str(record.get("accession") or record.get("projectAccession") or "").strip().upper()


def _project_text(record: dict[str, Any]) -> str:
    values = [
        record.get("title"),
        record.get("projectDescription"),
        record.get("sampleProcessingProtocol"),
        record.get("dataProcessingProtocol"),
        record.get("keywords"),
        record.get("experimentTypes"),
        record.get("organisms"),
        record.get("instruments"),
    ]
    return " ".join(_flatten_text(value) for value in values).casefold()


def _flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_flatten_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_flatten_text(item) for item in value)
    return str(value)


def _contains_term(text: str, term: str) -> bool:
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text))


def _excerpt(value: Any, limit: int = 280) -> str:
    text = " ".join(_flatten_text(value).split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."
