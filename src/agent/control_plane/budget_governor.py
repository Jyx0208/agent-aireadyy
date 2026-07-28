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
from agent.discovery.query_builder import classify_pride_query_strategy


class RepositoryRequestBudgetExceeded(RuntimeError):
    pass


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


def grant_execution_summary(store: AgentRunStore, run_id: str) -> dict[str, object]:
    grants = store.list_search_grants(run_id)
    counts = {
        "issued": 0,
        "consumed": 0,
        "abandoned": 0,
        "rejected": 0,
        "expired": 0,
    }
    for grant in grants:
        status = str(grant.status)
        if status in counts:
            counts[status] += 1
    run = store.load_run(run_id)
    search_batches = int(run.dynamic_usage.search_batches) if run is not None else 0
    issued_or_terminal = counts["issued"] + counts["consumed"] + counts["abandoned"]
    return {
        "grant_counts": counts,
        "search_batches": search_batches,
        "approved_query_units_pending": sum(
            grant.query_units for grant in grants if grant.status == "issued"
        ),
        "execution_gap": issued_or_terminal > 0 and search_batches <= 0,
        "active_grant_id": run.active_grant_id if run is not None else None,
    }


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
        # Unconsumed grants must not deadlock later proposals.
        self.abandon_active_grant("unconsumed_grant_replaced_by_new_proposal")
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
        self.store.increment_dynamic_usage(self.run_id, budget_reviews=1)
        self.store.append_event(
            self.run_id,
            "budget_decision_recorded",
            decision.model_dump(mode="json"),
        )
        if decision.decision == "replan":
            return BudgetReviewResult(outcome="replan", decision=decision, reason="budget_agent_requested_replan")
        if decision.decision == "stop":
            run = self._require_run()
            execution = grant_execution_summary(self.store, self.run_id)
            # Do not permanently stop search before any repository search executed.
            if int(run.dynamic_usage.search_batches) <= 0:
                self.store.append_event(
                    self.run_id,
                    "budget_stop_refused_before_any_search",
                    {
                        "proposal_id": proposal.proposal_id,
                        "execution": execution,
                        "reason": "budget_stop_refused_before_any_search",
                    },
                )
                return BudgetReviewResult(
                    outcome="replan",
                    decision=decision,
                    reason="budget_stop_refused_before_any_search",
                )
            self._mark_search_stopped("budget_agent_stop")
            return BudgetReviewResult(outcome="stopped", decision=decision, reason="budget_agent_stop")
        approved = [proposal.queries[index] for index in decision.approved_query_indexes]
        denial_reason = self._grant_denial_reason(approved, proposal)
        if denial_reason:
            self.store.append_event(
                self.run_id,
                "search_grant_rejected",
                {"proposal_id": proposal.proposal_id, "reason": denial_reason},
            )
            return BudgetReviewResult(outcome="denied", decision=decision, reason=denial_reason)
        self.abandon_active_grant("unconsumed_grant_replaced_by_new_grant")
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

    def consume_grant(self, grant_id: str, queries: list[str] | None = None) -> SearchGrant:
        """Consume a one-use grant.

        When queries is omitted, the stored approved query set is used. Callers should
        bind the search action to the grant first so query mismatches never reach here.
        """
        grant = self.store.load_search_grant(grant_id)
        if grant is None or grant.run_id != self.run_id:
            raise ValueError("search_grant_not_found")
        if grant.status != "issued":
            raise ValueError(f"grant_already_{grant.status}")
        if queries is None:
            query_hash = grant.query_hash
            consumed_queries = list(grant.approved_queries)
        else:
            canonical_queries = canonicalize_queries(queries)
            query_hash = hash_queries(canonical_queries)
            if query_hash != grant.query_hash:
                raise ValueError("search_grant_query_mismatch")
            consumed_queries = canonical_queries
        # WP-D5: grant consume + active clear + usage + event + attempt ledger
        # are one SQLite transaction inside AgentRunStore.consume_search_grant.
        consumed = self.store.consume_search_grant(
            self.run_id,
            grant_id,
            query_hash,
            executed_queries=consumed_queries,
            record_usage=True,
            clear_active_grant=True,
            append_consumed_event=True,
        )
        return consumed

    def abandon_active_grant(self, reason: str) -> SearchGrant | None:
        run = self._require_run()
        grant_id = run.active_grant_id
        if not grant_id:
            return None
        return self.abandon_grant(grant_id, reason)

    def abandon_grant(self, grant_id: str, reason: str) -> SearchGrant:
        abandoned = self.store.abandon_search_grant(self.run_id, grant_id, reason=reason)
        run = self._require_run()
        if run.active_grant_id == grant_id:
            self._set_active_grant(None)
        self.store.append_event(
            self.run_id,
            "search_grant_abandoned",
            {
                "grant_id": grant_id,
                "reason": reason,
                "approved_queries": abandoned.approved_queries,
            },
        )
        return abandoned

    def stop_for_hard_limit(self, reason: str) -> None:
        self._mark_search_stopped(reason)

    def record_repository_request(self, repository: str, operation: str) -> None:
        if self.elapsed_seconds() >= self._require_run().dynamic_limits.max_elapsed_seconds:
            self.stop_for_hard_limit("hard_elapsed_time_limit")
            raise RepositoryRequestBudgetExceeded("hard_elapsed_time_limit")
        try:
            usage = self.store.increment_dynamic_usage(self.run_id, repository_requests=1)
        except ValueError as exc:
            if str(exc) != "hard_repository_request_limit":
                raise
            self.stop_for_hard_limit("hard_repository_request_limit")
            raise RepositoryRequestBudgetExceeded("hard_repository_request_limit") from exc
        self.store.append_event(
            self.run_id,
            "repository_request_started",
            {
                "repository": repository,
                "operation": operation,
                "repository_requests": usage.dynamic_usage.repository_requests,
            },
        )

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

    def _grant_denial_reason(
        self,
        approved: list[str],
        proposal: SearchProposalRecord,
    ) -> str | None:
        run = self._require_run()
        if self.elapsed_seconds() >= run.dynamic_limits.max_elapsed_seconds:
            return "hard_elapsed_time_limit"
        if run.dynamic_usage.query_units + len(approved) > run.dynamic_limits.max_query_units:
            return "hard_query_unit_limit"
        projected_query_units = run.dynamic_usage.query_units + len(approved)
        initial_limit = min(
            run.dynamic_limits.initial_query_units,
            run.dynamic_limits.max_query_units,
        )
        expanded_limit = min(
            max(initial_limit, run.dynamic_limits.expanded_query_units),
            run.dynamic_limits.max_query_units,
        )
        if projected_query_units > initial_limit:
            metrics = run.latest_metrics
            # Only the true first grant may expand without measured metrics.
            if (
                metrics is None
                and run.dynamic_usage.search_batches <= 0
                and run.dynamic_usage.query_units <= 0
            ):
                pass
            elif metrics is None:
                return "quality_budget_expansion_requires_measured_gap"
            else:
                quality_gap = max(
                    metrics.quality_gap,
                    metrics.semantic_coverage_gap,
                    metrics.hard_constraint_evidence_gap,
                    metrics.metadata_gap,
                )
                expected_dimensions = {
                    str(value).strip()
                    for value in proposal.expected_gain_dimensions
                    if str(value).strip()
                }
                if not expected_dimensions:
                    return "quality_budget_expansion_requires_expected_gain_dimension"
                if metrics.no_gain_streak >= 2 and not proposal.alternatives_considered:
                    return "no_gain_recovery_requires_alternative_strategy"
                if projected_query_units <= expanded_limit:
                    if quality_gap < 0.15 and metrics.high_relevance_gain <= 0.0:
                        return "quality_budget_expansion_requires_measured_gap"
                elif quality_gap < 0.30 or (
                    metrics.strategy_novelty < 0.20 and metrics.high_relevance_gain <= 0.0
                ):
                    return "maximum_quality_budget_requires_strong_gap_and_novel_strategy"
        repository = str(run.request.get("repository") or "pride")
        if (
            run.search_recovery_required
            and run.candidate_search_count == 0
            and repository in {"pride", "auto"}
            and classify_pride_query_strategy(approved) != "atomic_seed"
        ):
            return "search_recovery_requires_atomic_queries"
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


def quality_budget_tier(run: AgentRunRecord) -> str:
    limits = run.dynamic_limits
    usage = run.dynamic_usage
    initial_query = min(limits.initial_query_units, limits.max_query_units)
    expanded_query = min(max(initial_query, limits.expanded_query_units), limits.max_query_units)
    initial_requests = min(
        limits.initial_repository_requests,
        limits.max_repository_requests,
    )
    expanded_requests = min(
        max(initial_requests, limits.expanded_repository_requests),
        limits.max_repository_requests,
    )
    if usage.query_units <= initial_query and usage.repository_requests <= initial_requests:
        return "initial"
    if usage.query_units <= expanded_query and usage.repository_requests <= expanded_requests:
        return "expanded"
    return "maximum_quality"
