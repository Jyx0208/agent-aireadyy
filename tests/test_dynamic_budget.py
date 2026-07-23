import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText
from pydantic import ValidationError

from agent.control_plane.budget_agent import run_budget_agent_review
from agent.control_plane.models import (
    AgentBudget,
    AgentRunRecord,
    BudgetDecision,
    BudgetDecisionInput,
    DynamicBudgetLimits,
    DynamicBudgetUsage,
    RoundMetrics,
    SearchGrant,
    SearchProposalInput,
    SearchProposalRecord,
)
from agent.control_plane.budget_governor import BudgetGovernor, quality_budget_tier
from agent.control_plane.discovery_metrics import evaluate_round_metrics
from agent.control_plane.store import AgentRunStore
from agent.control_plane.openai_agents import _load_agents_sdk
from agent.discovery.models import DatasetManifest, DatasetRequest, DiscoveredFile, DiscoveredProject


def test_dynamic_budget_contracts_reject_invalid_decision_shapes() -> None:
    proposal = SearchProposalInput(
        objective="Improve metadata coverage",
        reasoning_summary="Most selected files lack sample metadata.",
        evidence_refs=["metadata_gap:0.7"],
        queries=["human plasma DDA SDRF", "human plasma Orbitrap raw"],
        expected_gain_dimensions=["metadata_completeness"],
        expected_gain="More sample-level metadata",
        alternatives_considered=["broaden generic terms"],
        stop_condition="No new usable files",
    )
    assert len(proposal.queries) == 2
    limits = DynamicBudgetLimits()
    assert limits.initial_query_units == 200
    assert limits.expanded_query_units == 600
    assert limits.max_query_units == 2000
    assert limits.initial_repository_requests == 3000
    assert limits.expanded_repository_requests == 10000
    assert limits.max_repository_requests == 25000
    transport = BudgetDecisionInput(
        proposal_id="proposal_1",
        decision="stop",
        reasoning_summary="Stop without counterfactual fields",
    )
    assert transport.decision == "stop"
    with pytest.raises(ValidationError):
        BudgetDecision(
            proposal_id="proposal_1",
            decision="shrink",
            approved_query_indexes=[],
            rejected_query_indexes=[0, 1],
            reasoning_summary="Nothing approved",
        )
    with pytest.raises(ValidationError):
        BudgetDecision(
            proposal_id="proposal_1",
            decision="stop",
            reasoning_summary="Stop",
        )


def test_search_grant_is_single_use_by_contract() -> None:
    grant = SearchGrant(
        grant_id="grant_1",
        run_id="run_1",
        proposal_id="proposal_1",
        approved_queries=["human plasma DDA SDRF"],
        query_hash="abc",
        query_units=1,
    )
    assert grant.status == "issued"
    assert grant.single_use is True


def test_store_persists_and_consumes_one_use_grant_atomically(tmp_path: Path) -> None:
    store = AgentRunStore(tmp_path / "state.sqlite")
    run = store.save_run(AgentRunRecord(run_id="run_budget", workflow="discovery"))
    proposal = SearchProposalRecord(
        proposal_id="proposal_1",
        run_id=run.run_id,
        query_hash="hash_1",
        objective="Find metadata",
        reasoning_summary="Metadata is missing.",
        queries=["human plasma SDRF"],
        expected_gain="More metadata",
        stop_condition="No gain",
    )
    store.save_search_proposal(proposal)
    decision = BudgetDecision(
        proposal_id=proposal.proposal_id,
        decision="grant",
        approved_query_indexes=[0],
        reasoning_summary="The query is novel.",
    )
    store.save_budget_decision(run.run_id, decision)
    grant = SearchGrant(
        grant_id="grant_1",
        run_id=run.run_id,
        proposal_id=proposal.proposal_id,
        approved_queries=proposal.queries,
        query_hash=proposal.query_hash,
        query_units=1,
    )
    store.issue_search_grant(grant)
    assert store.load_search_grant(grant.grant_id) == grant
    consumed = store.consume_search_grant(run.run_id, grant.grant_id, grant.query_hash)
    assert consumed.status == "consumed"
    assert store.load_search_grant(grant.grant_id) == consumed
    with pytest.raises(ValueError, match="grant_already_consumed"):
        store.consume_search_grant(run.run_id, grant.grant_id, grant.query_hash)


def test_store_round_trips_search_proposals_and_budget_decisions(tmp_path: Path) -> None:
    store = AgentRunStore(tmp_path / "state.sqlite")
    run = store.save_run(AgentRunRecord(run_id="run_round_trip", workflow="discovery"))
    proposal = SearchProposalRecord(
        proposal_id="proposal_round_trip",
        run_id=run.run_id,
        query_hash="hash_round_trip",
        objective="Find sample metadata",
        reasoning_summary="Sample metadata is incomplete.",
        queries=["human plasma SDRF"],
        expected_gain="More sample metadata",
        stop_condition="No new usable metadata",
    )
    decision = BudgetDecision(
        proposal_id=proposal.proposal_id,
        decision="grant",
        approved_query_indexes=[0],
        reasoning_summary="The query is novel.",
    )

    store.save_search_proposal(proposal)
    store.save_budget_decision(run.run_id, decision)

    assert store.load_search_proposal(proposal.proposal_id) == proposal
    assert store.list_search_proposals(run.run_id) == [proposal]
    assert store.load_budget_decision(proposal.proposal_id) == decision


def test_store_updates_dynamic_usage_with_atomic_limit_enforcement(tmp_path: Path) -> None:
    store = AgentRunStore(tmp_path / "state.sqlite")
    run = store.save_run(
        AgentRunRecord(
            run_id="run_usage",
            workflow="discovery",
            dynamic_limits=DynamicBudgetLimits(max_query_units=1, max_repository_requests=2),
        )
    )

    store.increment_dynamic_usage(
        run.run_id,
        query_units=1,
        repository_requests=2,
        search_batches=1,
        budget_reviews=1,
    )
    updated = store.load_run(run.run_id)
    assert updated is not None
    assert updated.dynamic_usage.query_units == 1
    assert updated.dynamic_usage.repository_requests == 2
    assert updated.dynamic_usage.search_batches == 1
    assert updated.dynamic_usage.budget_reviews == 1

    with pytest.raises(ValueError):
        store.increment_dynamic_usage(run.run_id, query_units=1)
    unchanged = store.load_run(run.run_id)
    assert unchanged is not None
    assert unchanged.dynamic_usage.query_units == 1

    store.increment_dynamic_usage(run.run_id, query_units=1, enforce_limits=False)
    bypassed = store.load_run(run.run_id)
    assert bypassed is not None
    assert bypassed.dynamic_usage.query_units == 2

    with pytest.raises(ValueError):
        store.increment_dynamic_usage(run.run_id, query_units=-1, enforce_limits=False)


def test_store_enforces_tool_call_budget_atomically(tmp_path: Path) -> None:
    store = AgentRunStore(tmp_path / "state.sqlite")
    run = store.save_run(
        AgentRunRecord(
            run_id="run_tool_calls",
            workflow="discovery",
            budget={"max_tool_calls": 1},
        )
    )

    store.increment_tool_call_count(run.run_id)
    updated = store.load_run(run.run_id)
    assert updated is not None
    assert updated.tool_call_count == 1

    with pytest.raises(ValueError, match="tool_call_budget_exhausted"):
        store.increment_tool_call_count(run.run_id)
    unchanged = store.load_run(run.run_id)
    assert unchanged is not None
    assert unchanged.tool_call_count == 1


def _manifest_with_files(
    request: DatasetRequest,
    *,
    valid: int,
    weak_keep: int,
    needs_review: int,
) -> DatasetManifest:
    statuses = ["valid"] * valid + ["weak_keep"] * weak_keep + ["needs_review"] * needs_review
    project = DiscoveredProject(project_accession="PXD_METRICS", project_title="Metrics fixture")
    files = [
        DiscoveredFile(
            project_accession=project.project_accession,
            project_title=project.project_title,
            file_name=f"sample_{index}.raw",
            file_type=".raw",
            validity_status=status,
            evidence_level="file",
        )
        for index, status in enumerate(statuses)
    ]
    return DatasetManifest(
        request=request,
        projects=[project],
        files=files,
        summary={
            "selected_projects": 1,
            "selected_files": len(files),
            "validity_status_counts": {
                "valid": valid,
                "weak_keep": weak_keep,
                "needs_review": needs_review,
            },
            "instrument_family_distribution": {"orbitrap": len(files)},
            "unknown_counts": {"fragmentation_method": needs_review},
        },
    )


def test_round_metrics_reward_new_usable_candidates_and_penalize_repeated_queries() -> None:
    request = DatasetRequest(repository="pride", max_files=50)
    previous = DatasetManifest(request=request, summary={"selected_files": 0})
    current = _manifest_with_files(request, valid=4, weak_keep=1, needs_review=1)
    limits = DynamicBudgetLimits(max_query_units=20, max_repository_requests=100)
    usage = DynamicBudgetUsage(query_units=5, repository_requests=12, search_batches=1)
    novel = evaluate_round_metrics(
        current,
        previous,
        request=request,
        queries=["human plasma DDA SDRF"],
        prior_queries=["mouse liver phosphoproteomics"],
        usage=usage,
        limits=limits,
        round_index=2,
    )
    repeated = evaluate_round_metrics(
        current,
        previous,
        request=request,
        queries=["human plasma DDA SDRF"],
        prior_queries=["human plasma DDA SDRF"],
        usage=usage,
        limits=limits,
        round_index=2,
    )
    assert novel.last_round_yield > 0
    assert novel.strategy_novelty > repeated.strategy_novelty
    assert repeated.query_repetition == 1.0
    assert novel.counts["usable_files"] == 5


def _dynamic_store_and_run(
    tmp_path: Path,
    *,
    max_query_units: int = 30,
) -> tuple[AgentRunStore, AgentRunRecord]:
    store = AgentRunStore(tmp_path / "state.sqlite")
    run = store.save_run(
        AgentRunRecord(
            run_id="dynamic_run",
            workflow="discovery",
            status="running",
            dynamic_budget_enabled=True,
            dynamic_limits=DynamicBudgetLimits(
                initial_query_units=12,
                expanded_query_units=30,
                max_query_units=max_query_units,
                initial_repository_requests=80,
                expanded_repository_requests=160,
                max_repository_requests=200,
            ),
            budget=AgentBudget(max_turns=50, max_tool_calls=100),
        )
    )
    return store, run


def _proposal(queries: list[str]) -> SearchProposalInput:
    return SearchProposalInput(
        objective="Improve metadata coverage",
        reasoning_summary="The measured metadata gap is high.",
        evidence_refs=["metadata_gap:0.7"],
        queries=queries,
        expected_gain_dimensions=["metadata_completeness"],
        expected_gain="More usable metadata",
        alternatives_considered=["generic broad search"],
        stop_condition="No new usable files",
    )


def _grant_decision(proposal_id: str, indexes: list[int]) -> BudgetDecision:
    return BudgetDecision(
        proposal_id=proposal_id,
        decision="grant",
        approved_query_indexes=indexes,
        reasoning_summary="The approved queries target measured gaps.",
    )


def test_governor_issues_subset_grant_and_rejects_tamper_and_replay(tmp_path: Path) -> None:
    store, run = _dynamic_store_and_run(tmp_path, max_query_units=3)
    governor = BudgetGovernor(store, run.run_id)
    proposal = governor.register_proposal(
        SearchProposalInput(
            objective="Improve metadata",
            reasoning_summary="Metadata gap is high.",
            queries=["human plasma SDRF", "human plasma Orbitrap"],
            expected_gain="More usable metadata",
            stop_condition="No new usable files",
        )
    )
    result = governor.apply_decision(
        BudgetDecision(
            proposal_id=proposal.proposal_id,
            decision="shrink",
            approved_query_indexes=[0],
            rejected_query_indexes=[1],
            reasoning_summary="The first query targets the measured gap.",
        )
    )
    assert result.outcome == "granted"
    assert result.grant is not None
    assert result.grant.approved_queries == ["human plasma SDRF"]
    with pytest.raises(ValueError, match="search_grant_query_mismatch"):
        governor.consume_grant(result.grant.grant_id, ["changed query"])
    governor.consume_grant(result.grant.grant_id, ["human plasma SDRF"])
    with pytest.raises(ValueError, match="grant_already_consumed"):
        governor.consume_grant(result.grant.grant_id, ["human plasma SDRF"])


def test_governor_denies_grant_over_remaining_query_units(tmp_path: Path) -> None:
    store, run = _dynamic_store_and_run(tmp_path, max_query_units=1)
    governor = BudgetGovernor(store, run.run_id)
    proposal = governor.register_proposal(_proposal(["query one", "query two"]))
    result = governor.apply_decision(_grant_decision(proposal.proposal_id, [0, 1]))
    assert result.outcome == "denied"
    assert result.reason == "hard_query_unit_limit"
    assert result.grant is None


def test_quality_budget_expansion_requires_a_measured_gap(tmp_path: Path) -> None:
    store, run = _dynamic_store_and_run(tmp_path, max_query_units=30)
    governor = BudgetGovernor(store, run.run_id)
    store.increment_dynamic_usage(run.run_id, query_units=12)
    proposal = governor.register_proposal(_proposal(["new intent query"]))

    denied = governor.apply_decision(_grant_decision(proposal.proposal_id, [0]))

    assert denied.outcome == "denied"
    assert denied.reason == "quality_budget_expansion_requires_measured_gap"

    current = store.load_run(run.run_id)
    assert current is not None
    store.save_run(current.model_copy(update={"latest_metrics": _metrics()}))
    second = governor.register_proposal(_proposal(["orthogonal evidence query"]))
    granted = governor.apply_decision(_grant_decision(second.proposal_id, [0]))

    assert granted.outcome == "granted"
    assert granted.grant is not None


def test_quality_budget_tier_uses_query_and_repository_usage(tmp_path: Path) -> None:
    store, run = _dynamic_store_and_run(tmp_path, max_query_units=60)
    assert quality_budget_tier(run) == "initial"
    expanded = store.increment_dynamic_usage(
        run.run_id,
        query_units=13,
        repository_requests=81,
    )
    assert quality_budget_tier(expanded) == "expanded"
    maximum = store.increment_dynamic_usage(
        run.run_id,
        query_units=18,
        repository_requests=80,
    )
    assert quality_budget_tier(maximum) == "maximum_quality"


def test_governor_rejects_exact_consumed_query_without_retryable_failure(tmp_path: Path) -> None:
    store, run = _dynamic_store_and_run(tmp_path)
    governor = BudgetGovernor(store, run.run_id)
    first = governor.register_proposal(_proposal(["human plasma SDRF"]))
    first_review = governor.apply_decision(_grant_decision(first.proposal_id, [0]))
    assert first_review.grant is not None
    governor.consume_grant(first_review.grant.grant_id, ["human plasma SDRF"])
    second = governor.register_proposal(_proposal(["human   plasma SDRF"]))
    second_review = governor.apply_decision(_grant_decision(second.proposal_id, [0]))
    assert second_review.outcome == "denied"
    assert second_review.reason == "duplicate_query_not_authorized"


class FakeBudgetDecisionModel(Model):
    def __init__(self, payloads: dict[str, Any] | list[dict[str, Any]]) -> None:
        self.payloads = payloads if isinstance(payloads, list) else [payloads]
        self.calls = 0

    async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
        self.calls += 1
        if self.calls <= len(self.payloads):
            output = [
                ResponseFunctionToolCall(
                    arguments=json.dumps(self.payloads[self.calls - 1]),
                    call_id=f"budget_call_{self.calls}",
                    name="submit_budget_decision",
                    type="function_call",
                    status="completed",
                )
            ]
        else:
            output = [
                ResponseOutputMessage(
                    id="budget_message",
                    content=[
                        ResponseOutputText(
                            annotations=[],
                            text="Budget decision submitted.",
                            type="output_text",
                        )
                    ],
                    role="assistant",
                    status="completed",
                    type="message",
                )
            ]
        return ModelResponse(output=output, usage=Usage(requests=1), response_id=None)

    async def stream_response(self, *args: Any, **kwargs: Any):
        if False:
            yield None


def _metrics() -> RoundMetrics:
    return RoundMetrics(
        candidate_shortfall=0.6,
        quality_gap=0.3,
        metadata_gap=0.7,
        diversity_gap=0.4,
        strategy_novelty=0.8,
        last_round_yield=0.5,
        query_repetition=0.2,
        budget_pressure=0.1,
    )


def test_budget_agent_submits_structured_grant_with_fake_model(tmp_path: Path) -> None:
    store, run = _dynamic_store_and_run(tmp_path)
    governor = BudgetGovernor(store, run.run_id)
    proposal = governor.register_proposal(_proposal(["human plasma SDRF"]))
    model = FakeBudgetDecisionModel(
        {
            "decision": {
                "proposal_id": proposal.proposal_id,
                "decision": "grant",
                "approved_query_indexes": [0],
                "reasoning_summary": "The query targets the measured metadata gap.",
            }
        }
    )
    result = asyncio.run(
        run_budget_agent_review(
            sdk=_load_agents_sdk(),
            model=model,
            proposal=proposal,
            metrics=_metrics(),
            governor=governor,
            max_turns=3,
        )
    )
    assert result.outcome == "granted"
    assert result.grant is not None
    assert model.calls == 2


def test_budget_agent_corrects_invalid_stop_to_replan(tmp_path: Path) -> None:
    store, run = _dynamic_store_and_run(tmp_path)
    governor = BudgetGovernor(store, run.run_id)
    proposal = governor.register_proposal(_proposal(["human plasma SDRF"]))
    model = FakeBudgetDecisionModel(
        [
            {
                "decision": {
                    "proposal_id": proposal.proposal_id,
                    "decision": "stop",
                    "reasoning_summary": "Stop without counterfactual fields",
                }
            },
            {
                "decision": {
                    "proposal_id": proposal.proposal_id,
                    "decision": "replan",
                    "reasoning_summary": "Try a materially different metadata strategy.",
                }
            },
        ]
    )
    result = asyncio.run(
        run_budget_agent_review(
            sdk=_load_agents_sdk(),
            model=model,
            proposal=proposal,
            metrics=_metrics(),
            governor=governor,
            max_turns=3,
        )
    )
    assert result.outcome == "replan"
    assert result.grant is None
    assert model.calls == 3
