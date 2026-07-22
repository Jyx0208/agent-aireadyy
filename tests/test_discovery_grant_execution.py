from __future__ import annotations

from pathlib import Path

from agent.control_plane.budget_governor import BudgetGovernor, grant_execution_summary
from agent.control_plane.discovery import DiscoveryToolService
from agent.control_plane.models import (
    AgentRunRecord,
    BudgetDecision,
    DynamicBudgetLimits,
    SearchProposalInput,
)
from agent.control_plane.openai_agents import _discovery_failure_stop_reason
from agent.control_plane.store import AgentRunStore
from agent.discovery.models import DatasetRequest
from agent.discovery.search_environment import (
    CandidateSearchAction,
    CandidateSearchObservation,
    CandidatePreview,
    QueryYield,
    RepositoryQuery,
)


class _FakeSearchEnvironment:
    def __init__(self) -> None:
        self.calls: list[CandidateSearchAction] = []
        self.candidate_accessions: list[str] = []

    def search(self, action: CandidateSearchAction) -> CandidateSearchObservation:
        self.calls.append(action)
        accession = "PXDGRANT1"
        self.candidate_accessions = [accession]
        return CandidateSearchObservation(
            status="completed",
            search_id="search_0001",
            query_yields=[
                QueryYield(
                    query=item.query,
                    executed_query=item.query,
                    intent_dimension=item.intent_dimension,
                    requested_depth=item.depth,
                    raw_result_count=1,
                    new_candidate_count=1,
                    duplicate_count=0,
                    top_accessions=[accession],
                )
                for item in action.queries
            ],
            raw_result_count=len(action.queries),
            candidate_count=1,
            new_candidate_count=1,
            duplicate_count=0,
            duplicate_rate=0.0,
            high_relevance_candidate_count=1,
            semantic_coverage=0.4,
            previews=[
                CandidatePreview(
                    project_accession=accession,
                    title="HLA ligandome study",
                    project_score=0.9,
                    confidence=0.8,
                )
            ],
            recommended_action="inspect_high_relevance_candidates",
        )


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


def test_candidate_search_with_grant_binds_approved_queries_and_ignores_agent_rewrite(
    tmp_path: Path,
) -> None:
    store = AgentRunStore(tmp_path / "state.sqlite")
    request = DatasetRequest(repository="pride", max_projects=5)
    run = store.save_run(
        AgentRunRecord(
            run_id="grant_bind",
            workflow="discovery",
            status="running",
            request=request.model_dump(mode="json"),
            dynamic_budget_enabled=True,
            dynamic_limits=DynamicBudgetLimits(initial_query_units=20, max_query_units=40),
            budget={"max_tool_calls": 50, "max_discovery_rounds": 4},
        )
    )
    governor = BudgetGovernor(store, run.run_id)
    proposal = governor.register_proposal(
        _proposal(["immunopeptidomics", "HLA ligandome", "MHC ligandome"])
    )
    decision = BudgetDecision(
        proposal_id=proposal.proposal_id,
        decision="grant",
        approved_query_indexes=[0, 1, 2],
        rejected_query_indexes=[],
        reasoning_summary="Approve the full immunopeptidomics seed set.",
    )
    review = governor.apply_decision(decision)
    assert review.outcome == "granted"
    assert review.grant is not None

    fake_env = _FakeSearchEnvironment()
    service = DiscoveryToolService(
        run_id=run.run_id,
        request=request,
        output_dir=tmp_path / "out",
        store=store,
        search_environment=fake_env,
        dynamic_budget=True,
        budget_governor=governor,
    )

    # Agent deliberately rewrites / drops queries; server must still execute approved set.
    observation = service.search_repository_candidates(
        CandidateSearchAction(
            queries=[
                RepositoryQuery(query="immunopeptidomics WRONG", depth=30),
                RepositoryQuery(query="totally different term", depth=10),
            ],
            candidate_limit=50,
            rationale="Attempt search with rewritten queries.",
        ),
        grant_id=review.grant.grant_id,
    )

    assert observation.status == "completed"
    assert len(fake_env.calls) == 1
    executed = [item.query for item in fake_env.calls[0].queries]
    assert executed == ["immunopeptidomics", "HLA ligandome", "MHC ligandome"]
    persisted = store.load_run(run.run_id)
    assert persisted is not None
    assert persisted.active_grant_id is None
    assert persisted.dynamic_usage.search_batches == 1
    assert persisted.candidate_search_count == 1


def test_unconsumed_grant_is_abandoned_when_new_proposal_arrives(tmp_path: Path) -> None:
    store = AgentRunStore(tmp_path / "state.sqlite")
    run = store.save_run(
        AgentRunRecord(
            run_id="grant_replace",
            workflow="discovery",
            dynamic_budget_enabled=True,
            dynamic_limits=DynamicBudgetLimits(initial_query_units=20, max_query_units=40),
            budget={"max_tool_calls": 50},
        )
    )
    governor = BudgetGovernor(store, run.run_id)
    first = governor.register_proposal(_proposal(["immunopeptidomics", "HLA ligandome"]))
    granted = governor.apply_decision(
        BudgetDecision(
            proposal_id=first.proposal_id,
            decision="grant",
            approved_query_indexes=[0, 1],
            reasoning_summary="Initial grant.",
        )
    )
    assert granted.grant is not None
    active_before = store.load_run(run.run_id)
    assert active_before is not None
    assert active_before.active_grant_id == granted.grant.grant_id

    second = governor.register_proposal(_proposal(["MHC peptidome", "HLA eluted ligand"]))
    assert second.proposal_id != first.proposal_id
    active_after = store.load_run(run.run_id)
    assert active_after is not None
    assert active_after.active_grant_id is None
    abandoned = store.load_search_grant(granted.grant.grant_id)
    assert abandoned is not None
    assert abandoned.status == "abandoned"


def test_budget_stop_before_any_search_is_converted_to_replan(tmp_path: Path) -> None:
    store = AgentRunStore(tmp_path / "state.sqlite")
    run = store.save_run(
        AgentRunRecord(
            run_id="no_stop_before_search",
            workflow="discovery",
            dynamic_budget_enabled=True,
            dynamic_limits=DynamicBudgetLimits(),
            budget={"max_tool_calls": 50},
        )
    )
    governor = BudgetGovernor(store, run.run_id)
    proposal = governor.register_proposal(_proposal(["immunopeptidomics"]))
    result = governor.apply_decision(
        BudgetDecision(
            proposal_id=proposal.proposal_id,
            decision="stop",
            approved_query_indexes=[],
            rejected_query_indexes=[0],
            reasoning_summary="Stop early without evidence.",
            unresolved_gaps=["semantic_coverage_gap"],
            unexplored_strategies=["HLA ligandome seeds"],
            why_not_continue="No search executed yet but agent requested stop.",
        )
    )
    assert result.outcome == "replan"
    assert result.reason == "budget_stop_refused_before_any_search"
    persisted = store.load_run(run.run_id)
    assert persisted is not None
    assert persisted.search_stopped is False


def test_discovery_failure_stop_reason_prefers_grant_execution_gap() -> None:
    run = AgentRunRecord(
        run_id="fail_reason",
        workflow="discovery",
        dynamic_usage={"search_batches": 0},
        active_grant_id="grant_stuck",
        search_stop_reason="budget_agent_stop",
    )
    assert _discovery_failure_stop_reason(run) == "search_grant_issued_but_never_executed"


def test_grant_execution_summary_flags_execution_gap(tmp_path: Path) -> None:
    store = AgentRunStore(tmp_path / "state.sqlite")
    run = store.save_run(AgentRunRecord(run_id="exec_gap", workflow="discovery"))
    governor = BudgetGovernor(store, run.run_id)
    proposal = governor.register_proposal(_proposal(["immunopeptidomics"]))
    review = governor.apply_decision(
        BudgetDecision(
            proposal_id=proposal.proposal_id,
            decision="grant",
            approved_query_indexes=[0],
            reasoning_summary="Approve one seed.",
        )
    )
    assert review.grant is not None
    summary = grant_execution_summary(store, run.run_id)
    assert summary["execution_gap"] is True
    assert summary["grant_counts"]["issued"] == 1
    assert summary["search_batches"] == 0
