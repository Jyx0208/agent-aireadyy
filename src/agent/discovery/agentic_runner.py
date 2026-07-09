from __future__ import annotations

from typing import Callable

from pydantic import Field

from agent.discovery.agentic import (
    AgenticDiscoveryPlan,
    AgenticDiscoveryPlanner,
    AgenticTraceStep,
    build_agentic_self_check,
)
from agent.discovery.memory import DiscoveryMemory
from agent.discovery.models import DatasetManifest, DatasetRequest
from agent.discovery.pride_discovery import discover_pride_dataset
from agent.discovery.task_profiles import TaskProfile
from agent.models import JsonModel


DiscoveryFunction = Callable[..., DatasetManifest]


class AgenticDiscoveryRound(JsonModel):
    round_index: int
    queries: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    suggested_next_queries: list[str] = Field(default_factory=list)
    trace: list[AgenticTraceStep] = Field(default_factory=list)
    summary: dict[str, object] = Field(default_factory=dict)


class AgenticDiscoveryRunResult(JsonModel):
    plan: AgenticDiscoveryPlan
    rounds: list[AgenticDiscoveryRound] = Field(default_factory=list)
    manifest: DatasetManifest


def run_agentic_discovery(
    *,
    request: DatasetRequest,
    planner: AgenticDiscoveryPlanner,
    prompt: str,
    memory: DiscoveryMemory | None = None,
    max_rounds: int = 1,
    task_profile: TaskProfile | None = None,
    discovery_func: DiscoveryFunction = discover_pride_dataset,
) -> AgenticDiscoveryRunResult:
    bounded_rounds = max(1, min(int(max_rounds), 2))
    initial_plan = planner.plan(prompt=prompt, request=request, task_profile=task_profile)
    manifest = discovery_func(request, memory=memory, queries=initial_plan.queries)
    checked_plan = build_agentic_self_check(initial_plan, manifest)
    rounds = [
        _round_record(
            round_index=1,
            queries=initial_plan.queries,
            checked_plan=checked_plan,
            manifest=manifest,
        )
    ]

    if bounded_rounds >= 2 and _should_run_second_round(checked_plan):
        combined_queries = _dedupe([*initial_plan.queries, *checked_plan.suggested_next_queries])
        round2_base = checked_plan.model_copy(
            update={
                "queries": combined_queries,
                "suggested_next_queries": [],
            }
        )
        manifest = discovery_func(request, memory=memory, queries=combined_queries)
        checked_plan = build_agentic_self_check(round2_base, manifest)
        rounds.append(
            _round_record(
                round_index=2,
                queries=combined_queries,
                checked_plan=checked_plan,
                manifest=manifest,
            )
        )

    return AgenticDiscoveryRunResult(plan=checked_plan, rounds=rounds, manifest=manifest)


def _should_run_second_round(plan: AgenticDiscoveryPlan) -> bool:
    if not plan.suggested_next_queries:
        return False
    triggers = {
        "no_selected_files",
        "fragmentation_diversity_or_metadata_weak",
        "project_level_evidence_overrepresented",
        "instrument_diversity_low",
    }
    return bool(triggers.intersection(plan.warnings))


def _round_record(
    *,
    round_index: int,
    queries: list[str],
    checked_plan: AgenticDiscoveryPlan,
    manifest: DatasetManifest,
) -> AgenticDiscoveryRound:
    return AgenticDiscoveryRound(
        round_index=round_index,
        queries=queries,
        warnings=checked_plan.warnings,
        suggested_next_queries=checked_plan.suggested_next_queries,
        trace=checked_plan.trace,
        summary=_compact_summary(manifest),
    )


def _compact_summary(manifest: DatasetManifest) -> dict[str, object]:
    keys = [
        "selected_projects",
        "selected_files",
        "candidate_projects_seen",
        "eligible_projects_seen",
        "excluded_projects",
        "excluded_files",
        "validity_status_counts",
        "evidence_level_distribution",
        "sdrf_match_status_distribution",
        "instrument_family_distribution",
        "fragmentation_method_distribution",
        "lc_gradient_distribution",
        "unknown_counts",
    ]
    return {key: manifest.summary[key] for key in keys if key in manifest.summary}


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result
