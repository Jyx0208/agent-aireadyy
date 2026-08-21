from __future__ import annotations

import json
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from agent.control_plane.budget_governor import BudgetGovernor, grant_execution_summary
from agent.control_plane.discovery_metrics import elapsed_seconds_since, evaluate_round_metrics
from agent.control_plane.models import (
    AgentRunRecord,
    ArtifactReference,
    DiscoveryAuditIssue,
    DiscoveryQualityAudit,
    DiscoveryRepairAction,
    DiscoveryRoundObservation,
    DynamicBudgetLimits,
    RoundMetrics,
    SearchDiagnosis,
    VerifiedProjectBatch,
    minimum_high_relevance_inspections,
)
from agent.control_plane.policy import evaluate_tool_policy
from agent.control_plane.store import AgentRunStore
from agent.discovery.diversity import diversity_summary, select_diverse_items, validity_summary
from agent.discovery.constraints import (
    ScientificConstraint,
    evaluate_constraint_value,
    is_substantive_constraint_value,
)
from agent.discovery.manifest import write_dataset_manifest
from agent.discovery.memory import DiscoveryMemory
from agent.discovery.models import DatasetManifest, DatasetRequest, DiscoveredFile, DiscoveredProject
from agent.discovery.portfolio import (
    RecoveryAction,
    RecoveryAttempt,
    PortfolioState,
    assess_portfolio_coverage,
    initialize_portfolio_state,
    select_portfolio_files,
    update_portfolio_state,
)
from agent.discovery.ontology import normalize_labeling_strategy
from agent.discovery.project_judgment import (
    ProjectJudgmentInput,
    is_qualified_project_judgment,
    summarize_project_judgments,
)
from agent.discovery.publication import business_completion_allows_success
from agent.discovery.query_portfolio import MAX_REPOSITORY_QUERY_DEPTH
from agent.discovery.repository_discovery import discover_repository_dataset
from agent.discovery.query_builder import classify_pride_query_strategy
from agent.discovery.search_environment import (
    CandidateInspectionAction,
    CandidateSearchAction,
    CandidateSearchObservation,
    DiscoverySearchEnvironment,
    RepositoryQuery,
)
from agent.discovery.task_readiness import annotate_manifest_task_readiness
from agent.discovery.validity import normalize_acquisition_mode
from agent.repositories.metering import meter_repository_requests


DiscoveryFunction = Callable[..., DatasetManifest]
_HARD_BUILTIN_EVIDENCE_FIELDS = ("acquisition_mode", "labeling_strategy")
_EVIDENCE_NUMBER_RE = re.compile(
    r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?",
    re.IGNORECASE,
)
_EVIDENCE_TOKEN_RE = re.compile(
    r"(?<![\w.])[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?(?![\w.])|[^\W_]+",
    re.IGNORECASE,
)
_PROJECT_EVIDENCE_REF_NAMES = frozenset(
    {
        "project_title",
        "project_description_excerpt",
        "sample_processing_excerpt",
        "data_processing_excerpt",
        "selected_file_examples",
        "species",
        "acquisition_mode",
        "labeling_strategy",
        "instrument_names",
        "project_publication_date",
        "immunopeptide_evidence_terms",
        "validity_status_counts",
        "evidence_level_counts",
        "sdrf",
    }
)


class DiscoveryToolService:
    def __init__(
        self,
        *,
        run_id: str,
        request: DatasetRequest,
        output_dir: str | Path,
        store: AgentRunStore,
        task_type: str | None = None,
        memory: DiscoveryMemory | None = None,
        discovery_func: DiscoveryFunction = discover_repository_dataset,
        dynamic_budget: bool = False,
        budget_governor: BudgetGovernor | None = None,
        search_environment: DiscoverySearchEnvironment | None = None,
    ) -> None:
        self.run_id = run_id
        self.request = request
        self.output_dir = Path(output_dir)
        self.store = store
        self.task_type = task_type
        self.memory = memory
        self.discovery_func = discovery_func
        self.dynamic_budget = dynamic_budget
        self.budget_governor = budget_governor
        self.search_environment = search_environment
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _required_high_relevance_inspections(
        self,
        high_relevance_candidate_count: int,
    ) -> int:
        """Return the inspection gate appropriate for the requested quantity mode."""

        available = max(0, int(high_relevance_candidate_count or 0))
        target = max(1, int(self.request.max_projects or 1))
        if str(self.request.quota_flexibility or "") == "fixed":
            # A fixed target means "stop after N qualified projects", not
            # "find N and then inspect 2N before allowing delivery".
            return min(available, target)
        return minimum_high_relevance_inspections(available, target)

    def search_repository_candidates(
        self,
        action: CandidateSearchAction,
        grant_id: str | None = None,
        *,
        scheduler_owned: bool = False,
    ) -> CandidateSearchObservation:
        if self.search_environment is None:
            return self._blocked_candidate_search("candidate_search_environment_unavailable")
        run = self._require_run()
        if run.search_stopped:
            return self._blocked_candidate_search("dynamic_search_stopped")
        if run.selected_round_index is not None:
            return self._blocked_candidate_search("manifest_already_selected")
        policy = evaluate_tool_policy("search_repository_candidates", run)
        if policy.outcome != "allow":
            return self._blocked_candidate_search(policy.reason)
        bound_action = action
        if self.dynamic_budget and not scheduler_owned:
            if self.budget_governor is None:
                raise RuntimeError("dynamic_budget_governor_required")
            if not grant_id:
                return self._blocked_candidate_search("search_grant_required")
            try:
                bound_action = self._bind_candidate_search_to_grant(action, grant_id)
                # Always consume against the grant's approved query set, never the
                # model's possibly rewritten texts.
                self.budget_governor.consume_grant(grant_id)
            except ValueError as exc:
                reason = str(exc)
                if reason in {
                    "search_grant_query_mismatch",
                    "search_grant_not_found",
                    "search_grant_run_mismatch",
                } or reason.startswith("grant_already_"):
                    try:
                        self.budget_governor.abandon_grant(
                            grant_id,
                            f"candidate_search_failed:{reason}",
                        )
                    except Exception:
                        pass
                return self._blocked_candidate_search(reason)

        arguments = {
            "action": bound_action.model_dump(mode="json"),
            "grant_id": grant_id,
            "scheduler_owned": scheduler_owned,
            "request": self.request.model_dump(mode="json"),
        }
        tool_call, claimed = self.store.claim_tool_call(
            run_id=self.run_id,
            tool_name="search_repository_candidates",
            arguments=arguments,
        )
        if not claimed and tool_call.output:
            return CandidateSearchObservation.model_validate(tool_call.output)
        if not claimed:
            return self._blocked_candidate_search("identical_tool_call_already_in_progress")

        queries = [item.query for item in bound_action.queries]
        run = self.store.increment_tool_call_count(self.run_id)
        if not self.dynamic_budget or scheduler_owned:
            run = self.store.increment_dynamic_usage(
                self.run_id,
                query_units=len(queries),
                search_batches=1,
            )
        search_request_budget = self._candidate_search_request_budget(len(queries))
        current_run = self._require_run()
        remaining_repository_requests = max(
            0,
            int(current_run.dynamic_limits.max_repository_requests)
            - int(current_run.dynamic_usage.repository_requests),
        )
        self.store.append_event(
            self.run_id,
            "candidate_search_started",
            {
                "queries": queries,
                "action": bound_action.model_dump(mode="json"),
                "grant_id": grant_id,
                "scheduler_owned": scheduler_owned,
                "search_repository_request_budget": search_request_budget,
                "inspection_repository_request_reserve": max(
                    0,
                    remaining_repository_requests - search_request_budget,
                ),
                "idempotency_key": tool_call.idempotency_key,
            },
        )

        try:
            request_callback = self._candidate_search_request_callback(
                search_request_budget
            )
            with meter_repository_requests(request_callback):
                budgeted_search = getattr(
                    self.search_environment,
                    "search_with_request_budget",
                    None,
                )
                if callable(budgeted_search):
                    observation = budgeted_search(
                        bound_action,
                        request_budget=search_request_budget,
                    )
                else:
                    observation = self.search_environment.search(bound_action)
            if self.dynamic_budget and grant_id:
                try:
                    self.store.mark_search_attempt_executed(
                        self.run_id,
                        grant_id,
                        status="executed",
                        extra={"search_id": getattr(observation, "search_id", None)},
                    )
                except Exception:
                    pass
            metered_run = self._require_run()
            previous_high = metered_run.latest_high_relevance_candidate_count
            high_gain_count = max(
                0,
                observation.high_relevance_candidate_count - previous_high,
            )
            # corpus_term_coverage is the diagnostic rename of semantic_coverage.
            # Treat 0.0 default as "unset" when semantic_coverage is the only filled alias.
            semantic_coverage_value = float(getattr(observation, "semantic_coverage", 0.0) or 0.0)
            corpus_coverage_value = float(
                getattr(observation, "corpus_term_coverage", 0.0) or 0.0
            )
            corpus_coverage = (
                corpus_coverage_value
                if corpus_coverage_value > 0.0 or semantic_coverage_value <= 0.0
                else semantic_coverage_value
            )
            previous_corpus = float(
                getattr(metered_run, "latest_corpus_term_coverage", 0.0)
                or metered_run.latest_semantic_coverage
                or 0.0
            )
            coverage_gain = max(0.0, corpus_coverage - previous_corpus)
            no_gain = high_gain_count <= 0 and coverage_gain <= 0.001
            no_gain_streak = metered_run.no_gain_action_count + 1 if no_gain else 0
            recommended_action = (
                "replan_with_a_materially_different_strategy_or_inspect"
                if no_gain_streak >= 2
                else "inspect_high_relevance_candidates"
                if observation.high_relevance_candidate_count > 0
                else "search_unresolved_intent_with_new_strategy"
            )
            observation = observation.model_copy(
                update={
                    "new_high_relevance_candidate_count": high_gain_count,
                    "semantic_coverage_gain": coverage_gain,
                    "corpus_term_coverage_gain": coverage_gain,
                    "recommended_action": recommended_action,
                }
            )
            base_metrics = self.current_metrics()
            preview_count = len(observation.previews)
            review_count = sum(item.needs_review for item in observation.previews)
            metrics = base_metrics.model_copy(
                update={
                    "candidate_shortfall": max(
                        0.0,
                        1.0 - min(observation.candidate_count, 10) / 10.0,
                    ),
                    "quality_gap": 1.0 - corpus_coverage,
                    "semantic_coverage_gap": 1.0 - corpus_coverage,
                    "corpus_term_coverage_gap": 1.0 - corpus_coverage,
                    "hard_constraint_evidence_gap": (
                        float(observation.hard_constraint_evidence_gap)
                        if getattr(observation, "cem_summary", None)
                        else 1.0  # open gap when CEM not computed; never needs_review ratio
                    ),
                    "n_hard_conjunction_pass": int(
                        getattr(observation, "n_hard_conjunction_pass", 0) or 0
                    ),
                    "n_hard_pass_inspected": int(
                        getattr(observation, "n_hard_pass_inspected", 0) or 0
                    ),
                    "unknown_hard_rate": float(
                        getattr(observation, "unknown_hard_rate", 1.0) or 1.0
                    ),
                    "candidate_level_conjunction_coverage": float(
                        getattr(observation, "candidate_level_conjunction_coverage", 0.0)
                        or 0.0
                    ),
                    "duplicate_rate": observation.duplicate_rate,
                    "high_relevance_gain": high_gain_count
                    / max(1, observation.high_relevance_candidate_count),
                    "last_round_yield": min(
                        1.0,
                        observation.new_candidate_count / max(1, len(queries) * 5),
                    ),
                    "no_gain_streak": no_gain_streak,
                    "counts": {
                        **base_metrics.counts,
                        "candidate_projects": observation.candidate_count,
                        "high_relevance_candidates": observation.high_relevance_candidate_count,
                    },
                }
            )
            run = metered_run.model_copy(
                update={
                    "candidate_search_count": metered_run.candidate_search_count + 1,
                    "latest_candidate_search_id": observation.search_id,
                    "latest_high_relevance_candidate_count": observation.high_relevance_candidate_count,
                    "latest_semantic_coverage": corpus_coverage,
                    "latest_corpus_term_coverage": corpus_coverage,
                    "no_gain_action_count": no_gain_streak,
                    "search_recovery_required": no_gain_streak >= 2,
                    "latest_metrics": metrics,
                }
            )
            self.store.save_run(run)
            self.store.complete_tool_call(
                tool_call.idempotency_key,
                observation.model_dump(mode="json"),
            )
            self.store.append_event(
                self.run_id,
                "candidate_search_completed",
                {
                    "queries": queries,
                    "observation": observation.model_dump(mode="json"),
                    "metrics": metrics.model_dump(mode="json"),
                },
            )
            return observation
        except Exception as exc:
            order_violation = re.match(
                r"open_ended_theme_order_violation:\s*expected\s+(.+?);\s*search",
                str(exc),
                flags=re.IGNORECASE,
            )
            if order_violation:
                expected_theme = order_violation.group(1).strip()
                candidate_accessions = list(
                    getattr(self.search_environment, "candidate_accessions", []) or []
                )
                high_relevance = getattr(
                    self.search_environment,
                    "high_relevance_accessions",
                    None,
                )
                high_relevance_count = (
                    len(list(high_relevance()))
                    if callable(high_relevance)
                    else 0
                )
                observation = CandidateSearchObservation(
                    status="completed",
                    search_id=run.latest_candidate_search_id or "search_order_wait",
                    query_yields=[
                        {
                            "query": item.query,
                            "executed_query": item.query,
                            "intent_dimension": item.intent_dimension,
                            "requested_depth": item.depth,
                            "raw_result_count": 0,
                            "new_candidate_count": 0,
                            "duplicate_count": 0,
                            "skipped_reason": (
                                f"waiting_for_confirmed_theme:{expected_theme}"
                            ),
                        }
                        for item in bound_action.queries
                    ],
                    raw_result_count=0,
                    candidate_count=len(candidate_accessions),
                    new_candidate_count=0,
                    duplicate_count=0,
                    duplicate_rate=0.0,
                    high_relevance_candidate_count=high_relevance_count,
                    recommended_action=f"search_confirmed_theme:{expected_theme}",
                    rationale=action.rationale,
                )
                self.store.complete_tool_call(
                    tool_call.idempotency_key,
                    observation.model_dump(mode="json"),
                )
                self.store.append_event(
                    self.run_id,
                    "candidate_search_completed",
                    {
                        "queries": queries,
                        "observation": observation.model_dump(mode="json"),
                        "safe_reorder": {
                            "status": "waiting_for_preceding_theme",
                            "expected_theme": expected_theme,
                        },
                    },
                )
                return observation
            failed = CandidateSearchObservation(
                status="failed",
                search_id=run.latest_candidate_search_id or "search_failed",
                raw_result_count=0,
                candidate_count=0,
                new_candidate_count=0,
                duplicate_count=0,
                duplicate_rate=0.0,
                failures=[str(exc)],
                rationale=action.rationale,
            )
            self.store.complete_tool_call(
                tool_call.idempotency_key,
                failed.model_dump(mode="json"),
                status="failed",
                error=str(exc),
            )
            self.store.append_event(
                self.run_id,
                "candidate_search_failed",
                {"queries": queries, "error": str(exc)},
            )
            return failed

    def inspect_repository_candidates(
        self,
        action: CandidateInspectionAction,
        *,
        scheduler_owned: bool = False,
    ) -> DiscoveryRoundObservation:
        if self.search_environment is None:
            return self._blocked_environment_inspection(
                action,
                "candidate_search_environment_unavailable",
            )
        run = self._require_run()
        if run.selected_round_index is not None:
            return self._blocked_environment_inspection(action, "manifest_already_selected")
        if not scheduler_owned:
            policy = evaluate_tool_policy("inspect_repository_candidates", run)
            if policy.outcome != "allow":
                return self._blocked_environment_inspection(action, policy.reason)
        if action.search_id != run.latest_candidate_search_id:
            return self._blocked_environment_inspection(action, "candidate_search_id_mismatch")

        arguments = {
            "action": action.model_dump(mode="json"),
            "request": self.request.model_dump(mode="json"),
            "task_type": self.task_type,
            "scheduler_owned": scheduler_owned,
        }
        tool_call, claimed = self.store.claim_tool_call(
            run_id=self.run_id,
            tool_name="inspect_repository_candidates",
            arguments=arguments,
        )
        if not claimed and tool_call.output:
            return DiscoveryRoundObservation.model_validate(tool_call.output)
        if not claimed:
            return self._blocked_environment_inspection(
                action,
                "identical_tool_call_already_in_progress",
            )

        previous_pool = (
            _load_manifest(Path(run.candidate_pool_manifest_path))
            if run.candidate_pool_manifest_path
            and Path(run.candidate_pool_manifest_path).exists()
            else None
        )
        round_index = run.discovery_round_count + 1
        run = self.store.increment_tool_call_count(self.run_id)
        run = self.store.save_run(
            run.model_copy(
                update={
                    "status": "running",
                    "discovery_round_count": round_index,
                    "candidate_inspection_count": run.candidate_inspection_count + 1,
                }
            )
        )
        self.store.append_event(
            self.run_id,
            "candidate_inspection_started",
            {
                "round_index": round_index,
                "action": action.model_dump(mode="json"),
                "idempotency_key": tool_call.idempotency_key,
            },
        )
        try:
            request_callback = self._repository_request_callback()
            with meter_repository_requests(request_callback):
                result = self.search_environment.inspect(action)
            return self._persist_environment_inspection(
                run=run,
                round_index=round_index,
                action=action,
                result_manifest=result.manifest,
                usable_files=result.usable_files,
                successful_accessions=result.inspected_accessions,
                failed_accessions=result.failed_accessions,
                previous_pool=previous_pool,
                tool_call_id=tool_call.idempotency_key,
            )
        except Exception as exc:
            observation = DiscoveryRoundObservation(
                status="failed",
                round_index=round_index,
                recommended_action="inspect_other_candidates_or_search",
                blockers=[str(exc)],
                candidate_search={"search_id": action.search_id},
            )
            self.store.complete_tool_call(
                tool_call.idempotency_key,
                observation.model_dump(mode="json"),
                status="failed",
                error=str(exc),
            )
            self.store.append_event(
                self.run_id,
                "candidate_inspection_failed",
                {"round_index": round_index, "error": str(exc)},
            )
            return observation

    def search_and_inspect_repository_candidates(
        self,
        action: CandidateSearchAction,
        grant_id: str | None = None,
    ) -> dict[str, Any]:
        """Search one chunk, then review its new ranked projects immediately."""
        search = self.search_repository_candidates(action, grant_id=grant_id)
        refreshed_run = self._require_run()
        batch_size = min(
            40,
            max(1, int(getattr(self.request, "inspection_batch_size", 30) or 30)),
        )
        first_round_shortfall = (
            refreshed_run.candidate_search_count == 1
            and search.candidate_count < batch_size
        )
        exhausted = any(
            item.skipped_reason == "repository_seed_exhausted"
            for item in search.query_yields
        )
        waiting_for_theme = next(
            (
                str(item.skipped_reason).split(":", 1)[1]
                for item in search.query_yields
                if str(item.skipped_reason or "").startswith(
                    "waiting_for_confirmed_theme:"
                )
            ),
            None,
        )
        pipeline: dict[str, Any] = {
            "mode": "interleaved_search_review",
            "global_dedupe_key": "project_accession",
            "inspection_batch_size": batch_size,
            "first_round_candidate_shortfall": first_round_shortfall,
            "next_action": (
                "search_failed"
                if search.status != "completed"
                else f"search_confirmed_theme:{waiting_for_theme}"
                if waiting_for_theme
                else "advance_to_next_confirmed_theme"
                if exhausted
                else "deepen_primary_theme"
                if first_round_shortfall
                else "continue_primary_theme_and_review_new_candidates"
            ),
            "skipped_inspection_reason": None,
        }
        automatic_inspection: DiscoveryRoundObservation | None = None

        ranked_accessions: list[str] = []
        high_relevance = getattr(
            self.search_environment,
            "high_relevance_accessions",
            None,
        )
        if search.status == "completed" and callable(high_relevance):
            ranked_accessions = list(high_relevance())
        already_inspected = {
            str(accession).strip().upper()
            for accession in refreshed_run.inspected_candidate_accessions
        }
        pending: list[str] = []
        seen = set(already_inspected)
        for raw_accession in ranked_accessions:
            accession = str(raw_accession).strip().upper()
            if not accession or accession in seen:
                continue
            seen.add(accession)
            pending.append(accession)
            if len(pending) >= batch_size:
                break

        if search.status != "completed":
            pipeline["skipped_inspection_reason"] = "search_not_completed"
        elif not pending:
            pipeline["skipped_inspection_reason"] = "no_new_ranked_candidates"
        else:
            self.store.append_event(
                self.run_id,
                "candidate_pipeline_review_started",
                {
                    "search_id": search.search_id,
                    "accessions": pending,
                    "dedupe_key": "project_accession",
                    "batch_size": batch_size,
                    "first_round_candidate_shortfall": first_round_shortfall,
                },
            )
            automatic_inspection = self.inspect_repository_candidates(
                CandidateInspectionAction(
                    search_id=search.search_id,
                    accessions=pending,
                    rationale=(
                        "Automatically review newly discovered high-relevance projects "
                        "while the next primary-theme search chunk can continue."
                    ),
                )
            )
            self.store.append_event(
                self.run_id,
                "candidate_pipeline_review_completed",
                {
                    "search_id": search.search_id,
                    "accessions": pending,
                    "status": automatic_inspection.status,
                    "next_action": pipeline["next_action"],
                },
            )

        return {
            "search": search.model_dump(mode="json"),
            "automatic_inspection": (
                automatic_inspection.model_dump(mode="json")
                if automatic_inspection is not None
                else None
            ),
            "pipeline": pipeline,
        }

    def run_confirmed_term_pipeline(self) -> dict[str, Any]:
        """Run confirmed terms in order with scheduler-owned pagination and review.

        One user-confirmed phrase is one logical repository task. Pagination
        chunks are an internal transport detail: the Agent does not choose the
        next offset, repeat an exhausted term, or decide when to advance. In
        fixed-target mode each search chunk is reviewed immediately and no new
        work is scheduled after enough qualified projects exist. Open-ended
        mode still exhausts one phrase before draining its review queue.
        """

        if self.search_environment is None:
            return {
                "status": "blocked",
                "reason": "candidate_search_environment_unavailable",
                "all_terms_exhausted": False,
                "pending_review_count": 0,
                "term_count": 0,
            }
        terms = [
            " ".join(str(term).split())
            for term in self.request.query_terms or []
            if str(term).strip()
        ]
        terms = list(dict.fromkeys(terms))
        if not terms:
            return {
                "status": "blocked",
                "reason": "confirmed_repository_terms_required",
                "all_terms_exhausted": False,
                "pending_review_count": 0,
                "term_count": 0,
            }

        is_exhausted = getattr(self.search_environment, "is_query_exhausted", None)
        reviewable = getattr(self.search_environment, "reviewable_accessions", None)
        if not callable(is_exhausted):
            return {
                "status": "blocked",
                "reason": "repository_exhaustion_probe_unavailable",
                "all_terms_exhausted": False,
                "pending_review_count": 0,
                "term_count": len(terms),
            }
        if not callable(reviewable):
            return {
                "status": "blocked",
                "reason": "candidate_review_queue_unavailable",
                "all_terms_exhausted": False,
                "pending_review_count": 0,
                "term_count": len(terms),
            }

        fixed_target = self.request.quota_flexibility == "fixed"
        target_project_count = max(1, int(self.request.max_projects))

        def qualified_project_count() -> int:
            return sum(
                is_qualified_project_judgment(judgment)
                for judgment in self._require_run().project_judgments.values()
            )

        target_reached = (
            fixed_target
            and qualified_project_count() >= target_project_count
        )
        self.store.append_event(
            self.run_id,
            "confirmed_theme_pipeline_started",
            {
                "terms": terms,
                "term_count": len(terms),
                "pagination": "internal_until_repository_exhaustion",
                "review_queue": "global_project_accession_dedupe",
                "review_workers": 4,
                "execution_mode": "fixed_target" if fixed_target else "exhaustive",
                "target_project_count": (
                    target_project_count if fixed_target else None
                ),
            },
        )
        term_results: list[dict[str, Any]] = []
        failed_terms: list[str] = []
        attempted_reviews: set[str] = set()
        max_chunks_per_term = max(
            1,
            int(self._require_run().dynamic_limits.max_repository_requests),
        )
        repository_chunk_size = (
            100 if fixed_target else MAX_REPOSITORY_QUERY_DEPTH
        )

        for term_index, term in enumerate(terms, start=1):
            if target_reached:
                break
            self._check_pipeline_cancel()
            term_role = "primary_theme" if term_index == 1 else "theme_synonym"
            self.store.append_event(
                self.run_id,
                "repository_term_task_started",
                {
                    "term": term,
                    "term_index": term_index,
                    "term_count": len(terms),
                    "role": term_role,
                    "status": "running",
                },
            )
            chunks_completed = 0
            raw_result_count = 0
            new_candidate_count = 0
            no_progress_chunks = 0
            terminal_error = ""
            review_totals = {
                "review_batches_completed": 0,
                "queued_project_count": 0,
                "failed_review_count": 0,
                "reviewed_project_count": len(
                    self._require_run().inspected_candidate_accessions
                ),
                "pending_review_count": 0,
                "deferred_candidate_count": 0,
            }

            while chunks_completed < max_chunks_per_term:
                self._check_pipeline_cancel()
                if (
                    fixed_target
                    and qualified_project_count() >= target_project_count
                ):
                    target_reached = True
                    break
                if bool(is_exhausted(term)):
                    break
                search = self.search_repository_candidates(
                    CandidateSearchAction(
                        queries=[
                            RepositoryQuery(
                                query=term,
                                depth=repository_chunk_size,
                                intent_dimension="confirmed theme",
                                expected_gain=(
                                    "Read the next internal repository pages for this "
                                    "confirmed phrase until the repository is exhausted."
                                ),
                                budget_role=term_role,
                            )
                        ],
                        candidate_limit=1_000,
                        rationale=(
                            "Deterministic confirmed-term pagination chunk "
                            f"{chunks_completed + 1}; offsets and synonym order "
                            "are scheduler-owned."
                        ),
                    ),
                    scheduler_owned=True,
                )
                chunks_completed += 1
                raw_result_count += int(search.raw_result_count)
                new_candidate_count += int(search.new_candidate_count)
                exhausted_after_chunk = bool(is_exhausted(term))
                self.store.append_event(
                    self.run_id,
                    "repository_term_chunk_completed",
                    {
                        "term": term,
                        "term_index": term_index,
                        "chunk_index": chunks_completed,
                        "search_id": search.search_id,
                        "status": search.status,
                        "raw_result_count": search.raw_result_count,
                        "new_candidate_count": search.new_candidate_count,
                        "candidate_count": search.candidate_count,
                        "exhausted": exhausted_after_chunk,
                        "chunk_size": repository_chunk_size,
                    },
                )
                if search.status != "completed":
                    terminal_error = (
                        search.stop_reason
                        or next(iter(search.failures), "")
                        or "repository_search_not_completed"
                    )
                    break
                if fixed_target:
                    review_summary = self._drain_candidate_review_queue(
                        reviewable=reviewable,
                        attempted_reviews=attempted_reviews,
                        term=term,
                        term_index=term_index,
                        term_count=len(terms),
                        stop_when_qualified=target_project_count,
                    )
                    for key in (
                        "review_batches_completed",
                        "queued_project_count",
                        "failed_review_count",
                    ):
                        review_totals[key] += int(review_summary.get(key) or 0)
                    for key in (
                        "reviewed_project_count",
                        "pending_review_count",
                        "deferred_candidate_count",
                    ):
                        review_totals[key] = int(review_summary.get(key) or 0)
                    if int(review_summary.get("failed_review_count") or 0) > 0:
                        terminal_error = "candidate_review_queue_not_drained"
                        break
                    target_reached = bool(review_summary.get("target_reached"))
                    if target_reached:
                        self.store.append_event(
                            self.run_id,
                            "fixed_project_target_reached",
                            {
                                "term": term,
                                "term_index": term_index,
                                "target_project_count": target_project_count,
                                "qualified_project_count": qualified_project_count(),
                                "reviewed_project_count": len(
                                    self._require_run().inspected_candidate_accessions
                                ),
                                "search_chunks_completed": chunks_completed,
                                "deferred_candidate_count": int(
                                    review_summary.get("deferred_candidate_count") or 0
                                ),
                            },
                        )
                        break
                if exhausted_after_chunk:
                    break
                if search.raw_result_count <= 0:
                    no_progress_chunks += 1
                else:
                    no_progress_chunks = 0
                if no_progress_chunks >= 2:
                    terminal_error = "repository_term_pagination_made_no_progress"
                    break

            exhausted = bool(is_exhausted(term))
            if target_reached:
                term_payload = {
                    "term": term,
                    "term_index": term_index,
                    "term_count": len(terms),
                    "role": term_role,
                    "status": "completed",
                    "completion_reason": "fixed_project_target_reached",
                    "exhausted": exhausted,
                    "chunks_completed": chunks_completed,
                    "raw_result_count": raw_result_count,
                    "new_candidate_count": new_candidate_count,
                    **review_totals,
                }
                self.store.append_event(
                    self.run_id,
                    "repository_term_task_completed",
                    term_payload,
                )
                term_results.append(dict(term_payload))
                break
            if not exhausted and not terminal_error:
                terminal_error = "repository_term_safety_limit_reached"
            if terminal_error:
                failed_terms.append(term)
                self.store.append_event(
                    self.run_id,
                    "repository_term_task_failed",
                    {
                        "term": term,
                        "term_index": term_index,
                        "term_count": len(terms),
                        "role": term_role,
                        "status": "failed",
                        "chunks_completed": chunks_completed,
                        "raw_result_count": raw_result_count,
                        "new_candidate_count": new_candidate_count,
                        "reason": terminal_error,
                        "exhausted": exhausted,
                    },
                )
                term_results.append(
                    {
                        "term": term,
                        "status": "failed",
                        "exhausted": exhausted,
                        "chunks_completed": chunks_completed,
                        "raw_result_count": raw_result_count,
                        "new_candidate_count": new_candidate_count,
                        "reason": terminal_error,
                    }
                )
                break

            review_summary = (
                review_totals
                if fixed_target
                else self._drain_candidate_review_queue(
                    reviewable=reviewable,
                    attempted_reviews=attempted_reviews,
                    term=term,
                    term_index=term_index,
                    term_count=len(terms),
                )
            )
            if (
                int(review_summary.get("failed_review_count") or 0) > 0
                or int(review_summary.get("pending_review_count") or 0) > 0
            ):
                terminal_error = "candidate_review_queue_not_drained"
                failed_terms.append(term)
                term_payload = {
                    "term": term,
                    "term_index": term_index,
                    "term_count": len(terms),
                    "role": term_role,
                    "status": "failed",
                    "exhausted": exhausted,
                    "chunks_completed": chunks_completed,
                    "raw_result_count": raw_result_count,
                    "new_candidate_count": new_candidate_count,
                    "reason": terminal_error,
                    **review_summary,
                }
                self.store.append_event(
                    self.run_id,
                    "repository_term_task_failed",
                    term_payload,
                )
                term_results.append(dict(term_payload))
                break
            term_payload = {
                "term": term,
                "term_index": term_index,
                "term_count": len(terms),
                "role": term_role,
                "status": "completed",
                "exhausted": exhausted,
                "chunks_completed": chunks_completed,
                "raw_result_count": raw_result_count,
                "new_candidate_count": new_candidate_count,
                **review_summary,
            }
            self.store.append_event(
                self.run_id,
                "repository_term_task_completed",
                term_payload,
            )
            term_results.append(dict(term_payload))

        run = self._require_run()
        reviewed = {
            str(accession).strip().upper()
            for accession in run.inspected_candidate_accessions
        }
        pending = [
            str(accession).strip().upper()
            for accession in reviewable()
            if str(accession).strip().upper() not in reviewed
        ]
        qualified_count = qualified_project_count()
        target_reached = (
            fixed_target and qualified_count >= target_project_count
        )
        deferred_candidate_count = len(pending) if target_reached else 0
        all_terms_exhausted = not failed_terms and all(
            bool(is_exhausted(term))
            for term in terms
        )
        status = (
            "completed"
            if target_reached or (all_terms_exhausted and not pending)
            else "partial"
        )
        payload = {
            "status": status,
            "term_count": len(terms),
            "completed_term_count": sum(
                item.get("status") == "completed" for item in term_results
            ),
            "all_terms_exhausted": all_terms_exhausted,
            "target_project_count": (
                target_project_count if fixed_target else None
            ),
            "qualified_project_count": qualified_count,
            "target_reached": target_reached,
            "failed_terms": failed_terms,
            "candidate_count": len(
                list(getattr(self.search_environment, "candidate_accessions", []) or [])
            ),
            "reviewed_project_count": len(reviewed),
            "pending_review_count": 0 if target_reached else len(pending),
            "deferred_candidate_count": deferred_candidate_count,
            "terms": term_results,
        }
        self.store.append_event(
            self.run_id,
            "confirmed_theme_pipeline_completed",
            payload,
        )
        return payload

    def _drain_candidate_review_queue(
        self,
        *,
        reviewable: Callable[..., list[str]],
        attempted_reviews: set[str],
        term: str,
        term_index: int,
        term_count: int,
        stop_when_qualified: int | None = None,
    ) -> dict[str, Any]:
        batch_size = min(
            40,
            max(1, int(getattr(self.request, "inspection_batch_size", 30) or 30)),
        )
        batches_completed = 0
        queued_count = 0
        failed_count = 0
        while True:
            self._check_pipeline_cancel()
            run = self._require_run()
            qualified_count = sum(
                is_qualified_project_judgment(judgment)
                for judgment in run.project_judgments.values()
            )
            if (
                stop_when_qualified is not None
                and qualified_count >= stop_when_qualified
            ):
                break
            inspected = {
                str(accession).strip().upper()
                for accession in run.inspected_candidate_accessions
            }
            pending = []
            for raw_accession in reviewable():
                accession = str(raw_accession).strip().upper()
                if (
                    not accession
                    or accession in inspected
                    or accession in attempted_reviews
                ):
                    continue
                pending.append(accession)
            if not pending:
                break
            remaining_target = (
                max(1, stop_when_qualified - qualified_count)
                if stop_when_qualified is not None
                else batch_size
            )
            batch = pending[: min(batch_size, remaining_target)]
            attempted_reviews.update(batch)
            queued_count += len(batch)
            self.store.append_event(
                self.run_id,
                "candidate_review_queue_batch_started",
                {
                    "term": term,
                    "term_index": term_index,
                    "term_count": term_count,
                    "batch_index": batches_completed + 1,
                    "batch_size": len(batch),
                    "accessions": batch,
                    "dedupe_key": "project_accession",
                    "review_workers": min(4, len(batch)),
                },
            )
            latest_search_id = str(
                getattr(self.search_environment, "latest_search_id", "")
                or run.latest_candidate_search_id
                or ""
            )
            if not latest_search_id:
                failed_count += len(batch)
                break
            observation = self.inspect_repository_candidates(
                CandidateInspectionAction(
                    search_id=latest_search_id,
                    accessions=batch,
                    rationale=(
                        "Deterministic review queue for newly discovered, globally "
                        "deduplicated repository projects."
                    ),
                ),
                scheduler_owned=True,
            )
            batches_completed += 1
            if observation.status != "completed":
                failed_count += len(batch)
            else:
                failed_count += len(observation.inspection_outcomes) - sum(
                    item.get("category")
                    in {"usable_files", "scientific_exclusion", "no_usable_files"}
                    for item in observation.inspection_outcomes
                )
                updated_run = self._backfill_judgments_for_inspected_pool_projects(
                    self._require_run()
                )
                pool_path = str(updated_run.candidate_pool_manifest_path or "")
                if pool_path and Path(pool_path).exists():
                    self._maybe_emit_partial_l1_delivery(
                        run=updated_run,
                        manifest=_load_manifest(Path(pool_path)),
                    )
            self.store.append_event(
                self.run_id,
                "candidate_review_queue_batch_completed",
                {
                    "term": term,
                    "term_index": term_index,
                    "term_count": term_count,
                    "batch_index": batches_completed,
                    "batch_size": len(batch),
                    "status": observation.status,
                    "reviewed_project_count": len(
                        self._require_run().inspected_candidate_accessions
                    ),
                },
            )
        reviewed_accessions = {
            str(item).strip().upper()
            for item in self._require_run().inspected_candidate_accessions
        }
        qualified_count = sum(
            is_qualified_project_judgment(judgment)
            for judgment in self._require_run().project_judgments.values()
        )
        target_reached = (
            stop_when_qualified is not None
            and qualified_count >= stop_when_qualified
        )
        remaining_candidate_count = sum(
            str(accession).strip().upper() not in reviewed_accessions
            for accession in reviewable()
        )
        return {
            "review_batches_completed": batches_completed,
            "queued_project_count": queued_count,
            "failed_review_count": failed_count,
            "reviewed_project_count": len(
                self._require_run().inspected_candidate_accessions
            ),
            "qualified_project_count": qualified_count,
            "target_reached": target_reached,
            "pending_review_count": (
                0 if target_reached else remaining_candidate_count
            ),
            "deferred_candidate_count": (
                remaining_candidate_count if target_reached else 0
            ),
        }

    def _check_pipeline_cancel(self) -> None:
        checker = getattr(self.search_environment, "_check_cancel", None)
        if callable(checker):
            checker()

    def publish_verified_file_batches(
        self,
        *,
        manifest: DatasetManifest,
        terminal: bool = False,
    ) -> dict[str, Any] | None:
        """Publish every complete verified-file batch and an optional final tail."""

        return self._maybe_emit_partial_l1_delivery(
            run=self._require_run(),
            manifest=manifest,
            terminal=terminal,
        )

    def _maybe_emit_partial_l1_delivery(
        self,
        *,
        run: AgentRunRecord,
        manifest: DatasetManifest,
        terminal: bool = False,
    ) -> dict[str, Any] | None:
        """Emit incremental L1 usable batch files every N verified usable files.

        "越多越好" keeps searching under safety ceilings, but operators can start
        batch work when each tranche of ~N qualified files is ready.  A terminal
        call also publishes the final short tranche.
        """
        batch_size = int(getattr(self.request, "partial_delivery_batch_size", None) or 0)
        if batch_size <= 0:
            # Default 500 files when maximize / open-ended harvest is on.
            maximize = bool(getattr(self.request, "harvest_all_qualified", False)) or str(
                getattr(self.request, "portfolio_size_preference", "") or ""
            ).startswith("maximize")
            batch_size = 500 if maximize else 0
        if batch_size <= 0:
            return None
        projects_by_accession = {
            project.project_accession: project
            for project in manifest.projects
            if project.project_accession
        }
        qualified_projects = {
            accession
            for accession, judgment in run.project_judgments.items()
            if is_qualified_project_judgment(judgment)
        }

        def file_identifier(file: DiscoveredFile) -> str:
            repository = str(file.repository or "unknown").strip().casefold()
            accession = str(file.project_accession or "").strip().upper()
            native = str(
                file.file_accession_or_path or file.file_name or ""
            ).strip()
            return f"{repository}:{accession}:{native}"

        files = sorted(
            (
                file
                for file in manifest.files
                if _is_delivery_eligible(
                    projects_by_accession.get(file.project_accession),
                    file,
                )
                and file.task_readiness_status != "not_ready"
                and file.project_accession in qualified_projects
            ),
            key=file_identifier,
        )
        projects = sorted({file.project_accession for file in files})
        published = list(run.published_verified_project_batches)
        published_file_identifiers: set[str] = set()
        published_project_accessions: set[str] = set()
        for item in published:
            published_project_accessions.update(
                str(accession).strip().upper()
                for accession in item.project_accessions
            )
            published_file_identifiers.update(item.file_identifiers)
            if item.file_identifiers or not Path(item.manifest_path).exists():
                continue
            legacy_manifest = _load_manifest(Path(item.manifest_path))
            published_file_identifiers.update(
                file_identifier(file) for file in legacy_manifest.files
            )
        unpublished_files = [
            file
            for file in files
            if file_identifier(file) not in published_file_identifiers
        ]
        latest_payload: dict[str, Any] | None = None
        while len(unpublished_files) >= batch_size or (
            terminal and unpublished_files
        ):
            batch_index = len(published) + 1
            batch_files = unpublished_files[:batch_size]
            unpublished_files = unpublished_files[len(batch_files):]
            terminal_batch = terminal and not unpublished_files
            batch_file_identifiers = [
                file_identifier(file) for file in batch_files
            ]
            batch_accessions = sorted(
                {file.project_accession for file in batch_files}
            )
            keep = set(batch_accessions)
            batch_projects = [
                project
                for project in manifest.projects
                if project.project_accession in keep
            ]
            batch_manifest = manifest.model_copy(
                update={
                    "projects": batch_projects,
                    "files": batch_files,
                    "summary": {
                        **dict(manifest.summary),
                        "artifact_type": "verified_file_batch",
                        "batch_index": batch_index,
                        "batch_size": batch_size,
                        "delivery_unit": "file",
                        "terminal": terminal_batch,
                        "verified_project_count": len(batch_projects),
                        "verified_file_count": len(batch_files),
                    },
                }
            )
            paths = write_dataset_manifest(
                batch_manifest,
                self.output_dir / "verified_batches" / f"batch_{batch_index:03d}",
            )
            batch_record = VerifiedProjectBatch(
                batch_index=batch_index,
                batch_size=batch_size,
                project_count=len(batch_projects),
                file_count=len(batch_files),
                cumulative_verified_project_count=len(
                    published_project_accessions | set(batch_accessions)
                ),
                cumulative_verified_file_count=(
                    len(published_file_identifiers)
                    + len(batch_file_identifiers)
                ),
                project_accessions=batch_accessions,
                file_identifiers=batch_file_identifiers,
                delivery_unit="file",
                manifest_path=str(paths["dataset_manifest_json"]),
                terminal=terminal_batch,
                message=(
                    f"Verified file batch {batch_index} is ready: "
                    f"{len(batch_files)} files from "
                    f"{len(batch_projects)} projects."
                ),
            )
            latest_payload = batch_record.model_dump(mode="json")
            published.append(batch_record)
            published_project_accessions.update(batch_accessions)
            published_file_identifiers.update(batch_file_identifiers)
            self.store.append_event(
                self.run_id,
                "verified_project_batch_published",
                latest_payload,
            )
        self.store.save_run(
            run.model_copy(
                update={
                    "verified_project_accessions": projects,
                    "verified_project_batch_size": batch_size,
                    "published_verified_project_batches": published,
                }
            )
        )
        return latest_payload

    def _persist_environment_inspection(
            self,
            *,
            run: AgentRunRecord,
            round_index: int,
            action: CandidateInspectionAction,
            result_manifest: DatasetManifest,
            usable_files: int,
            successful_accessions: list[str],
            failed_accessions: list[str],
            previous_pool: DatasetManifest | None,
            tool_call_id: str,
        ) -> DiscoveryRoundObservation:
            manifest = result_manifest
            if self.task_type:
                manifest = annotate_manifest_task_readiness(manifest, self.task_type)
            summary = dict(manifest.summary)
            summary["openai_agents_control_plane"] = {
                "run_id": self.run_id,
                "round_index": round_index,
                "runtime": "openai_agents",
                "search_id": action.search_id,
            }
            manifest = manifest.model_copy(update={"run_id": self.run_id, "summary": summary})
            round_dir = self.output_dir / f"round_{round_index:02d}"
            paths = write_dataset_manifest(manifest, round_dir)
            events = self.store.list_events(self.run_id)
            search_event = next(
                (
                    event
                    for event in reversed(events)
                    if event.event_type == "candidate_search_completed"
                    and (event.payload.get("observation") or {}).get("search_id") == action.search_id
                ),
                None,
            )
            candidate_search = (
                dict(search_event.payload.get("observation") or {})
                if search_event is not None
                else {"search_id": action.search_id}
            )
            queries = [str(value) for value in (search_event.payload.get("queries") or [])] if search_event else []
            observation = _observation_from_manifest(
                manifest,
                round_index=round_index,
                queries=queries,
                paths=paths,
            ).model_copy(update={"candidate_search": candidate_search})
            diagnosis = self._diagnose_search_result(
                run=run,
                proposed_queries=queries or ["candidate_pool_inspection"],
                summary=manifest.summary,
            )
            artifacts = dict(run.artifacts)
            artifacts[f"discovery_round_{round_index:02d}"] = ArtifactReference(
                path=str(paths["dataset_manifest_json"]),
                artifact_type="dataset_manifest",
                schema_version="dataset-manifest/v1",
            )
            round_manifests = [
                _load_manifest(Path(reference.path))
                for name, reference in sorted(artifacts.items())
                if name.startswith("discovery_round_") and Path(reference.path).exists()
            ]
            pool_manifest = _merge_discovery_manifests(
                round_manifests,
                request=self.request,
                run_id=self.run_id,
                retain_all_candidates=True,
            )
            pool_paths = write_dataset_manifest(pool_manifest, self.output_dir / "candidate_pool")
            pool_observation = _observation_from_manifest(
                pool_manifest,
                round_index=round_index,
                queries=queries,
                paths=pool_paths,
            )
            metered_run = self._require_run()
            prior_queries = [
                str(query)
                for event in events
                if event.event_type == "candidate_search_completed"
                and event is not search_event
                for query in event.payload.get("queries", [])
            ]
            metrics = evaluate_round_metrics(
                pool_manifest,
                previous_pool,
                request=self.request,
                queries=queries,
                prior_queries=prior_queries,
                usage=metered_run.dynamic_usage,
                limits=metered_run.dynamic_limits,
                round_index=round_index,
            )
            rich_metrics = metered_run.latest_metrics
            selected_count = max(1, len(manifest.files))
            needs_review = sum(file.needs_review for file in manifest.files)
            metrics = metrics.model_copy(
                update={
                    "semantic_coverage_gap": (
                        rich_metrics.semantic_coverage_gap if rich_metrics else 1.0
                    ),
                    # WP-A: keep CEM gap from search metrics; needs_review ratio is not hard gap.
                    "hard_constraint_evidence_gap": float(
                        getattr(rich_metrics, "hard_constraint_evidence_gap", 1.0)
                        if rich_metrics is not None
                        else 1.0
                    ),
                    "duplicate_rate": rich_metrics.duplicate_rate if rich_metrics else 0.0,
                    "high_relevance_gain": (
                        rich_metrics.high_relevance_gain if rich_metrics else 0.0
                    ),
                    "inspection_yield": min(1.0, usable_files / selected_count),
                    "no_gain_streak": metered_run.no_gain_action_count,
                }
            )
            inspected_accessions = _normalize_accessions(
                [*metered_run.inspected_candidate_accessions, *successful_accessions]
            )
            minimum_inspections = self._required_high_relevance_inspections(
                metered_run.latest_high_relevance_candidate_count,
            )
            harvest_all = bool(getattr(self.request, "harvest_all_qualified", False)) or (
                str(getattr(self.request, "quantity_scope", "") or "") == "portfolio"
                and str(getattr(self.request, "portfolio_size_preference", "") or "").startswith("maximize")
            )
            inspection_budget_remaining = round_index < metered_run.budget.max_discovery_rounds
            # In maximize mode, do not claim selection_ready merely because round budget
            # ended after inspecting a tiny batch. Require either enough inspections or
            # an explicit stop / exhausted high-relevance pool.
            if harvest_all:
                selection_ready = (
                    len(inspected_accessions) >= minimum_inspections
                    or metered_run.search_stopped
                    or (
                        not inspection_budget_remaining
                        and len(inspected_accessions) >= max(25, min(minimum_inspections, 100))
                    )
                )
            else:
                selection_ready = (
                    len(inspected_accessions) >= minimum_inspections
                    or metered_run.search_stopped
                    or not inspection_budget_remaining
                )
            artifacts["candidate_pool"] = ArtifactReference(
                path=str(pool_paths["dataset_manifest_json"]),
                artifact_type="dataset_manifest",
                schema_version="dataset-manifest/v1",
            )
            unresolved = candidate_search.get("unresolved_intent_terms") or []
            if not selection_ready:
                recommendation = "inspect_more_high_relevance_candidates"
            elif unresolved and usable_files > 0:
                recommendation = "search_unresolved_intent_or_finalize_with_explicit_gaps"
            else:
                recommendation = observation.recommended_action
            observation = observation.model_copy(
                update={
                    "candidate_pool_manifest_path": str(pool_paths["dataset_manifest_json"]),
                    "pooled_selected_projects": pool_observation.selected_projects,
                    "pooled_selected_files": pool_observation.selected_files,
                    "metrics": metrics,
                    "diagnosis": diagnosis,
                    "project_assessments": _project_assessments(
                        manifest,
                        candidate_search,
                    ),
                    "inspection_outcomes": list(
                        manifest.summary.get("inspection_outcomes") or []
                    ),
                    "verified_project_count": len(
                        metered_run.verified_project_accessions
                    ),
                    "published_verified_project_batches": (
                        metered_run.published_verified_project_batches
                    ),
                    "inspected_candidate_count": len(inspected_accessions),
                    "minimum_high_relevance_inspections": minimum_inspections,
                    "selection_ready": selection_ready,
                    "recommended_action": recommendation,
                    "warnings": _dedupe(
                        [
                            *observation.warnings,
                            *(
                                ["inspection_failed_accessions:" + ",".join(failed_accessions)]
                                if failed_accessions
                                else []
                            ),
                        ]
                    ),
                }
            )
            self.store.complete_tool_call(tool_call_id, observation.model_dump(mode="json"))
            run = metered_run.model_copy(
                update={
                    "artifacts": artifacts,
                    "candidate_pool_manifest_path": str(pool_paths["dataset_manifest_json"]),
                    "current_manifest_path": (
                        str(pool_paths["dataset_manifest_json"])
                        if pool_observation.selected_files > 0
                        else str(paths["dataset_manifest_json"])
                    ),
                    "warnings": pool_observation.warnings,
                    "blockers": pool_observation.blockers,
                    "latest_metrics": metrics,
                    "consecutive_zero_yield": diagnosis.consecutive_zero_yield,
                    "search_recovery_required": diagnosis.recovery_required,
                    "last_search_strategy": diagnosis.strategy,
                    "inspected_candidate_accessions": inspected_accessions,
                }
            )
            self.store.save_run(run)
            self.store.append_event(
                self.run_id,
                "candidate_inspection_completed",
                {
                    "round_index": round_index,
                    "action": action.model_dump(mode="json"),
                    "observation": observation.model_dump(mode="json"),
                },
            )
            self.store.append_event(
                self.run_id,
                "round_value_evaluated",
                metrics.model_dump(mode="json"),
            )
            return observation

    def _repository_request_callback(self) -> Callable[[str, str], None]:
        if self.dynamic_budget:
            if self.budget_governor is None:
                raise RuntimeError("dynamic_budget_governor_required")
            return self.budget_governor.record_repository_request

        def callback(_repository: str, _operation: str) -> None:
            self.store.increment_dynamic_usage(
                self.run_id,
                repository_requests=1,
            )

        return callback

    def _candidate_search_request_budget(self, query_count: int) -> int:
        run = self._require_run()
        remaining = max(
            0,
            int(run.dynamic_limits.max_repository_requests)
            - int(run.dynamic_usage.repository_requests),
        )
        if remaining <= 0:
            return 0
        minimum_search = min(remaining, max(2, int(query_count) * 2))
        inspection_projects = min(20, max(1, int(self.request.max_projects)))
        desired_inspection_reserve = inspection_projects * 3
        inspection_reserve = min(
            desired_inspection_reserve,
            max(0, remaining - minimum_search),
        )
        return max(0, remaining - inspection_reserve)

    def _candidate_search_request_callback(
        self,
        request_budget: int,
    ) -> Callable[[str, str], None]:
        callback = self._repository_request_callback()
        search_requests = 0

        def budgeted_callback(repository: str, operation: str) -> None:
            nonlocal search_requests
            if operation == "search_projects":
                if search_requests >= request_budget:
                    raise RuntimeError(
                        "search_request_budget_reserved_for_inspection"
                    )
                search_requests += 1
            callback(repository, operation)

        return budgeted_callback

    def _blocked_candidate_search(self, reason: str) -> CandidateSearchObservation:
        run = self._require_run()
        self.store.append_event(
            self.run_id,
            "tool_denied",
            {"tool": "search_repository_candidates", "reason": reason},
        )
        return CandidateSearchObservation(
            status="blocked",
            search_id=run.latest_candidate_search_id or "search_blocked",
            raw_result_count=0,
            candidate_count=0,
            new_candidate_count=0,
            duplicate_count=0,
            duplicate_rate=0.0,
            failures=[reason],
        )

    def _blocked_environment_inspection(
        self,
        action: CandidateInspectionAction,
        reason: str,
    ) -> DiscoveryRoundObservation:
        run = self._require_run()
        self.store.append_event(
            self.run_id,
            "tool_denied",
            {
                "tool": "inspect_repository_candidates",
                "reason": reason,
                "search_id": action.search_id,
            },
        )
        return DiscoveryRoundObservation(
            status="blocked",
            round_index=run.discovery_round_count + 1,
            recommended_action="revise_inspection_or_search",
            blockers=[reason],
            candidate_search={"search_id": action.search_id},
        )

    def search_repository_datasets(
        self,
        queries: list[str],
        grant_id: str | None = None,
    ) -> DiscoveryRoundObservation:
        queries = _normalize_queries(queries)
        run = self._require_run()
        if self.dynamic_budget and run.search_stopped:
            return self._blocked_observation(queries, "dynamic_search_stopped")
        if run.selected_round_index is not None:
            return DiscoveryRoundObservation(
                status="blocked",
                round_index=run.discovery_round_count + 1,
                queries=queries,
                recommended_action="stop",
                blockers=["manifest_already_selected"],
            )
        policy = evaluate_tool_policy("search_repository_datasets", run)
        if policy.outcome != "allow":
            observation = DiscoveryRoundObservation(
                status="blocked",
                round_index=run.discovery_round_count + 1,
                queries=queries,
                recommended_action="stop",
                blockers=[policy.reason],
            )
            self.store.append_event(
                self.run_id,
                "tool_denied",
                {"tool": "search_repository_datasets", "policy": policy.model_dump(mode="json")},
            )
            return observation
        if self.dynamic_budget:
            if self.budget_governor is None:
                raise RuntimeError("dynamic_budget_governor_required")
            if not grant_id:
                return self._blocked_observation(queries, "search_grant_required")
            try:
                grant = self.budget_governor.store.load_search_grant(grant_id)
                if grant is None or grant.run_id != self.run_id:
                    raise ValueError("search_grant_not_found")
                # Force exact approved queries for dataset search as well.
                queries = list(grant.approved_queries)
                self.budget_governor.consume_grant(grant_id)
            except ValueError as exc:
                reason = str(exc)
                if reason in {
                    "search_grant_query_mismatch",
                    "search_grant_not_found",
                    "search_grant_run_mismatch",
                } or reason.startswith("grant_already_"):
                    try:
                        self.budget_governor.abandon_grant(
                            grant_id,
                            f"dataset_search_failed:{reason}",
                        )
                    except Exception:
                        pass
                return self._blocked_observation(queries, reason)
        elif run.discovery_round_count >= run.budget.max_discovery_rounds:
            return self._blocked_observation(queries, "discovery_round_budget_exhausted")

        proposed_strategy = self._query_strategy(queries)
        if run.search_recovery_required and proposed_strategy != "atomic_seed":
            diagnosis = SearchDiagnosis(
                health="selectivity_suspected",
                strategy=proposed_strategy,
                proposed_queries=queries,
                executed_queries=[],
                consecutive_zero_yield=run.consecutive_zero_yield,
                recovery_required=True,
                reason="Previous zero-yield search requires atomic high-recall repository seeds.",
            )
            observation = DiscoveryRoundObservation(
                status="blocked",
                round_index=run.discovery_round_count + 1,
                queries=queries,
                recommended_action="retry_with_atomic_repository_seeds",
                blockers=["search_recovery_requires_atomic_queries"],
                diagnosis=diagnosis,
            )
            self.store.append_event(
                self.run_id,
                "search_strategy_rejected",
                diagnosis.model_dump(mode="json"),
            )
            return observation

        run = self._require_run()
        previous_pool = (
            _load_manifest(Path(run.candidate_pool_manifest_path))
            if run.candidate_pool_manifest_path and Path(run.candidate_pool_manifest_path).exists()
            else None
        )
        prior_queries = [
            str(query)
            for event in self.store.list_events(self.run_id)
            if event.event_type == "tool_started"
            and event.payload.get("tool") == "search_repository_datasets"
            for query in event.payload.get("queries", [])
        ]

        arguments = {
            "queries": queries,
            "request": self.request.model_dump(mode="json"),
            "task_type": self.task_type,
        }
        tool_call, claimed = self.store.claim_tool_call(
            run_id=self.run_id,
            tool_name="search_repository_datasets",
            arguments=arguments,
        )
        if not claimed and tool_call.output:
            cached = DiscoveryRoundObservation.model_validate(tool_call.output)
            self.store.append_event(
                self.run_id,
                "tool_result_reused",
                {"tool": tool_call.tool_name, "idempotency_key": tool_call.idempotency_key},
            )
            return cached
        if not claimed:
            self.store.append_event(
                self.run_id,
                "tool_call_already_in_progress",
                {"tool": tool_call.tool_name, "idempotency_key": tool_call.idempotency_key},
            )
            return DiscoveryRoundObservation(
                status="blocked",
                round_index=run.discovery_round_count + 1,
                queries=queries,
                recommended_action="wait_for_existing_tool_call",
                blockers=["identical_tool_call_already_in_progress"],
            )

        round_index = run.discovery_round_count + 1
        run = run.model_copy(
            update={
                "status": "running",
                "tool_call_count": run.tool_call_count + 1,
                "discovery_round_count": round_index,
            }
        )
        run = self.store.save_run(run)
        self.store.append_event(
            self.run_id,
            "tool_started",
            {
                "tool": "search_repository_datasets",
                "round_index": round_index,
                "queries": queries,
                "idempotency_key": tool_call.idempotency_key,
            },
        )

        try:
            if self.dynamic_budget:
                assert self.budget_governor is not None
                request_callback = self.budget_governor.record_repository_request
            else:
                self.store.increment_dynamic_usage(
                    self.run_id,
                    query_units=len(queries),
                    search_batches=1,
                    enforce_limits=False,
                )

                def request_callback(repository: str, operation: str) -> None:
                    self.store.increment_dynamic_usage(
                        self.run_id,
                        repository_requests=1,
                        enforce_limits=False,
                    )

            with meter_repository_requests(request_callback):
                manifest = self.discovery_func(self.request, memory=self.memory, queries=queries)
            if self.task_type:
                manifest = annotate_manifest_task_readiness(manifest, self.task_type)
            summary = dict(manifest.summary)
            summary["openai_agents_control_plane"] = {
                "run_id": self.run_id,
                "round_index": round_index,
                "runtime": "openai_agents",
            }
            manifest = manifest.model_copy(update={"run_id": self.run_id, "summary": summary})
            round_dir = self.output_dir / f"round_{round_index:02d}"
            paths = write_dataset_manifest(manifest, round_dir)
            observation = _observation_from_manifest(
                manifest,
                round_index=round_index,
                queries=queries,
                paths=paths,
            )
            diagnosis = self._diagnose_search_result(
                run=run,
                proposed_queries=queries,
                summary=manifest.summary,
            )
            recommendation = observation.recommended_action
            if diagnosis.health == "selectivity_suspected":
                recommendation = "retry_with_atomic_repository_seeds"
            elif diagnosis.health == "repository_unavailable":
                recommendation = "retry_repository_or_stop"
            elif diagnosis.health == "no_match_after_recovery":
                recommendation = "stop_or_adjust_hard_constraints"
            observation = observation.model_copy(
                update={"diagnosis": diagnosis, "recommended_action": recommendation}
            )
            artifacts = dict(run.artifacts)
            artifacts[f"discovery_round_{round_index:02d}"] = ArtifactReference(
                path=str(paths["dataset_manifest_json"]),
                artifact_type="dataset_manifest",
                schema_version="dataset-manifest/v1",
            )
            round_manifests = [
                _load_manifest(Path(reference.path))
                for name, reference in sorted(artifacts.items())
                if name.startswith("discovery_round_") and Path(reference.path).exists()
            ]
            pool_manifest = _merge_discovery_manifests(
                round_manifests,
                request=self.request,
                run_id=self.run_id,
                retain_all_candidates=True,
            )
            pool_paths = write_dataset_manifest(pool_manifest, self.output_dir / "candidate_pool")
            pool_observation = _observation_from_manifest(
                pool_manifest,
                round_index=round_index,
                queries=queries,
                paths=pool_paths,
            )
            metered_run = self._require_run()
            metrics = evaluate_round_metrics(
                pool_manifest,
                previous_pool,
                request=self.request,
                queries=queries,
                prior_queries=prior_queries,
                usage=metered_run.dynamic_usage,
                limits=metered_run.dynamic_limits,
                round_index=round_index,
            )
            artifacts["candidate_pool"] = ArtifactReference(
                path=str(pool_paths["dataset_manifest_json"]),
                artifact_type="dataset_manifest",
                schema_version="dataset-manifest/v1",
            )
            retained_previous_candidates = observation.selected_files <= 0 and pool_observation.selected_files > 0
            if retained_previous_candidates and diagnosis.recovery_required:
                diagnosis = diagnosis.model_copy(
                    update={
                        "recovery_required": False,
                        "reason": (
                            f"{diagnosis.reason} A prior round still provides a usable candidate pool, "
                            "so selection may proceed."
                        ),
                    }
                )
            observation = observation.model_copy(
                update={
                    "candidate_pool_manifest_path": str(pool_paths["dataset_manifest_json"]),
                    "pooled_selected_projects": pool_observation.selected_projects,
                    "pooled_selected_files": pool_observation.selected_files,
                    "metrics": metrics,
                    "diagnosis": diagnosis,
                    "recommended_action": (
                        "accept_candidate_pool_or_retry" if retained_previous_candidates else recommendation
                    ),
                }
            )
            self.store.complete_tool_call(
                tool_call.idempotency_key,
                observation.model_dump(mode="json"),
            )
            run = metered_run.model_copy(
                update={
                    "artifacts": artifacts,
                    "candidate_pool_manifest_path": str(pool_paths["dataset_manifest_json"]),
                    "current_manifest_path": (
                        str(pool_paths["dataset_manifest_json"])
                        if pool_observation.selected_files > 0
                        else str(paths["dataset_manifest_json"])
                    ),
                    "warnings": pool_observation.warnings,
                    "blockers": pool_observation.blockers,
                    "latest_metrics": metrics,
                    "consecutive_zero_yield": diagnosis.consecutive_zero_yield,
                    "search_recovery_required": diagnosis.recovery_required,
                    "search_recovery_attempts": (
                        metered_run.search_recovery_attempts + (1 if diagnosis.recovery_attempted else 0)
                    ),
                    "last_search_strategy": diagnosis.strategy,
                }
            )
            self.store.save_run(run)
            self.store.append_event(
                self.run_id,
                "search_diagnosis_recorded",
                diagnosis.model_dump(mode="json"),
            )
            if diagnosis.recovery_attempted and diagnosis.health == "healthy_yield":
                self.store.append_event(
                    self.run_id,
                    "search_recovery_succeeded",
                    diagnosis.model_dump(mode="json"),
                )
            self.store.append_event(
                self.run_id,
                "round_value_evaluated",
                metrics.model_dump(mode="json"),
            )
            self.store.append_event(
                self.run_id,
                "tool_completed",
                {
                    "tool": "search_repository_datasets",
                    "round_index": round_index,
                    "observation": observation.model_dump(mode="json"),
                    "selected_manifest_path": run.current_manifest_path,
                    "selected_manifest_retained": retained_previous_candidates,
                },
            )
            return observation
        except Exception as exc:
            failure_text = str(exc).casefold()
            failure_health = (
                "response_invalid"
                if any(term in failure_text for term in ("json", "response schema", "response shape", "decode"))
                else "repository_unavailable"
            )
            diagnosis = SearchDiagnosis(
                health=failure_health,
                strategy=self._query_strategy(queries),
                proposed_queries=queries,
                executed_queries=[],
                consecutive_zero_yield=run.consecutive_zero_yield,
                recovery_required=False,
                reason=str(exc),
            )
            observation = DiscoveryRoundObservation(
                status="failed",
                round_index=round_index,
                queries=queries,
                recommended_action="retry_repository_or_stop",
                warnings=["repository_discovery_tool_failed"],
                blockers=[str(exc)],
                diagnosis=diagnosis,
            )
            self.store.complete_tool_call(
                tool_call.idempotency_key,
                observation.model_dump(mode="json"),
                status="failed",
                error=str(exc),
            )
            warnings = _dedupe([*run.warnings, f"repository_discovery_tool_failed:{exc}"])
            self.store.save_run(run.model_copy(update={"warnings": warnings}))
            self.store.append_event(
                self.run_id,
                "search_diagnosis_recorded",
                diagnosis.model_dump(mode="json"),
            )
            self.store.append_event(
                self.run_id,
                "tool_failed",
                {
                    "tool": "search_repository_datasets",
                    "round_index": round_index,
                    "error": str(exc),
                },
            )
            return observation

    def record_project_judgments(
        self,
        judgments: list[ProjectJudgmentInput],
    ) -> dict[str, Any]:
        run = self._require_run()
        if run.selected_round_index is not None:
            return {"status": "blocked", "blockers": ["manifest_already_selected"]}
        policy = evaluate_tool_policy("submit_project_judgments", run)
        if policy.outcome != "allow":
            return {"status": "blocked", "blockers": [policy.reason]}
        if not judgments:
            return {"status": "blocked", "blockers": ["project_judgments_required"]}

        accessions = [item.project_accession for item in judgments]
        if len(accessions) != len(set(accessions)):
            return {"status": "blocked", "blockers": ["duplicate_project_judgment"]}
        inspected = {item.upper() for item in run.inspected_candidate_accessions}
        invalid_backed = [
            item.project_accession
            for item in judgments
            if item.evidence_stage == "inspection"
            and item.project_accession not in inspected
        ]
        if invalid_backed:
            return {
                "status": "blocked",
                "blockers": [
                    "project_not_inspected:" + ",".join(sorted(invalid_backed))
                ],
            }
        known_accessions = getattr(self.search_environment, "candidate_accessions", None)
        if isinstance(known_accessions, list) and known_accessions:
            known = {str(item).upper() for item in known_accessions}
            unknown = sorted(accession for accession in accessions if accession not in known)
            if unknown:
                return {
                    "status": "blocked",
                    "blockers": ["project_outside_candidate_pool:" + ",".join(unknown)],
                }
        inspected_manifest: DatasetManifest | None = None
        if run.candidate_pool_manifest_path and Path(run.candidate_pool_manifest_path).exists():
            inspected_manifest = _load_manifest(Path(run.candidate_pool_manifest_path))
        project_by_accession = {
            project.project_accession.upper(): project
            for project in (inspected_manifest.projects if inspected_manifest is not None else [])
        }
        files_by_accession: dict[str, list[DiscoveredFile]] = {}
        for file in inspected_manifest.files if inspected_manifest is not None else []:
            files_by_accession.setdefault(file.project_accession.upper(), []).append(file)

        active_constraints = {
            constraint.id: constraint for constraint in self.request.scientific_constraints
        }
        audit_blockers: list[str] = []
        repair_context: dict[str, dict[str, Any]] = {}
        for judgment in judgments:
            project = project_by_accession.get(judgment.project_accession)
            project_files = files_by_accession.get(judgment.project_accession, [])
            evidence_values = _project_evidence_values(
                project,
                project_files,
            )
            available_evidence_refs = set(evidence_values)
            assessment_context: list[dict[str, Any]] = []
            for assessment in judgment.constraint_assessments:
                cited_values = {
                    ref: _bounded_repair_evidence(evidence_values[ref])
                    for ref in assessment.evidence_refs
                    if ref in evidence_values
                }
                assessment_context.append(
                    {
                        "constraint_id": assessment.constraint_id,
                        "cited_evidence_values": cited_values,
                    }
                )
            repair_context[judgment.project_accession] = {
                "evidence_stage": judgment.evidence_stage,
                "available_evidence_refs": sorted(available_evidence_refs),
                "constraint_assessments": assessment_context,
            }
            for problem, constraint_id, invalid_refs in (
                _constraint_assessment_evidence_problems(
                    judgment,
                    available_evidence_refs,
                    constraints=active_constraints,
                    evidence_values=evidence_values,
                )
            ):
                if problem == "required":
                    audit_blockers.append(
                        "constraint_evidence_refs_required:"
                        + judgment.project_accession
                        + ":"
                        + constraint_id
                    )
                    continue
                if problem == "unsupported":
                    audit_blockers.append(
                        "constraint_observed_value_not_supported_by_evidence:"
                        + judgment.project_accession
                        + ":"
                        + constraint_id
                    )
                    continue
                audit_blockers.append(
                    "unavailable_constraint_evidence_ref:"
                    + judgment.project_accession
                    + ":"
                    + constraint_id
                    + ":"
                    + ",".join(invalid_refs)
                )
            if judgment.evidence_stage != "inspection":
                continue
            if (
                judgment.status == "evidence_backed"
                and judgment.hard_gate == "pass"
                and judgment.grade is not None
                and judgment.grade >= 2
                and judgment.decision != "include"
            ):
                audit_blockers.append(
                    "evidence_backed_usable_grade_requires_include:"
                    + judgment.project_accession
                )
            if (
                inspected_manifest is not None
                and judgment.decision == "include"
                and (project is None or not project_files or judgment.target_file_count <= 0)
            ):
                audit_blockers.append(
                    "included_project_has_no_inspected_files:" + judgment.project_accession
                )
            unknown_refs = sorted(
                ref for ref in judgment.evidence_refs
                if ref not in _PROJECT_EVIDENCE_REF_NAMES
            )
            if unknown_refs:
                audit_blockers.append(
                    "unknown_evidence_ref:"
                    + judgment.project_accession
                    + ":"
                    + ",".join(unknown_refs)
                )
            unavailable_refs = sorted(
                ref for ref in judgment.evidence_refs
                if ref in _PROJECT_EVIDENCE_REF_NAMES
                and ref not in available_evidence_refs
            )
            if unavailable_refs:
                audit_blockers.append(
                    "unavailable_evidence_ref:"
                    + judgment.project_accession
                    + ":"
                    + ",".join(unavailable_refs)
                )
            assessments = {
                item.constraint_id: item for item in judgment.constraint_assessments
            }
            for constraint_id, constraint in active_constraints.items():
                if constraint.scope == "portfolio":
                    continue
                assessment = assessments.get(constraint_id)
                if assessment is None:
                    audit_blockers.append(
                        "constraint_not_assessed:"
                        + judgment.project_accession
                        + ":"
                        + constraint_id
                    )
                    continue
                if judgment.decision == "include" and constraint.strength == "hard":
                    if constraint.scope in {"file", "sample"}:
                        passing_files = _files_passing_hard_scoped_constraints(
                            project_files,
                            judgment,
                            [constraint],
                        )
                        if assessment.status not in {"pass", "partial"} or not passing_files:
                            audit_blockers.append(
                                "hard_scoped_constraint_has_no_delivery_files:"
                                + judgment.project_accession
                                + ":"
                                + constraint_id
                            )
                    elif assessment.status != "pass":
                        audit_blockers.append(
                            "hard_constraint_not_passed:"
                            + judgment.project_accession
                            + ":"
                            + constraint_id
                        )
                    elif evaluate_constraint_value(
                        constraint,
                        assessment.observed_value,
                    ) is not True:
                        audit_blockers.append(
                            "hard_constraint_observed_value_conflict:"
                            + judgment.project_accession
                            + ":"
                            + constraint_id
                        )
            if judgment.grade == 3 and judgment.decision == "include" and project is not None:
                project_only = sum(
                    file.evidence_level == "project" for file in project_files
                )
                weak_or_review = sum(
                    file.needs_review or file.validity_status != "valid"
                    for file in project_files
                )
                if project.needs_review or project_only > len(project_files) * 0.2:
                    audit_blockers.append(
                        "grade_3_requires_file_level_evidence:"
                        + judgment.project_accession
                    )
                elif weak_or_review > len(project_files) * 0.5:
                    audit_blockers.append(
                        "grade_3_requires_majority_strict_files:"
                        + judgment.project_accession
                    )
        if audit_blockers:
            return {
                "status": "blocked",
                "blockers": _dedupe(audit_blockers),
                "recommended_action": (
                    "Use repair_context instead of guessing. For a search-stage provisional judgment, "
                    "leave constraint_assessments empty and record unresolved facts in missing_information. "
                    "For an inspection-stage constraint, cite only available_evidence_refs and copy a "
                    "literal persisted value into observed_value; put synthesized counts and conclusions "
                    "in reason/explanation. Use grade 2 with explicit limitations when evidence is weak, "
                    "and never turn soft preferences into hard blockers."
                ),
                "repair_context": repair_context,
            }
        merged = dict(run.project_judgments)
        for judgment in judgments:
            previous = merged.get(judgment.project_accession)
            if (
                previous is not None
                and previous.evidence_stage == "inspection"
                and judgment.evidence_stage == "search"
            ):
                return {
                    "status": "blocked",
                    "blockers": [
                        "project_judgment_stage_regression:" + judgment.project_accession
                    ],
                }

        arguments = {"judgments": [item.model_dump(mode="json") for item in judgments]}
        tool_call, claimed = self.store.claim_tool_call(
            run_id=self.run_id,
            tool_name="submit_project_judgments",
            arguments=arguments,
        )
        if not claimed and tool_call.output:
            return dict(tool_call.output)
        if not claimed:
            return {
                "status": "blocked",
                "blockers": ["identical_tool_call_already_in_progress"],
            }

        previous_summary = summarize_project_judgments(
            merged,
            target_project_count=self.request.max_projects,
        )
        updated_count = sum(item.project_accession in merged for item in judgments)
        created_count = len(judgments) - updated_count
        for judgment in judgments:
            merged[judgment.project_accession] = judgment
        summary = summarize_project_judgments(
            merged,
            target_project_count=self.request.max_projects,
        )
        previous_qualified = int(previous_summary["qualified_projects"])
        qualified_count = int(summary["qualified_projects"])
        submitted_inspection = any(item.evidence_stage == "inspection" for item in judgments)
        qualified_no_gain_count = (
            0
            if qualified_count > previous_qualified
            else run.qualified_no_gain_count + 1
            if submitted_inspection
            else run.qualified_no_gain_count
        )
        latest_metrics = run.latest_metrics
        if latest_metrics is not None:
            latest_metrics = latest_metrics.model_copy(
                update={
                    "counts": {
                        **latest_metrics.counts,
                        "assessed_projects": int(summary["assessed_projects"]),
                        "qualified_projects": qualified_count,
                        "investigate_projects": int(summary["investigate_projects"]),
                        "rejected_projects": int(summary["rejected_projects"]),
                    },
                    "deltas": {
                        **latest_metrics.deltas,
                        "qualified_projects": qualified_count - previous_qualified,
                    },
                    "no_gain_streak": qualified_no_gain_count,
                }
            )
        run = self.store.save_run(
            run.model_copy(
                update={
                    "project_judgments": merged,
                    "qualified_project_count": qualified_count,
                    "qualified_no_gain_count": qualified_no_gain_count,
                    "latest_metrics": latest_metrics,
                    "tool_call_count": run.tool_call_count + 1,
                }
            )
        )
        candidate_pool_path = str(run.candidate_pool_manifest_path or "").strip()
        if candidate_pool_path and Path(candidate_pool_path).exists():
            try:
                self._maybe_emit_partial_l1_delivery(
                    run=run,
                    manifest=_load_manifest(Path(candidate_pool_path)),
                )
            except Exception as exc:
                warnings = _dedupe(
                    [*self._require_run().warnings, f"verified_project_batch_retryable:{exc}"]
                )
                self.store.save_run(self._require_run().model_copy(update={"warnings": warnings}))
                self.store.append_event(
                    self.run_id,
                    "verified_project_batch_failed",
                    {"error": str(exc), "retryable": True},
                )
        payload = {
            "status": "completed",
            "project_accessions": accessions,
            "created_count": created_count,
            "updated_count": updated_count,
            "recorded_count": len(judgments),
            "qualified_project_count": summary["qualified_projects"],
            "target_project_count": summary["qualified_target"],
            "quality_target_reached": summary["quality_target_reached"],
            "project_judgment_summary": summary,
        }
        self.store.complete_tool_call(tool_call.idempotency_key, payload)
        self.store.append_event(
            self.run_id,
            "project_judgments_recorded",
            {
                **payload,
                "judgments": [item.model_dump(mode="json") for item in judgments],
            },
        )
        return payload

    def select_discovery_manifest(
        self,
        round_index: int,
        rationale: str,
        project_accessions: list[str] | None = None,
        file_identifiers: list[str] | None = None,
    ) -> dict[str, Any]:
        run = self._require_run()
        policy = evaluate_tool_policy("select_discovery_manifest", run)
        if policy.outcome != "allow":
            payload = {"status": "blocked", "round_index": round_index, "blockers": [policy.reason]}
            self.store.append_event(
                self.run_id,
                "tool_denied",
                {"tool": "select_discovery_manifest", "policy": policy.model_dump(mode="json")},
            )
            return payload

        round_index = int(round_index)
        rationale = " ".join(str(rationale or "").split()).strip()
        if round_index < 0 or round_index > run.discovery_round_count:
            return self._selection_rejected(round_index, "manifest_round_not_found")
        if not rationale:
            return self._selection_rejected(round_index, "selection_rationale_required")
        if len(rationale) > 2000:
            return self._selection_rejected(round_index, "selection_rationale_too_long")

        selected_accessions = _normalize_accessions(project_accessions or [])
        selection_audit: DiscoveryQualityAudit | None = None

        required_inspections = self._required_high_relevance_inspections(
            run.latest_high_relevance_candidate_count,
        )
        inspected_count = len(run.inspected_candidate_accessions)
        inspection_budget_remaining = (
            not run.search_stopped
            and run.discovery_round_count < run.budget.max_discovery_rounds
        )
        if (
            self.search_environment is not None
            and inspected_count < required_inspections
            and inspection_budget_remaining
        ):
            selection_audit = self.audit_discovery_state(meter_tool=False)
            if not selection_audit.ready_for_selection:
                return self._selection_rejected(
                    round_index,
                    "high_relevance_candidates_require_more_inspection",
                )

        manifest_path = self._manifest_path_for_selection(run, round_index)
        if manifest_path is None or not manifest_path.exists():
            return self._selection_rejected(round_index, "manifest_round_not_found")

        manifest = _load_manifest(manifest_path)
        portfolio_state: PortfolioState | None = None
        frozen_file_identifiers: set[str] = set()
        if self.request.portfolio_spec:
            portfolio_state = self._load_portfolio_state()
            if portfolio_state.status != "frozen":
                return self._selection_rejected(round_index, "portfolio_must_be_frozen_before_publication")
            frozen_file_identifiers = {
                value.casefold() for value in portfolio_state.selected_file_identifiers
            }
            requested_file_identifiers = {
                str(value).strip().casefold()
                for value in (file_identifiers or portfolio_state.selected_file_identifiers)
                if str(value).strip()
            }
            if requested_file_identifiers != frozen_file_identifiers:
                return self._selection_rejected(round_index, "selection_file_ids_do_not_match_frozen_portfolio")
            file_identifiers = list(requested_file_identifiers)
            frozen_projects = set(portfolio_state.selected_project_accessions)
            if selected_accessions and set(selected_accessions) != {value.upper() for value in frozen_projects}:
                return self._selection_rejected(round_index, "selection_projects_do_not_match_frozen_portfolio")
            selected_accessions = sorted(frozen_projects)
        judgment_gate_enabled = self._quality_audit_required(run)
        eligible_accessions = {
            accession
            for accession, judgment in run.project_judgments.items()
            if is_qualified_project_judgment(judgment)
        }
        if judgment_gate_enabled:
            ineligible = sorted(
                accession
                for accession in selected_accessions
                if accession not in eligible_accessions
            )
            if ineligible:
                return self._selection_rejected(
                    round_index,
                    "project_judgment_not_eligible:" + ",".join(ineligible),
                )
            if not selected_accessions:
                # Prefer all currently eligible projects in the manifest. The old
                # max_projects hard-cap was truncating broad "越多越好" runs back to 20.
                selected_accessions = sorted(
                    accession
                    for accession in eligible_accessions
                    if any(
                        project.project_accession.upper() == accession
                        for project in manifest.projects
                    )
                )
            if not selected_accessions:
                return self._selection_rejected(
                    round_index,
                    "no_evidence_backed_grade_2_or_3_projects",
                )

            harvest_all = bool(getattr(self.request, "harvest_all_qualified", False)) or (
                str(getattr(self.request, "quantity_scope", "") or "") == "portfolio"
                and str(getattr(self.request, "portfolio_size_preference", "") or "").startswith("maximize")
            )
            # Maximize mode: never treat max_projects as "enough, stop searching".
            # Keep searching while gain remains and safety ceilings allow.
            target_reached = (
                False
                if harvest_all
                else len(eligible_accessions) >= self.request.max_projects
            )
            can_continue = (
                not run.search_stopped
                and run.discovery_round_count < run.budget.max_discovery_rounds
                and run.qualified_no_gain_count < 2
                and run.remaining_model_turn_budget() > 0
            )
            if self.search_environment is not None and not target_reached and can_continue:
                selection_audit = self.audit_discovery_state(meter_tool=False)
                if not selection_audit.ready_for_selection:
                    return self._selection_rejected(
                        round_index,
                        "qualified_project_target_requires_more_search",
                    )
        candidate_count = len(manifest.projects)
        harvest_all = bool(getattr(self.request, "harvest_all_qualified", False)) or (
            str(getattr(self.request, "quantity_scope", "") or "") == "portfolio"
            and str(getattr(self.request, "portfolio_size_preference", "") or "").startswith("maximize")
        )
        if (
            not selected_accessions
            and round_index == 0
            and candidate_count > self.request.max_projects
            and not harvest_all
        ):
            return self._selection_rejected(
                round_index,
                "explicit_project_selection_required_for_candidate_pool",
            )
        # Soft ceiling only for small curated targets. Portfolio maximize keeps every
        # eligible grade 2-3 project; quality gate already excluded low-quality ones.
        soft_cap = self.request.max_projects
        portfolio_mode = harvest_all or (
            str(getattr(self.request, "quantity_scope", "") or "") == "portfolio"
            or str(getattr(self.request, "portfolio_size_preference", "") or "").startswith("maximize")
            or soft_cap >= 100
        )
        if selected_accessions and len(selected_accessions) > soft_cap and not portfolio_mode:
            return self._selection_rejected(
                round_index,
                "selection_exceeds_max_projects",
            )
        if selected_accessions and portfolio_mode:
            # Keep the full eligible/agent-selected quality set.
            pass
        if selected_accessions:
            available = {project.project_accession.upper() for project in manifest.projects}
            missing = [accession for accession in selected_accessions if accession not in available]
            if missing:
                return self._selection_rejected(
                    round_index,
                    "selection_project_not_in_manifest:" + ",".join(missing),
                )

        if judgment_gate_enabled:
            audit = selection_audit or self.audit_discovery_state(meter_tool=False)
            run = self._require_run()
            if not audit.ready_for_selection:
                return self._selection_rejected(
                    round_index,
                    "discovery_quality_audit_requires_repair",
                )
            delivery_accessions = {
                accession
                for action in audit.repair_actions
                if action.action == "select_manifest"
                for accession in action.project_accessions
            }
            non_delivery = sorted(set(selected_accessions) - delivery_accessions)
            if non_delivery:
                return self._selection_rejected(
                    round_index,
                    "selection_project_not_delivery_eligible:" + ",".join(non_delivery),
                )
            required_selection_count = min(
                int(self.request.max_projects),
                len(delivery_accessions),
            )
            if len(selected_accessions) < required_selection_count:
                return self._selection_rejected(
                    round_index,
                    "selection_omits_delivery_eligible_projects",
                )
            if harvest_all and set(selected_accessions) != delivery_accessions:
                return self._selection_rejected(
                    round_index,
                    "maximize_selection_must_retain_all_delivery_eligible_projects",
                )

        arguments = {
            "round_index": round_index,
            "project_accessions": selected_accessions,
            "file_identifiers": file_identifiers or [],
            "rationale": rationale,
        }
        tool_call, claimed = self.store.claim_tool_call(
            run_id=self.run_id,
            tool_name="select_discovery_manifest",
            arguments=arguments,
        )
        if not claimed and tool_call.output:
            return dict(tool_call.output)
        if not claimed:
            return self._selection_rejected(round_index, "identical_tool_call_already_in_progress")

        run = self.store.save_run(run.model_copy(update={"tool_call_count": run.tool_call_count + 1}))
        selected_set = set(selected_accessions) if selected_accessions else None
        deliverable = _delivery_manifest_subset(
            manifest,
            selected_accessions=selected_set,
            selected_file_identifiers={
                str(value).casefold() for value in (file_identifiers or []) if str(value).strip()
            } or None,
            project_judgments=run.project_judgments,
            scientific_constraints=self.request.scientific_constraints,
        )
        if portfolio_state is not None:
            portfolio_coverage = assess_portfolio_coverage(
                deliverable.files,
                portfolio_state.spec,
            )
            if not portfolio_coverage.ready:
                payload = {
                    "status": "blocked",
                    "round_index": round_index,
                    "manifest_path": str(manifest_path),
                    "selected_files": len(deliverable.files),
                    "blockers": ["frozen_portfolio_final_audit_failed"],
                    "portfolio_coverage": portfolio_coverage.model_dump(mode="json"),
                }
                self.store.complete_tool_call(tool_call.idempotency_key, payload)
                self.store.append_event(self.run_id, "manifest_selection_rejected", payload)
                return payload
        manifest = _merge_discovery_manifests(
            [deliverable],
            request=self.request,
            run_id=self.run_id,
        )
        if judgment_gate_enabled:
            judgment_summary = summarize_project_judgments(
                run.project_judgments,
                target_project_count=self.request.max_projects,
            )
            manifest = manifest.model_copy(
                update={
                    "summary": {
                        **manifest.summary,
                        "project_judgment_summary": judgment_summary,
                        "project_judgments": {
                            accession: run.project_judgments[accession].model_dump(mode="json")
                            for accession in selected_accessions
                            if accession in run.project_judgments
                        },
                    }
                }
            )
        selected_files = _selected_file_count(manifest)
        if selected_files <= 0:
            payload = {
                "status": "blocked",
                "round_index": round_index,
                "manifest_path": str(manifest_path),
                "selected_files": 0,
                "blockers": ["selected_manifest_has_no_files"],
            }
            self.store.complete_tool_call(tool_call.idempotency_key, payload)
            self.store.append_event(self.run_id, "manifest_selection_rejected", payload)
            return payload

        if judgment_gate_enabled:
            final_audit = self.audit_selected_manifest(
                manifest,
                selection_accessions=set(selected_accessions),
            )
            if not final_audit.ready_for_selection:
                payload = {
                    "status": "blocked",
                    "round_index": round_index,
                    "manifest_path": str(manifest_path),
                    "selected_files": selected_files,
                    "blockers": ["final_manifest_quality_audit_requires_repair"],
                    "audit": final_audit.model_dump(mode="json"),
                }
                self.store.complete_tool_call(tool_call.idempotency_key, payload)
                self.store.append_event(self.run_id, "manifest_selection_rejected", payload)
                return payload
            run = self._require_run()

        if run.business_completion is not None and not business_completion_allows_success(
            run.business_completion
        ):
            payload = {
                "status": "blocked",
                "round_index": round_index,
                "manifest_path": str(manifest_path),
                "selected_files": selected_files,
                "blockers": ["build_ready_publication_contract_not_satisfied"],
                "business_completion": run.business_completion.model_dump(mode="json"),
            }
            self.store.complete_tool_call(tool_call.idempotency_key, payload)
            self.store.append_event(self.run_id, "manifest_selection_rejected", payload)
            return payload

        paths = write_dataset_manifest(manifest, self.output_dir / "final_selection")
        manifest_path = paths["dataset_manifest_json"]

        _, warnings = _recommend_next_action(manifest.summary, selected_files)
        run = self.store.save_run(
            run.model_copy(
                update={
                    "current_manifest_path": str(manifest_path),
                    "selected_round_index": round_index,
                    "selection_rationale": rationale,
                    "warnings": warnings,
                    "blockers": [],
                }
            )
        )
        payload = {
            "status": "completed",
            "round_index": round_index,
            "manifest_path": str(manifest_path),
            "selected_projects": int(manifest.summary.get("selected_projects") or len(manifest.projects)),
            "selected_files": selected_files,
            "selected_project_accessions": [
                project.project_accession for project in manifest.projects
            ],
            "rationale": rationale,
        }
        self.store.complete_tool_call(tool_call.idempotency_key, payload)
        self.store.append_event(self.run_id, "manifest_selected", payload)
        return payload

    def auto_select_best_manifest(self) -> AgentRunRecord:
        run = self._require_run()
        if run.selected_round_index is not None:
            return run
        if run.business_completion is not None and not business_completion_allows_success(
            run.business_completion
        ):
            blocker = "build_ready_publication_contract_not_satisfied"
            updated = self.store.save_run(
                run.model_copy(
                    update={
                        "blockers": _dedupe([*run.blockers, blocker]),
                        "stop_reason": "selection_quality_gate_not_completed",
                    }
                )
            )
            self.store.append_event(
                self.run_id,
                "manifest_selection_rejected",
                {
                    "status": "blocked",
                    "round_index": 0,
                    "blockers": [blocker],
                    "auto_selected": True,
                    "business_completion": run.business_completion.model_dump(
                        mode="json"
                    ),
                },
            )
            return updated

        harvest_all = bool(getattr(self.request, "harvest_all_qualified", False)) or (
            str(getattr(self.request, "quantity_scope", "") or "") == "portfolio"
            and str(getattr(self.request, "portfolio_size_preference", "") or "").startswith("maximize")
        )
        eligible_accessions = {
            accession
            for accession, judgment in run.project_judgments.items()
            if is_qualified_project_judgment(judgment)
        }
        rejection_reasons: list[str] = []
        if not run.project_judgments:
            rejection_reasons.append("auto_selection_requires_project_judgments")
        if not eligible_accessions:
            rejection_reasons.append("auto_selection_requires_eligible_projects")
        required_inspections = self._required_high_relevance_inspections(
            run.latest_high_relevance_candidate_count,
        )
        can_continue = (
            not run.search_stopped
            and run.discovery_round_count < run.budget.max_discovery_rounds
            and run.qualified_no_gain_count < 2
            and run.remaining_model_turn_budget() > 0
        )
        needs_more_inspection = (
            len(run.inspected_candidate_accessions) < required_inspections
            and can_continue
        )
        needs_more_qualified = (
            not harvest_all
            and len(eligible_accessions) < self.request.max_projects
            and can_continue
        )
        selection_audit = (
            self.audit_discovery_state(meter_tool=False)
            if needs_more_inspection or needs_more_qualified
            else None
        )
        if (
            needs_more_inspection
            and selection_audit is not None
            and not selection_audit.ready_for_selection
        ):
            rejection_reasons.append("high_relevance_candidates_require_more_inspection")
        if (
            needs_more_qualified
            and selection_audit is not None
            and not selection_audit.ready_for_selection
        ):
            rejection_reasons.append("qualified_project_target_requires_more_search")
        if rejection_reasons:
            updated = self.store.save_run(
                run.model_copy(
                    update={
                        "blockers": _dedupe([*run.blockers, *rejection_reasons]),
                        "stop_reason": "selection_quality_gate_not_completed",
                    }
                )
            )
            self.store.append_event(
                self.run_id,
                "manifest_selection_rejected",
                {
                    "status": "blocked",
                    "round_index": 0,
                    "blockers": rejection_reasons,
                    "auto_selected": True,
                },
            )
            return updated

        audit = selection_audit or self.audit_discovery_state(meter_tool=False)
        run = self._require_run()
        if not audit.ready_for_selection:
            updated = self.store.save_run(
                run.model_copy(
                    update={
                        "blockers": _dedupe(
                            [*run.blockers, "discovery_quality_audit_requires_repair"]
                        ),
                        "stop_reason": "selection_quality_gate_not_completed",
                    }
                )
            )
            self.store.append_event(
                self.run_id,
                "manifest_selection_rejected",
                {
                    "status": "blocked",
                    "round_index": 0,
                    "blockers": ["discovery_quality_audit_requires_repair"],
                    "audit": audit.model_dump(mode="json"),
                    "auto_selected": True,
                },
            )
            return updated
        audit_delivery_accessions = {
            accession
            for action in audit.repair_actions
            if action.action == "select_manifest"
            for accession in action.project_accessions
        }
        eligible_accessions &= audit_delivery_accessions

        def eligible_manifest(manifest: DatasetManifest) -> DatasetManifest | None:
            if harvest_all and eligible_accessions:
                projects = [
                    project
                    for project in manifest.projects
                    if project.project_accession.upper() in eligible_accessions
                ]
                files = [
                    file
                    for file in manifest.files
                    if file.project_accession.upper() in eligible_accessions
                ]
            elif run.project_judgments:
                projects = [
                    project
                    for project in manifest.projects
                    if project.project_accession.upper() in eligible_accessions
                ]
                files = [
                    file
                    for file in manifest.files
                    if file.project_accession.upper() in eligible_accessions
                ]
            else:
                projects = list(manifest.projects)
                files = list(manifest.files)
            deliverable = _delivery_manifest_subset(
                manifest.model_copy(update={"projects": projects, "files": files}),
                project_judgments=run.project_judgments,
                scientific_constraints=self.request.scientific_constraints,
            )
            if not deliverable.projects or not deliverable.files:
                return None
            filtered = _merge_discovery_manifests(
                [deliverable],
                request=self.request,
                run_id=self.run_id,
            )
            return filtered.model_copy(
                update={
                    "summary": {
                        **filtered.summary,
                        "project_judgment_summary": summarize_project_judgments(
                            run.project_judgments,
                            target_project_count=self.request.max_projects,
                        ),
                        "project_judgments": {
                            accession: run.project_judgments[accession].model_dump(mode="json")
                            for accession in sorted(
                                project.project_accession.upper() for project in filtered.projects
                            )
                            if accession in run.project_judgments
                        },
                        "harvest_all_qualified": harvest_all,
                    }
                }
            )

        candidates: list[tuple[tuple[int, int, int, float], int, Path, DatasetManifest]] = []
        if run.candidate_pool_manifest_path:
            path = Path(run.candidate_pool_manifest_path)
            if path.exists():
                manifest = eligible_manifest(_load_manifest(path))
                if manifest is not None:
                    # Prefer the full candidate pool strongly in maximize mode.
                    rank = _manifest_rank(manifest)
                    if harvest_all:
                        rank = (rank[0] + 10_000, rank[1], rank[2], rank[3])
                    candidates.append((rank, 0, path, manifest))
        for name, reference in run.artifacts.items():
            if not name.startswith("discovery_round_"):
                continue
            path = Path(reference.path)
            if not path.exists():
                continue
            manifest = eligible_manifest(_load_manifest(path))
            if manifest is not None:
                candidates.append((_manifest_rank(manifest), int(name.rsplit("_", 1)[-1]), path, manifest))
        candidates = [item for item in candidates if _selected_file_count(item[3]) > 0]
        if not candidates:
            return run
        _, round_index, path, manifest = max(candidates, key=lambda item: item[0])
        final_audit = self.audit_selected_manifest(manifest)
        run = self._require_run()
        if not final_audit.ready_for_selection:
            blocker = "final_manifest_quality_audit_requires_repair"
            updated = self.store.save_run(
                run.model_copy(
                    update={
                        "blockers": _dedupe([*run.blockers, blocker]),
                        "stop_reason": "selection_quality_gate_not_completed",
                    }
                )
            )
            self.store.append_event(
                self.run_id,
                "manifest_selection_rejected",
                {
                    "status": "blocked",
                    "round_index": round_index,
                    "blockers": [blocker],
                    "audit": final_audit.model_dump(mode="json"),
                    "auto_selected": True,
                },
            )
            return updated
        if run.project_judgments or harvest_all:
            paths = write_dataset_manifest(manifest, self.output_dir / "final_selection")
            path = paths["dataset_manifest_json"]
        rationale = (
            "Deterministic maximize harvest retained every evidence-backed grade 2-3 project "
            "from the inspected candidate pool."
            if harvest_all
            else "Deterministic fallback selected the highest-ranked persisted candidate manifest."
        )
        _, warnings = _recommend_next_action(manifest.summary, _selected_file_count(manifest))
        run = self.store.save_run(
            run.model_copy(
                update={
                    "current_manifest_path": str(path),
                    "selected_round_index": round_index,
                    "selection_rationale": rationale,
                    "warnings": warnings,
                    "blockers": [],
                    "qualified_project_count": len(
                        {
                            project.project_accession.upper()
                            for project in manifest.projects
                        }
                    ),
                }
            )
        )
        self.store.append_event(
            self.run_id,
            "manifest_selected",
            {
                "status": "completed",
                "round_index": round_index,
                "manifest_path": str(path),
                "selected_projects": len(manifest.projects),
                "selected_files": _selected_file_count(manifest),
                "selected_project_accessions": [
                    project.project_accession for project in manifest.projects
                ],
                "rationale": rationale,
                "harvest_all_qualified": harvest_all,
                "auto_selected": True,
            },
        )
        return run

    def continue_maximize_inspection(
        self,
        *,
        max_batches: int = 12,
        batch_size: int = 100,
    ) -> AgentRunRecord:
        """Deterministically continue inspecting high-relevance candidates.

        The LLM often stops after 1-2 inspection batches even when hundreds of
        high-relevance candidates remain. For maximize harvests, keep inspecting
        until coverage, round budget, or repository safety limits intervene.
        """
        run = self._require_run()
        if self.search_environment is None:
            return run
        harvest_all = bool(getattr(self.request, "harvest_all_qualified", False)) or (
            str(getattr(self.request, "quantity_scope", "") or "") == "portfolio"
            and str(getattr(self.request, "portfolio_size_preference", "") or "").startswith("maximize")
        )
        if not harvest_all:
            return run
        if run.search_stopped or run.selected_round_index is not None:
            return run

        latest_search_id = getattr(self.search_environment, "latest_search_id", None)
        if not latest_search_id and run.latest_candidate_search_id:
            latest_search_id = run.latest_candidate_search_id
        if not latest_search_id:
            return run

        high_relevance_fn = getattr(self.search_environment, "high_relevance_accessions", None)
        if not callable(high_relevance_fn):
            return run

        target_inspections = self._required_high_relevance_inspections(
            max(int(run.latest_high_relevance_candidate_count or 0), 1),
        )
        # Practical upper bound so we do not try to inspect thousands in one post-pass.
        target_inspections = max(target_inspections, 100)
        target_inspections = min(target_inspections, 400)

        batches_done = 0
        while batches_done < max_batches:
            run = self._require_run()
            if run.discovery_round_count >= run.budget.max_discovery_rounds:
                break
            if run.search_stopped:
                break
            inspected = {item.upper() for item in run.inspected_candidate_accessions}
            remaining = [
                accession
                for accession in high_relevance_fn(limit=1000)
                if accession.upper() not in inspected
            ]
            if not remaining:
                break
            if len(inspected) >= target_inspections and batches_done > 0:
                break
            batch = remaining[: max(1, min(batch_size, 40))]
            action = CandidateInspectionAction(
                search_id=str(latest_search_id),
                accessions=batch,
                rationale=(
                    "Deterministic maximize continuation: inspect remaining high-relevance "
                    "candidates after the model stopped early."
                ),
            )
            self.store.append_event(
                self.run_id,
                "maximize_inspection_continuation",
                {
                    "batch_size": len(batch),
                    "already_inspected": len(inspected),
                    "remaining_high_relevance": len(remaining),
                    "target_inspections": target_inspections,
                    "batch_index": batches_done + 1,
                },
            )
            observation = self.inspect_repository_candidates(action)
            batches_done += 1
            if observation.status == "blocked" and any(
                "hard_repository_request_limit" in str(item) for item in observation.blockers
            ):
                break
            # Stop if an inspection produced nothing usable and we already have a pool.
            if observation.selected_projects <= 0 and run.candidate_pool_manifest_path:
                # Still continue a couple batches; empty can be species filtering.
                if batches_done >= 3:
                    continue
        return self._require_run()

    def _backfill_judgments_for_inspected_pool_projects(
        self,
        run: AgentRunRecord,
    ) -> AgentRunRecord:
        """Create quality judgments for inspected pool projects the agent forgot to score.

        Quality still matters: only inspected projects with usable non-excluded files are
        promoted, as grade-2 evidence-backed includes. Explicit agent rejections are kept.
        """
        pool_path = run.candidate_pool_manifest_path
        if not pool_path or not Path(pool_path).exists():
            return run
        manifest = _load_manifest(Path(pool_path))
        inspected = {item.upper() for item in run.inspected_candidate_accessions}
        if not inspected:
            return run
        existing = dict(run.project_judgments)
        files_by_project: dict[str, list[DiscoveredFile]] = {}
        for file in manifest.files:
            files_by_project.setdefault(file.project_accession.upper(), []).append(file)

        created = 0
        for project in manifest.projects:
            accession = project.project_accession.upper()
            if accession not in inspected:
                continue
            if accession in existing:
                continue
            project_files = [
                file
                for file in files_by_project.get(accession, [])
                if str(getattr(file, "validity_status", "") or "") != "exclude"
            ]
            if not project_files:
                continue
            # Species hard filter for human-only harvests.
            if self.request.species_policy == "include_only" and self.request.species:
                allowed = {str(item).casefold() for item in self.request.species}
                observed = {
                    str(item).casefold()
                    for item in (project.canonical_species or project.species or [])
                }
                if observed and observed.isdisjoint(allowed):
                    existing[accession] = ProjectJudgmentInput(
                        project_accession=accession,
                        grade=0,
                        status="rejected",
                        hard_gate="fail",
                        confidence=0.8,
                        decision="exclude",
                        next_action="exclude_project",
                        explanation="Auto-harvest rejected project for species mismatch.",
                        evidence_refs=["species"],
                        target_file_count=len(project_files),
                        evidence_stage="inspection",
                    )
                    created += 1
                    continue
            # Immunopeptidomics harvests must not auto-promote plain proteomics just
            # because files exist. Require explicit HLA/MHC/immunopeptidome evidence.
            goal = str(self.request.goal or "").casefold()
            immuno_goal = goal == "immunopeptidomics" or bool(self.request.immunopeptide_scope)
            if immuno_goal:
                evidence_blob = " ".join(
                    [
                        str(project.project_title or ""),
                        str(getattr(project, "project_description", "") or ""),
                        str(project.immunopeptide_scope or ""),
                        " ".join(str(x) for x in (project.immunopeptide_evidence_terms or [])),
                        " ".join(str(x) for x in (project.hla_class or [])),
                        " ".join(str(file.file_name or "") for file in project_files[:30]),
                    ]
                ).casefold()
                immuno_tokens = (
                    "hla",
                    "mhc",
                    "immunopeptid",
                    "ligandome",
                    "antigen presentation",
                    "neoantigen",
                    "mapp",
                    "peptidome",
                    "hla-ip",
                    "mhc-ip",
                )
                if not any(token in evidence_blob for token in immuno_tokens):
                    existing[accession] = ProjectJudgmentInput(
                        project_accession=accession,
                        grade=0,
                        status="rejected",
                        hard_gate="fail",
                        confidence=0.85,
                        decision="exclude",
                        next_action="exclude_project",
                        explanation=(
                            "Auto-harvest rejected project: inspected files exist, but no "
                            "HLA/MHC/immunopeptidomics evidence was found for an immunopeptide goal."
                        ),
                        evidence_refs=[
                            "project_description_excerpt",
                            "immunopeptide_evidence_terms",
                            "selected_file_examples",
                        ],
                        target_file_count=len(project_files),
                        evidence_stage="inspection",
                    )
                    created += 1
                    continue
            validish = any(
                str(getattr(file, "validity_status", "") or "") in {"valid", "weak_keep"}
                for file in project_files
            )
            if not validish:
                continue
            grade = 3 if any(str(getattr(file, "validity_status", "") or "") == "valid" for file in project_files) else 2
            existing[accession] = ProjectJudgmentInput(
                project_accession=accession,
                grade=grade,
                status="evidence_backed",
                hard_gate="pass",
                confidence=0.75 if grade == 3 else 0.65,
                decision="include",
                next_action="include_in_manifest",
                explanation=(
                    "Auto-harvest promoted an inspected project with usable files and "
                    "goal-compatible evidence that the agent left unscored."
                ),
                evidence_refs=[
                    "selected_file_examples",
                    "validity_status_counts",
                ],
                target_file_count=len(project_files),
                evidence_stage="inspection",
            )
            created += 1

        if created <= 0:
            return run
        summary = summarize_project_judgments(
            existing,
            target_project_count=self.request.max_projects,
        )
        updated = self.store.save_run(
            run.model_copy(
                update={
                    "project_judgments": existing,
                    "qualified_project_count": int(summary["qualified_projects"]),
                }
            )
        )
        self.store.append_event(
            self.run_id,
            "project_judgments_backfilled",
            {
                "created_count": created,
                "qualified_projects": summary["qualified_projects"],
                "assessed_projects": summary["assessed_projects"],
            },
        )
        return updated

    def state_summary(self) -> dict[str, Any]:
        return self._state_summary(self._require_run())

    def inspect_project_sdrf(self, project_accession: str) -> dict[str, Any]:
        accession = _evidence_excerpt(project_accession, limit=64).strip().upper()
        run = self._require_run()
        policy = evaluate_tool_policy("inspect_project_sdrf", run)
        if policy.outcome != "allow":
            result = {
                "status": "blocked",
                "project_accession": accession,
                "reason": policy.reason,
            }
            self.store.append_event(
                self.run_id,
                "tool_denied",
                {"tool": "inspect_project_sdrf", **result},
            )
            return result

        inspected = {value.upper() for value in run.inspected_candidate_accessions}
        if not accession or accession not in inspected:
            result = {
                "status": "blocked",
                "project_accession": accession,
                "reason": "project_not_in_inspected_candidate_pool",
            }
            self.store.append_event(
                self.run_id,
                "tool_denied",
                {"tool": "inspect_project_sdrf", **result},
            )
            return result

        pool_path = Path(run.candidate_pool_manifest_path or "")
        if not run.candidate_pool_manifest_path or not pool_path.exists():
            result = {
                "status": "blocked",
                "project_accession": accession,
                "reason": "candidate_pool_manifest_unavailable",
            }
            self.store.append_event(
                self.run_id,
                "tool_denied",
                {"tool": "inspect_project_sdrf", **result},
            )
            return result

        manifest = _load_manifest(pool_path)
        project = next(
            (
                item
                for item in manifest.projects
                if item.project_accession.upper() == accession
            ),
            None,
        )
        if project is None:
            result = {
                "status": "blocked",
                "project_accession": accession,
                "reason": "project_not_in_inspected_candidate_pool",
            }
            self.store.append_event(
                self.run_id,
                "tool_denied",
                {"tool": "inspect_project_sdrf", **result},
            )
            return result

        run = self.store.save_run(
            run.model_copy(update={"tool_call_count": run.tool_call_count + 1})
        )
        result = {
            "status": "completed",
            "project_accession": accession,
            "sdrf": _bounded_sdrf_summary(project.sdrf_summary),
        }
        self.store.append_event(
            self.run_id,
            "tool_completed",
            {
                "tool": "inspect_project_sdrf",
                "project_accession": accession,
                "sdrf_status": result["sdrf"]["status"],
                "row_count": result["sdrf"]["row_count"],
                "content_sha256": result["sdrf"]["content_sha256"],
            },
        )
        return result

    def get_discovery_state(self) -> dict[str, Any]:
        run = self._require_run()
        policy = evaluate_tool_policy("get_discovery_state", run)
        if policy.outcome != "allow":
            payload = self._state_summary(run)
            payload["policy"] = policy.model_dump(mode="json")
            self.store.append_event(
                self.run_id,
                "tool_denied",
                {"tool": "get_discovery_state", "policy": policy.model_dump(mode="json")},
            )
            return payload

        run = self.store.save_run(
            run.model_copy(update={"tool_call_count": run.tool_call_count + 1})
        )
        payload = self._state_summary(run)
        payload["policy"] = policy.model_dump(mode="json")
        self.store.append_event(
            self.run_id,
            "tool_completed",
            {"tool": "get_discovery_state", "state": payload},
        )
        return payload

    def _load_portfolio_state(self) -> PortfolioState:
        run = self._require_run()
        if isinstance(run.portfolio_state, dict):
            try:
                return PortfolioState.model_validate(run.portfolio_state)
            except Exception:
                # Stored state is advisory; the manifest and request remain the
                # authoritative sources and can deterministically rebuild it.
                pass
        return initialize_portfolio_state(self.request)

    def _portfolio_manifest(self) -> DatasetManifest | None:
        run = self._require_run()
        path = Path(run.candidate_pool_manifest_path or run.current_manifest_path or "")
        if not str(path) or not path.exists() or not path.is_file():
            return None
        try:
            return _load_manifest(path)
        except Exception:
            return None

    def _persist_portfolio_state(
        self,
        state: PortfolioState,
        *,
        event_type: str = "portfolio_state_updated",
    ) -> PortfolioState:
        self.store.save_run(
            self._require_run().model_copy(
                update={"portfolio_state": state.model_dump(mode="json")}
            )
        )
        self.store.append_event(
            self.run_id,
            event_type,
            {
                "status": state.status,
                "gaps": [gap.model_dump(mode="json") for gap in state.gaps],
                "recovery_actions": [
                    action.model_dump(mode="json") for action in state.recovery_actions
                ],
                "selected_project_accessions": state.selected_project_accessions,
            },
        )
        return state

    def get_portfolio_state(self) -> dict[str, Any]:
        """Return compact portfolio contract/coverage state for the Agent and UI."""

        state = self._load_portfolio_state()
        manifest = self._portfolio_manifest()
        if manifest is not None:
            state = update_portfolio_state(state, manifest.files)
        state = self._persist_portfolio_state(state, event_type="portfolio_state_observed")
        return state.model_dump(mode="json")

    def assess_portfolio_coverage(
        self,
        file_identifiers: list[str] | None = None,
    ) -> dict[str, Any]:
        """Recompute coverage from persisted manifest evidence, never Agent claims."""

        state = self._load_portfolio_state()
        manifest = self._portfolio_manifest()
        if manifest is None:
            state = self._persist_portfolio_state(
                state.model_copy(update={"status": "blocked", "gaps": []}),
                event_type="portfolio_coverage_blocked",
            )
            return {
                "status": "blocked",
                "reason": "candidate_manifest_unavailable",
                "state": state.model_dump(mode="json"),
            }
        state = update_portfolio_state(
            state,
            manifest.files,
            selected_file_identifiers=file_identifiers,
        )
        state = self._persist_portfolio_state(state)
        return {
            "status": "completed",
            "coverage": state.coverage.model_dump(mode="json") if state.coverage else None,
            "gaps": [gap.model_dump(mode="json") for gap in state.gaps],
            "state": state.model_dump(mode="json"),
        }

    def plan_portfolio_recovery(self) -> dict[str, Any]:
        state = self._load_portfolio_state()
        if state.coverage is None:
            result = self.assess_portfolio_coverage()
            return result
        completed_attempts = sum(
            1 for attempt in state.recovery_attempts if attempt.status == "completed"
        )
        if state.gaps and completed_attempts >= state.spec.max_recovery_rounds:
            state = state.model_copy(
                update={
                    "status": "blocked",
                    "recovery_actions": [
                        *state.recovery_actions,
                        RecoveryAction(
                            id="recovery-budget-exhausted",
                            kind="stop_with_limitations",
                            priority=100,
                            rationale="The bounded portfolio recovery budget is exhausted.",
                            expected_gain="Report the remaining evidence gaps without relaxing hard conditions.",
                        ),
                    ],
                }
            )
        state = self._persist_portfolio_state(state, event_type="portfolio_recovery_planned")
        return {
            "status": "needs_recovery" if state.gaps and state.status != "blocked" else state.status,
            "actions": [action.model_dump(mode="json") for action in state.recovery_actions],
            "attempts": [attempt.model_dump(mode="json") for attempt in state.recovery_attempts],
            "gaps": [gap.model_dump(mode="json") for gap in state.gaps],
            "state": state.model_dump(mode="json"),
        }

    def record_portfolio_recovery(
        self,
        action_id: str,
        status: str,
        observation: str = "",
        gain: dict[str, int] | None = None,
        approval_granted: bool = False,
    ) -> dict[str, Any]:
        """Persist the bounded recovery loop around an Agent search/inspection."""

        state = self._load_portfolio_state()
        action = next((item for item in state.recovery_actions if item.id == action_id), None)
        if action is None:
            return {"status": "blocked", "reason": "portfolio_recovery_action_unknown"}
        normalized_status = str(status or "").strip().lower()
        if normalized_status not in {"running", "completed", "failed", "stopped"}:
            return {"status": "blocked", "reason": "portfolio_recovery_status_invalid"}
        if (
            action.requires_approval
            and normalized_status in {"running", "completed"}
            and not approval_granted
        ):
            return {"status": "blocked", "reason": "portfolio_recovery_approval_required"}
        completed_attempts = sum(
            1 for attempt in state.recovery_attempts if attempt.status == "completed"
        )
        if normalized_status == "running" and completed_attempts >= state.spec.max_recovery_rounds:
            return {"status": "blocked", "reason": "portfolio_recovery_budget_exhausted"}
        existing_attempt = next(
            (
                item
                for item in reversed(state.recovery_attempts)
                if item.action_id == action_id and item.status == "running"
            ),
            None,
        )
        attempt = (
            existing_attempt.model_copy(
                update={
                    "status": normalized_status,
                    "observation": str(observation or "").strip()[:2000],
                    "gain": {str(key): int(value) for key, value in (gain or {}).items()},
                }
            )
            if existing_attempt is not None
            else RecoveryAttempt(
                attempt_id=f"recovery-attempt-{len(state.recovery_attempts) + 1}",
                action_id=action_id,
                status=normalized_status,
                observation=str(observation or "").strip()[:2000],
                gain={str(key): int(value) for key, value in (gain or {}).items()},
            )
        )
        action_status = "accepted" if normalized_status == "running" else (
            "executed" if normalized_status == "completed" else "skipped"
        )
        updated_actions = [
            item.model_copy(update={"status": action_status}) if item.id == action_id else item
            for item in state.recovery_actions
        ]
        state = state.model_copy(
            update={
                "recovery_actions": updated_actions,
                "recovery_attempts": [
                    attempt
                    if item.attempt_id == attempt.attempt_id
                    else item
                    for item in state.recovery_attempts
                ]
                if existing_attempt is not None
                else [*state.recovery_attempts, attempt],
            }
        )
        state = self._persist_portfolio_state(state, event_type="portfolio_recovery_recorded")
        return {
            "status": "completed",
            "attempt": attempt.model_dump(mode="json"),
            "state": state.model_dump(mode="json"),
        }

    def freeze_portfolio(
        self,
        file_identifiers: list[str],
        rationale: str,
    ) -> dict[str, Any]:
        state = self._load_portfolio_state()
        manifest = self._portfolio_manifest()
        if manifest is None:
            return {"status": "blocked", "reason": "candidate_manifest_unavailable"}
        selected = list(file_identifiers)
        if not selected:
            selected = [
                f"{row.repository}:{row.project_accession}:{row.file_accession_or_path or row.file_name}"
                for row in select_portfolio_files(manifest.files, state.spec)
            ]
        state = update_portfolio_state(
            state,
            manifest.files,
            selected_file_identifiers=selected,
        )
        if state.coverage is None or not state.coverage.ready:
            state = self._persist_portfolio_state(
                state.model_copy(update={"status": "blocked"}),
                event_type="portfolio_freeze_blocked",
            )
            return {
                "status": "blocked",
                "reason": "hard_portfolio_gaps",
                "state": state.model_dump(mode="json"),
            }
        state = state.model_copy(
            update={
                "status": "frozen",
                "frozen_rationale": str(rationale or "").strip()[:2000]
                or "Evidence-backed portfolio frozen.",
            }
        )
        state = self._persist_portfolio_state(state, event_type="portfolio_frozen")
        return {
            "status": "frozen",
            "selected_file_identifiers": state.selected_file_identifiers,
            "selected_project_accessions": state.selected_project_accessions,
            "coverage": state.coverage.model_dump(mode="json") if state.coverage else None,
            "state": state.model_dump(mode="json"),
            "next_step": "Call select_discovery_manifest with matching project_accessions; the service will publish the exact frozen selected_file_identifiers.",
        }

    def audit_discovery_state(
        self,
        *,
        meter_tool: bool = True,
        manifest_override: DatasetManifest | None = None,
        selection_accessions: set[str] | None = None,
        final_selection: bool = False,
    ) -> DiscoveryQualityAudit:
        """Audit selection readiness and return bounded repair operations.

        The report is deterministic and public: it summarizes persisted facts,
        not private model reasoning.  It is used both as an SDK tool and as the
        final server-side guard before a manifest can be selected.
        """

        run = self._require_run()
        if meter_tool:
            policy = evaluate_tool_policy("audit_discovery_state", run)
            if policy.outcome != "allow":
                report = DiscoveryQualityAudit(
                    run_id=self.run_id,
                    status="blocked",
                    issues=[
                        DiscoveryAuditIssue(
                            code="quality_audit_policy_denied",
                            severity="error",
                            summary=policy.reason,
                        )
                    ],
                    repair_actions=[
                        DiscoveryRepairAction(
                            action="stop_with_limitations",
                            reason=policy.reason,
                        )
                    ],
                    limitations=[policy.reason],
                )
                return self._persist_discovery_quality_audit(
                    report,
                    event_type="discovery_quality_audit_denied",
                )
            run = self.store.save_run(
                run.model_copy(update={"tool_call_count": run.tool_call_count + 1})
            )

        manifest_path = Path(run.candidate_pool_manifest_path or run.current_manifest_path or "")
        manifest = manifest_override
        if manifest is None:
            manifest = (
                _load_manifest(manifest_path)
                if str(manifest_path) and manifest_path.exists() and manifest_path.is_file()
                else None
            )
        if manifest is not None and (run.search_stopped or final_selection):
            self._maybe_emit_partial_l1_delivery(
                run=run,
                manifest=manifest,
                terminal=True,
            )
            run = self._require_run()
        issues: list[DiscoveryAuditIssue] = []
        actions: list[DiscoveryRepairAction] = []
        limitations: list[str] = []

        events = self.store.list_events(self.run_id)
        requested: list[str] = []
        succeeded_from_events: list[str] = []
        failed_from_events: list[str] = []
        for event in events:
            if event.event_type not in {
                "candidate_inspection_started",
                "candidate_inspection_completed",
            }:
                continue
            action_payload = event.payload.get("action") or {}
            requested.extend(
                str(value).upper()
                for value in action_payload.get("accessions") or []
                if str(value).strip()
            )
            observation = event.payload.get("observation") or {}
            succeeded_from_events.extend(
                str(item.get("project_accession") or "").upper()
                for item in observation.get("project_assessments") or []
                if isinstance(item, dict) and str(item.get("project_accession") or "").strip()
            )
            for warning in observation.get("warnings") or []:
                text = str(warning or "")
                if text.startswith("inspection_failed_accessions:"):
                    failed_from_events.extend(
                        value.strip().upper()
                        for value in text.split(":", 1)[1].split(",")
                        if value.strip()
                    )

        requested_accessions = _normalize_accessions(requested)
        completed_inspection_accessions = _normalize_accessions(
            [*run.inspected_candidate_accessions, *succeeded_from_events]
        )
        completed_inspection_set = set(completed_inspection_accessions)
        failed_accessions = [
            accession
            for accession in _normalize_accessions(
                [
                    *failed_from_events,
                    *(set(requested_accessions) - completed_inspection_set),
                ]
            )
            if accession not in completed_inspection_set
        ]

        agent_turns_used = int(run.sdk_turn_count)
        agent_turns_remaining = run.remaining_model_turn_budget()
        tool_calls_remaining = max(
            0,
            int(run.budget.max_tool_calls) - int(run.tool_call_count),
        )
        discovery_rounds_remaining = max(
            0,
            int(run.budget.max_discovery_rounds) - int(run.discovery_round_count),
        )
        query_units_remaining = max(
            0,
            int(run.dynamic_limits.max_query_units)
            - int(run.dynamic_usage.query_units),
        )
        repository_requests_remaining = max(
            0,
            int(run.dynamic_limits.max_repository_requests)
            - int(run.dynamic_usage.repository_requests),
        )
        elapsed_seconds = elapsed_seconds_since(run.dynamic_usage.started_at)
        elapsed_seconds_remaining = max(
            0,
            int(run.dynamic_limits.max_elapsed_seconds - elapsed_seconds),
        )
        global_repair_limitations: list[str] = []
        if agent_turns_remaining <= 0:
            global_repair_limitations.append("agent_turn_budget_exhausted")
        if tool_calls_remaining <= 0:
            global_repair_limitations.append("tool_call_budget_exhausted")
        if elapsed_seconds >= run.dynamic_limits.max_elapsed_seconds:
            global_repair_limitations.append("hard_elapsed_time_limit")
        search_repair_limitations = list(global_repair_limitations)
        if discovery_rounds_remaining <= 0:
            search_repair_limitations.append("discovery_round_budget_exhausted")
        if query_units_remaining <= 0:
            search_repair_limitations.append("hard_query_unit_limit")
        if repository_requests_remaining <= 0:
            search_repair_limitations.append("hard_repository_request_limit")
        if (
            run.search_stopped
            and str(run.search_stop_reason or "").startswith("hard_")
        ):
            search_repair_limitations.append(str(run.search_stop_reason))
        search_repair_limitations = _dedupe(search_repair_limitations)

        target = max(1, int(self.request.max_projects))
        harvest_all = bool(getattr(self.request, "harvest_all_qualified", False)) or (
            str(getattr(self.request, "quantity_scope", "") or "") == "portfolio"
            and str(getattr(self.request, "portfolio_size_preference", "") or "").startswith(
                "maximize"
            )
        )
        open_ended = str(getattr(self.request, "quota_flexibility", "") or "") == "open_ended"
        fixed_target = str(getattr(self.request, "quota_flexibility", "") or "") == "fixed"
        open_ended_search = harvest_all or open_ended
        required_inspections = self._required_high_relevance_inspections(
            run.latest_high_relevance_candidate_count,
        )
        can_continue = (
            not search_repair_limitations
            and run.remaining_model_turn_budget() > 0
            and self.search_environment is not None
            and not run.search_stopped
            and run.qualified_no_gain_count < 2
        )

        if manifest is None:
            issues.append(
                DiscoveryAuditIssue(
                    code="candidate_manifest_missing",
                    severity="error",
                    summary="No persisted inspected candidate manifest is available.",
                    evidence_refs=["candidate_pool_manifest_path"],
                )
            )
            actions.append(
                DiscoveryRepairAction(
                    action="search_more" if can_continue else "stop_with_limitations",
                    reason=(
                        "Search and inspect candidates before selection."
                        if can_continue
                        else "No candidate manifest exists and the discovery budget is exhausted."
                    ),
                )
            )
            report = DiscoveryQualityAudit(
                run_id=self.run_id,
                status="repair_required" if can_continue else "blocked",
                counts={
                    "target_projects": target,
                    "required_inspections": required_inspections,
                    "agent_turns_used": agent_turns_used,
                    "agent_turns_remaining": agent_turns_remaining,
                    "tool_calls_remaining": tool_calls_remaining,
                    "discovery_rounds_remaining": discovery_rounds_remaining,
                    "query_units_remaining": query_units_remaining,
                    "repository_requests_remaining": repository_requests_remaining,
                    "elapsed_seconds_remaining": elapsed_seconds_remaining,
                },
                requested_inspection_accessions=requested_accessions,
                succeeded_inspection_accessions=completed_inspection_accessions,
                failed_inspection_accessions=failed_accessions,
                issues=issues,
                repair_actions=actions,
                limitations=_dedupe(
                    ["candidate_manifest_missing", *search_repair_limitations]
                ),
            )
            return self._persist_discovery_quality_audit(report)

        projects_by_accession = {
            project.project_accession.upper(): project for project in manifest.projects
        }
        files_by_accession: dict[str, list[DiscoveredFile]] = {}
        for file in manifest.files:
            files_by_accession.setdefault(file.project_accession.upper(), []).append(file)

        manifest_accessions = {
            *projects_by_accession,
            *files_by_accession,
        }
        # A terminal inspection can legitimately produce no assessable project:
        # e.g. every repository file was unusable, or the project was excluded by
        # a scientific hard constraint. Keep that outcome as completed inspection
        # evidence, but never send it into an impossible project-judgment loop.
        assessable_inspection_accessions = [
            accession
            for accession in completed_inspection_accessions
            if accession in manifest_accessions
        ]
        non_assessable_inspection_accessions = [
            accession
            for accession in completed_inspection_accessions
            if accession not in manifest_accessions
        ]
        audit_accessions = (
            set(_normalize_accessions(list(selection_accessions)))
            if final_selection and selection_accessions is not None
            else set(manifest_accessions)
            if final_selection
            else None
        )
        all_judgments = run.project_judgments
        judgments = (
            {
                accession: judgment
                for accession, judgment in all_judgments.items()
                if accession in audit_accessions
            }
            if audit_accessions is not None
            else all_judgments
        )
        inspected_set = set(completed_inspection_accessions)
        assessable_inspected_set = set(assessable_inspection_accessions)
        inspection_judged_set = {
            judgment.project_accession
            for judgment in all_judgments.values()
            if judgment.evidence_stage == "inspection"
        }
        judgments_required_for = (
            audit_accessions
            if audit_accessions is not None
            else assessable_inspected_set
        )
        missing_judgments = sorted(judgments_required_for - inspection_judged_set)
        if missing_judgments:
            issues.append(
                DiscoveryAuditIssue(
                    code="inspected_projects_missing_judgments",
                    severity="error",
                    summary="Every assessable inspected project must receive an auditable judgment.",
                    project_accessions=missing_judgments,
                    evidence_refs=["inspected_candidate_accessions", "project_judgments"],
                )
            )
            actions.append(
                DiscoveryRepairAction(
                    action="rescore_projects",
                    reason="Score every assessable inspected project before selection.",
                    project_accessions=missing_judgments,
                )
            )

        active_constraints = {
            constraint.id: constraint
            for constraint in self.request.scientific_constraints
            if constraint.scope != "portfolio"
        }
        hard_builtin_constraints = _hard_builtin_constraint_values(self.request)
        portfolio_constraints = [
            constraint
            for constraint in self.request.scientific_constraints
            if constraint.scope == "portfolio"
        ]
        scoped_hard_constraints = [
            constraint
            for constraint in active_constraints.values()
            if constraint.strength == "hard"
            and constraint.scope in {"file", "sample"}
        ]
        evidence_repair_projects: set[str] = set()
        evidence_repair_ids: set[str] = set()
        for judgment in judgments.values():
            accession = judgment.project_accession
            evidence_values = _project_evidence_values(
                projects_by_accession.get(accession),
                files_by_accession.get(accession, []),
            )
            available_refs = set(evidence_values)
            problems = _constraint_assessment_evidence_problems(
                judgment,
                available_refs,
                constraints=active_constraints,
                evidence_values=evidence_values,
            )
            if problems:
                evidence_repair_projects.add(accession)
                evidence_repair_ids.update(
                    constraint_id for _problem, constraint_id, _refs in problems
                )
        if evidence_repair_projects:
            projects = sorted(evidence_repair_projects)
            constraint_ids = sorted(evidence_repair_ids)
            issues.append(
                DiscoveryAuditIssue(
                    code="constraint_assessment_evidence_invalid",
                    severity="error",
                    summary=(
                        "Every constraint assessment must cite non-empty evidence refs "
                        "that are actually available for that persisted project."
                    ),
                    project_accessions=projects,
                    constraint_ids=constraint_ids,
                    evidence_refs=[
                        "project_judgments.constraint_assessments.evidence_refs",
                        "candidate_pool_manifest_path",
                    ],
                )
            )
            actions.append(
                DiscoveryRepairAction(
                    action="rescore_projects",
                    reason="Re-score constraints using only persisted project evidence refs.",
                    project_accessions=projects,
                    constraint_ids=constraint_ids,
                )
            )
        qualified = {
            accession
            for accession, judgment in judgments.items()
            if is_qualified_project_judgment(judgment)
        }
        if final_selection:
            unqualified_selected = sorted(manifest_accessions - qualified)
            if unqualified_selected:
                issues.append(
                    DiscoveryAuditIssue(
                        code="selected_manifest_contains_unqualified_projects",
                        severity="error",
                        summary=(
                            "Every project in the final manifest must have an inspection-backed, "
                            "evidence-backed grade 2-3 judgment with a passing hard gate."
                        ),
                        project_accessions=unqualified_selected,
                        evidence_refs=["selected_manifest", "project_judgments"],
                    )
                )
                actions.append(
                    DiscoveryRepairAction(
                        action="rescore_projects",
                        reason="Remove or re-score unqualified projects before publication.",
                        project_accessions=unqualified_selected,
                    )
                )
        delivery_eligible: set[str] = set()
        constraint_repair_projects: set[str] = set()
        constraint_repair_ids: set[str] = set()
        review_projects: list[str] = []
        asset_gap_projects: list[str] = []
        min_file_projects: list[str] = []
        min_sample_projects: list[str] = []
        builtin_constraint_projects: set[str] = set()
        builtin_constraint_fields: set[str] = set()
        usable_file_count = 0
        needs_review_file_count = sum(
            file.needs_review or file.validity_status == "needs_review"
            for file in manifest.files
        )
        for accession in sorted(qualified):
            judgment = judgments[accession]
            project = projects_by_accession.get(accession)
            project_files = files_by_accession.get(accession, [])
            base_usable_files = [
                file
                for file in project_files
                if _is_delivery_eligible(project, file)
            ]
            failed_builtin_fields = _hard_builtin_constraint_failures(
                hard_builtin_constraints,
                project,
                base_usable_files,
            )
            if failed_builtin_fields:
                builtin_constraint_projects.add(accession)
                builtin_constraint_fields.update(failed_builtin_fields)
            usable_files = (
                _files_passing_hard_scoped_constraints(
                    base_usable_files,
                    judgment,
                    scoped_hard_constraints,
                )
                if scoped_hard_constraints
                else base_usable_files
            )
            if not failed_builtin_fields:
                usable_file_count += len(usable_files)
            assessments = {
                assessment.constraint_id: assessment
                for assessment in judgment.constraint_assessments
            }
            for constraint_id, constraint in active_constraints.items():
                assessment = assessments.get(constraint_id)
                unresolved = assessment is None
                if assessment is not None and constraint.strength == "hard":
                    if constraint.scope in {"file", "sample"}:
                        constraint_files = _files_passing_hard_scoped_constraints(
                            base_usable_files,
                            judgment,
                            [constraint],
                        )
                        unresolved = (
                            assessment.status not in {"pass", "partial"}
                            or not constraint_files
                        )
                    else:
                        unresolved = (
                            assessment.status != "pass"
                            or evaluate_constraint_value(
                                constraint,
                                assessment.observed_value,
                            ) is not True
                        )
                if unresolved:
                    constraint_repair_projects.add(accession)
                    constraint_repair_ids.add(constraint_id)
            if project is None or not project_files:
                issues.append(
                    DiscoveryAuditIssue(
                        code="qualified_project_has_no_inspected_files",
                        severity="error",
                        summary="A qualified project has no persisted inspected files.",
                        project_accessions=[accession],
                        evidence_refs=["candidate_pool_manifest_path"],
                    )
                )
                continue
            if not _is_delivery_eligible(project):
                review_projects.append(accession)
                continue
            if not base_usable_files:
                asset_gap_projects.append(accession)
                continue
            if scoped_hard_constraints and not usable_files:
                constraint_repair_projects.add(accession)
                constraint_repair_ids.update(
                    constraint.id for constraint in scoped_hard_constraints
                )
                continue
            if (
                self.request.per_project_min_files is not None
                and self.request.is_hard_constraint("per_project_min_files")
                and len(usable_files) < int(self.request.per_project_min_files)
            ):
                min_file_projects.append(accession)
                continue
            if (
                self.request.per_project_min_samples is not None
                and self.request.is_hard_constraint("per_project_min_samples")
            ):
                observed_samples = _project_sample_count(project)
                if (
                    observed_samples is None
                    or observed_samples < int(self.request.per_project_min_samples)
                ):
                    min_sample_projects.append(accession)
                    continue
            if (
                accession not in constraint_repair_projects
                and accession not in evidence_repair_projects
                and accession not in builtin_constraint_projects
            ):
                delivery_eligible.add(accession)

        if final_selection:
            non_delivery_file_projects: set[str] = set()
            for file in manifest.files:
                accession = file.project_accession.upper()
                project = projects_by_accession.get(accession)
                if accession not in qualified or not _is_delivery_eligible(project, file):
                    non_delivery_file_projects.add(accession)
                    continue
                if scoped_hard_constraints:
                    file_judgment = judgments.get(accession)
                    if file_judgment is None or file not in _files_passing_hard_scoped_constraints(
                        [file],
                        file_judgment,
                        scoped_hard_constraints,
                    ):
                        non_delivery_file_projects.add(accession)
            if non_delivery_file_projects:
                projects = sorted(non_delivery_file_projects)
                issues.append(
                    DiscoveryAuditIssue(
                        code="selected_manifest_contains_non_delivery_files",
                        severity="error",
                        summary=(
                            "The final manifest contains files that fail delivery evidence or a "
                            "hard file/sample-scoped constraint."
                        ),
                        project_accessions=projects,
                        constraint_ids=[
                            constraint.id for constraint in scoped_hard_constraints
                        ],
                        evidence_refs=["selected_manifest.files", "project_judgments"],
                    )
                )
                actions.append(
                    DiscoveryRepairAction(
                        action="rescore_projects",
                        reason="Remove every hard-failing or non-delivery file before publication.",
                        project_accessions=projects,
                        constraint_ids=[
                            constraint.id for constraint in scoped_hard_constraints
                        ],
                    )
                )

        if asset_gap_projects:
            projects = sorted(asset_gap_projects)
            issues.append(
                DiscoveryAuditIssue(
                    code="qualified_project_has_no_delivery_assets",
                    severity="error",
                    summary=(
                        "Qualified projects need a concrete file identifier, download URL, "
                        "known file role, file-level evidence, and positive asset size."
                    ),
                    project_accessions=projects,
                    evidence_refs=[
                        "file_accession_or_path",
                        "download_url",
                        "file_role",
                        "evidence_level",
                        "expected_size_bytes",
                    ],
                )
            )
            actions.append(
                DiscoveryRepairAction(
                    action="inspect_candidates",
                    reason="Resolve concrete file assets before delivery.",
                    project_accessions=projects,
                )
            )

        if min_file_projects:
            projects = sorted(min_file_projects)
            issues.append(
                DiscoveryAuditIssue(
                    code="hard_per_project_min_files_not_met",
                    severity="error",
                    summary=(
                        "At least one qualified project has fewer delivery-eligible files "
                        "than the user-owned hard minimum."
                    ),
                    project_accessions=projects,
                    evidence_refs=["per_project_min_files", "candidate_pool_manifest_path"],
                )
            )
            actions.append(
                DiscoveryRepairAction(
                    action="search_more",
                    reason="Find projects that satisfy the hard per-project file minimum.",
                    project_accessions=projects,
                )
            )

        if min_sample_projects:
            projects = sorted(min_sample_projects)
            issues.append(
                DiscoveryAuditIssue(
                    code="hard_per_project_min_samples_not_met",
                    severity="error",
                    summary=(
                        "The hard per-project sample minimum is unmet or lacks a "
                        "machine-verifiable sample count."
                    ),
                    project_accessions=projects,
                    evidence_refs=["per_project_min_samples", "sdrf", "raw_metadata"],
                )
            )
            actions.append(
                DiscoveryRepairAction(
                    action="inspect_candidates",
                    reason="Resolve sample counts or find projects meeting the hard minimum.",
                    project_accessions=projects,
                )
            )

        if builtin_constraint_projects:
            projects = sorted(builtin_constraint_projects)
            fields = sorted(builtin_constraint_fields)
            issues.append(
                DiscoveryAuditIssue(
                    code="hard_builtin_constraint_not_met",
                    severity="error",
                    summary=(
                        "Every selected project and delivery file must contain matching "
                        "observed evidence for each concrete hard built-in constraint."
                    ),
                    project_accessions=projects,
                    constraint_ids=fields,
                    evidence_refs=[
                        "selected_manifest.projects",
                        "selected_manifest.files",
                        *fields,
                    ],
                )
            )
            actions.append(
                DiscoveryRepairAction(
                    action="rescore_projects",
                    reason=(
                        "Remove projects with missing or conflicting hard built-in "
                        "evidence, or inspect stronger persisted evidence."
                    ),
                    project_accessions=projects,
                    constraint_ids=fields,
                )
            )

        for constraint in portfolio_constraints:
            if constraint.strength != "hard":
                continue
            observed_value = _portfolio_constraint_observed_value(
                manifest,
                qualified,
                constraint.dimension,
            )
            if evaluate_constraint_value(constraint, observed_value) is True:
                continue
            issues.append(
                DiscoveryAuditIssue(
                    code="hard_portfolio_constraint_not_met",
                    severity="error",
                    summary=(
                        "A hard portfolio-level requirement is unmet or cannot be "
                        "verified from the persisted delivery evidence."
                    ),
                    constraint_ids=[constraint.id],
                    evidence_refs=[
                        "scientific_constraints",
                        "candidate_pool_manifest_path",
                        constraint.dimension,
                    ],
                )
            )
            actions.append(
                DiscoveryRepairAction(
                    action="search_more" if can_continue else "stop_with_limitations",
                    reason="Expand or revise the portfolio until the hard aggregate constraint is met.",
                    constraint_ids=[constraint.id],
                )
            )

        if constraint_repair_projects:
            projects = sorted(constraint_repair_projects)
            constraint_ids = sorted(constraint_repair_ids)
            issues.append(
                DiscoveryAuditIssue(
                    code="qualified_projects_have_unresolved_constraints",
                    severity="error",
                    summary="Qualified projects must assess every active constraint and pass every hard constraint.",
                    project_accessions=projects,
                    constraint_ids=constraint_ids,
                    evidence_refs=["project_judgments.constraint_assessments"],
                )
            )
            actions.append(
                DiscoveryRepairAction(
                    action="rescore_projects",
                    reason="Re-score projects against every active scientific constraint.",
                    project_accessions=projects,
                    constraint_ids=constraint_ids,
                )
            )

        if review_projects:
            issues.append(
                DiscoveryAuditIssue(
                    code="qualified_project_still_needs_review",
                    severity="error",
                    summary="Included projects must have at least one non-review delivery file and no unresolved project-level review flag.",
                    project_accessions=review_projects,
                    evidence_refs=["project.needs_review", "file.needs_review", "validity_status"],
                )
            )
            actions.append(
                DiscoveryRepairAction(
                    action="rescore_projects",
                    reason="Downgrade/exclude unresolved projects or inspect stronger file-level evidence.",
                    project_accessions=review_projects,
                )
            )

        audited_delivery_files: list[DiscoveredFile] = []
        for accession in sorted(delivery_eligible):
            project = projects_by_accession.get(accession)
            files = [
                file
                for file in files_by_accession.get(accession, [])
                if _is_delivery_eligible(project, file)
            ]
            if scoped_hard_constraints:
                judgment = judgments.get(accession)
                files = (
                    _files_passing_hard_scoped_constraints(
                        files,
                        judgment,
                        scoped_hard_constraints,
                    )
                    if judgment is not None
                    else []
                )
            audited_delivery_files.extend(files)
        strict_valid_file_count = sum(
            file.validity_status == "valid" and not file.needs_review
            for file in audited_delivery_files
        )
        inherited_usable_file_count = sum(
            "usable_inherited" in file.validity_reasons
            or (
                "usable_direct" not in file.validity_reasons
                and file.evidence_level == "project"
            )
            for file in audited_delivery_files
        )
        direct_usable_file_count = max(
            0,
            strict_valid_file_count - inherited_usable_file_count,
        )
        weak_keep_file_count = sum(
            file.validity_status == "weak_keep" and not file.needs_review
            for file in audited_delivery_files
        )
        pending_file_count = sum(
            file.needs_review
            or file.validity_status in {"needs_review", "weak_keep"}
            for file in manifest.files
        )
        usable_file_count = len(audited_delivery_files)

        # Wave A policy: qualified progress with zero strict-valid is not graduation.
        if (
            len(qualified) > 0
            and strict_valid_file_count == 0
            and can_continue
        ):
            limitations.append(
                "zero_strict_valid_files_continue_search:"
                f"qualified={len(qualified)},pending_files={pending_file_count}"
            )
            issues.append(
                DiscoveryAuditIssue(
                    code="zero_strict_valid_files_with_qualified_projects",
                    severity="error",
                    summary=(
                        "Projects may be judgment-qualified, but no strict-valid files exist yet. "
                        "Continue search under budget; do not treat qualified headcount as build-ready."
                    ),
                    evidence_refs=[
                        "strict_valid_files",
                        "qualified_projects",
                        "weak_keep_files",
                    ],
                )
            )
            actions.append(
                DiscoveryRepairAction(
                    action="search_more",
                    reason=(
                        "No strict-valid delivery files yet (build-ready path blocked). "
                        "Search a materially different query hypothesis; avoid looping the same strategy "
                        "(no-progress cap ~2 rounds per strategy, ~3 strategies)."
                    ),
                )
            )

        if failed_accessions:
            issues.append(
                DiscoveryAuditIssue(
                    code="candidate_inspections_failed",
                    severity="warning",
                    summary="Some requested candidate inspections did not produce persisted evidence.",
                    project_accessions=failed_accessions,
                    evidence_refs=["candidate_inspection_completed.warnings"],
                )
            )

        latest_search_observation: dict[str, Any] = {}
        for event in reversed(events):
            if event.event_type == "candidate_search_completed":
                candidate = event.payload.get("observation") or {}
                if isinstance(candidate, dict):
                    latest_search_observation = candidate
                break

        def _term_map(values: Any) -> dict[str, str]:
            result: dict[str, str] = {}
            for value in values if isinstance(values, list) else []:
                text = " ".join(str(value or "").split()).strip()
                if text:
                    result.setdefault(text.casefold(), text)
            return result

        intent_terms = _term_map(latest_search_observation.get("intent_terms"))
        candidate_covered = _term_map(
            latest_search_observation.get("covered_intent_terms")
        )
        matched_by_project: dict[str, set[str]] = {}
        for preview in latest_search_observation.get("previews") or []:
            if not isinstance(preview, dict):
                continue
            accession = str(preview.get("project_accession") or "").strip().upper()
            if not accession:
                continue
            matched_by_project.setdefault(accession, set()).update(
                _term_map(preview.get("matched_intent_terms"))
            )
        backed_keys = {
            term
            for accession in delivery_eligible
            for term in matched_by_project.get(accession, set())
        }
        selection_backed_terms = [
            text for key, text in intent_terms.items() if key in backed_keys
        ]
        uncovered_terms = [
            text for key, text in intent_terms.items() if key not in backed_keys
        ]
        unsupported_coverage_terms = [
            text for key, text in candidate_covered.items() if key not in backed_keys
        ]
        selection_backed_coverage = (
            len(set(intent_terms) & backed_keys) / len(intent_terms)
            if intent_terms
            else None
        )
        if unsupported_coverage_terms:
            message = (
                "Candidate-preview coverage is not fully backed by the delivery-eligible selection: "
                + ", ".join(unsupported_coverage_terms)
            )
            limitations.append(message)
            issues.append(
                DiscoveryAuditIssue(
                    code="preview_coverage_not_backed_by_selection",
                    severity="warning",
                    summary=message,
                    evidence_refs=[
                        "candidate_search_completed.observation.previews",
                        "project_judgments",
                    ],
                )
            )

        if len(completed_inspection_accessions) < required_inspections and can_continue:
            known_accessions = getattr(self.search_environment, "candidate_accessions", [])
            remaining = sorted(
                {
                    str(value).upper()
                    for value in (known_accessions if isinstance(known_accessions, list) else [])
                    if str(value).strip()
                }
                - inspected_set
            )[:100]
            issues.append(
                DiscoveryAuditIssue(
                    code="high_relevance_inspection_coverage_incomplete",
                    severity="error",
                    summary="High-relevance inspection coverage is below the deterministic minimum.",
                    evidence_refs=["latest_high_relevance_candidate_count", "inspected_candidate_accessions"],
                )
            )
            actions.append(
                DiscoveryRepairAction(
                    action="inspect_candidates",
                    reason="Inspect more persisted high-relevance candidates before selection.",
                    project_accessions=remaining,
                )
            )
        elif len(completed_inspection_accessions) < required_inspections and open_ended_search:
            limitations.append("high_relevance_inspection_coverage_incomplete")
            issues.append(
                DiscoveryAuditIssue(
                    code="high_relevance_inspection_coverage_stopped",
                    severity="warning",
                    summary=(
                        "The open-ended run stopped before reaching the deterministic "
                        "high-relevance inspection minimum."
                    ),
                    evidence_refs=[
                        "latest_high_relevance_candidate_count",
                        "inspected_candidate_accessions",
                    ],
                )
            )

        if open_ended_search and can_continue:
            issues.append(
                DiscoveryAuditIssue(
                    code="portfolio_search_not_converged",
                    severity="error",
                    summary=(
                        "Portfolio maximize mode must continue until repeated qualified gain stalls, "
                        "search is explicitly stopped, or a hard ceiling is reached."
                    ),
                    evidence_refs=["qualified_no_gain_count", "search_stop_reason"],
                )
            )
            actions.append(
                DiscoveryRepairAction(
                    action="search_more",
                    reason="Search a materially different unresolved dimension before maximizing selection.",
                )
            )
        elif (
            open_ended_search
            and bool(search_repair_limitations)
        ):
            incomplete_limitation = (
                "portfolio_maximize_incomplete"
                if harvest_all
                else "open_ended_search_incomplete"
            )
            limitations = _dedupe(
                [
                    *limitations,
                    incomplete_limitation,
                    *search_repair_limitations,
                ]
            )
            issues.append(
                DiscoveryAuditIssue(
                    code="portfolio_search_stopped_at_hard_ceiling",
                    severity="warning",
                    summary=(
                        "Portfolio maximize mode reached an authoritative safety ceiling; "
                        "the selected projects are usable but coverage is explicitly incomplete."
                    ),
                    evidence_refs=[
                        "budget",
                        "dynamic_limits",
                        "dynamic_usage",
                        "discovery_round_count",
                    ],
                )
            )
        elif open_ended_search and run.search_stopped:
            incomplete_limitation = (
                "portfolio_maximize_incomplete"
                if harvest_all
                else "open_ended_search_incomplete"
            )
            limitations = _dedupe(
                [
                    *limitations,
                    incomplete_limitation,
                    str(run.search_stop_reason or "search_stopped"),
                ]
            )
            issues.append(
                DiscoveryAuditIssue(
                    code="portfolio_search_stopped_before_convergence",
                    severity="warning",
                    summary=(
                        "The open-ended search was explicitly stopped before convergence; "
                        "the selected evidence remains usable with incomplete coverage."
                    ),
                    evidence_refs=["search_stopped", "search_stop_reason"],
                )
            )
        elif not harvest_all and len(delivery_eligible) < target:
            shortfall = target - len(delivery_eligible)
            message = (
                f"The quality-qualified target is short by {shortfall} project(s)."
            )
            if can_continue:
                issues.append(
                    DiscoveryAuditIssue(
                        code="quality_target_not_reached",
                        severity="error",
                        summary=message,
                        evidence_refs=["project_judgments", "candidate_pool_manifest_path"],
                    )
                )
                actions.append(
                    DiscoveryRepairAction(
                        action="search_more",
                        reason="Use a materially different query strategy to close the qualified-project gap.",
                    )
                )
            elif fixed_target:
                limitations = _dedupe(
                    [
                        *limitations,
                        "fixed_project_target_shortfall",
                        *search_repair_limitations,
                        *(
                            [str(run.search_stop_reason or "search_stopped")]
                            if run.search_stopped
                            else []
                        ),
                    ]
                )
                issues.append(
                    DiscoveryAuditIssue(
                        code="fixed_quality_target_shortfall",
                        severity="error",
                        summary=(
                            message
                            + " quota_flexibility=fixed forbids publishing this shortfall as complete."
                        ),
                        evidence_refs=[
                            "quota_flexibility",
                            "project_judgments",
                            "search_stop_reason",
                        ],
                    )
                )
                actions.append(
                    DiscoveryRepairAction(
                        action="stop_with_limitations",
                        reason=(
                            "The fixed qualified-project target is unmet and no safe bounded "
                            "search continuation remains."
                        ),
                    )
                )
            else:
                limitations.append(message)
                issues.append(
                    DiscoveryAuditIssue(
                        code="quality_target_shortfall_at_stop",
                        severity="warning",
                        summary=message,
                        evidence_refs=["search_stop_reason", "project_judgments"],
                    )
                )

        core_error_codes = {
            issue.code for issue in issues if issue.severity == "error"
        }
        ready = bool(delivery_eligible) and not core_error_codes
        if ready:
            actions = [
                DiscoveryRepairAction(
                    action="select_manifest",
                    reason="All selection quality gates pass; retain only delivery-eligible projects.",
                    project_accessions=sorted(delivery_eligible),
                )
            ]
            status = "ready"
        else:
            # Keep action order stable and remove exact duplicates for replay/UI.
            deduped_actions: list[DiscoveryRepairAction] = []
            seen_actions: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()
            for action in actions:
                key = (
                    action.action,
                    tuple(action.project_accessions),
                    tuple(action.constraint_ids),
                )
                if key not in seen_actions:
                    seen_actions.add(key)
                    deduped_actions.append(action)
            actions = deduped_actions or [
                DiscoveryRepairAction(
                    action="stop_with_limitations",
                    reason="No safe automated repair remains inside the hard discovery ceilings.",
                )
            ]
            repair_available = any(
                action.action in {"search_more", "inspect_candidates", "rescore_projects"}
                for action in actions
            )
            if repair_available and global_repair_limitations:
                limitations = _dedupe(
                    [*limitations, *global_repair_limitations]
                )
                issues.append(
                    DiscoveryAuditIssue(
                        code="autonomous_repair_ceiling_exhausted",
                        severity="error",
                        summary=(
                            "Autonomous repair cannot continue within the authoritative "
                            "agent-turn and tool-call ceilings."
                        ),
                        evidence_refs=["budget", "agent_events", "tool_call_count"],
                    )
                )
                actions = [
                    DiscoveryRepairAction(
                        action="stop_with_limitations",
                        reason=(
                            "Autonomous repair stopped at authoritative ceilings: "
                            + ", ".join(global_repair_limitations)
                        ),
                    )
                ]
                repair_available = False
            status = "repair_required" if repair_available else "blocked"

        report = DiscoveryQualityAudit(
            run_id=self.run_id,
            status=status,
            ready_for_selection=ready,
            counts={
                "target_projects": target,
                "portfolio_maximize": int(harvest_all),
                "open_ended_search": int(open_ended_search),
                "final_selection": int(final_selection),
                "candidate_projects": len(manifest.projects),
                "candidate_files": len(manifest.files),
                "requested_inspections": len(requested_accessions),
                "inspected_projects": len(completed_inspection_accessions),
                "assessable_inspections": len(assessable_inspection_accessions),
                "non_assessable_inspections": len(non_assessable_inspection_accessions),
                "failed_inspections": len(failed_accessions),
                "required_inspections": required_inspections,
                "judged_projects": len(judgments),
                "qualified_projects": len(qualified),
                "delivery_eligible_projects": len(delivery_eligible),
                "usable_files": usable_file_count,
                "strict_valid_files": strict_valid_file_count,
                "direct_usable_files": direct_usable_file_count,
                "inherited_usable_files": inherited_usable_file_count,
                "pending_files": pending_file_count,
                "weak_keep_files": weak_keep_file_count,
                "needs_review_files": needs_review_file_count,
                "agent_turns_used": agent_turns_used,
                "agent_turns_remaining": agent_turns_remaining,
                "tool_calls_remaining": tool_calls_remaining,
                "discovery_rounds_remaining": discovery_rounds_remaining,
                "query_units_remaining": query_units_remaining,
                "repository_requests_remaining": repository_requests_remaining,
                "elapsed_seconds_remaining": elapsed_seconds_remaining,
            },
            requested_inspection_accessions=requested_accessions,
            succeeded_inspection_accessions=assessable_inspection_accessions,
            non_assessable_inspection_accessions=non_assessable_inspection_accessions,
            failed_inspection_accessions=failed_accessions,
            selection_backed_coverage=selection_backed_coverage,
            selection_backed_intent_terms=selection_backed_terms,
            uncovered_intent_terms=uncovered_terms,
            unsupported_coverage_terms=unsupported_coverage_terms,
            issues=issues,
            repair_actions=actions,
            limitations=limitations,
        )
        return self._persist_discovery_quality_audit(report)

    def audit_selected_manifest(
        self,
        manifest: DatasetManifest | None = None,
        *,
        selection_accessions: set[str] | None = None,
    ) -> DiscoveryQualityAudit:
        """Audit the exact manifest that would cross the publication boundary."""

        if manifest is None:
            run = self._require_run()
            path = Path(run.current_manifest_path or "")
            if not str(path) or not path.exists() or not path.is_file():
                report = DiscoveryQualityAudit(
                    run_id=self.run_id,
                    status="blocked",
                    issues=[
                        DiscoveryAuditIssue(
                            code="selected_manifest_missing",
                            severity="error",
                            summary="The selected manifest is missing at the publication boundary.",
                            evidence_refs=["current_manifest_path"],
                        )
                    ],
                    repair_actions=[
                        DiscoveryRepairAction(
                            action="stop_with_limitations",
                            reason="No selected manifest is available for final quality audit.",
                        )
                    ],
                    limitations=["selected_manifest_missing"],
                )
                return self._persist_discovery_quality_audit(report)
            manifest = _load_manifest(path)
        return self.audit_discovery_state(
            meter_tool=False,
            manifest_override=manifest,
            selection_accessions=selection_accessions,
            final_selection=True,
        )

    def _quality_audit_required(self, run: AgentRunRecord) -> bool:
        return bool(
            run.project_judgments
            or self.search_environment is not None
            or self.request.quota_flexibility == "fixed"
            or _hard_builtin_constraint_values(self.request)
            or self.request.is_hard_constraint("per_project_min_files")
            or self.request.is_hard_constraint("per_project_min_samples")
            or any(
                constraint.strength == "hard"
                for constraint in self.request.scientific_constraints
            )
        )

    def _persist_discovery_quality_audit(
        self,
        report: DiscoveryQualityAudit,
        *,
        event_type: str = "discovery_quality_audited",
    ) -> DiscoveryQualityAudit:
        run = self._require_run()
        self.store.save_run(
            run.model_copy(update={"latest_discovery_audit": report})
        )
        self.store.append_event(
            self.run_id,
            event_type,
            report.model_dump(mode="json"),
        )
        return report

    def _state_summary(self, run: AgentRunRecord) -> dict[str, Any]:
        target_project_count = int((run.request or {}).get("max_projects") or 1)
        judgment_summary = summarize_project_judgments(
            run.project_judgments,
            target_project_count=target_project_count,
        )
        return {
            "run_id": run.run_id,
            "status": run.status,
            "tool_call_count": run.tool_call_count,
            "discovery_round_count": run.discovery_round_count,
            "candidate_search_count": run.candidate_search_count,
            "candidate_inspection_count": run.candidate_inspection_count,
            "inspected_candidate_accessions": run.inspected_candidate_accessions,
            "minimum_high_relevance_inspections": self._required_high_relevance_inspections(
                run.latest_high_relevance_candidate_count,
            ),
            "no_gain_action_count": run.no_gain_action_count,
            "latest_candidate_search_id": run.latest_candidate_search_id,
            "latest_high_relevance_candidate_count": run.latest_high_relevance_candidate_count,
            "latest_semantic_coverage": run.latest_semantic_coverage,
            "model_usage": {
                "requests": run.model_requests,
                "sdk_turns": run.sdk_turn_count,
                "input_tokens": run.model_input_tokens,
                "output_tokens": run.model_output_tokens,
                "total_tokens": run.model_total_tokens,
            },
            "max_discovery_rounds": run.budget.max_discovery_rounds,
            "current_manifest_path": run.current_manifest_path,
            "candidate_pool_manifest_path": run.candidate_pool_manifest_path,
            "selected_round_index": run.selected_round_index,
            "selection_rationale": run.selection_rationale,
            "warnings": run.warnings,
            "blockers": run.blockers,
            "dynamic_budget_enabled": run.dynamic_budget_enabled,
            "budget_mode": (
                "budget_agent_grant_chain"
                if run.dynamic_budget_enabled
                else "agent_autonomous_hard_ceilings"
            ),
            "hard_budget_remaining": {
                "model_turns": run.remaining_model_turn_budget(),
                "discovery_rounds": max(
                    0, int(run.budget.max_discovery_rounds) - int(run.discovery_round_count)
                ),
                "tool_calls": max(0, int(run.budget.max_tool_calls) - int(run.tool_call_count)),
                "query_units": max(
                    0,
                    int(run.dynamic_limits.max_query_units) - int(run.dynamic_usage.query_units),
                ),
                "repository_requests": max(
                    0,
                    int(run.dynamic_limits.max_repository_requests)
                    - int(run.dynamic_usage.repository_requests),
                ),
            },
            "dynamic_limits": run.dynamic_limits.model_dump(mode="json"),
            "dynamic_usage": run.dynamic_usage.model_dump(mode="json"),
            "active_grant_id": run.active_grant_id,
            "grant_execution": grant_execution_summary(self.store, run.run_id),
            "active_grant": self._active_grant_payload(run),
            "search_stopped": run.search_stopped,
            "search_stop_reason": run.search_stop_reason,
            "latest_metrics": run.latest_metrics.model_dump(mode="json") if run.latest_metrics else None,
            "project_judgments": {
                accession: judgment.model_dump(mode="json")
                for accession, judgment in run.project_judgments.items()
            },
            "project_judgment_summary": judgment_summary,
            "qualified_project_count": judgment_summary["qualified_projects"],
            "target_project_count": judgment_summary["qualified_target"],
            "quality_target_reached": judgment_summary["quality_target_reached"],
            "qualified_no_gain_count": run.qualified_no_gain_count,
            "consecutive_zero_yield": run.consecutive_zero_yield,
            "search_recovery_required": run.search_recovery_required,
            "search_recovery_attempts": run.search_recovery_attempts,
            "last_search_strategy": run.last_search_strategy,
            "portfolio": run.portfolio_state,
        }

    def _bind_candidate_search_to_grant(
        self,
        action: CandidateSearchAction,
        grant_id: str,
    ) -> CandidateSearchAction:
        """Force the search action to use the grant's approved query texts.

        The agent may still supply depths/rationale, but query wording is taken from
        the one-use grant so approval and execution cannot drift apart.
        """
        if self.budget_governor is None:
            raise RuntimeError("dynamic_budget_governor_required")
        grant = self.budget_governor.store.load_search_grant(grant_id)
        if grant is None or grant.run_id != self.run_id:
            raise ValueError("search_grant_not_found")
        if grant.status != "issued":
            raise ValueError(f"grant_already_{grant.status}")
        by_key = {
            " ".join(item.query.casefold().split()): item
            for item in action.queries
        }
        bound_queries: list[RepositoryQuery] = []
        for approved in grant.approved_queries:
            key = " ".join(approved.casefold().split())
            previous = by_key.get(key)
            bound_queries.append(
                RepositoryQuery(
                    query=approved,
                    depth=previous.depth if previous is not None else 20,
                    intent_dimension=(
                        previous.intent_dimension if previous is not None else "general"
                    ),
                    expected_gain=previous.expected_gain if previous is not None else "",
                    budget_role=(
                        getattr(previous, "budget_role", None)
                        if previous is not None
                        else "primary_theme"
                    )
                    or "primary_theme",
                )
            )
        if not bound_queries:
            raise ValueError("search_grant_query_mismatch")
        return action.model_copy(
            update={
                "queries": bound_queries,
                "rationale": (
                    f"{action.rationale.strip()} "
                    f"[bound to grant {grant_id} approved queries]"
                ).strip(),
            }
        )

    def _active_grant_payload(self, run: AgentRunRecord) -> dict[str, Any] | None:
        if not run.active_grant_id:
            return None
        grant = self.store.load_search_grant(run.active_grant_id)
        if grant is None:
            return None
        return {
            "grant_id": grant.grant_id,
            "status": grant.status,
            "approved_queries": list(grant.approved_queries),
            "query_units": grant.query_units,
            "proposal_id": grant.proposal_id,
        }

    def _query_strategy(self, queries: list[str]) -> str:
        if self.request.repository in {"pride", "auto"}:
            return classify_pride_query_strategy(queries)
        return "repository_semantic"

    def _diagnose_search_result(
        self,
        *,
        run: AgentRunRecord,
        proposed_queries: list[str],
        summary: dict[str, Any],
    ) -> SearchDiagnosis:
        executed_queries = [str(value) for value in summary.get("queries", []) if str(value).strip()]
        if not executed_queries:
            executed_queries = proposed_queries
        strategy = self._query_strategy(executed_queries)
        candidate_projects = max(
            int(summary.get("candidate_projects_seen") or 0),
            int(summary.get("selected_projects") or 0),
            1 if int(summary.get("selected_files") or 0) > 0 else 0,
        )
        failures = summary.get("failures") if isinstance(summary.get("failures"), list) else []
        recovery_attempted = run.search_recovery_required and strategy == "atomic_seed"
        if candidate_projects > 0:
            return SearchDiagnosis(
                health="healthy_yield",
                strategy=strategy,
                proposed_queries=proposed_queries,
                executed_queries=executed_queries,
                consecutive_zero_yield=0,
                recovery_required=False,
                recovery_attempted=recovery_attempted,
                reason=f"Repository returned {candidate_projects} candidate project(s).",
            )
        if failures:
            return SearchDiagnosis(
                health="repository_unavailable",
                strategy=strategy,
                proposed_queries=proposed_queries,
                executed_queries=executed_queries,
                consecutive_zero_yield=run.consecutive_zero_yield,
                recovery_required=False,
                recovery_attempted=recovery_attempted,
                reason="Repository requests failed before candidates could be evaluated.",
            )
        consecutive = run.consecutive_zero_yield + 1
        if strategy == "atomic_seed":
            return SearchDiagnosis(
                health="no_match_after_recovery",
                strategy=strategy,
                proposed_queries=proposed_queries,
                executed_queries=executed_queries,
                consecutive_zero_yield=consecutive,
                recovery_required=False,
                recovery_attempted=recovery_attempted or self._query_strategy(proposed_queries) != strategy,
                reason="Atomic high-recall repository seeds returned no candidate projects.",
            )
        return SearchDiagnosis(
            health="selectivity_suspected",
            strategy=strategy,
            proposed_queries=proposed_queries,
            executed_queries=executed_queries,
            consecutive_zero_yield=consecutive,
            recovery_required=True,
            recovery_attempted=False,
            reason="Compound repository queries returned no candidates; retry with atomic seeds.",
        )

    @property
    def dynamic_limits(self) -> DynamicBudgetLimits:
        return self._require_run().dynamic_limits

    def current_metrics(self) -> RoundMetrics:
        run = self._require_run()
        if run.latest_metrics is not None:
            return run.latest_metrics
        empty = DatasetManifest(
            run_id=self.run_id,
            request=self.request,
            summary={"selected_projects": 0, "selected_files": 0},
        )
        return evaluate_round_metrics(
            empty,
            None,
            request=self.request,
            queries=[],
            prior_queries=[],
            usage=run.dynamic_usage,
            limits=run.dynamic_limits,
            round_index=0,
        )

    def _blocked_observation(self, queries: list[str], reason: str) -> DiscoveryRoundObservation:
        run = self._require_run()
        self.store.append_event(
            self.run_id,
            "tool_denied",
            {"tool": "search_repository_datasets", "reason": reason, "queries": queries},
        )
        return DiscoveryRoundObservation(
            status="blocked",
            round_index=run.discovery_round_count + 1,
            queries=queries,
            recommended_action="stop" if run.search_stopped else "revise_queries_or_request_budget",
            blockers=[reason],
        )

    def _manifest_path_for_selection(self, run: AgentRunRecord, round_index: int) -> Path | None:
        if round_index == 0:
            return Path(run.candidate_pool_manifest_path) if run.candidate_pool_manifest_path else None
        reference = run.artifacts.get(f"discovery_round_{round_index:02d}")
        return Path(reference.path) if reference is not None else None

    def _selection_rejected(self, round_index: int, reason: str) -> dict[str, Any]:
        payload = {"status": "blocked", "round_index": round_index, "blockers": [reason]}
        self.store.append_event(self.run_id, "manifest_selection_rejected", payload)
        return payload

    def publish_latest_manifest(self) -> dict[str, str]:
        run = self._require_run()
        if not run.current_manifest_path:
            return {}
        manifest_path = Path(run.current_manifest_path)
        if not manifest_path.exists() or not manifest_path.is_file():
            return {}
        manifest = DatasetManifest.model_validate(
            json.loads(manifest_path.read_text(encoding="utf-8"))
        )
        if self._quality_audit_required(run):
            audit = self.audit_selected_manifest(manifest)
            if not audit.ready_for_selection:
                run = self._require_run()
                blocker = "final_manifest_quality_audit_requires_repair"
                self.store.save_run(
                    run.model_copy(
                        update={
                            "status": "blocked",
                            "stop_reason": "selection_quality_gate_not_completed",
                            "blockers": _dedupe([*run.blockers, blocker]),
                        }
                    )
                )
                self.store.append_event(
                    self.run_id,
                    "manifest_publication_rejected",
                    {
                        "reason": blocker,
                        "audit_status": audit.status,
                    },
                )
                return {}
            run = self._require_run()
        paths = write_dataset_manifest(manifest, self.output_dir)
        artifacts = dict(run.artifacts)
        for name, path in paths.items():
            artifacts[f"selected:{name}"] = ArtifactReference(
                path=str(path),
                artifact_type=name,
            )
        self.store.save_run(run.model_copy(update={"artifacts": artifacts}))
        return {name: str(path) for name, path in paths.items()}

    def _require_run(self) -> AgentRunRecord:
        run = self.store.load_run(self.run_id)
        if run is None:
            raise KeyError(f"Unknown agent run: {self.run_id}")
        return run


def _load_manifest(path: Path) -> DatasetManifest:
    return DatasetManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _normalized_builtin_evidence_value(
    field_name: str,
    value: Any,
) -> str | None:
    if not is_substantive_constraint_value(value):
        return None
    if field_name == "acquisition_mode":
        normalized = normalize_acquisition_mode(str(value))
    elif field_name == "labeling_strategy":
        normalized = normalize_labeling_strategy(str(value))
    else:
        raise ValueError(f"unsupported hard built-in constraint: {field_name}")
    normalized = str(normalized or "").strip().casefold()
    return normalized if normalized and normalized != "unknown" else None


def _hard_builtin_constraint_values(request: DatasetRequest) -> dict[str, str]:
    constraints: dict[str, str] = {}
    for field_name in _HARD_BUILTIN_EVIDENCE_FIELDS:
        if not request.is_hard_constraint(field_name):
            continue
        expected = _normalized_builtin_evidence_value(
            field_name,
            getattr(request, field_name, None),
        )
        if expected is not None:
            constraints[field_name] = expected
    return constraints


def _hard_builtin_constraint_failures(
    constraints: dict[str, str],
    project: DiscoveredProject | None,
    files: list[DiscoveredFile],
) -> set[str]:
    failures: set[str] = set()
    for field_name, expected in constraints.items():
        project_value = _normalized_builtin_evidence_value(
            field_name,
            getattr(project, field_name, None) if project is not None else None,
        )
        file_values = [
            _normalized_builtin_evidence_value(
                field_name,
                getattr(file, field_name, None),
            )
            for file in files
        ]
        if (
            project_value != expected
            or not file_values
            or any(value != expected for value in file_values)
        ):
            failures.add(field_name)
    return failures


def _is_delivery_eligible(
    project: DiscoveredProject | None,
    file: DiscoveredFile | None = None,
) -> bool:
    if (
        project is None
        or project.needs_review
        or project.validity_status not in {"valid", "weak_keep"}
    ):
        return False
    if file is None:
        return True
    # Files, rather than projects, are the delivery unit.  Project-level
    # evidence may make a file valid by inheritance, but weak_keep is never
    # silently promoted into a downloadable delivery.
    if file.needs_review or file.validity_status != "valid":
        return False
    if not str(file.file_accession_or_path or "").strip():
        return False
    if not str(file.download_url or "").strip():
        return False
    if file.file_role == "unknown":
        return False
    return True


def _files_passing_hard_scoped_constraints(
    files: list[DiscoveredFile],
    judgment: ProjectJudgmentInput,
    constraints: list[ScientificConstraint],
) -> list[DiscoveredFile]:
    """Return files with explicit, machine-verifiable per-file observations."""

    assessments = {
        assessment.constraint_id: assessment
        for assessment in judgment.constraint_assessments
    }
    passing: list[DiscoveredFile] = []
    for file in files:
        file_passes = True
        for constraint in constraints:
            if constraint.strength != "hard" or constraint.scope not in {"file", "sample"}:
                continue
            assessment = assessments.get(constraint.id)
            if assessment is None or assessment.status not in {"pass", "partial"}:
                file_passes = False
                break
            observed_map = assessment.observed_value
            if not isinstance(observed_map, dict):
                file_passes = False
                break
            normalized_observations = {
                str(key).strip().casefold(): value
                for key, value in observed_map.items()
                if str(key).strip()
            }
            identifiers = {
                str(file.file_name or "").strip().casefold(),
                str(file.file_accession_or_path or "").strip().casefold(),
            }
            observed = next(
                (
                    normalized_observations[key]
                    for key in identifiers
                    if key and key in normalized_observations
                ),
                None,
            )
            if evaluate_constraint_value(constraint, observed) is not True:
                file_passes = False
                break
        if file_passes:
            passing.append(file)
    return passing


def _portfolio_constraint_observed_value(
    manifest: DatasetManifest,
    accessions: set[str],
    dimension: str,
) -> Any:
    normalized = str(dimension or "").strip().casefold().replace("-", "_")
    projects = [
        project
        for project in manifest.projects
        if project.project_accession.upper() in accessions
    ]
    files = [
        file
        for file in manifest.files
        if file.project_accession.upper() in accessions
        and _is_delivery_eligible(
            next(
                (
                    project
                    for project in projects
                    if project.project_accession.upper()
                    == file.project_accession.upper()
                ),
                None,
            ),
            file,
        )
    ]
    if normalized in {"project_count", "projects", "qualified_project_count"}:
        return len(projects)
    if normalized in {"file_count", "files", "delivery_file_count"}:
        return len(files)
    if normalized in {"instrument_family_count", "instrument_families"}:
        return len(
            {
                str(value).strip().casefold()
                for item in [*projects, *files]
                for value in item.instrument_families
                if str(value).strip()
            }
        )
    if normalized in {"species_count", "species_diversity"}:
        return len(
            {
                str(value).strip().casefold()
                for item in [*projects, *files]
                for value in item.species
                if str(value).strip()
            }
        )
    if normalized in {"acquisition_mode_count", "acquisition_diversity"}:
        return len(
            {
                str(item.acquisition_mode).strip().casefold()
                for item in [*projects, *files]
                if str(item.acquisition_mode or "").strip()
            }
        )
    return None


def _project_sample_count(project: DiscoveredProject | None) -> int | None:
    if project is None:
        return None
    sources = [
        project.raw_metadata,
        project.sdrf_summary,
    ]
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in (
            "sample_count",
            "sampleCount",
            "number_of_samples",
            "numberOfSamples",
            "biological_sample_count",
        ):
            value = source.get(key)
            if isinstance(value, bool):
                continue
            try:
                count = int(value)
            except (TypeError, ValueError):
                continue
            if count >= 0:
                return count
    return None


def _delivery_manifest_subset(
    manifest: DatasetManifest,
    *,
    selected_accessions: set[str] | None = None,
    selected_file_identifiers: set[str] | None = None,
    project_judgments: dict[str, ProjectJudgmentInput] | None = None,
    scientific_constraints: list[ScientificConstraint] | None = None,
) -> DatasetManifest:
    projects_by_accession = {
        project.project_accession.upper(): project for project in manifest.projects
    }
    judgments = project_judgments or {}
    scoped_hard_constraints = [
        constraint
        for constraint in (scientific_constraints or [])
        if constraint.strength == "hard" and constraint.scope in {"file", "sample"}
    ]
    files: list[DiscoveredFile] = []
    for file in manifest.files:
        accession = file.project_accession.upper()
        if selected_accessions is not None and accession not in selected_accessions:
            continue
        if selected_file_identifiers is not None:
            identifier = (
                f"{file.repository}:{file.project_accession}:"
                f"{file.file_accession_or_path or file.file_name}"
            ).casefold()
            short_identifier = str(file.file_accession_or_path or file.file_name).casefold()
            if identifier not in selected_file_identifiers and short_identifier not in selected_file_identifiers:
                continue
        if not _is_delivery_eligible(projects_by_accession.get(accession), file):
            continue
        if scoped_hard_constraints:
            judgment = judgments.get(accession)
            if judgment is None or file not in _files_passing_hard_scoped_constraints(
                [file],
                judgment,
                scoped_hard_constraints,
            ):
                continue
        files.append(file)
    delivered_accessions = {file.project_accession.upper() for file in files}
    projects = [
        project
        for project in manifest.projects
        if project.project_accession.upper() in delivered_accessions
        and _is_delivery_eligible(project)
    ]
    return manifest.model_copy(update={"projects": projects, "files": files})


def _selected_file_count(manifest: DatasetManifest) -> int:
    return int(manifest.summary.get("selected_files") or len(manifest.files))


def _project_key(project: DiscoveredProject) -> tuple[str, str]:
    return str(project.repository), project.project_accession.casefold()


def _file_key(file: DiscoveredFile) -> tuple[str, str, str]:
    native = file.file_accession_or_path or file.native_accession or file.file_name
    return str(file.repository), file.project_accession.casefold(), str(native).casefold()


def _project_rank(project: DiscoveredProject) -> tuple[float, float, float, int]:
    return (
        float(project.trust_score or project.confidence or 0.0),
        float(project.project_score or 0.0),
        float(project.evidence_completeness or 0.0),
        len(project.evidence),
    )


def _file_rank(file: DiscoveredFile) -> tuple[int, int, float, float, float, float, int]:
    validity = {"valid": 4, "weak_keep": 3, "needs_review": 2, "exclude": 0}
    readiness = {"ready": 3, "weak_ready": 2, "not_ready": 0, None: 1}
    return (
        validity.get(file.validity_status, 0),
        readiness.get(file.task_readiness_status, 1),
        float(file.task_ai_readiness_score or 0.0),
        float(file.data_value_score or 0.0),
        float(file.trust_score or file.confidence or 0.0),
        float(file.file_score or 0.0),
        file.file_level_evidence_count,
    )


def _manifest_rank(manifest: DatasetManifest) -> tuple[int, int, int, float]:
    valid = sum(1 for file in manifest.files if file.validity_status == "valid")
    usable = sum(1 for file in manifest.files if file.validity_status in {"valid", "weak_keep"})
    ready = sum(1 for file in manifest.files if file.task_readiness_status == "ready")
    mean_trust = (
        sum(float(file.trust_score or file.confidence or 0.0) for file in manifest.files) / len(manifest.files)
        if manifest.files
        else 0.0
    )
    return valid, usable, ready, mean_trust


def _merge_discovery_manifests(
    manifests: list[DatasetManifest],
    *,
    request: DatasetRequest,
    run_id: str,
    retain_all_candidates: bool = False,
) -> DatasetManifest:
    projects: dict[tuple[str, str], DiscoveredProject] = {}
    files: dict[tuple[str, str, str], DiscoveredFile] = {}
    for manifest in manifests:
        for project in manifest.projects:
            key = _project_key(project)
            if key not in projects or _project_rank(project) > _project_rank(projects[key]):
                projects[key] = project
        for file in manifest.files:
            if file.validity_status == "exclude":
                continue
            key = _file_key(file)
            if key not in files or _file_rank(file) > _file_rank(files[key]):
                files[key] = file

    grouped: dict[tuple[str, str], list[DiscoveredFile]] = {}
    for file in files.values():
        grouped.setdefault((str(file.repository), file.project_accession.casefold()), []).append(file)
    items = [
        (project, grouped.get(key, []))
        for key, project in projects.items()
        if grouped.get(key)
    ]
    selected_items = items if retain_all_candidates else select_diverse_items(items, request)
    selected_projects = [project for project, _ in selected_items]
    selected_files = [file for _, project_files in selected_items for file in project_files]
    diversity = diversity_summary(selected_files)
    validity = validity_summary(selected_files)
    evidence = Counter(str(file.evidence_level or "unknown") for file in selected_files)
    readiness = Counter(str(file.task_readiness_status or "not_set") for file in selected_files)
    summary = {
        "selected_projects": len(selected_projects),
        "selected_files": len(selected_files),
        "candidate_projects_seen": len(projects),
        "candidate_files_seen": len(files),
        "evidence_level_distribution": dict(sorted(evidence.items())),
        "task_readiness_status_counts": dict(sorted(readiness.items())),
        "candidate_pool": {
            "merged_rounds": len(manifests),
            "deduplicated_projects": len(projects),
            "deduplicated_files": len(files),
            "retains_all_inspected_candidates": retain_all_candidates,
        },
        "openai_agents_control_plane": {
            "run_id": run_id,
            "runtime": "openai_agents",
            "artifact": "candidate_pool",
        },
        **diversity,
        **validity,
    }
    return DatasetManifest(
        run_id=run_id,
        request=request,
        projects=selected_projects,
        files=selected_files,
        summary=summary,
    )


def _project_assessments(
    manifest: DatasetManifest,
    candidate_search: dict[str, Any],
) -> list[dict[str, Any]]:
    previews = {
        str(item.get("project_accession") or "").upper(): item
        for item in candidate_search.get("previews") or []
        if isinstance(item, dict)
    }
    assessments: list[dict[str, Any]] = []
    for project in manifest.projects:
        project_files = [
            file
            for file in manifest.files
            if file.project_accession.casefold() == project.project_accession.casefold()
        ]
        preview = previews.get(project.project_accession.upper(), {})
        raw_metadata = (
            project.raw_metadata if isinstance(project.raw_metadata, dict) else {}
        )
        assessments.append(
            {
                "project_accession": project.project_accession,
                "project_title": project.project_title or "",
                "project_description_excerpt": _evidence_excerpt(
                    project.project_description
                ),
                "sample_processing_excerpt": _evidence_excerpt(
                    raw_metadata.get("sampleProcessingProtocol")
                ),
                "data_processing_excerpt": _evidence_excerpt(
                    raw_metadata.get("dataProcessingProtocol")
                ),
                "selected_file_count": len(project_files),
                "selected_file_examples": [
                    file.file_name for file in project_files[:8]
                ],
                "species": project.species,
                "acquisition_mode": project.acquisition_mode,
                "labeling_strategy": project.labeling_strategy,
                "instrument_names": list(project.instrument_names),
                "instrument_families": list(project.instrument_families),
                "instrument_generation_score": project.instrument_generation_score,
                "instrument_generation_label": project.instrument_generation_label,
                "project_publication_date": project.project_publication_date,
                "project_submission_date": project.project_submission_date,
                "immunopeptide_scope": project.immunopeptide_scope,
                "hla_class": list(project.hla_class),
                "immunopeptide_evidence_terms": list(
                    project.immunopeptide_evidence_terms
                ),
                "validity_status": project.validity_status,
                "validity_status_counts": dict(
                    Counter(file.validity_status for file in project_files)
                ),
                "evidence_level_counts": dict(
                    Counter(str(file.evidence_level or "unknown") for file in project_files)
                ),
                "file_evidence_warning_counts": dict(
                    Counter(
                        warning
                        for file in project_files
                        for warning in file.evidence_warnings
                    )
                ),
                "sdrf": _bounded_sdrf_summary(project.sdrf_summary),
                "task_readiness_status_counts": dict(
                    Counter(str(file.task_readiness_status or "not_set") for file in project_files)
                ),
                "matched_intent_terms": list(preview.get("matched_intent_terms") or []),
                "query_hits": list(preview.get("query_hits") or []),
                "needs_review": project.needs_review,
                "available_evidence_refs": sorted(
                    _available_project_evidence_refs(project, project_files)
                ),
            }
        )
    return assessments


def _available_project_evidence_refs(
    project: DiscoveredProject | None,
    project_files: list[DiscoveredFile],
) -> set[str]:
    return set(_project_evidence_values(project, project_files))


def _project_evidence_values(
    project: DiscoveredProject | None,
    project_files: list[DiscoveredFile],
) -> dict[str, Any]:
    """Return the persisted value behind every available public evidence ref."""

    if project is None:
        return {}
    raw_metadata = project.raw_metadata if isinstance(project.raw_metadata, dict) else {}
    evidence: dict[str, Any] = {}
    candidates: dict[str, Any] = {
        "project_title": project.project_title,
        "project_description_excerpt": project.project_description,
        "sample_processing_excerpt": raw_metadata.get("sampleProcessingProtocol"),
        "data_processing_excerpt": raw_metadata.get("dataProcessingProtocol"),
        "acquisition_mode": project.acquisition_mode,
        "labeling_strategy": project.labeling_strategy,
        "project_publication_date": project.project_publication_date,
        "species": project.species,
        "instrument_names": project.instrument_names,
        "immunopeptide_evidence_terms": project.immunopeptide_evidence_terms,
    }
    for ref, value in candidates.items():
        if _has_substantive_evidence_value(value):
            evidence[ref] = value
    file_names = list(
        dict.fromkeys(
            value
            for file in project_files
            for value in (
                str(file.file_name or "").strip(),
                str(file.file_accession_or_path or "").strip(),
            )
            if value
        )
    )
    if file_names:
        evidence["selected_file_examples"] = file_names
    if project_files:
        evidence["validity_status_counts"] = dict(
            Counter(file.validity_status for file in project_files)
        )
        evidence["evidence_level_counts"] = dict(
            Counter(file.evidence_level for file in project_files)
        )
    if isinstance(project.sdrf_summary, dict):
        sdrf_status = str(project.sdrf_summary.get("status") or "").strip().casefold()
        try:
            sdrf_rows = int(project.sdrf_summary.get("row_count") or 0)
        except (TypeError, ValueError):
            sdrf_rows = 0
        if sdrf_status == "available" and sdrf_rows > 0:
            evidence["sdrf"] = project.sdrf_summary
    return evidence


def _constraint_assessment_evidence_problems(
    judgment: ProjectJudgmentInput,
    available_evidence_refs: set[str],
    *,
    constraints: dict[str, ScientificConstraint] | None = None,
    evidence_values: dict[str, Any] | None = None,
) -> list[tuple[str, str, list[str]]]:
    problems: list[tuple[str, str, list[str]]] = []
    active_constraints = constraints or {}
    persisted_values = evidence_values or {}
    for assessment in judgment.constraint_assessments:
        if not assessment.evidence_refs:
            problems.append(("required", assessment.constraint_id, []))
            continue
        unavailable = sorted(
            ref
            for ref in assessment.evidence_refs
            if ref not in available_evidence_refs
        )
        if unavailable:
            problems.append(("unavailable", assessment.constraint_id, unavailable))
            continue
        constraint = active_constraints.get(assessment.constraint_id)
        if (
            constraint is not None
            and constraint.evidence_required
            and assessment.status in {"pass", "partial", "fail"}
            and not _evidence_supports_observed_value(
                assessment.observed_value,
                [persisted_values.get(ref) for ref in assessment.evidence_refs],
            )
        ):
            problems.append(
                ("unsupported", assessment.constraint_id, list(assessment.evidence_refs))
            )
    return problems


def _has_substantive_evidence_value(value: Any) -> bool:
    return is_substantive_constraint_value(value)


def _evidence_supports_observed_value(
    observed_value: Any,
    cited_values: list[Any],
) -> bool:
    """Require every claimed observed atom to be grounded in cited persistence."""

    observed_atoms = _evidence_atoms(observed_value, include_mapping_keys=True)
    evidence_atoms = [
        atom
        for value in cited_values
        for atom in _evidence_atoms(value, include_mapping_keys=True)
    ]
    if not observed_atoms or not evidence_atoms:
        return False

    def supported(observed: tuple[str, bool]) -> bool:
        observed_text, observed_is_number = observed
        for evidence_text, evidence_is_number in evidence_atoms:
            if observed_is_number:
                if (
                    evidence_is_number
                    and observed_text == evidence_text
                ) or observed_text in _evidence_numeric_tokens(evidence_text):
                    return True
                continue
            if evidence_is_number:
                continue
            observed_tokens = _evidence_tokens(observed_text)
            evidence_tokens = _evidence_tokens(evidence_text)
            if _contains_token_sequence(evidence_tokens, observed_tokens):
                return True
        return False

    return all(supported(atom) for atom in observed_atoms)


def _evidence_atoms(
    value: Any,
    *,
    include_mapping_keys: bool,
) -> list[tuple[str, bool]]:
    if not is_substantive_constraint_value(value):
        return []
    if isinstance(value, dict):
        atoms: list[tuple[str, bool]] = []
        for key, item in value.items():
            if include_mapping_keys:
                atoms.extend(_evidence_atoms(key, include_mapping_keys=False))
            atoms.extend(_evidence_atoms(item, include_mapping_keys=include_mapping_keys))
        return atoms
    if isinstance(value, (list, tuple, set)):
        return [
            atom
            for item in value
            for atom in _evidence_atoms(item, include_mapping_keys=include_mapping_keys)
        ]
    if isinstance(value, bool):
        return [("true" if value else "false", False)]
    if isinstance(value, (int, float)):
        number = _canonical_evidence_number(str(value))
        return [(number, True)] if number is not None else []
    text = " ".join(str(value or "").split()).strip().casefold()
    number = _canonical_evidence_number(text)
    if number is not None:
        return [(number, True)]
    return [(text, False)] if text else []


def _canonical_evidence_number(value: str) -> str | None:
    text = str(value or "").strip().casefold()
    if not _EVIDENCE_NUMBER_RE.fullmatch(text):
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    normalized = format(number.normalize(), "f")
    return "0" if normalized in {"-0", "+0"} else normalized


def _evidence_tokens(value: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for match in _EVIDENCE_TOKEN_RE.finditer(str(value or "").casefold()):
        token = match.group(0)
        tokens.append(_canonical_evidence_number(token) or token)
    return tuple(tokens)


def _evidence_numeric_tokens(value: str) -> set[str]:
    return {
        number
        for token in _evidence_tokens(value)
        if (number := _canonical_evidence_number(token)) is not None
    }


def _contains_token_sequence(
    evidence_tokens: tuple[str, ...],
    observed_tokens: tuple[str, ...],
) -> bool:
    if not observed_tokens or len(observed_tokens) > len(evidence_tokens):
        return False
    width = len(observed_tokens)
    return any(
        evidence_tokens[index : index + width] == observed_tokens
        for index in range(len(evidence_tokens) - width + 1)
    )


def _evidence_excerpt(value: Any, *, limit: int = 700) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _bounded_repair_evidence(value: Any, *, depth: int = 0) -> Any:
    """Return enough persisted evidence for one-step Agent repair without echoing a full manifest."""

    if depth >= 3:
        return _evidence_excerpt(value, limit=240)
    if isinstance(value, dict):
        return {
            str(key): _bounded_repair_evidence(item, depth=depth + 1)
            for key, item in list(value.items())[:8]
        }
    if isinstance(value, (list, tuple, set)):
        return [
            _bounded_repair_evidence(item, depth=depth + 1)
            for item in list(value)[:8]
        ]
    if isinstance(value, str):
        return _evidence_excerpt(value, limit=240)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _evidence_excerpt(value, limit=240)


def _bounded_sdrf_summary(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    canonical_raw = raw.get("canonical_fields")
    canonical = canonical_raw if isinstance(canonical_raw, dict) else {}
    allowed_fields = (
        "cell_line",
        "organism",
        "disease",
        "treatment",
        "control",
        "assay",
        "fraction",
    )
    canonical_fields = {
        field: [
            _evidence_excerpt(item, limit=240)
            for item in (canonical.get(field) or [])[:12]
            if str(item or "").strip()
        ]
        for field in allowed_fields
        if isinstance(canonical.get(field), list)
    }
    counts_raw = raw.get("match_status_counts")
    counts = counts_raw if isinstance(counts_raw, dict) else {}
    examples_raw = raw.get("file_match_examples")
    examples = examples_raw if isinstance(examples_raw, list) else []
    missing_raw = raw.get("missing_columns")
    missing = missing_raw if isinstance(missing_raw, list) else []
    conflicts_raw = raw.get("conflicts")
    conflicts = conflicts_raw if isinstance(conflicts_raw, list) else []
    errors_raw = raw.get("errors")
    errors = errors_raw if isinstance(errors_raw, list) else []
    digest = str(raw.get("content_sha256") or "").strip().lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        digest = ""
    return {
        "status": _evidence_excerpt(raw.get("status") or "not_captured", limit=80),
        "source_url": _public_sdrf_url(raw.get("source_url")),
        "content_sha256": digest or None,
        "row_count": _bounded_nonnegative_int(raw.get("row_count")),
        "match_status_counts": {
            key: _bounded_nonnegative_int(counts.get(key))
            for key in ("matched", "no_file_match", "no_sdrf")
        },
        "canonical_fields": canonical_fields,
        "file_match_examples": [
            {
                "file_name": _evidence_excerpt(item.get("file_name"), limit=240),
                "status": _evidence_excerpt(item.get("status"), limit=40),
                "matched_row_count": _bounded_nonnegative_int(item.get("matched_row_count")),
            }
            for item in examples[:8]
            if isinstance(item, dict)
        ],
        "missing_columns": [
            _evidence_excerpt(item, limit=80)
            for item in missing[:12]
        ],
        "conflicts": [
            _evidence_excerpt(item, limit=300)
            for item in conflicts[:12]
        ],
        "errors": [
            _evidence_excerpt(item, limit=500)
            for item in errors[:8]
        ],
    }


def _public_sdrf_url(value: Any) -> str | None:
    text = _evidence_excerpt(value, limit=500)
    if not text:
        return None
    try:
        parsed = urlsplit(text)
        if parsed.scheme not in {"http", "https", "ftp"} or not parsed.hostname:
            return None
        port = f":{parsed.port}" if parsed.port is not None else ""
        return urlunsplit((parsed.scheme, f"{parsed.hostname}{port}", parsed.path, "", ""))
    except ValueError:
        return None


def _bounded_nonnegative_int(value: Any) -> int:
    try:
        return max(0, min(int(value or 0), 10_000_000))
    except (TypeError, ValueError, OverflowError):
        return 0


def _normalize_accessions(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        accession = str(value or "").strip().upper()
        if not accession or accession in normalized:
            continue
        normalized.append(accession)
    return normalized


def _normalize_queries(queries: list[str]) -> list[str]:
    if not isinstance(queries, list):
        raise ValueError("queries must be a list of repository search strings")
    result: list[str] = []
    seen: set[str] = set()
    for value in queries:
        text = " ".join(str(value or "").split()).strip()
        if not text:
            continue
        if len(text) > 240:
            raise ValueError("each discovery query must be at most 240 characters")
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    if not result:
        raise ValueError("at least one non-empty discovery query is required")
    if len(result) > 40:
        raise ValueError("at most 40 discovery queries are allowed per round")
    return result


def _observation_from_manifest(
    manifest: DatasetManifest,
    *,
    round_index: int,
    queries: list[str],
    paths: dict[str, Path],
) -> DiscoveryRoundObservation:
    summary = manifest.summary
    selected_files = int(summary.get("selected_files") or len(manifest.files))
    selected_projects = int(summary.get("selected_projects") or len(manifest.projects))
    recommended_action, warnings = _recommend_next_action(summary, selected_files)
    status = "completed" if selected_files > 0 else "blocked"
    blockers = [] if selected_files > 0 else ["no_selected_files"]
    return DiscoveryRoundObservation(
        status=status,
        round_index=round_index,
        queries=queries,
        manifest_path=str(paths["dataset_manifest_json"]),
        selected_projects=selected_projects,
        selected_files=selected_files,
        candidate_projects_seen=int(summary.get("candidate_projects_seen") or 0),
        validity_status_counts=_int_mapping(summary.get("validity_status_counts")),
        evidence_level_distribution=_int_mapping(summary.get("evidence_level_distribution")),
        instrument_family_distribution=_int_mapping(summary.get("instrument_family_distribution")),
        unknown_counts=_int_mapping(summary.get("unknown_counts")),
        recommended_action=recommended_action,
        warnings=warnings,
        blockers=blockers,
        files={name: str(path) for name, path in paths.items()},
    )


def _recommend_next_action(summary: dict[str, Any], selected_files: int) -> tuple[str, list[str]]:
    if selected_files == 0:
        return "broaden_or_rephrase_queries", ["no_selected_files"]
    warnings: list[str] = []
    unknown = _int_mapping(summary.get("unknown_counts"))
    if int(unknown.get("fragmentation_method") or 0) > selected_files / 2:
        warnings.append("fragmentation_metadata_weak")
    evidence = _int_mapping(summary.get("evidence_level_distribution"))
    if int(evidence.get("project") or 0) > selected_files / 3:
        warnings.append("project_level_evidence_overrepresented")
    instruments = _int_mapping(summary.get("instrument_family_distribution"))
    if selected_files >= 10 and len(instruments) <= 1:
        warnings.append("instrument_diversity_low")
    if warnings:
        return "refine_queries_for_missing_evidence_or_diversity", warnings
    return "accept_current_manifest", []


def _int_mapping(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(key): int(item or 0) for key, item in value.items()}


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value).strip()))
