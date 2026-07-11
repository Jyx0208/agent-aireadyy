from __future__ import annotations

from agent.discovery.models import DatasetRequest
from agent.discovery.runtime_evaluation import (
    DiscoveryBenchmarkScenario,
    DiscoveryRuntimeResult,
    compare_runtime_pairs,
    result_from_record,
)


def _scenario(scenario_id: str = "case") -> DiscoveryBenchmarkScenario:
    return DiscoveryBenchmarkScenario(
        id=scenario_id,
        prompt="Find human DDA data",
        request=DatasetRequest(
            species=["Homo sapiens"],
            canonical_species=["human"],
            species_policy="include_only",
            acquisition_mode="dda",
            labeling_strategy="label_free",
        ),
        expected_project_accessions=["PXD000001"],
    )


def _record(*, accession: str = "PXD000001", files: list[dict] | None = None) -> dict:
    return {
        "status": "completed",
        "summary": {"agentic": {"enabled": True, "rounds": 2}},
        "projects": [{"project_accession": accession}],
        "files": files
        if files is not None
        else [
            {
                "project_accession": accession,
                "validity_status": "valid",
                "task_readiness_status": "ready",
                "species": ["Homo sapiens"],
                "acquisition_mode": "dda",
                "labeling_strategy": "label_free",
            }
        ],
    }


def _result(scenario_id: str, runtime: str, score: float, **updates) -> DiscoveryRuntimeResult:
    values = {
        "scenario_id": scenario_id,
        "runtime": runtime,
        "status": "completed",
        "elapsed_seconds": 1.0,
        "project_count": 1,
        "file_count": 1,
        "valid_files": 1,
        "usable_files": 1,
        "task_ready_files": 1,
        "expected_accession_recall": 1.0,
        "hard_constraint_violations": 0,
        "repository_requests": 10,
        "query_units": 1,
        "tool_calls": 1,
        "rounds": 1,
        "recovery_attempts": 0,
        "quality_score": score,
    }
    values.update(updates)
    return DiscoveryRuntimeResult.model_validate(values)


def test_result_scores_known_accession_and_precisions() -> None:
    result = result_from_record(
        scenario=_scenario(),
        runtime="workflow",
        record=_record(),
        elapsed_seconds=2.5,
        repository_requests=4,
    )

    assert result.expected_accession_recall == 1.0
    assert result.hard_constraint_violations == 0
    assert result.quality_score == 1.0
    assert result.repository_requests == 4


def test_more_wrong_files_do_not_create_a_quality_win() -> None:
    files = [
        {
            "project_accession": "PXD999999",
            "validity_status": "weak_keep",
            "species": ["Mus musculus"],
            "acquisition_mode": "dia",
            "labeling_strategy": "tmt",
        }
        for _ in range(20)
    ]
    result = result_from_record(
        scenario=_scenario(),
        runtime="openai_agents",
        record=_record(accession="PXD999999", files=files),
        elapsed_seconds=1.0,
    )

    assert result.file_count == 20
    assert result.expected_accession_recall == 0.0
    assert result.hard_constraint_violations == 60
    assert result.quality_score < 0.2


def test_known_labeling_conflict_is_excluded_from_discovery_selection() -> None:
    from agent.discovery.validity import assess_file_validity
    from agent.discovery.models import DiscoveredFile

    decision = assess_file_validity(
        DiscoveredFile(
            project_accession="PXD000001",
            file_name="tmt.raw",
            file_type="raw",
            acquisition_mode="dda",
            labeling_strategy="TMT",
        ),
        _scenario().request,
    )

    assert decision.status == "exclude"
    assert decision.reasons == ["labeling_hard_constraint_conflict"]


def test_three_clean_agent_wins_pass_the_improvement_gate() -> None:
    workflow = [_result(str(index), "workflow", 0.60) for index in range(3)]
    agent = [_result(str(index), "openai_agents", 0.70, repository_requests=20) for index in range(3)]

    report = compare_runtime_pairs(workflow=workflow, agent=agent)

    assert report.agent_wins == 3
    assert report.aggregate_repository_request_ratio == 2.0
    assert report.aggregate_elapsed_time_ratio == 1.0
    assert report.agent_real_improvement is True


def test_excessive_repository_cost_fails_the_gate() -> None:
    workflow = [_result(str(index), "workflow", 0.60) for index in range(3)]
    agent = [_result(str(index), "openai_agents", 0.70, repository_requests=21) for index in range(3)]

    report = compare_runtime_pairs(workflow=workflow, agent=agent)

    assert report.agent_real_improvement is False
    assert "agent repository request ratio exceeds 2.0" in report.gate_reasons


def test_workflow_llm_fallback_makes_comparison_inconclusive() -> None:
    record = _record()
    record["summary"]["agentic"] = {
        "enabled": False,
        "requested": True,
        "fallback": {"reason": "llm_unavailable"},
    }
    workflow_result = result_from_record(
        scenario=_scenario(),
        runtime="workflow",
        record=record,
        elapsed_seconds=1.0,
    )
    agent_result = _result("case", "openai_agents", 0.9)

    report = compare_runtime_pairs(workflow=[workflow_result], agent=[agent_result])

    assert workflow_result.eligible_for_comparison is False
    assert report.inconclusive is True
    assert report.pairs[0].outcome == "ineligible"


def test_mismatched_scenario_ids_are_rejected() -> None:
    workflow = [_result("a", "workflow", 0.6)]
    agent = [_result("b", "openai_agents", 0.7)]

    try:
        compare_runtime_pairs(workflow=workflow, agent=agent)
    except ValueError as exc:
        assert "same scenario ids" in str(exc)
    else:
        raise AssertionError("expected mismatched scenario ids to fail")
