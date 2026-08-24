from __future__ import annotations

from agent.discovery.models import DatasetRequest
from agent.discovery.neutral_pool import build_neutral_queries, collect_neutral_pool
from agent.discovery.replacement_evaluation import PromptVariant, ReplacementBenchmarkScenario


def _scenario() -> ReplacementBenchmarkScenario:
    return ReplacementBenchmarkScenario(
        id="rt_task",
        hidden_request=DatasetRequest(
            query_terms=["retention time prediction"],
            species=["Homo sapiens"],
            species_policy="include_only",
            acquisition_mode="dda",
        ),
        task_type="rt_prediction",
        prompt_variants=[
            PromptVariant(
                id="explicit",
                ambiguity_level="clear",
                mode="raw_prompt",
                prompt="Find human DDA data for retention-time prediction.",
                hard_constraint_fields=["species", "acquisition_mode"],
            )
        ],
    )


class FakeClient:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def search_projects(self, keyword: str, page_size: int = 100):
        self.queries.append(keyword)
        return [
            {"accession": "PXD000001", "title": "Human DDA retention study"},
            {"accession": "PXD000001", "title": "Duplicate"},
        ]

    def get_project(self, accession: str):
        return {
            "accession": accession,
            "title": "Human DDA retention study",
            "projectDescription": "Data-dependent LC-MS/MS with a 90 min gradient.",
            "organisms": [{"name": "Homo sapiens"}],
            "instruments": [{"name": "Q Exactive"}],
        }

    def list_project_files(self, _accession: str, page_size: int = 1000, max_files=None):
        return [
            {"fileName": "sample.raw"},
            {"fileName": "results.mzid"},
            {"fileName": "study.sdrf.tsv"},
        ]


def test_neutral_queries_are_fixed_task_seeds_plus_explicit_constraints() -> None:
    scenario = _scenario()
    queries = build_neutral_queries(scenario, scenario.prompt_variants[0])

    assert "retention" in queries
    assert "homo sapiens" in [query.casefold() for query in queries]
    assert "dda" in [query.casefold() for query in queries]
    assert len(queries) <= 8


def test_neutral_collector_dedupes_and_records_query_provenance() -> None:
    client = FakeClient()
    result = collect_neutral_pool([_scenario()], client, query_depth=10)

    assert len(result.candidates) == 1
    assert result.candidates[0]["project_accession"] == "PXD000001"
    assert result.candidates[0]["species"] == ["Homo sapiens"]
    assert result.candidates[0]["acquisition_mode"] == "dda"
    assert result.candidates[0]["matched_queries"] == client.queries
    assert result.candidates[0]["paired_raw_and_results"] is True
    assert result.candidates[0]["file_role_counts"]["raw_acquisition"] == 1
    assert result.candidates[0]["file_inventory_exhausted"] is True
    assert result.candidates[0]["file_inventory_truncated"] is False
    assert len(result.query_trace) == len(client.queries)
    assert all(row["pagination"]["mode"] == "budgeted" for row in result.query_trace)
    assert all(row["pagination"]["exhausted"] is True for row in result.query_trace)
    assert all(row["pagination"]["truncated"] is False for row in result.query_trace)


def test_neutral_collector_marks_candidates_outside_enrichment_as_not_inspected() -> None:
    class TwoProjectClient(FakeClient):
        def search_projects(self, keyword: str, page_size: int = 100):
            self.queries.append(keyword)
            return [
                {"accession": "PXD000001", "title": "First"},
                {"accession": "PXD000002", "title": "Second"},
            ][:page_size]

    result = collect_neutral_pool(
        [_scenario()],
        TwoProjectClient(),
        query_depth=10,
        max_candidates_per_variant=2,
        enrich_projects=1,
    )

    second = next(
        row for row in result.candidates if row["project_accession"] == "PXD000002"
    )
    assert second["selected_file_count"] == 0
    assert second["file_inventory_stop_reason"] == "not_inspected"
    assert second["file_inventory_exhausted"] is False
    assert second["file_inventory_truncated"] is True
    assert second["file_inventory_project_accession"] == "PXD000002"
