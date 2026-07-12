from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import Field

from agent.discovery.models import DatasetRequest
from agent.models import JsonModel


RuntimeName = Literal["workflow", "openai_agents"]
PairOutcome = Literal["agent_win", "tie", "workflow_win", "ineligible"]


class DiscoveryBenchmarkScenario(JsonModel):
    id: str
    prompt: str
    task_type: str | None = None
    request: DatasetRequest
    expected_project_accessions: list[str] = Field(default_factory=list)
    notes: str = ""


class DiscoveryRuntimeResult(JsonModel):
    scenario_id: str
    runtime: RuntimeName
    status: str
    eligible_for_comparison: bool = True
    ineligible_reason: str | None = None
    elapsed_seconds: float = Field(ge=0.0)
    project_count: int = Field(ge=0)
    file_count: int = Field(ge=0)
    valid_files: int = Field(ge=0)
    usable_files: int = Field(ge=0)
    task_ready_files: int = Field(ge=0)
    expected_accession_hits: list[str] = Field(default_factory=list)
    expected_accession_recall: float = Field(ge=0.0, le=1.0)
    hard_constraint_violations: int = Field(ge=0)
    repository_requests: int = Field(ge=0)
    query_units: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    rounds: int = Field(ge=0)
    recovery_attempts: int = Field(ge=0)
    quality_score: float = Field(ge=0.0, le=1.0)
    quality_components: dict[str, float] = Field(default_factory=dict)
    output_dir: str | None = None


class DiscoveryPairComparison(JsonModel):
    scenario_id: str
    workflow: DiscoveryRuntimeResult
    agent: DiscoveryRuntimeResult
    eligible: bool
    outcome: PairOutcome
    quality_delta: float
    repository_request_ratio: float
    false_early_stop: bool
    added_hard_constraint_violations: int = Field(ge=0)
    reasons: list[str] = Field(default_factory=list)


class DiscoveryRuntimeBenchmarkReport(JsonModel):
    pairs: list[DiscoveryPairComparison]
    total_pairs: int = Field(ge=0)
    eligible_pairs: int = Field(ge=0)
    agent_wins: int = Field(ge=0)
    ties: int = Field(ge=0)
    workflow_wins: int = Field(ge=0)
    average_quality_delta: float
    aggregate_repository_request_ratio: float
    aggregate_elapsed_time_ratio: float
    false_early_stops: int = Field(ge=0)
    added_hard_constraint_violations: int = Field(ge=0)
    inconclusive: bool
    agent_real_improvement: bool
    gate_reasons: list[str] = Field(default_factory=list)


def result_from_record(
    *,
    scenario: DiscoveryBenchmarkScenario,
    runtime: RuntimeName,
    record: dict[str, Any],
    elapsed_seconds: float,
    repository_requests: int | None = None,
) -> DiscoveryRuntimeResult:
    files = [item for item in record.get("files", []) if isinstance(item, dict)]
    projects = [item for item in record.get("projects", []) if isinstance(item, dict)]
    summary = record.get("summary") if isinstance(record.get("summary"), dict) else {}
    agent = record.get("agent") if isinstance(record.get("agent"), dict) else {}
    accessions = {
        str(item.get("project_accession") or "").strip().upper()
        for item in [*projects, *files]
        if str(item.get("project_accession") or "").strip()
    }
    expected = {item.strip().upper() for item in scenario.expected_project_accessions if item.strip()}
    hits = sorted(expected & accessions)
    recall = len(hits) / max(1, len(expected))
    valid = sum(item.get("validity_status") == "valid" for item in files)
    usable = sum(item.get("validity_status") in {"valid", "weak_keep"} for item in files)
    task_ready = sum(item.get("task_readiness_status") == "ready" for item in files)
    violations = _count_hard_constraint_violations(scenario.request, files)
    denominator = max(1, len(files))
    components = {
        "expected_accession_recall": recall,
        "constraint_compliance": 1.0 if violations == 0 else 0.0,
        "valid_precision": valid / denominator,
        "usable_precision": usable / denominator,
        "task_ready_precision": task_ready / denominator,
    }
    quality_score = (
        0.35 * components["expected_accession_recall"]
        + 0.25 * components["constraint_compliance"]
        + 0.15 * components["valid_precision"]
        + 0.15 * components["usable_precision"]
        + 0.10 * components["task_ready_precision"]
    )
    eligible = True
    ineligible_reason = None
    if runtime == "workflow":
        agentic = summary.get("agentic") if isinstance(summary.get("agentic"), dict) else {}
        if agentic.get("requested") is True and agentic.get("enabled") is False:
            eligible = False
            fallback = agentic.get("fallback") if isinstance(agentic.get("fallback"), dict) else {}
            ineligible_reason = str(fallback.get("reason") or "workflow_llm_fallback")
    workflow_rounds = 0
    if runtime == "workflow" and isinstance(summary.get("agentic"), dict):
        workflow_rounds = int(summary["agentic"].get("rounds") or 0)
    return DiscoveryRuntimeResult(
        scenario_id=scenario.id,
        runtime=runtime,
        status=str(record.get("status") or "unknown"),
        eligible_for_comparison=eligible,
        ineligible_reason=ineligible_reason,
        elapsed_seconds=max(0.0, elapsed_seconds),
        project_count=len(projects),
        file_count=len(files),
        valid_files=valid,
        usable_files=usable,
        task_ready_files=task_ready,
        expected_accession_hits=hits,
        expected_accession_recall=recall,
        hard_constraint_violations=violations,
        repository_requests=max(
            0,
            int(repository_requests if repository_requests is not None else agent.get("repository_requests") or 0),
        ),
        query_units=max(0, int(agent.get("query_units") or 0)),
        tool_calls=max(0, int(agent.get("tool_calls") or 0)),
        rounds=max(0, int(agent.get("discovery_rounds") or workflow_rounds)),
        recovery_attempts=max(0, int(agent.get("recovery_attempts") or 0)),
        quality_score=quality_score,
        quality_components=components,
        output_dir=str(record.get("output_dir") or "") or None,
    )


def compare_runtime_pairs(
    *,
    workflow: Sequence[DiscoveryRuntimeResult],
    agent: Sequence[DiscoveryRuntimeResult],
) -> DiscoveryRuntimeBenchmarkReport:
    workflow_by_id = _index_results(workflow, "workflow")
    agent_by_id = _index_results(agent, "openai_agents")
    if workflow_by_id.keys() != agent_by_id.keys():
        raise ValueError("workflow and agent results must contain the same scenario ids")
    pairs = [
        _compare_pair(workflow_by_id[scenario_id], agent_by_id[scenario_id])
        for scenario_id in workflow_by_id
    ]
    eligible = [pair for pair in pairs if pair.eligible]
    agent_wins = sum(pair.outcome == "agent_win" for pair in eligible)
    ties = sum(pair.outcome == "tie" for pair in eligible)
    workflow_wins = sum(pair.outcome == "workflow_win" for pair in eligible)
    average_delta = sum(pair.quality_delta for pair in eligible) / max(1, len(eligible))
    workflow_requests = sum(pair.workflow.repository_requests for pair in eligible)
    agent_requests = sum(pair.agent.repository_requests for pair in eligible)
    request_ratio = agent_requests / max(1, workflow_requests)
    workflow_elapsed = sum(pair.workflow.elapsed_seconds for pair in eligible)
    agent_elapsed = sum(pair.agent.elapsed_seconds for pair in eligible)
    elapsed_ratio = agent_elapsed / max(0.001, workflow_elapsed)
    false_stops = sum(pair.false_early_stop for pair in eligible)
    added_violations = sum(pair.added_hard_constraint_violations for pair in eligible)
    inconclusive = len(eligible) < 3 or len(eligible) != len(pairs)
    gate_reasons: list[str] = []
    if inconclusive:
        gate_reasons.append("at least three clean paired runs are required")
    if average_delta < 0.03:
        gate_reasons.append("average quality delta is below 0.03")
    if agent_wins <= workflow_wins:
        gate_reasons.append("agent wins do not exceed workflow wins")
    if false_stops:
        gate_reasons.append("agent introduced false early stops")
    if added_violations:
        gate_reasons.append("agent introduced hard-constraint violations")
    if request_ratio > 2.0:
        gate_reasons.append("agent repository request ratio exceeds 2.0")
    return DiscoveryRuntimeBenchmarkReport(
        pairs=pairs,
        total_pairs=len(pairs),
        eligible_pairs=len(eligible),
        agent_wins=agent_wins,
        ties=ties,
        workflow_wins=workflow_wins,
        average_quality_delta=average_delta,
        aggregate_repository_request_ratio=request_ratio,
        aggregate_elapsed_time_ratio=elapsed_ratio,
        false_early_stops=false_stops,
        added_hard_constraint_violations=added_violations,
        inconclusive=inconclusive,
        agent_real_improvement=not gate_reasons,
        gate_reasons=gate_reasons,
    )


def _compare_pair(
    workflow: DiscoveryRuntimeResult,
    agent: DiscoveryRuntimeResult,
) -> DiscoveryPairComparison:
    eligible = workflow.eligible_for_comparison and agent.eligible_for_comparison
    delta = agent.quality_score - workflow.quality_score
    false_stop = workflow.usable_files > 0 and agent.usable_files == 0
    added_violations = max(0, agent.hard_constraint_violations - workflow.hard_constraint_violations)
    reasons: list[str] = []
    if not eligible:
        reasons.append(workflow.ineligible_reason or agent.ineligible_reason or "runtime result is ineligible")
        outcome: PairOutcome = "ineligible"
    elif delta >= 0.03:
        outcome = "agent_win"
    elif delta <= -0.03:
        outcome = "workflow_win"
    else:
        outcome = "tie"
    if false_stop:
        reasons.append("workflow found usable files but agent stopped with none")
    if added_violations:
        reasons.append("agent added hard-constraint violations")
    return DiscoveryPairComparison(
        scenario_id=workflow.scenario_id,
        workflow=workflow,
        agent=agent,
        eligible=eligible,
        outcome=outcome,
        quality_delta=delta,
        repository_request_ratio=agent.repository_requests / max(1, workflow.repository_requests),
        false_early_stop=false_stop,
        added_hard_constraint_violations=added_violations,
        reasons=reasons,
    )


def _index_results(
    results: Sequence[DiscoveryRuntimeResult], expected_runtime: RuntimeName
) -> dict[str, DiscoveryRuntimeResult]:
    indexed: dict[str, DiscoveryRuntimeResult] = {}
    for result in results:
        if result.runtime != expected_runtime:
            raise ValueError(f"expected {expected_runtime} result, got {result.runtime}")
        if result.scenario_id in indexed:
            raise ValueError(f"duplicate scenario id: {result.scenario_id}")
        indexed[result.scenario_id] = result
    if not indexed:
        raise ValueError("at least one result is required")
    return indexed


def _count_hard_constraint_violations(request: DatasetRequest, files: Sequence[dict[str, Any]]) -> int:
    violations = 0
    requested_species = {item.casefold() for item in [*request.species, *request.canonical_species]}
    for item in files:
        if item.get("validity_status") == "exclude":
            violations += 1
        acquisition = str(item.get("acquisition_mode") or "").strip().casefold()
        if acquisition and acquisition not in {"unknown", "not_specified"} and acquisition != request.acquisition_mode.casefold():
            violations += 1
        if request.species_policy == "include_only" and requested_species:
            observed_species = {
                str(value).strip().casefold()
                for value in [*(item.get("species") or []), *(item.get("canonical_species") or [])]
                if str(value).strip()
            }
            if observed_species and requested_species.isdisjoint(observed_species):
                violations += 1
        labeling = str(item.get("labeling_strategy") or "").strip().casefold()
        if labeling and labeling not in {"unknown", "not_specified"} and labeling != request.labeling_strategy.casefold():
            violations += 1
        if request.goal == "ptm":
            observed_ptm = str(item.get("ptm_type") or "").strip().casefold()
            requested_ptms = {item.casefold() for item in (request.ptm_types or [request.ptm_type])}
            if observed_ptm and observed_ptm not in {"unknown", "unknown_ptm"} and observed_ptm not in requested_ptms:
                violations += 1
    return violations
