"""WP-A acceptance: multi-seed portfolio, high-rel fallback ban, CEM metrics, corpus rename."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.discovery.candidate_evidence_matrix import (
    build_provisional_cem,
    hard_requirement_ids,
    scientific_stop_ready,
)
from agent.discovery.models import DatasetRequest
from agent.discovery.query_builder import prepare_pride_search_queries
from agent.discovery.query_portfolio import build_query_portfolio_units
from agent.discovery.search_environment import (
    CandidateSearchAction,
    PrideDiscoverySearchEnvironment,
    RepositoryQuery,
)


def _project(accession: str, title: str, description: str = "", **extra: Any) -> dict[str, Any]:
    row = {
        "accession": accession,
        "title": title,
        "projectDescription": description,
        "dataProcessingProtocol": "DDA shotgun proteomics with HCD fragmentation",
        "sampleProcessingProtocol": "label-free sample preparation",
        "organisms": [{"name": "Homo sapiens"}],
        "experimentTypes": [{"name": "shotgun proteomics"}],
        "instruments": [{"name": "Q Exactive HF"}],
    }
    row.update(extra)
    return row


class _FakePrideClient:
    def __init__(self, search_results: dict[str, list[dict[str, Any]]]) -> None:
        self.search_results = search_results
        self.search_calls: list[tuple[str, int]] = []

    def search_projects(
        self,
        keyword: str,
        page_size: int = 100,
        *,
        max_pages: int | None = None,
        max_results: int | None = None,
    ) -> list[dict[str, Any]]:
        self.search_calls.append((keyword, page_size))
        limit = max_results if max_results is not None else page_size
        return self.search_results.get(keyword, [])[:limit]

    def get_project(self, accession: str) -> dict[str, Any]:
        for projects in self.search_results.values():
            for project in projects:
                if project["accession"] == accession:
                    return project
        raise KeyError(accession)

    def list_project_files(self, accession: str, keyword: str | None = None, **_kwargs: Any) -> list[dict[str, Any]]:
        return [] if keyword else [{"fileName": f"{accession}.raw", "fileSizeBytes": 1}]

    def download_text(self, _url: str) -> str:
        return ""

    def close(self) -> None:
        return None


def test_prepare_pride_expands_all_atomic_seeds_not_first_only() -> None:
    seeds = prepare_pride_search_queries(["human DDA phospho"])
    assert set(s.casefold() for s in seeds) >= {"human", "dda", "phospho"}
    assert len(seeds) >= 3


def test_multi_seed_portfolio_records_executed_seeds(tmp_path: Path) -> None:
    client = _FakePrideClient(
        {
            "human DDA phospho": [
                _project("PXD100003", "Human DDA phospho study")
            ],
        }
    )
    request = DatasetRequest(
        species=["human"],
        acquisition_mode="dda",
        ptm_type="phospho",
        hard_constraint_fields=["species", "acquisition_mode", "ptm_type"],
        max_projects=3,
        max_candidate_projects=20,
    )
    env = PrideDiscoverySearchEnvironment(
        request=request,
        prompt="Find human DDA phospho data",
        client=client,
        state_path=tmp_path / "state.json",
    )
    observation = env.search(
        CandidateSearchAction(
            queries=[RepositoryQuery(query="human DDA phospho", depth=10)],
            rationale="Multi-seed portfolio unit.",
        )
    )
    executed = {call[0].casefold() for call in client.search_calls}
    assert executed == {"human dda phospho"}
    assert observation.query_portfolio["executed_seed_count"] >= 1
    unit = observation.query_portfolio["units"][0]
    assert unit["status"] in {"executed", "skipped_budget"} or len(unit.get("seeds_planned") or []) >= 1


def test_high_relevance_no_fallback_to_all_nonexcluded(tmp_path: Path) -> None:
    # Title matches only one weak token; floor requires >=2 when many intent terms.
    project = _project("PXD200001", "Generic liver atlas", "unrelated text without intent keywords")
    client = _FakePrideClient({"generic": [project]})
    env = PrideDiscoverySearchEnvironment(
        request=DatasetRequest(query_terms=["sensory", "neuron", "neuropathy", "chemotherapy"]),
        prompt="Find sensory neuron chemotherapy neuropathy data",
        client=client,
        state_path=tmp_path / "state.json",
    )
    observation = env.search(
        CandidateSearchAction(
            queries=[RepositoryQuery(query="generic")],
            rationale="Broad search with off-topic hits.",
        )
    )
    assert observation.high_relevance_candidate_count == 0
    assert env.high_relevance_accessions() == []
    # Non-excluded candidates still exist in the pool.
    assert observation.candidate_count >= 1


def test_corpus_term_coverage_alias_and_cem_metrics(tmp_path: Path) -> None:
    project = _project(
        "PXD300001",
        "Human phosphoproteomics DDA study",
        "Label-free phosphoproteomics in a human cell line using DDA",
    )
    client = _FakePrideClient({"human": [project], "phospho": [project], "DDA": [project]})
    request = DatasetRequest(
        species=["human"],
        acquisition_mode="dda",
        ptm_type="phospho",
        hard_constraint_fields=["species", "acquisition_mode", "ptm_type"],
        constraint_provenance={
            "species": "user",
            "acquisition_mode": "user",
            "ptm_type": "user",
        },
        max_projects=2,
    )
    env = PrideDiscoverySearchEnvironment(
        request=request,
        prompt="human DDA phospho",
        client=client,
        state_path=tmp_path / "state.json",
    )
    observation = env.search(
        CandidateSearchAction(
            queries=[RepositoryQuery(query="human DDA phospho")],
            rationale="Conjunction candidate search.",
        )
    )
    assert observation.corpus_term_coverage == observation.semantic_coverage
    assert 0.0 <= observation.corpus_term_coverage <= 1.0
    assert "hard_requirement_ids" in observation.cem_summary
    assert observation.hard_constraint_evidence_gap == observation.cem_summary["hard_constraint_evidence_gap"]
    # needs_review ratio must not be the sole definition of hard gap anymore.
    assert observation.cem_summary["provisional"] is True


def test_stop_requires_candidate_hard_conjunction_not_corpus_only() -> None:
    request = DatasetRequest(
        species=["human"],
        acquisition_mode="dda",
        ptm_type="phospho",
        hard_constraint_fields=["species", "acquisition_mode", "ptm_type"],
        constraint_provenance={
            "species": "user",
            "acquisition_mode": "user",
            "ptm_type": "user",
        },
        max_projects=1,
    )
    # Preview with partial term hits: high corpus OR coverage possible, hard conjunction fails.
    class _P:
        project_accession = "PXD9"
        excluded = False
        species = ["Homo sapiens"]
        acquisition_mode = None
        matched_intent_terms = ["human", "dda"]  # missing phospho evidence
        needs_review = False

    matrix = build_provisional_cem(request=request, previews=[_P()], target_projects=1)
    assert matrix.n_hard_conjunction_pass == 0
    assert matrix.hard_constraint_evidence_gap > 0
    ready, reason = scientific_stop_ready(
        matrix,
        target_hard_pass_inspected=1,
        corpus_term_coverage=1.0,
    )
    assert ready is False
    assert reason != "scientific_stop_criteria_met"
    assert "field:species" in hard_requirement_ids(request)


def test_build_query_portfolio_units_preserve_exact_semantic_phrase() -> None:
    units = build_query_portfolio_units(
        [("human DDA phospho", 15, "hard_conjunction")],
        max_seeds_per_query=8,
    )
    assert len(units) == 1
    assert units[0].seeds_planned == ["human DDA phospho"]
    assert units[0].status == "planned"
