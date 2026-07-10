from pathlib import Path

import pytest
from pydantic import ValidationError

from agent.control_plane.models import (
    AgentRunRecord,
    BudgetDecision,
    BudgetDecisionInput,
    DynamicBudgetLimits,
    DynamicBudgetUsage,
    SearchGrant,
    SearchProposalInput,
    SearchProposalRecord,
)
from agent.control_plane.discovery_metrics import evaluate_round_metrics
from agent.control_plane.store import AgentRunStore
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
    assert limits.max_query_units == 30
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
