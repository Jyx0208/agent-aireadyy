from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent.discovery.models import DatasetRequest
from agent.discovery.query_portfolio import build_query_portfolio_units
from agent.discovery.search_environment import (
    CandidateInspectionAction,
    CandidateSearchAction,
    PrideDiscoverySearchEnvironment,
    RepositoryQuery,
    _extract_candidate_terms,
)
from agent.pride.client import PrideClient


def test_deep_repository_query_preserves_depth_in_query_portfolio() -> None:
    query = RepositoryQuery(
        query="immunopeptidomics",
        depth=200,
        intent_dimension="scientific_theme",
        budget_role="primary_theme",
    )

    units = build_query_portfolio_units([query])

    assert len(units) == 1
    assert units[0].depth == 200


def _project(accession: str, title: str, description: str = "") -> dict[str, Any]:
    return {
        "accession": accession,
        "title": title,
        "projectDescription": description,
        "dataProcessingProtocol": "DDA shotgun proteomics with HCD fragmentation",
        "sampleProcessingProtocol": "label-free sample preparation",
        "organisms": [{"name": "Homo sapiens"}],
        "experimentTypes": [{"name": "shotgun proteomics"}],
        "instruments": [{"name": "Q Exactive HF"}],
    }


def _raw_file(accession: str) -> dict[str, Any]:
    return {
        "fileName": f"{accession}_sample.raw",
        "fileSizeBytes": 1_000,
        "publicFileLocations": [{"value": f"https://example.test/{accession}.raw"}],
    }


class _FakePrideClient:
    def __init__(
        self,
        search_results: dict[str, list[dict[str, Any]]],
        *,
        search_failures: dict[str, Exception] | None = None,
    ) -> None:
        self.search_results = search_results
        self.search_failures = search_failures or {}
        self.search_calls: list[tuple[str, int]] = []
        self.search_options: list[dict[str, int | None]] = []
        self.project_calls: list[str] = []
        self.projects = {
            str(project["accession"]): project
            for projects in search_results.values()
            for project in projects
        }

    def search_projects(
        self,
        keyword: str,
        page_size: int = 100,
        *,
        max_pages: int | None = None,
        max_results: int | None = None,
    ) -> list[dict[str, Any]]:
        self.search_calls.append((keyword, page_size))
        self.search_options.append(
            {"max_pages": max_pages, "max_results": max_results}
        )
        if keyword in self.search_failures:
            raise self.search_failures[keyword]
        limit = max_results if max_results is not None else page_size
        return self.search_results.get(keyword, [])[:limit]

    def get_project(self, accession: str) -> dict[str, Any]:
        self.project_calls.append(accession)
        return self.projects[accession]

    def list_project_files(
        self,
        accession: str,
        keyword: str | None = None,
        **_kwargs: Any,
    ) -> list[dict[str, Any]]:
        return [] if keyword else [_raw_file(accession)]

    def download_text(self, _url: str) -> str:
        return ""

    def close(self) -> None:
        return None


class _PagedFakePrideClient(_FakePrideClient):
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        super().__init__({"immunopeptidomics": rows})
        self.start_pages: list[int] = []

    def search_projects(
        self,
        keyword: str,
        page_size: int = 100,
        *,
        max_pages: int | None = None,
        max_results: int | None = None,
        start_page: int = 0,
        on_page: Any = None,
    ) -> list[dict[str, Any]]:
        self.start_pages.append(start_page)
        source = self.search_results.get(keyword, [])
        collected: list[dict[str, Any]] = []
        for page in range(start_page, start_page + int(max_pages or 1)):
            batch = source[page * page_size : (page + 1) * page_size]
            if not batch:
                break
            collected.extend(batch)
            if on_page is not None:
                on_page(page + 1, len(batch), len(collected))
            if len(batch) < page_size:
                break
        return collected[: int(max_results or len(collected))]


def _request() -> DatasetRequest:
    return DatasetRequest(
        goal="general",
        query_terms=["sensory neuron", "chemotherapy neuropathy"],
        species=["Homo sapiens"],
        species_policy="include_only",
        acquisition_mode="dda",
        labeling_strategy="label_free",
        max_projects=5,
        max_files=10,
        max_candidate_projects=20,
        max_files_per_project=5,
    )


def test_search_observation_reports_query_yield_depth_duplicates_and_coverage(tmp_path: Path) -> None:
    target = _project(
        "PXD000001",
        "Human sensory neuron chemotherapy neuropathy proteome",
    )
    shared = _project("PXD000002", "Human neuron proteomics")
    other = _project("PXD000003", "Unrelated human liver proteomics")
    client = _FakePrideClient(
        {
            "sensory neuron": [target, shared],
            "neuropathy": [shared, other],
        }
    )
    environment = PrideDiscoverySearchEnvironment(
        request=_request(),
        prompt="Find human sensory neuron data for chemotherapy-induced neuropathy.",
        client=client,
        state_path=tmp_path / "candidate_state.json",
    )

    observation = environment.search(
        CandidateSearchAction(
            queries=[
                RepositoryQuery(query="sensory neuron", depth=7, intent_dimension="cell model"),
                RepositoryQuery(query="neuropathy", depth=11, intent_dimension="disease context"),
            ],
            candidate_limit=10,
            rationale="Cover the cell model and disease context.",
        )
    )

    # Repository themes remain exact phrases; their terms are not atomized.
    assert ("sensory neuron", 100) in client.search_calls
    assert ("neuropathy", 100) in client.search_calls
    assert client.search_calls[0] == ("sensory neuron", 100)
    assert observation.query_yields[0].executed_query == "sensory neuron"
    assert observation.raw_result_count >= 4
    assert observation.candidate_count == 3
    assert observation.new_candidate_count == 3
    assert observation.duplicate_count >= 1
    assert any(y.executed_query == "neuropathy" for y in observation.query_yields)
    assert observation.query_portfolio.get("executed_seed_count", 0) >= 2
    assert observation.previews[0].project_accession == "PXD000001"
    assert "sensory" in observation.covered_intent_terms
    assert "chemotherapy" in observation.covered_intent_terms
    assert observation.semantic_coverage > 0.0
    assert (tmp_path / "candidate_state.json").is_file()


def test_search_emits_structured_query_and_page_progress(tmp_path: Path) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    client = _FakePrideClient(
        {"neuropathy": [_project("PXD000010", "Human neuropathy proteomics")]}
    )
    environment = PrideDiscoverySearchEnvironment(
        request=_request(),
        prompt="Find human neuropathy proteomics.",
        client=client,
        state_path=tmp_path / "candidate_state.json",
        search_event=lambda event_type, payload: events.append((event_type, payload)),
    )

    environment.search(
        CandidateSearchAction(
            queries=[RepositoryQuery(query="neuropathy", depth=11)],
            candidate_limit=10,
            rationale="Exercise live structured repository progress.",
        )
    )

    assert [event_type for event_type, _payload in events] == [
        "repository_query_started",
        "repository_query_page_completed",
        "repository_query_completed",
    ]
    assert events[1][1]["page"] == 1
    assert events[1][1]["cumulative_count"] == 1
    assert events[2][1]["new_candidate_count"] == 1


def test_exact_accession_hit_is_pinned_when_broad_results_fill_the_candidate_pool(
    tmp_path: Path,
) -> None:
    exact = _project("PXD055544", "Exact project with sparse indexed metadata")
    broad = [
        _project(
            f"PXD09{index:04d}",
            f"Human sensory neuron chemotherapy neuropathy project {index}",
        )
        for index in range(4)
    ]
    request = _request().model_copy(
        update={"max_projects": 1, "max_candidate_projects": 2}
    )
    state_path = tmp_path / "candidate_state.json"
    client = _FakePrideClient({"PXD055544": [exact], "sensory": broad})
    environment = PrideDiscoverySearchEnvironment(
        request=request,
        prompt="Find human sensory neuron chemotherapy neuropathy data.",
        client=client,
        state_path=state_path,
    )

    observation = environment.search(
        CandidateSearchAction(
            queries=[
                RepositoryQuery(query="PXD055544", depth=5),
                RepositoryQuery(query="sensory neuron", depth=20),
            ],
            candidate_limit=2,
            rationale="Inspect an exact user-requested accession beside a broad search.",
        )
    )

    assert "PXD055544" in environment.candidate_accessions
    assert "PXD055544" in [item.project_accession for item in observation.previews]

    reloaded = PrideDiscoverySearchEnvironment(
        request=request,
        prompt="Find human sensory neuron chemotherapy neuropathy data.",
        client=client,
        state_path=state_path,
    )
    assert "PXD055544" in reloaded.candidate_accessions


def test_intent_terms_drop_accessions_and_generic_workflow_words() -> None:
    terms = _extract_candidate_terms(
        "Inspect PXD055544 as a human immunopeptidomics candidate. "
        "Verify matched SDRF assay evidence at file level, retain only "
        "delivery-eligible assets, and explain the judgment."
    )

    assert "pxd055544" not in terms
    assert "and" not in terms
    assert "the" not in terms
    assert "file" not in terms
    assert "immunopeptidomics" in terms
    assert "sdrf" in terms
    assert "assay" in terms
    assert "delivery-eligible" in terms


def test_search_environment_requests_multiple_pride_pages_for_each_new_seed(tmp_path: Path) -> None:
    project = _project("PXD000001", "Human sensory neuron proteomics")
    client = _FakePrideClient({"sensory neuron": [project]})
    environment = PrideDiscoverySearchEnvironment(
        request=_request(),
        prompt="Find human sensory neuron data.",
        client=client,
        state_path=tmp_path / "candidate_state.json",
    )

    environment.search(
        CandidateSearchAction(
            queries=[RepositoryQuery(query="sensory neuron", depth=20)],
            rationale="Search beyond the first PRIDE result page.",
        )
    )

    assert client.search_options[0] == {"max_pages": 1, "max_results": 20}


def test_budgeted_search_distributes_pages_across_seeds_and_preserves_inspection_capacity(
    tmp_path: Path,
) -> None:
    request = _request().model_copy(
        update={"max_projects": 2_000, "max_candidate_projects": 5_000}
    )
    seeds = [f"seed-{index}" for index in range(7)]
    client = _FakePrideClient(
        {
            seed: [_project(f"PXD{index:06d}", f"Human neuron project {index}")]
            for index, seed in enumerate(seeds, start=1)
        }
    )
    environment = PrideDiscoverySearchEnvironment(
        request=request,
        prompt="Find human stem-cell-derived sensory-neuron chemotherapy neuropathy projects.",
        client=client,
        state_path=tmp_path / "candidate_state.json",
    )

    environment.search_with_request_budget(
        CandidateSearchAction(
            queries=[RepositoryQuery(query=seed, depth=50) for seed in seeds],
            candidate_limit=1_000,
            rationale="Exercise the live run's seven-seed search shape.",
        ),
        request_budget=20,
    )

    allocated_pages = [int(options["max_pages"] or 0) for options in client.search_options]
    assert len(allocated_pages) == len(seeds)
    assert all(pages >= 1 for pages in allocated_pages)
    assert sum(allocated_pages) <= 20


def test_search_reports_total_repository_outage_as_operational_failure(
    tmp_path: Path,
) -> None:
    client = _FakePrideClient(
        {},
        search_failures={
            "sensory": ConnectionError("PRIDE unavailable for sensory seed"),
            "neuropathy": TimeoutError("PRIDE timed out for neuropathy seed"),
        },
    )
    environment = PrideDiscoverySearchEnvironment(
        request=_request(),
        prompt="Find human sensory neuron chemotherapy neuropathy data.",
        client=client,
        state_path=tmp_path / "candidate_state.json",
    )

    observation = environment.search(
        CandidateSearchAction(
            queries=[
                RepositoryQuery(query="sensory"),
                RepositoryQuery(query="neuropathy"),
            ],
            rationale="Exercise a complete PRIDE search outage.",
        )
    )

    assert observation.status == "failed"
    assert observation.stop_reason == "all_repository_search_attempts_failed"
    assert observation.recommended_action == "retry_repository_or_stop"
    assert observation.raw_result_count == 0
    assert observation.candidate_count == 0
    assert observation.failures == [
        "sensory: PRIDE unavailable for sensory seed",
        "neuropathy: PRIDE timed out for neuropathy seed",
    ]
    assert [failure.model_dump() for failure in observation.operational_failures] == [
        {
            "query": "sensory",
            "executed_query": "sensory",
            "intent_dimension": "general",
            "requested_depth": 20,
            "error_type": "ConnectionError",
            "message": "PRIDE unavailable for sensory seed",
        },
        {
            "query": "neuropathy",
            "executed_query": "neuropathy",
            "intent_dimension": "general",
            "requested_depth": 20,
            "error_type": "TimeoutError",
            "message": "PRIDE timed out for neuropathy seed",
        },
    ]
    assert [item.error for item in observation.query_yields] == [
        "PRIDE unavailable for sensory seed",
        "PRIDE timed out for neuropathy seed",
    ]


def test_search_reports_http_success_error_payload_as_invalid_response_failure(
    tmp_path: Path,
) -> None:
    class MaintenanceResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"error": "repository under maintenance"}

    class MaintenanceHTTPClient:
        def get(self, *_args: Any, **_kwargs: Any) -> MaintenanceResponse:
            return MaintenanceResponse()

    client = PrideClient(retries=1)
    client._client = MaintenanceHTTPClient()
    environment = PrideDiscoverySearchEnvironment(
        request=_request(),
        prompt="Find human sensory neuron data.",
        client=client,
        state_path=tmp_path / "candidate_state.json",
    )

    observation = environment.search(
        CandidateSearchAction(
            queries=[RepositoryQuery(query="sensory")],
            rationale="Exercise a malformed successful PRIDE response.",
        )
    )

    assert observation.status == "failed"
    assert observation.stop_reason == "all_repository_search_attempts_failed"
    assert observation.raw_result_count == 0
    assert observation.candidate_count == 0
    assert observation.operational_failures[0].error_type == (
        "PrideInvalidResponseError"
    )
    assert (
        "repository under maintenance"
        in observation.operational_failures[0].message
    )
    assert observation.query_yields[0].raw_result_count == 0
    assert "invalid PRIDE response" in (observation.query_yields[0].error or "")


def test_search_treats_successful_empty_repository_response_as_scientific_zero_yield(
    tmp_path: Path,
) -> None:
    environment = PrideDiscoverySearchEnvironment(
        request=_request(),
        prompt="Find human sensory neuron data.",
        client=_FakePrideClient({"sensory": []}),
        state_path=tmp_path / "candidate_state.json",
    )

    observation = environment.search(
        CandidateSearchAction(
            queries=[RepositoryQuery(query="sensory")],
            rationale="Exercise a legitimate empty PRIDE result set.",
        )
    )

    assert observation.status == "completed"
    assert observation.stop_reason is None
    assert observation.raw_result_count == 0
    assert observation.candidate_count == 0
    assert observation.failures == []
    assert observation.operational_failures == []
    assert observation.query_yields[0].error is None
    assert observation.query_yields[0].raw_result_count == 0


def test_search_reports_zero_request_budget_as_operationally_blocked(
    tmp_path: Path,
) -> None:
    environment = PrideDiscoverySearchEnvironment(
        request=_request(),
        prompt="Find human sensory neuron data.",
        client=_FakePrideClient({"sensory": []}),
        state_path=tmp_path / "candidate_state.json",
    )

    observation = environment.search_with_request_budget(
        CandidateSearchAction(
            queries=[RepositoryQuery(query="sensory")],
            rationale="Exercise a search budget reserved for inspection.",
        ),
        request_budget=0,
    )

    assert observation.status == "blocked"
    assert observation.stop_reason == "search_request_budget_reserved_for_inspection"
    assert observation.raw_result_count == 0
    assert observation.query_yields[0].skipped_reason == (
        "search_request_budget_reserved_for_inspection"
    )
    assert observation.operational_failures[0].error_type == "RequestBudgetExhausted"


def test_search_keeps_partial_repository_failure_auditable_when_another_seed_succeeds(
    tmp_path: Path,
) -> None:
    project = _project("PXD000001", "Human chemotherapy neuropathy proteomics")
    client = _FakePrideClient(
        {"neuropathy": [project]},
        search_failures={"sensory": ConnectionError("temporary PRIDE failure")},
    )
    environment = PrideDiscoverySearchEnvironment(
        request=_request(),
        prompt="Find human sensory neuron chemotherapy neuropathy data.",
        client=client,
        state_path=tmp_path / "candidate_state.json",
    )

    observation = environment.search(
        CandidateSearchAction(
            queries=[
                RepositoryQuery(query="sensory"),
                RepositoryQuery(query="neuropathy"),
            ],
            rationale="Exercise mixed repository failure and success.",
        )
    )

    assert observation.status == "completed"
    assert observation.stop_reason is None
    assert observation.raw_result_count == 1
    assert observation.candidate_count == 1
    assert observation.failures == ["sensory: temporary PRIDE failure"]
    assert len(observation.operational_failures) == 1
    assert observation.operational_failures[0].query == "sensory"
    assert observation.operational_failures[0].error_type == "ConnectionError"
    assert observation.operational_failures[0].message == "temporary PRIDE failure"
    assert observation.query_yields[0].error == "temporary PRIDE failure"
    assert observation.query_yields[1].raw_result_count == 1


def test_later_outage_does_not_discard_an_existing_inspectable_candidate_pool(
    tmp_path: Path,
) -> None:
    project = _project("PXD000001", "Human sensory neuron proteomics")
    client = _FakePrideClient({"sensory": [project]})
    environment = PrideDiscoverySearchEnvironment(
        request=_request(),
        prompt="Find human sensory neuron data.",
        client=client,
        state_path=tmp_path / "candidate_state.json",
    )
    first = environment.search(
        CandidateSearchAction(
            queries=[RepositoryQuery(query="sensory")],
            rationale="Create an inspectable candidate pool.",
        )
    )
    assert first.status == "completed"
    client.search_failures["neuropathy"] = ConnectionError("later PRIDE outage")

    second = environment.search(
        CandidateSearchAction(
            queries=[RepositoryQuery(query="neuropathy")],
            rationale="Exercise a later failed expansion round.",
        )
    )

    assert second.status == "completed"
    assert second.stop_reason is None
    assert second.candidate_count == 1
    assert second.failures == ["neuropathy: later PRIDE outage"]
    assert second.recommended_action == "review_candidate_previews"


def test_search_state_accumulates_new_candidates_across_actions(tmp_path: Path) -> None:
    first = _project("PXD000001", "Human sensory neuron proteome")
    second = _project("PXD000002", "Chemotherapy neuropathy proteome")
    client = _FakePrideClient({"first": [first], "second": [first, second]})
    state_path = tmp_path / "candidate_state.json"
    environment = PrideDiscoverySearchEnvironment(
        request=_request(),
        prompt="Find sensory neuron chemotherapy neuropathy data.",
        client=client,
        state_path=state_path,
    )

    environment.search(
        CandidateSearchAction(
            queries=[RepositoryQuery(query="first")],
            rationale="Initial search.",
        )
    )
    second_observation = environment.search(
        CandidateSearchAction(
            queries=[RepositoryQuery(query="second")],
            rationale="Cover the missing disease concept.",
        )
    )
    restored = PrideDiscoverySearchEnvironment(
        request=_request(),
        prompt="Find sensory neuron chemotherapy neuropathy data.",
        client=client,
        state_path=state_path,
    )

    assert second_observation.new_candidate_count == 1
    assert second_observation.candidate_count == 2
    assert restored.candidate_accessions == ["PXD000001", "PXD000002"]


def test_candidate_pool_retains_the_request_scale_instead_of_a_fixed_300(tmp_path: Path) -> None:
    projects = [
        _project(f"PXD{index:06d}", f"Human sensory neuron project {index}")
        for index in range(1, 351)
    ]
    request = _request().model_copy(update={"max_candidate_projects": 400})
    search_results = {
        f"seed-{chunk}": projects[chunk * 100 : (chunk + 1) * 100]
        for chunk in range(4)
    }
    environment = PrideDiscoverySearchEnvironment(
        request=request,
        prompt="Find as many relevant human sensory neuron projects as possible.",
        client=_FakePrideClient(search_results),
        state_path=tmp_path / "candidate_state.json",
    )

    observation = environment.search(
        CandidateSearchAction(
            queries=[
                RepositoryQuery(query=f"seed-{chunk}", depth=100)
                for chunk in range(4)
            ],
            candidate_limit=400,
            rationale="Exercise the configured candidate-pool scale.",
        )
    )

    assert observation.candidate_count == 350


def test_continuous_search_keeps_unbounded_pool_and_pages_preview(tmp_path: Path) -> None:
    projects = [
        _project(f"PXD{index:06d}", f"Human sensory neuron project {index}")
        for index in range(1, 401)
    ]
    request = _request().model_copy(
        update={
            "max_candidate_projects": 25,
            "continuous_discovery": True,
            "harvest_all_qualified": True,
        }
    )
    environment = PrideDiscoverySearchEnvironment(
        request=request,
        prompt="Find human sensory neuron projects.",
        client=_FakePrideClient(
            {
                f"seed-{chunk}": projects[chunk * 100 : (chunk + 1) * 100]
                for chunk in range(4)
            }
        ),
        memory=None,
        report=lambda _message: None,
        state_path=tmp_path / "candidate_state.json",
    )

    first = environment.search(
        CandidateSearchAction(
            queries=[
                RepositoryQuery(query=f"seed-{chunk}", depth=100)
                for chunk in range(4)
            ],
            candidate_limit=50,
            rationale="Build the continuous pool.",
        )
    )
    tail = environment.search(
        CandidateSearchAction(
            queries=[RepositoryQuery(query="seed-0", depth=100)],
            candidate_limit=50,
            preview_offset=350,
            rationale="Page through the persisted pool.",
        )
    )

    assert first.candidate_count == 400
    assert len(environment.candidate_accessions) == 400
    assert first.has_more_candidates is True
    assert first.next_preview_offset == 50
    assert tail.preview_offset == 350
    assert len(tail.previews) == 50
    assert tail.has_more_candidates is False


def test_inspection_only_fetches_agent_selected_candidates(tmp_path: Path) -> None:
    first = _project("PXD000001", "Human sensory neuron chemotherapy neuropathy")
    second = _project("PXD000002", "Human neuron proteomics")
    client = _FakePrideClient({"neuron": [first, second]})
    environment = PrideDiscoverySearchEnvironment(
        request=_request(),
        prompt="Find sensory neuron chemotherapy neuropathy data.",
        client=client,
        state_path=tmp_path / "candidate_state.json",
    )
    search = environment.search(
        CandidateSearchAction(
            queries=[RepositoryQuery(query="neuron")],
            rationale="Find candidate projects.",
        )
    )

    result = environment.inspect(
        CandidateInspectionAction(
            search_id=search.search_id,
            accessions=["PXD000001"],
            rationale="This candidate covers the complete intent.",
        )
    )

    assert client.project_calls == ["PXD000001"]
    assert [project.project_accession for project in result.manifest.projects] == ["PXD000001"]
    assert result.inspected_accessions == ["PXD000001"]
    assert result.usable_files == 1


def test_inspection_outcomes_separate_failures_exclusions_and_empty_projects(
    tmp_path: Path,
) -> None:
    usable = _project("PXD000001", "Human sensory neuron proteomics")
    failed = _project("PXD000002", "Human sensory neuron repository failure")
    excluded = _project("PXD000003", "Mouse sensory neuron proteomics")
    excluded["organisms"] = [{"name": "Mus musculus"}]
    empty = _project("PXD000004", "Human sensory neuron metadata only")

    class OutcomeClient(_FakePrideClient):
        def get_project(self, accession: str) -> dict[str, Any]:
            if accession == "PXD000002":
                self.project_calls.append(accession)
                raise ConnectionError("temporary PRIDE outage")
            return super().get_project(accession)

        def list_project_files(
            self,
            accession: str,
            keyword: str | None = None,
            **kwargs: Any,
        ) -> list[dict[str, Any]]:
            if accession == "PXD000004":
                return []
            return super().list_project_files(accession, keyword=keyword, **kwargs)

    client = OutcomeClient({"sensory": [usable, failed, excluded, empty]})
    request = _request().model_copy(
        update={"hard_constraint_fields": ["repository", "species"]}
    )
    environment = PrideDiscoverySearchEnvironment(
        request=request,
        prompt="Find human sensory neuron data.",
        client=client,
        state_path=tmp_path / "candidate_state.json",
    )
    search = environment.search(
        CandidateSearchAction(
            queries=[RepositoryQuery(query="sensory")],
            rationale="Find all inspection outcome types.",
        )
    )

    result = environment.inspect(
        CandidateInspectionAction(
            search_id=search.search_id,
            accessions=["PXD000001", "PXD000002", "PXD000003", "PXD000004"],
            rationale="Classify each requested candidate exactly once.",
        )
    )

    assert result.eligible_accessions == ["PXD000001"]
    assert result.failed_accessions == ["PXD000002"]
    assert result.excluded_accessions == ["PXD000003"]
    assert result.no_usable_files_accessions == ["PXD000004"]
    assert result.inspected_accessions == ["PXD000001", "PXD000003", "PXD000004"]
    categories = {
        outcome.project_accession: outcome.category
        for outcome in result.inspection_outcomes
    }
    assert categories == {
        "PXD000001": "usable_files",
        "PXD000002": "inspection_failure",
        "PXD000003": "scientific_exclusion",
        "PXD000004": "no_usable_files",
    }
    assert result.manifest.summary["inspection_outcome_counts"] == {
        "inspection_failure": 1,
        "no_usable_files": 1,
        "scientific_exclusion": 1,
        "usable_files": 1,
    }
    assert result.manifest.summary["search_environment"]["failed_accessions"] == [
        "PXD000002"
    ]


def test_inspection_project_parse_failure_is_a_failed_accession(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _project("PXD000005", "Human sensory neuron malformed metadata")
    client = _FakePrideClient({"sensory": [project]})
    environment = PrideDiscoverySearchEnvironment(
        request=_request(),
        prompt="Find human sensory neuron data.",
        client=client,
        state_path=tmp_path / "candidate_state.json",
    )
    search = environment.search(
        CandidateSearchAction(
            queries=[RepositoryQuery(query="sensory")],
            rationale="Find a candidate with malformed inspection metadata.",
        )
    )

    def fail_to_score(_project_record: dict[str, Any], _request: DatasetRequest):
        raise ValueError("malformed project metadata")

    monkeypatch.setattr(
        "agent.discovery.pride_discovery.score_project",
        fail_to_score,
    )

    result = environment.inspect(
        CandidateInspectionAction(
            search_id=search.search_id,
            accessions=["PXD000005"],
            rationale="Audit the parse failure without retrying a scientific exclusion.",
        )
    )

    assert result.failed_accessions == ["PXD000005"]
    assert result.excluded_accessions == []
    assert result.no_usable_files_accessions == []
    assert result.inspection_outcomes[0].category == "inspection_failure"
    assert result.inspection_outcomes[0].stage == "score_project"
    assert result.inspection_outcomes[0].reason == "parse_failure"


def test_inspection_file_parse_failure_is_not_reported_as_no_usable_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _project("PXD000008", "Human sensory neuron malformed file record")
    client = _FakePrideClient({"sensory": [project]})
    environment = PrideDiscoverySearchEnvironment(
        request=_request(),
        prompt="Find human sensory neuron data.",
        client=client,
        state_path=tmp_path / "candidate_state.json",
    )
    search = environment.search(
        CandidateSearchAction(
            queries=[RepositoryQuery(query="sensory")],
            rationale="Find a candidate with a malformed file record.",
        )
    )

    def fail_to_extract_file(*_args: Any, **_kwargs: Any):
        raise ValueError("malformed file metadata")

    monkeypatch.setattr(
        "agent.discovery.pride_discovery.extract_file_features",
        fail_to_extract_file,
    )

    result = environment.inspect(
        CandidateInspectionAction(
            search_id=search.search_id,
            accessions=["PXD000008"],
            rationale="Keep technical parse failures separate from empty projects.",
        )
    )

    assert result.failed_accessions == ["PXD000008"]
    assert result.no_usable_files_accessions == []
    assert result.inspection_outcomes[0].category == "inspection_failure"
    assert result.inspection_outcomes[0].stage == "score_files"
    assert result.inspection_outcomes[0].reason == "parse_failure"


def test_no_usable_files_reports_role_and_filter_reason_counts(
    tmp_path: Path,
) -> None:
    project = _project("PXD000009", "Human sensory neuron mixed acquisition")

    class FilterReasonClient(_FakePrideClient):
        def list_project_files(
            self,
            accession: str,
            keyword: str | None = None,
            **_kwargs: Any,
        ) -> list[dict[str, Any]]:
            if keyword:
                return []
            return [
                {
                    "fileName": "sample_DIA.raw",
                    "fileSizeBytes": 1_000,
                    "publicFileLocations": [
                        {"value": "https://example.test/sample_DIA.raw"}
                    ],
                },
                {
                    "fileName": "peptides.csv",
                    "fileSizeBytes": 200,
                    "publicFileLocations": [
                        {"value": "https://example.test/peptides.csv"}
                    ],
                },
            ]

    request = _request().model_copy(
        update={"hard_constraint_fields": ["repository", "acquisition_mode"]}
    )
    environment = PrideDiscoverySearchEnvironment(
        request=request,
        prompt="Find human DDA sensory neuron data.",
        client=FilterReasonClient({"sensory": [project]}),
        state_path=tmp_path / "candidate_state.json",
    )
    search = environment.search(
        CandidateSearchAction(
            queries=[RepositoryQuery(query="sensory")],
            rationale="Find a candidate with explainable file filtering.",
        )
    )

    result = environment.inspect(
        CandidateInspectionAction(
            search_id=search.search_id,
            accessions=["PXD000009"],
            rationale="Explain every file removed by the hard DDA filter.",
        )
    )

    outcome = result.inspection_outcomes[0]
    assert outcome.category == "no_usable_files"
    assert outcome.raw_file_count == 2
    assert outcome.file_role_counts == {
        "raw_acquisition": 1,
        "report_table": 1,
    }
    assert outcome.filter_reason_counts == {
        "acquisition_hard_constraint_conflict": 1,
        "unsupported_file_role:report_table": 1,
    }


def test_inspection_counts_eligible_projects_even_when_selection_limit_drops_one(
    tmp_path: Path,
) -> None:
    first = _project("PXD000006", "Human sensory neuron proteomics A")
    second = _project("PXD000007", "Human sensory neuron proteomics B")
    client = _FakePrideClient({"sensory": [first, second]})
    environment = PrideDiscoverySearchEnvironment(
        request=_request().model_copy(update={"max_projects": 1}),
        prompt="Find human sensory neuron data.",
        client=client,
        state_path=tmp_path / "candidate_state.json",
    )
    search = environment.search(
        CandidateSearchAction(
            queries=[RepositoryQuery(query="sensory")],
            rationale="Find more eligible projects than the selection limit.",
        )
    )

    result = environment.inspect(
        CandidateInspectionAction(
            search_id=search.search_id,
            accessions=["PXD000006", "PXD000007"],
            rationale="Inspect both projects before diversity selection.",
        )
    )

    assert len(result.manifest.projects) == 1
    assert result.eligible_accessions == ["PXD000006", "PXD000007"]
    assert result.inspected_accessions == ["PXD000006", "PXD000007"]
    assert result.failed_accessions == []
    assert result.manifest.summary["inspection_outcome_counts"] == {
        "usable_files": 2
    }


def test_inspection_rejects_accessions_outside_persisted_candidate_pool(tmp_path: Path) -> None:
    project = _project("PXD000001", "Human neuron proteomics")
    environment = PrideDiscoverySearchEnvironment(
        request=_request(),
        prompt="Find neuron data.",
        client=_FakePrideClient({"neuron": [project]}),
        state_path=tmp_path / "candidate_state.json",
    )
    search = environment.search(
        CandidateSearchAction(
            queries=[RepositoryQuery(query="neuron")],
            rationale="Find candidates.",
        )
    )

    try:
        environment.inspect(
            CandidateInspectionAction(
                search_id=search.search_id,
                accessions=["PXD999999"],
                rationale="Unknown candidate.",
            )
        )
    except ValueError as exc:
        assert "candidate pool" in str(exc)
    else:
        raise AssertionError("expected inspection outside candidate pool to fail")


def test_off_topic_candidates_leave_intent_terms_unresolved(tmp_path: Path) -> None:
    client = _FakePrideClient(
        {"generic": [_project("PXD000003", "Human liver proteomics atlas")]}
    )
    environment = PrideDiscoverySearchEnvironment(
        request=_request(),
        prompt="Find sensory neuron chemotherapy neuropathy data.",
        client=client,
        state_path=tmp_path / "candidate_state.json",
    )

    observation = environment.search(
        CandidateSearchAction(
            queries=[RepositoryQuery(query="generic")],
            rationale="Test a broad search.",
        )
    )

    assert "sensory" in observation.unresolved_intent_terms
    assert "neuropathy" in observation.unresolved_intent_terms
    # Off-topic project may match structured species tokens, but must not meet multi-term floor
    # via the banned all-nonexcluded fallback (count reflects true floor matches only).
    floor = min(2, max(1, len(observation.intent_terms)))
    true_high = sum(
        (not p.excluded) and len(p.matched_intent_terms) >= floor for p in observation.previews
    )
    assert observation.high_relevance_candidate_count == true_high


def test_agent_translated_query_supplies_semantic_terms_for_chinese_prompt(tmp_path: Path) -> None:
    project = _project(
        "PXD000010",
        "Human phosphoproteomics DDA study",
        "Label-free phosphoproteomics in a human cell line",
    )
    environment = PrideDiscoverySearchEnvironment(
        request=DatasetRequest(repository="pride"),
        prompt="寻找适合模型训练的人类磷酸化蛋白质组数据",
        client=_FakePrideClient({"human phosphoproteomics DDA": [project]}),
        state_path=tmp_path / "candidate_state.json",
    )

    observation = environment.search(
        CandidateSearchAction(
            queries=[
                RepositoryQuery(
                    query="human phosphoproteomics DDA",
                    intent_dimension="human phosphoproteomics acquisition",
                )
            ],
            rationale="Translate the user's biological intent into repository terms.",
        )
    )

    assert "phosphoproteomics" in observation.intent_terms
    assert "phosphoproteomics" in observation.covered_intent_terms
    assert observation.semantic_coverage > 0.0


def test_repository_seed_is_not_repeated_without_greater_depth(tmp_path: Path) -> None:
    project = _project("PXD000011", "Human sensory neuron proteomics")
    client = _FakePrideClient({"human sensory neuron": [project]})
    environment = PrideDiscoverySearchEnvironment(
        request=_request(),
        prompt="Find human sensory neuron data.",
        client=client,
        state_path=tmp_path / "candidate_state.json",
    )

    first = environment.search(
        CandidateSearchAction(
            queries=[RepositoryQuery(query="human sensory neuron", depth=20)],
            rationale="Initial semantic query.",
        )
    )
    repeated = environment.search(
        CandidateSearchAction(
            queries=[RepositoryQuery(query="human sensory neuron", depth=20)],
            rationale="Repeat the same exact repository phrase.",
        )
    )
    deeper = environment.search(
        CandidateSearchAction(
            queries=[RepositoryQuery(query="human sensory neuron", depth=30)],
            rationale="Increase depth for the same exact repository phrase.",
        )
    )

    # The full semantic phrase is the dedupe identity.
    first_executed = [y.executed_query.casefold() for y in first.query_yields if not y.skipped_reason]
    assert first_executed == ["human sensory neuron"]
    phrase_skips = [
        y for y in repeated.query_yields
        if y.executed_query.casefold() == "human sensory neuron" and y.skipped_reason
    ]
    assert phrase_skips, repeated.query_yields
    assert any(
        y.executed_query.casefold() == "human sensory neuron" and y.skipped_reason is None
        for y in deeper.query_yields
    )
    phrase_calls = [
        call for call in client.search_calls
        if call[0].casefold() == "human sensory neuron"
    ]
    assert len(phrase_calls) == 2


def test_continuous_search_has_no_pool_ceiling_and_resumes_exact_result_offset(
    tmp_path: Path,
) -> None:
    projects = [
        _project(f"PXD{index:06d}", f"Human immunopeptidomics project {index}")
        for index in range(1, 251)
    ]
    client = _PagedFakePrideClient(projects)
    events: list[tuple[str, dict[str, Any]]] = []
    request = DatasetRequest(
        repository="pride",
        query_terms=["immunopeptidomics"],
        continuous_discovery=True,
        max_candidate_projects=20,
    )
    environment = PrideDiscoverySearchEnvironment(
        request=request,
        prompt="Find as many immunopeptidomics projects as possible.",
        client=client,
        state_path=tmp_path / "candidate_state.json",
        search_event=lambda event_type, payload: events.append((event_type, payload)),
    )
    action = CandidateSearchAction(
        queries=[RepositoryQuery(query="immunopeptidomics", depth=80)],
        candidate_limit=10,
        rationale="Read the next continuous chunk.",
    )

    observations = [environment.search(action) for _ in range(5)]

    assert [item.raw_result_count for item in observations] == [80, 80, 80, 10, 0]
    assert observations[4].query_yields[0].skipped_reason == "repository_seed_exhausted"
    assert len(environment.candidate_accessions) == 250
    page_counts = [
        payload["cumulative_count"]
        for event_type, payload in events
        if event_type == "repository_query_page_completed"
    ]
    assert page_counts and max(page_counts) <= 80
    # Offset 80 resumes inside page zero; later calls advance to pages one and two.
    assert client.start_pages == [0, 0, 1, 2]


def test_continuous_search_rejects_synonym_before_primary_theme_is_exhausted(
    tmp_path: Path,
) -> None:
    request = DatasetRequest(
        repository="pride",
        query_terms=["immunopeptidomics", "HLA ligandome"],
        continuous_discovery=True,
        quota_flexibility="open_ended",
    )
    environment = PrideDiscoverySearchEnvironment(
        request=request,
        prompt="Find as many immunopeptidomics projects as possible.",
        client=_FakePrideClient({}),
        state_path=tmp_path / "candidate_state.json",
    )

    with pytest.raises(ValueError, match="expected immunopeptidomics"):
        environment.search(
            CandidateSearchAction(
                queries=[RepositoryQuery(query="HLA ligandome", depth=200)],
                candidate_limit=50,
                rationale="Try to advance too early.",
            )
        )


def test_continuous_search_skips_repeated_exhausted_theme_without_failing(
    tmp_path: Path,
) -> None:
    request = DatasetRequest(
        repository="pride",
        query_terms=["immunopeptidomics", "immunopeptidome"],
        continuous_discovery=True,
        quota_flexibility="open_ended",
    )
    environment = PrideDiscoverySearchEnvironment(
        request=request,
        prompt="Find as many immunopeptidomics projects as possible.",
        client=_FakePrideClient(
            {"immunopeptidomics": [_project("PXD000001", "Human immunopeptidomics")]}
        ),
        state_path=tmp_path / "candidate_state.json",
    )
    action = CandidateSearchAction(
        queries=[RepositoryQuery(query="immunopeptidomics", depth=200)],
        candidate_limit=50,
        rationale="Continue the primary theme.",
    )

    first = environment.search(action)
    repeated = environment.search(action)

    assert first.raw_result_count == 1
    assert repeated.query_yields[0].skipped_reason == "repository_seed_exhausted"
    assert repeated.failures == []
