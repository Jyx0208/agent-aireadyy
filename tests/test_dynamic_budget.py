import pytest
from pydantic import ValidationError

from agent.control_plane.models import (
    BudgetDecision,
    BudgetDecisionInput,
    DynamicBudgetLimits,
    SearchGrant,
    SearchProposalInput,
)


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
