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
)
from agent.control_plane.policy import evaluate_tool_policy
from agent.control_plane.store import AgentRunStore
from agent.discovery.diversity import diversity_summary, select_diverse_items, validity_summary
from agent.discovery.manifest import write_dataset_manifest
from agent.discovery.memory import DiscoveryMemory
from agent.discovery.models import DatasetManifest, DatasetRequest, DiscoveredFile, DiscoveredProject
from agent.discovery.repository_discovery import discover_repository_dataset
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
        self.output_dir.mkdir(parents=True, exist_ok=True)

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
            observation = observation.model_copy(
                update={
                    "candidate_pool_manifest_path": str(pool_paths["dataset_manifest_json"]),
                    "pooled_selected_projects": pool_observation.selected_projects,
                    "pooled_selected_files": pool_observation.selected_files,
                    "metrics": metrics,
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
                }
            )
            self.store.save_run(run)
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
            observation = DiscoveryRoundObservation(
                status="failed",
                round_index=round_index,
                queries=queries,
                recommended_action="revise_queries_or_stop",
                warnings=["repository_discovery_tool_failed"],
                blockers=[str(exc)],
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
                "tool_failed",
                {
                    "tool": "search_repository_datasets",
                    "round_index": round_index,
                    "error": str(exc),
                },
            )
            return observation

    def select_discovery_manifest(self, round_index: int, rationale: str) -> dict[str, Any]:
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

        manifest_path = self._manifest_path_for_selection(run, round_index)
        if manifest_path is None or not manifest_path.exists():
            return self._selection_rejected(round_index, "manifest_round_not_found")

        arguments = {"round_index": round_index, "rationale": rationale}
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
        manifest = _load_manifest(manifest_path)
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
            "rationale": rationale,
        }
        self.store.complete_tool_call(tool_call.idempotency_key, payload)
        self.store.append_event(self.run_id, "manifest_selected", payload)
        return payload

    def auto_select_best_manifest(self) -> AgentRunRecord:
        run = self._require_run()
        if run.selected_round_index is not None:
            return run
        candidates: list[tuple[tuple[int, int, int, float], int, Path, DatasetManifest]] = []
        if run.candidate_pool_manifest_path:
            path = Path(run.candidate_pool_manifest_path)
            if path.exists():
                manifest = _load_manifest(path)
                candidates.append((_manifest_rank(manifest), 0, path, manifest))
        for name, reference in run.artifacts.items():
            if not name.startswith("discovery_round_"):
                continue
            path = Path(reference.path)
            if not path.exists():
                continue
            manifest = _load_manifest(path)
            candidates.append((_manifest_rank(manifest), int(name.rsplit("_", 1)[-1]), path, manifest))
        candidates = [item for item in candidates if _selected_file_count(item[3]) > 0]
        if not candidates:
            return run
        _, round_index, path, manifest = max(candidates, key=lambda item: item[0])
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
        return {
            "run_id": run.run_id,
            "status": run.status,
            "tool_call_count": run.tool_call_count,
            "discovery_round_count": run.discovery_round_count,
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
        }

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
    selected_items = select_diverse_items(items, request)
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
