from __future__ import annotations

import hashlib
import uuid

from agent.control_plane.discovery_metrics import elapsed_seconds_since
from agent.control_plane.models import (
    AgentRunRecord,
    BudgetDecision,
    BudgetReviewResult,
    SearchGrant,
    SearchProposalInput,
    SearchProposalRecord,
)
from agent.control_plane.policy import evaluate_tool_policy
from agent.control_plane.store import AgentRunStore, canonical_json


def canonicalize_queries(queries: list[str]) -> list[str]:
    if len(queries) > 40:
        raise ValueError("search_proposal_query_limit_exceeded")
    result: list[str] = []
    seen: set[str] = set()
    for raw_query in queries:
        query = " ".join(str(raw_query).split())
        if not query:
            raise ValueError("search_query_empty")
        if len(query) > 240:
            raise ValueError("search_query_too_long")
        duplicate_key = query.casefold()
        if duplicate_key in seen:
            continue
        seen.add(duplicate_key)
        result.append(query)
    if not result:
        raise ValueError("search_proposal_requires_queries")
    return result


def hash_queries(queries: list[str]) -> str:
    return hashlib.sha256(canonical_json(queries).encode("utf-8")).hexdigest()


class BudgetGovernor:
    def __init__(self, store: AgentRunStore, run_id: str) -> None:
        self.store = store
        self.run_id = run_id

    def authorize_tool(self, tool_name: str) -> None:
        policy = evaluate_tool_policy(tool_name, self._require_run())
        if policy.outcome != "allow":
            raise ValueError(policy.reason)
        self.store.increment_tool_call_count(self.run_id)

    def register_proposal(self, payload: SearchProposalInput) -> SearchProposalRecord:
        self.authorize_tool("request_search_budget")
        queries = canonicalize_queries(payload.queries)
        proposal = SearchProposalRecord(
            **payload.model_dump(exclude={"queries"}),
            queries=queries,
            proposal_id=f"proposal_{uuid.uuid4().hex}",
            run_id=self.run_id,
            query_hash=hash_queries(queries),
        )
        self.store.save_search_proposal(proposal)
        self.store.append_event(self.run_id, "search_plan_proposed", proposal.model_dump(mode="json"))
        return proposal

    def apply_decision(self, decision: BudgetDecision) -> BudgetReviewResult:
        proposal = self.store.load_search_proposal(decision.proposal_id)
        if proposal is None or proposal.run_id != self.run_id:
            raise ValueError("budget_proposal_not_found")
        self._validate_indexes(proposal, decision)
        self.store.save_budget_decision(self.run_id, decision)
        if decision.decision == "replan":
            return BudgetReviewResult(outcome="replan", decision=decision, reason="budget_agent_requested_replan")
        if decision.decision == "stop":
            self._mark_search_stopped("budget_agent_stop")
            return BudgetReviewResult(outcome="stopped", decision=decision, reason="budget_agent_stop")
        approved = [proposal.queries[index] for index in decision.approved_query_indexes]
        denial_reason = self._grant_denial_reason(approved)
        if denial_reason:
            self.store.append_event(
                self.run_id,
                "search_grant_rejected",
                {"proposal_id": proposal.proposal_id, "reason": denial_reason},
            )
            return BudgetReviewResult(outcome="denied", decision=decision, reason=denial_reason)
        grant = SearchGrant(
            grant_id=f"grant_{uuid.uuid4().hex}",
            run_id=self.run_id,
            proposal_id=proposal.proposal_id,
            approved_queries=approved,
            query_hash=hash_queries(approved),
            query_units=len(approved),
        )
        self.store.issue_search_grant(grant)
        self._set_active_grant(grant.grant_id)
        self.store.append_event(self.run_id, "search_grant_issued", grant.model_dump(mode="json"))
        return BudgetReviewResult(outcome="granted", decision=decision, grant=grant, reason="budget_agent_grant")

    def consume_grant(self, grant_id: str, queries: list[str]) -> SearchGrant:
        canonical_queries = canonicalize_queries(queries)
        consumed = self.store.consume_search_grant(self.run_id, grant_id, hash_queries(canonical_queries))
        self._set_active_grant(None)
        self.store.increment_dynamic_usage(
            self.run_id,
            query_units=consumed.query_units,
            search_batches=1,
        )
        self.store.append_event(self.run_id, "search_grant_consumed", consumed.model_dump(mode="json"))
        return consumed

    def stop_for_hard_limit(self, reason: str) -> None:
        self._mark_search_stopped(reason)

    def elapsed_seconds(self) -> float:
        return elapsed_seconds_since(self._require_run().dynamic_usage.started_at)

    def _require_run(self) -> AgentRunRecord:
        run = self.store.load_run(self.run_id)
        if run is None:
            raise KeyError(f"Unknown agent run: {self.run_id}")
        return run

    def _validate_indexes(self, proposal: SearchProposalRecord, decision: BudgetDecision) -> None:
        approved = list(decision.approved_query_indexes)
        rejected = list(decision.rejected_query_indexes)
        if len(approved) != len(set(approved)) or len(rejected) != len(set(rejected)):
            raise ValueError("budget_decision_duplicate_indexes")
        indexes = approved + rejected
        if any(index < 0 or index >= len(proposal.queries) for index in indexes):
            raise ValueError("budget_decision_index_out_of_range")
        if set(approved) & set(rejected):
            raise ValueError("budget_decision_overlapping_indexes")
        if decision.decision == "grant" and set(approved) != set(range(len(proposal.queries))):
            raise ValueError("grant_must_approve_all_queries")
        if decision.decision == "shrink" and set(approved) == set(range(len(proposal.queries))):
            raise ValueError("shrink_requires_true_subset")

    def _grant_denial_reason(self, approved: list[str]) -> str | None:
        run = self._require_run()
        if run.active_grant_id:
            return "active_search_grant_exists"
        if self.elapsed_seconds() >= run.dynamic_limits.max_elapsed_seconds:
            return "hard_elapsed_time_limit"
        if run.dynamic_usage.query_units + len(approved) > run.dynamic_limits.max_query_units:
            return "hard_query_unit_limit"
        consumed_queries = {
            " ".join(query.casefold().split())
            for grant in self.store.list_search_grants(self.run_id)
            if grant.status == "consumed"
            for query in grant.approved_queries
        }
        if any(" ".join(query.casefold().split()) in consumed_queries for query in approved):
            return "duplicate_query_not_authorized"
        return None

    def _set_active_grant(self, grant_id: str | None) -> None:
        run = self._require_run()
        self.store.save_run(run.model_copy(update={"active_grant_id": grant_id}))

    def _mark_search_stopped(self, reason: str) -> None:
        run = self._require_run()
        self.store.save_run(
            run.model_copy(
                update={"search_stopped": True, "search_stop_reason": reason, "active_grant_id": None}
            )
        )
        self.store.append_event(self.run_id, "dynamic_search_stopped", {"reason": reason})
