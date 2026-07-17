from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from agent.control_plane.budget_governor import BudgetGovernor
from agent.control_plane.discovery_metrics import evaluate_round_metrics
from agent.control_plane.models import (
    AgentRunRecord,
    ArtifactReference,
    DiscoveryRoundObservation,
    DynamicBudgetLimits,
    RoundMetrics,
    SearchDiagnosis,
    minimum_high_relevance_inspections,
)
from agent.control_plane.policy import evaluate_tool_policy
from agent.control_plane.store import AgentRunStore
from agent.discovery.diversity import diversity_summary, select_diverse_items, validity_summary
from agent.discovery.manifest import write_dataset_manifest
from agent.discovery.memory import DiscoveryMemory
from agent.discovery.models import DatasetManifest, DatasetRequest, DiscoveredFile, DiscoveredProject
from agent.discovery.project_judgment import (
    ProjectJudgmentInput,
    is_qualified_project_judgment,
    summarize_project_judgments,
)
from agent.discovery.repository_discovery import discover_repository_dataset
from agent.discovery.query_builder import classify_pride_query_strategy
from agent.discovery.search_environment import (
    CandidateInspectionAction,
    CandidateSearchAction,
    CandidateSearchObservation,
    DiscoverySearchEnvironment,
)
from agent.discovery.task_readiness import annotate_manifest_task_readiness
from agent.repositories.metering import meter_repository_requests


DiscoveryFunction = Callable[..., DatasetManifest]


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

    def search_repository_candidates(
        self,
        action: CandidateSearchAction,
        grant_id: str | None = None,
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
        queries = [item.query for item in action.queries]
        if self.dynamic_budget:
            if self.budget_governor is None:
                raise RuntimeError("dynamic_budget_governor_required")
            if not grant_id:
                return self._blocked_candidate_search("search_grant_required")
            try:
                self.budget_governor.consume_grant(grant_id, queries)
            except ValueError as exc:
                return self._blocked_candidate_search(str(exc))

        arguments = {
            "action": action.model_dump(mode="json"),
            "grant_id": grant_id,
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

        run = self.store.increment_tool_call_count(self.run_id)
        if not self.dynamic_budget:
            run = self.store.increment_dynamic_usage(
                self.run_id,
                query_units=len(queries),
                search_batches=1,
            )
        self.store.append_event(
            self.run_id,
            "candidate_search_started",
            {
                "queries": queries,
                "action": action.model_dump(mode="json"),
                "idempotency_key": tool_call.idempotency_key,
            },
        )

        try:
            request_callback = self._repository_request_callback()
            with meter_repository_requests(request_callback):
                observation = self.search_environment.search(action)
            metered_run = self._require_run()
            previous_high = metered_run.latest_high_relevance_candidate_count
            high_gain_count = max(
                0,
                observation.high_relevance_candidate_count - previous_high,
            )
            coverage_gain = max(
                0.0,
                observation.semantic_coverage - metered_run.latest_semantic_coverage,
            )
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
                    "quality_gap": 1.0 - observation.semantic_coverage,
                    "semantic_coverage_gap": 1.0 - observation.semantic_coverage,
                    "hard_constraint_evidence_gap": review_count / max(1, preview_count),
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
                    "latest_semantic_coverage": observation.semantic_coverage,
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
    ) -> DiscoveryRoundObservation:
        if self.search_environment is None:
            return self._blocked_environment_inspection(
                action,
                "candidate_search_environment_unavailable",
            )
        run = self._require_run()
        if run.selected_round_index is not None:
            return self._blocked_environment_inspection(action, "manifest_already_selected")
        policy = evaluate_tool_policy("inspect_repository_candidates", run)
        if policy.outcome != "allow":
            return self._blocked_environment_inspection(action, policy.reason)
        if action.search_id != run.latest_candidate_search_id:
            return self._blocked_environment_inspection(action, "candidate_search_id_mismatch")

        arguments = {
            "action": action.model_dump(mode="json"),
            "request": self.request.model_dump(mode="json"),
            "task_type": self.task_type,
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

    def _persist_environment_inspection(
        self,
        *,
        run: AgentRunRecord,
        round_index: int,
        action: CandidateInspectionAction,
        result_manifest: DatasetManifest,
        usable_files: int,
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
                "hard_constraint_evidence_gap": needs_review / selected_count,
                "duplicate_rate": rich_metrics.duplicate_rate if rich_metrics else 0.0,
                "high_relevance_gain": (
                    rich_metrics.high_relevance_gain if rich_metrics else 0.0
                ),
                "inspection_yield": min(1.0, usable_files / selected_count),
                "no_gain_streak": metered_run.no_gain_action_count,
            }
        )
        inspected_accessions = _normalize_accessions(
            [*metered_run.inspected_candidate_accessions, *action.accessions]
        )
        minimum_inspections = minimum_high_relevance_inspections(
            metered_run.latest_high_relevance_candidate_count,
            self.request.max_projects,
        )
        inspection_budget_remaining = round_index < metered_run.budget.max_discovery_rounds
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
                "inspected_candidate_count": len(inspected_accessions),
                "minimum_high_relevance_inspections": minimum_inspections,
                "selection_ready": selection_ready,
                "recommended_action": recommendation,
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
                self.budget_governor.consume_grant(grant_id, queries)
            except ValueError as exc:
                return self._blocked_observation(queries, str(exc))
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

        required_inspections = minimum_high_relevance_inspections(
            run.latest_high_relevance_candidate_count,
            self.request.max_projects,
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
            return self._selection_rejected(
                round_index,
                "high_relevance_candidates_require_more_inspection",
            )

        manifest_path = self._manifest_path_for_selection(run, round_index)
        if manifest_path is None or not manifest_path.exists():
            return self._selection_rejected(round_index, "manifest_round_not_found")

        manifest = _load_manifest(manifest_path)
        judgment_gate_enabled = bool(run.project_judgments) or self.search_environment is not None
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

            target_reached = len(eligible_accessions) >= self.request.max_projects
            can_continue = (
                not run.search_stopped
                and run.discovery_round_count < run.budget.max_discovery_rounds
                and run.qualified_no_gain_count < 2
            )
            if self.search_environment is not None and not target_reached and can_continue:
                return self._selection_rejected(
                    round_index,
                    "qualified_project_target_requires_more_search",
                )
        candidate_count = len(manifest.projects)
        if (
            not selected_accessions
            and round_index == 0
            and candidate_count > self.request.max_projects
        ):
            return self._selection_rejected(
                round_index,
                "explicit_project_selection_required_for_candidate_pool",
            )
        if len(selected_accessions) > self.request.max_projects:
            return self._selection_rejected(
                round_index,
                "selection_exceeds_max_projects",
            )
        if selected_accessions:
            available = {project.project_accession.upper() for project in manifest.projects}
            missing = [accession for accession in selected_accessions if accession not in available]
            if missing:
                return self._selection_rejected(
                    round_index,
                    "selection_project_not_in_manifest:" + ",".join(missing),
                )

        arguments = {
            "round_index": round_index,
            "project_accessions": selected_accessions,
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
        if selected_accessions:
            selected_set = set(selected_accessions)
            filtered = manifest.model_copy(
                update={
                    "projects": [
                        project
                        for project in manifest.projects
                        if project.project_accession.upper() in selected_set
                    ],
                    "files": [
                        file
                        for file in manifest.files
                        if file.project_accession.upper() in selected_set
                    ],
                }
            )
            manifest = _merge_discovery_manifests(
                [filtered],
                request=self.request,
                run_id=self.run_id,
            )
        else:
            manifest = _merge_discovery_manifests(
                [manifest],
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
        eligible_accessions = {
            accession
            for accession, judgment in run.project_judgments.items()
            if is_qualified_project_judgment(judgment)
        }

        def eligible_manifest(manifest: DatasetManifest) -> DatasetManifest | None:
            if not run.project_judgments:
                return manifest
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
            if not projects or not files:
                return None
            filtered = _merge_discovery_manifests(
                [manifest.model_copy(update={"projects": projects, "files": files})],
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
                            for accession in sorted(eligible_accessions)
                            if accession in run.project_judgments
                        },
                    }
                }
            )

        candidates: list[tuple[tuple[int, int, int, float], int, Path, DatasetManifest]] = []
        if run.candidate_pool_manifest_path:
            path = Path(run.candidate_pool_manifest_path)
            if path.exists():
                manifest = eligible_manifest(_load_manifest(path))
                if manifest is not None:
                    candidates.append((_manifest_rank(manifest), 0, path, manifest))
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
        if run.project_judgments:
            paths = write_dataset_manifest(manifest, self.output_dir / "final_selection")
            path = paths["dataset_manifest_json"]
        rationale = "Deterministic fallback selected the highest-ranked persisted candidate manifest."
        _, warnings = _recommend_next_action(manifest.summary, _selected_file_count(manifest))
        run = self.store.save_run(
            run.model_copy(
                update={
                    "current_manifest_path": str(path),
                    "selected_round_index": round_index,
                    "selection_rationale": rationale,
                    "warnings": warnings,
                    "blockers": [],
                }
            )
        )
        self.store.append_event(
            self.run_id,
            "manifest_auto_selected",
            {
                "round_index": round_index,
                "manifest_path": str(path),
                "selected_files": _selected_file_count(manifest),
                "rationale": rationale,
            },
        )
        return run

    def state_summary(self) -> dict[str, Any]:
        return self._state_summary(self._require_run())

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

    @staticmethod
    def _state_summary(run: AgentRunRecord) -> dict[str, Any]:
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
            "minimum_high_relevance_inspections": minimum_high_relevance_inspections(
                run.latest_high_relevance_candidate_count,
                int((run.request or {}).get("max_projects") or 1),
            ),
            "no_gain_action_count": run.no_gain_action_count,
            "latest_candidate_search_id": run.latest_candidate_search_id,
            "latest_high_relevance_candidate_count": run.latest_high_relevance_candidate_count,
            "latest_semantic_coverage": run.latest_semantic_coverage,
            "model_usage": {
                "requests": run.model_requests,
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
            "dynamic_limits": run.dynamic_limits.model_dump(mode="json"),
            "dynamic_usage": run.dynamic_usage.model_dump(mode="json"),
            "active_grant_id": run.active_grant_id,
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
        manifest = DatasetManifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8")))
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
    ready = sum(1 for file in manifest.files if file.task_readiness_status in {"ready", "weak_ready"})
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
        assessments.append(
            {
                "project_accession": project.project_accession,
                "project_title": project.project_title or "",
                "selected_file_count": len(project_files),
                "species": project.species,
                "acquisition_mode": project.acquisition_mode,
                "labeling_strategy": project.labeling_strategy,
                "validity_status": project.validity_status,
                "validity_status_counts": dict(
                    Counter(file.validity_status for file in project_files)
                ),
                "evidence_level_counts": dict(
                    Counter(str(file.evidence_level or "unknown") for file in project_files)
                ),
                "task_readiness_status_counts": dict(
                    Counter(str(file.task_readiness_status or "not_set") for file in project_files)
                ),
                "matched_intent_terms": list(preview.get("matched_intent_terms") or []),
                "query_hits": list(preview.get("query_hits") or []),
                "needs_review": project.needs_review,
            }
        )
    return assessments


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
