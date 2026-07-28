from __future__ import annotations

from pathlib import Path

from agent.control_plane.budget_governor import BudgetGovernor
from agent.control_plane.models import (
    AgentRunRecord,
    BudgetDecision,
    DynamicBudgetLimits,
    SearchProposalInput,
)
from agent.control_plane.store import AgentRunStore
from agent.discovery.models import DatasetRequest


def _proposal(queries: list[str]) -> SearchProposalInput:
    return SearchProposalInput(
        objective="Recover immunopeptidomics projects",
        reasoning_summary="Need novel HLA ligandome coverage.",
        queries=queries,
        expected_gain_dimensions=["semantic_coverage"],
        expected_gain="Find HLA ligandome candidates",
        alternatives_considered=["broader MHC terms"],
        stop_condition="No new qualified projects",
    )


def test_consume_grant_is_single_txn_with_usage_event_and_attempt(tmp_path: Path) -> None:
    store = AgentRunStore(tmp_path / "state.sqlite")
    request = DatasetRequest(repository="pride", max_projects=5)
    run = store.save_run(
        AgentRunRecord(
            run_id="grant_atomic",
            workflow="discovery",
            status="running",
            request=request.model_dump(mode="json"),
            dynamic_budget_enabled=True,
            dynamic_limits=DynamicBudgetLimits(initial_query_units=20, max_query_units=40),
            budget={"max_tool_calls": 50, "max_discovery_rounds": 4},
        )
    )
    governor = BudgetGovernor(store, run.run_id)
    proposal = governor.register_proposal(_proposal(["immunopeptidomics", "HLA ligandome"]))
    decision = BudgetDecision(
        proposal_id=proposal.proposal_id,
        decision="grant",
        approved_query_indexes=[0, 1],
        rejected_query_indexes=[],
        reasoning_summary="Approve seeds.",
    )
    review = governor.apply_decision(decision)
    assert review.outcome == "granted"
    assert review.grant is not None

    consumed = governor.consume_grant(review.grant.grant_id)
    assert consumed.status == "consumed"

    persisted = store.load_run(run.run_id)
    assert persisted is not None
    assert persisted.active_grant_id is None
    assert persisted.dynamic_usage.search_batches == 1
    assert persisted.dynamic_usage.query_units == consumed.query_units

    events = store.list_events(run.run_id)
    assert any(event.event_type == "search_grant_consumed" for event in events)

    attempts = store.list_search_attempts(run.run_id)
    assert len(attempts) == 1
    assert attempts[0]["grant_id"] == review.grant.grant_id
    assert attempts[0]["status"] == "consumed_pending_execution"
    assert attempts[0]["executed_queries"] == ["immunopeptidomics", "HLA ligandome"]

    marked = store.mark_search_attempt_executed(run.run_id, review.grant.grant_id, status="executed")
    assert marked is not None
    assert marked["status"] == "executed"
    attempts2 = store.list_search_attempts(run.run_id)
    assert attempts2[0]["status"] == "executed"


def test_consume_grant_rejects_second_use(tmp_path: Path) -> None:
    store = AgentRunStore(tmp_path / "state.sqlite")
    request = DatasetRequest(repository="pride", max_projects=3)
    run = store.save_run(
        AgentRunRecord(
            run_id="grant_once",
            workflow="discovery",
            status="running",
            request=request.model_dump(mode="json"),
            dynamic_budget_enabled=True,
            dynamic_limits=DynamicBudgetLimits(initial_query_units=10, max_query_units=20),
            budget={"max_tool_calls": 20, "max_discovery_rounds": 3},
        )
    )
    governor = BudgetGovernor(store, run.run_id)
    proposal = governor.register_proposal(_proposal(["phosphoproteomics"]))
    decision = BudgetDecision(
        proposal_id=proposal.proposal_id,
        decision="grant",
        approved_query_indexes=[0],
        rejected_query_indexes=[],
        reasoning_summary="ok",
    )
    review = governor.apply_decision(decision)
    assert review.grant is not None
    governor.consume_grant(review.grant.grant_id)
    try:
        governor.consume_grant(review.grant.grant_id)
        raised = False
    except ValueError as exc:
        raised = True
        assert "grant_already_consumed" in str(exc)
    assert raised
