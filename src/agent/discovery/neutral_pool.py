from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from typing import Any, Protocol

from agent.discovery.features import extract_project_features
from agent.discovery.query_builder import prepare_pride_search_queries
from agent.discovery.replacement_evaluation import PromptVariant, ReplacementBenchmarkScenario
from agent.discovery.scoring import classify_file_role
from agent.models import JsonModel
from agent.pride.client import (
    PridePaginationState,
    list_project_files_paginated_with_state,
    search_projects_paginated_with_state,
)


class ProjectSearchClient(Protocol):
    def search_projects(self, keyword: str, page_size: int = 100) -> list[dict[str, Any]]: ...

    def get_project(self, accession: str) -> dict[str, Any]: ...

    def list_project_files(
        self, accession: str, page_size: int = 1000, max_files: int | None = None
    ) -> list[dict[str, Any]]: ...


class NeutralPoolResult(JsonModel):
    schema_version: str = "discovery-neutral-pool/v1"
    candidates: list[dict[str, Any]]
    query_trace: list[dict[str, Any]]


_TASK_SEEDS: dict[str, tuple[str, ...]] = {
    "rt_prediction": ("retention", "chromatography", "gradient", "DDA", "proteomics"),
    "fragment_intensity_prediction": ("HCD", "collision", "synthetic peptide", "Orbitrap", "DDA"),
    "psm_scoring": ("PSM", "search results", "target decoy", "proteomics", "metaproteomics"),
    "ptm_denovo": ("phosphoproteomics", "phosphopeptide", "enrichment", "localization", "DDA"),
    "chimeric_interpretation": ("chimeric", "coisolation", "cofragmentation", "isolation window", "DDA"),
    "denovo": ("de novo", "non-model", "metaproteomics", "MS/MS", "proteomics"),
}


def build_neutral_queries(
    scenario: ReplacementBenchmarkScenario,
    variant: PromptVariant,
    *,
    limit: int = 8,
) -> list[str]:
    values = [*_TASK_SEEDS.get(str(scenario.task_type or ""), ())]
    values.extend(str(term) for term in scenario.hidden_request.query_terms)
    if "species" in variant.hard_constraint_fields:
        values.extend(str(species) for species in scenario.hidden_request.species)
    if "acquisition_mode" in variant.hard_constraint_fields:
        values.append(str(scenario.hidden_request.acquisition_mode))
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = " ".join(value.split()).strip()
        prepared = prepare_pride_search_queries([normalized])
        atomic = prepared[0] if prepared else normalized
        key = atomic.casefold()
        if not atomic or key in seen:
            continue
        seen.add(key)
        result.append(atomic)
        if len(result) >= limit:
            break
    return result


def collect_neutral_pool(
    scenarios: Sequence[ReplacementBenchmarkScenario],
    client: ProjectSearchClient,
    *,
    query_depth: int = 20,
    max_candidates_per_variant: int = 40,
    enrich_projects: int = 20,
) -> NeutralPoolResult:
    candidates: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    for scenario in scenarios:
        for variant in scenario.prompt_variants:
            records: dict[str, dict[str, Any]] = {}
            hits: dict[str, list[str]] = {}
            queries = build_neutral_queries(scenario, variant)
            for query in queries:
                try:
                    page_size = max(1, min(int(query_depth), 100))
                    search_result = search_projects_paginated_with_state(
                        client,
                        query,
                        mode="budgeted",
                        page_size=page_size,
                        max_pages=max(1, (int(query_depth) + page_size - 1) // page_size),
                        max_results=max(1, int(query_depth)),
                    )
                    rows = search_result.records
                    pagination = search_result.state.to_dict()
                    error = None
                except Exception as exc:  # pragma: no cover - network boundary
                    rows = []
                    pagination = PridePaginationState.unavailable(
                        operation="search_projects",
                        mode="budgeted",
                        query_text=query,
                        keyword=query,
                        page_size=max(1, min(int(query_depth), 100)),
                        stop_reason="error",
                    ).to_dict()
                    error = str(exc)
                trace.append(
                    {
                        "scenario_id": scenario.id,
                        "variant_id": variant.id,
                        "query": query,
                        "result_count": len(rows),
                        "pagination": pagination,
                        "error": error,
                    }
                )
                for row in rows:
                    accession = _accession(row)
                    if not accession:
                        continue
                    records.setdefault(accession, row)
                    accession_hits = hits.setdefault(accession, [])
                    if query not in accession_hits:
                        accession_hits.append(query)
            accessions = list(records)[:max_candidates_per_variant]
            file_evidence: dict[str, dict[str, Any]] = {}
            for accession in accessions[:enrich_projects]:
                try:
                    records[accession] = client.get_project(accession)
                except Exception:  # pragma: no cover - network boundary
                    pass
                try:
                    file_result = list_project_files_paginated_with_state(
                        client,
                        accession,
                        mode="budgeted",
                        max_files=100,
                    )
                    file_evidence[accession] = _file_inventory_bundle(
                        file_result.records,
                        file_result.state,
                    )
                except Exception:  # pragma: no cover - network boundary
                    file_evidence[accession] = _file_inventory_bundle(
                        [],
                        PridePaginationState.unavailable(
                            operation="list_project_files",
                            mode="budgeted",
                            project_accession=accession,
                            page_size=100,
                            stop_reason="error",
                        ),
                    )
            for accession in accessions:
                candidates.append(
                    {
                        "scenario_id": scenario.id,
                        "variant_id": variant.id,
                        "project_accession": accession,
                        "matched_queries": hits.get(accession, []),
                        **_project_metadata(records[accession]),
                        **file_evidence.get(
                            accession,
                            _file_inventory_bundle(
                                [],
                                PridePaginationState.unavailable(
                                    operation="list_project_files",
                                    mode="budgeted",
                                    project_accession=accession,
                                    page_size=100,
                                    stop_reason="not_inspected",
                                ),
                            ),
                        ),
                    }
                )
    return NeutralPoolResult(candidates=candidates, query_trace=trace)


def _project_metadata(record: dict[str, Any]) -> dict[str, Any]:
    title = _text(record.get("title") or record.get("projectTitle"))
    description = _text(record.get("projectDescription") or record.get("description"))
    species = _list_text(record.get("organisms") or record.get("species"))
    features = extract_project_features(record)
    combined = " ".join(
        [title, description, _text(record.get("keywords")), _text(record.get("experimentTypes"))]
    ).casefold()
    acquisition = (
        "dia"
        if re.search(r"\bdata[- ]independent\b|\bdia\b|swath", combined)
        else "dda"
        if re.search(r"\bdata[- ]dependent\b|\bdda\b", combined)
        else None
    )
    labeling = (
        "label_free"
        if re.search(r"label[- ]free|lfq", combined)
        else "tmt"
        if re.search(r"\btmt\b|tandem mass tag", combined)
        else "itraq"
        if re.search(r"\bitraq\b", combined)
        else None
    )
    observed = [title, description, *species, *features.instrument_names]
    completeness = sum(bool(value) for value in observed) / max(1, len(observed))
    return {
        "project_title": title,
        "project_description": description,
        "species": species,
        "acquisition_mode": acquisition,
        "labeling_strategy": labeling,
        "instrument_families": features.instrument_families,
        "fragmentation_methods": features.fragmentation_methods,
        "validity_status": "needs_review",
        "evidence_completeness": round(completeness, 3),
        "selected_file_count": 0,
    }


def _accession(record: dict[str, Any]) -> str:
    return str(record.get("accession") or record.get("projectAccession") or "").strip().upper()


def _file_bundle(files: Sequence[dict[str, Any]]) -> dict[str, Any]:
    roles: Counter[str] = Counter()
    types: Counter[str] = Counter()
    for item in files:
        name = str(item.get("fileName") or item.get("name") or "")
        decision = classify_file_role(name)
        roles[decision.role] += 1
        types[str(decision.file_type or "unknown")] += 1
    raw_count = roles["raw_acquisition"] + roles["converted_peaklist"]
    return {
        "selected_file_count": len(files),
        "file_role_counts": dict(roles),
        "file_type_counts": dict(types),
        "task_readiness_counts": {},
        "missing_task_requirements": [],
        "paired_raw_and_results": bool(raw_count and roles["search_result"]),
    }


def _file_inventory_bundle(
    files: Sequence[dict[str, Any]],
    state: PridePaginationState,
) -> dict[str, Any]:
    return {
        **_file_bundle(files),
        **state.to_prefixed_dict("file_inventory"),
    }


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, dict):
        return " ".join(
            _text(value.get(key)) for key in ("name", "value", "description") if value.get(key)
        ).strip()
    if isinstance(value, list):
        return " ".join(filter(None, (_text(item) for item in value)))
    return str(value)


def _list_text(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value]
    result: list[str] = []
    for item in items:
        text = _text(item).strip()
        if text and text.casefold() not in {value.casefold() for value in result}:
            result.append(text)
    return result
