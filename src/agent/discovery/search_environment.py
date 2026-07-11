from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from pydantic import Field, model_validator

from agent.discovery.memory import DiscoveryMemory
from agent.discovery.models import DatasetManifest, DatasetRequest
from agent.discovery.pride_discovery import discover_pride_dataset
from agent.discovery.query_builder import prepare_pride_search_queries
from agent.discovery.scoring import score_project
from agent.models import JsonModel
from agent.pride.client import PrideClient
from agent.utils import write_json


_INTENT_STOPWORDS = {
    "about",
    "acquired",
    "against",
    "class",
    "data",
    "dataset",
    "datasets",
    "develop",
    "development",
    "find",
    "from",
    "human",
    "model",
    "need",
    "proteome",
    "proteomic",
    "proteomics",
    "sample",
    "samples",
    "study",
    "using",
    "with",
}


class RepositoryQuery(JsonModel):
    query: str = Field(min_length=1, max_length=240)
    depth: int = Field(default=20, ge=1, le=100)
    intent_dimension: str = Field(default="general", min_length=1, max_length=120)
    expected_gain: str = Field(default="", max_length=500)


class CandidateSearchAction(JsonModel):
    queries: list[RepositoryQuery] = Field(min_length=1, max_length=40)
    candidate_limit: int = Field(default=50, ge=1, le=300)
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
    intent_terms: list[str] = Field(default_factory=list)
    covered_intent_terms: list[str] = Field(default_factory=list)
    unresolved_intent_terms: list[str] = Field(default_factory=list)
    semantic_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    high_relevance_candidate_count: int = Field(default=0, ge=0)
    new_high_relevance_candidate_count: int = Field(default=0, ge=0)
    semantic_coverage_gain: float = Field(default=0.0, ge=0.0, le=1.0)
    recommended_action: str = "review_candidate_previews"
    failures: list[str] = Field(default_factory=list)
    rationale: str = ""


class CandidateInspectionAction(JsonModel):
    search_id: str = Field(min_length=1)
    accessions: list[str] = Field(min_length=1, max_length=25)
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


class CandidateInspectionResult(JsonModel):
    search_id: str
    inspected_accessions: list[str]
    manifest: DatasetManifest
    usable_files: int = Field(ge=0)
    valid_files: int = Field(ge=0)
    rationale: str


class DiscoverySearchEnvironment(Protocol):
    def search(self, action: CandidateSearchAction) -> CandidateSearchObservation: ...

    def inspect(self, action: CandidateInspectionAction) -> CandidateInspectionResult: ...

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
        should_cancel: Callable[[], bool] | None = None,
    ) -> None:
        self.request = request
        self.prompt = " ".join(str(prompt or "").split()).strip()
        self.state_path = Path(state_path)
        self.client = client or PrideClient()
        self._owns_client = client is None
        self.memory = memory
        self.report = report
        self.should_cancel = should_cancel
        self.intent_terms = _extract_intent_terms(self.prompt, request)
        self._records: dict[str, dict[str, Any]] = {}
        self._query_hits: dict[str, set[str]] = defaultdict(set)
        self._seed_depths: dict[str, int] = {}
        self._search_counter = 0
        self._latest_search_id: str | None = None
        self._load_state()

    @property
    def candidate_accessions(self) -> list[str]:
        return list(self._records)

    def search(self, action: CandidateSearchAction) -> CandidateSearchObservation:
        self._check_cancel()
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
        query_yields: list[QueryYield] = []
        for query_spec in action.queries:
            self._check_cancel()
            prepared = prepare_pride_search_queries([query_spec.query])
            executed_query = prepared[0] if prepared else query_spec.query
            seed_key = " ".join(executed_query.casefold().split())
            previous_depth = self._seed_depths.get(seed_key, 0)
            if previous_depth >= query_spec.depth:
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
                    )
                )
                continue
            self._report(
                f"Searching PRIDE projects: {query_spec.query} -> {executed_query} "
                f"(depth {query_spec.depth})."
            )
            new_for_query = 0
            duplicate_for_query = 0
            try:
                rows = self.client.search_projects(
                    executed_query,
                    page_size=query_spec.depth,
                )
                self._seed_depths[seed_key] = query_spec.depth
            except Exception as exc:  # pragma: no cover - network boundary
                failure = f"{query_spec.query}: {exc}"
                failures.append(failure)
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
                    )
                )
                continue
            raw_total += len(rows)
            top_accessions: list[str] = []
            for row in rows:
                accession = _project_accession(row)
                if not accession:
                    continue
                top_accessions.append(accession)
                if accession in self._records:
                    duplicate_for_query += 1
                else:
                    self._records[accession] = row
                    new_for_query += 1
                self._query_hits[accession].add(executed_query)
            duplicate_total += duplicate_for_query
            query_yields.append(
                QueryYield(
                    query=query_spec.query,
                    executed_query=executed_query,
                    intent_dimension=query_spec.intent_dimension,
                    requested_depth=query_spec.depth,
                    raw_result_count=len(rows),
                    new_candidate_count=new_for_query,
                    duplicate_count=duplicate_for_query,
                    top_accessions=top_accessions[:5],
                )
            )

        ranked = self._ranked_records()
        if len(ranked) > 300:
            retained = {accession for accession, _record, _preview in ranked[:300]}
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
            ranked = ranked[:300]
        self._search_counter += 1
        self._latest_search_id = f"search_{self._search_counter:04d}"
        previews = [preview for _accession, _record, preview in ranked[: action.candidate_limit]]
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
        self._save_state()
        return CandidateSearchObservation(
            search_id=self._latest_search_id,
            query_yields=query_yields,
            raw_result_count=raw_total,
            candidate_count=len(self._records),
            new_candidate_count=len(set(self._records) - before),
            duplicate_count=duplicate_total,
            duplicate_rate=duplicate_total / max(1, raw_total),
            previews=previews,
            intent_terms=self.intent_terms,
            covered_intent_terms=covered,
            unresolved_intent_terms=unresolved,
            semantic_coverage=len(covered) / max(1, len(self.intent_terms)),
            high_relevance_candidate_count=high_relevance,
            failures=failures,
            rationale=action.rationale,
        )

    def inspect(self, action: CandidateInspectionAction) -> CandidateInspectionResult:
        self._check_cancel()
        if action.search_id != self._latest_search_id:
            raise ValueError("candidate inspection search_id is not the latest persisted search")
        missing = [accession for accession in action.accessions if accession not in self._records]
        if missing:
            raise ValueError(
                "candidate inspection accession is outside the persisted candidate pool: "
                + ", ".join(missing)
            )
        records = [self._records[accession] for accession in action.accessions]
        inspection_request = self.request.model_copy(
            update={
                "max_projects": min(self.request.max_projects, len(records)),
                "max_candidate_projects": len(records),
            }
        )
        manifest = discover_pride_dataset(
            inspection_request,
            client=self.client,
            memory=self.memory,
            queries=[],
            candidate_records=records,
            report=self.report,
            should_cancel=self.should_cancel,
            early_stop_on_limits=False,
        )
        summary = {
            **manifest.summary,
            "search_environment": {
                "search_id": action.search_id,
                "inspected_accessions": action.accessions,
                "inspection_rationale": action.rationale,
            },
        }
        manifest = manifest.model_copy(update={"summary": summary})
        usable = sum(file.validity_status in {"valid", "weak_keep"} for file in manifest.files)
        valid = sum(file.validity_status == "valid" for file in manifest.files)
        return CandidateInspectionResult(
            search_id=action.search_id,
            inspected_accessions=action.accessions,
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
        raw_seed_depths = payload.get("seed_depths") if isinstance(payload, dict) else None
        if isinstance(raw_seed_depths, dict):
            self._seed_depths = {
                str(seed): max(0, int(depth))
                for seed, depth in raw_seed_depths.items()
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
                "schema_version": "discovery-candidate-state/v1",
                "search_counter": self._search_counter,
                "latest_search_id": self._latest_search_id,
                "intent_terms": self.intent_terms,
                "records": list(self._records.values()),
                "query_hits": {
                    accession: sorted(queries)
                    for accession, queries in self._query_hits.items()
                },
                "seed_depths": self._seed_depths,
            },
        )

    def _report(self, message: str) -> None:
        if self.report is not None:
            self.report(message)

    def _check_cancel(self) -> None:
        if self.should_cancel is not None and self.should_cancel():
            raise InterruptedError("Discovery cancelled.")


def _extract_intent_terms(prompt: str, request: DatasetRequest) -> list[str]:
    source = " ".join([prompt, *request.query_terms])
    return _extract_candidate_terms(source)


def _extract_candidate_terms(source: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", source):
        normalized = token.casefold().strip("-")
        if normalized in _INTENT_STOPWORDS or normalized in seen:
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
