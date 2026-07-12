from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.discovery.models import DatasetRequest
from agent.discovery.search_environment import (
    CandidateInspectionAction,
    CandidateSearchAction,
    PrideDiscoverySearchEnvironment,
    RepositoryQuery,
)


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
    def __init__(self, search_results: dict[str, list[dict[str, Any]]]) -> None:
        self.search_results = search_results
        self.search_calls: list[tuple[str, int]] = []
        self.project_calls: list[str] = []
        self.projects = {
            str(project["accession"]): project
            for projects in search_results.values()
            for project in projects
        }

    def search_projects(self, keyword: str, page_size: int = 100) -> list[dict[str, Any]]:
        self.search_calls.append((keyword, page_size))
        return self.search_results.get(keyword, [])[:page_size]

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
            "sensory": [target, shared],
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

    assert client.search_calls == [("sensory", 7), ("neuropathy", 11)]
    assert observation.query_yields[0].executed_query == "sensory"
    assert observation.raw_result_count == 4
    assert observation.candidate_count == 3
    assert observation.new_candidate_count == 3
    assert observation.duplicate_count == 1
    assert observation.query_yields[1].new_candidate_count == 1
    assert observation.previews[0].project_accession == "PXD000001"
    assert "sensory" in observation.covered_intent_terms
    assert "chemotherapy" in observation.covered_intent_terms
    assert observation.semantic_coverage > 0.0
    assert (tmp_path / "candidate_state.json").is_file()


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
    assert observation.high_relevance_candidate_count == 0


def test_agent_translated_query_supplies_semantic_terms_for_chinese_prompt(tmp_path: Path) -> None:
    project = _project(
        "PXD000010",
        "Human phosphoproteomics DDA study",
        "Label-free phosphoproteomics in a human cell line",
    )
    environment = PrideDiscoverySearchEnvironment(
        request=DatasetRequest(repository="pride"),
        prompt="寻找适合模型训练的人类磷酸化蛋白质组数据",
        client=_FakePrideClient({"human": [project]}),
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
    client = _FakePrideClient({"human": [project]})
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
            queries=[RepositoryQuery(query="human chemotherapy neuron", depth=20)],
            rationale="A different phrase that compiles to the same PRIDE seed.",
        )
    )
    deeper = environment.search(
        CandidateSearchAction(
            queries=[RepositoryQuery(query="human iPSC neuron", depth=30)],
            rationale="Increase depth for the shared repository seed.",
        )
    )

    assert first.query_yields[0].executed_query == "human"
    assert repeated.query_yields[0].skipped_reason is not None
    assert deeper.query_yields[0].skipped_reason is None
    assert client.search_calls == [("human", 20), ("human", 30)]
