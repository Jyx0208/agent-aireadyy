from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import Field, TypeAdapter, model_validator

from agent.discovery.models import DatasetRequest
from agent.discovery.ontology import normalize_species
from agent.models import JsonModel


AmbiguityLevel = Literal["structured", "clear", "vague", "ambiguous"]
VariantMode = Literal["parsed_spec", "raw_prompt"]
ReplacementRuntime = Literal["workflow", "openai_agents"]
BudgetTier = Literal["baseline", "1x", "2x", "max_quality"]


class PromptVariant(JsonModel):
    id: str = Field(min_length=1)
    ambiguity_level: AmbiguityLevel
    mode: VariantMode
    prompt: str = Field(min_length=1)
    simulated_clarification: str = ""
    hard_constraint_fields: list[str] = Field(default_factory=list)


class ReplacementBenchmarkScenario(JsonModel):
    id: str = Field(min_length=1)
    hidden_request: DatasetRequest
    prompt_variants: list[PromptVariant] = Field(min_length=1)
    relevance_judgments: dict[str, int] = Field(min_length=1)
    task_type: str | None = None
    notes: str = ""

    @model_validator(mode="after")
    def validate_scenario(self) -> "ReplacementBenchmarkScenario":
        variant_ids = [variant.id for variant in self.prompt_variants]
        if len(variant_ids) != len(set(variant_ids)):
            raise ValueError("prompt variant ids must be unique within a scenario")
        invalid = {
            accession: grade
            for accession, grade in self.relevance_judgments.items()
            if not accession.strip() or isinstance(grade, bool) or grade not in {0, 1, 2, 3}
        }
        if invalid:
            raise ValueError("relevance judgments must use non-empty accessions and grades 0-3")
        return self


class ReplacementRun(JsonModel):
    scenario_id: str
    variant_id: str
    repeat: int = Field(default=0, ge=0)
    runtime: ReplacementRuntime
    budget_tier: BudgetTier
    status: str
    eligible_for_comparison: bool = True
    ineligible_reason: str | None = None
    selected_project_accessions: list[str] = Field(default_factory=list)
    hard_constraint_violations: int = Field(default=0, ge=0)
    task_ready_precision: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_completeness: float = Field(default=0.0, ge=0.0, le=1.0)
    repository_requests: int = Field(default=0, ge=0)
    query_units: int = Field(default=0, ge=0)
    model_input_tokens: int = Field(default=0, ge=0)
    model_output_tokens: int = Field(default=0, ge=0)
    elapsed_seconds: float = Field(default=0.0, ge=0.0)


class ScoredReplacementRun(ReplacementRun):
    ndcg_at_5: float = Field(ge=0.0, le=1.0)
    high_relevance_recall: float = Field(ge=0.0, le=1.0)
    quality_score: float = Field(ge=0.0, le=1.0)
    relevance_grades: list[int] = Field(default_factory=list)


class ReplacementGate(JsonModel):
    min_pairs: int = Field(default=12, ge=1)
    min_average_quality_delta: float = 0.05
    min_win_rate: float = Field(default=0.60, ge=0.0, le=1.0)
    max_loss_rate: float = Field(default=0.10, ge=0.0, le=1.0)
    min_vague_quality_delta: float = 0.08
    max_repository_request_ratio: float = Field(default=4.0, ge=1.0)


class ReplacementPair(JsonModel):
    scenario_id: str
    variant_id: str
    repeat: int
    ambiguity_level: AmbiguityLevel
    eligible: bool = True
    ineligible_reason: str | None = None
    workflow: ScoredReplacementRun
    agent: ScoredReplacementRun
    quality_delta: float
    outcome: Literal["agent_win", "tie", "workflow_win"]
    added_hard_constraint_violations: int = Field(ge=0)


class ReplacementTierReport(JsonModel):
    budget_tier: BudgetTier
    pairs: list[ReplacementPair]
    total_pairs: int = Field(ge=0)
    pair_count: int = Field(ge=0)
    agent_wins: int = Field(ge=0)
    ties: int = Field(ge=0)
    workflow_wins: int = Field(ge=0)
    win_rate: float = Field(ge=0.0, le=1.0)
    loss_rate: float = Field(ge=0.0, le=1.0)
    average_quality_delta: float
    vague_quality_delta: float
    repository_request_ratio: float
    elapsed_time_ratio: float
    added_hard_constraint_violations: int = Field(ge=0)
    replacement_ready: bool
    gate_reasons: list[str] = Field(default_factory=list)


class ReplacementBenchmarkReport(JsonModel):
    tiers: list[ReplacementTierReport]
    replacement_ready: bool
    winning_budget_tier: BudgetTier | None = None


def load_replacement_scenarios(path: str | Path) -> list[ReplacementBenchmarkScenario]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    scenarios = TypeAdapter(list[ReplacementBenchmarkScenario]).validate_python(payload)
    if not scenarios:
        raise ValueError("replacement benchmark scenarios cannot be empty")
    ids = [scenario.id for scenario in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("replacement benchmark scenario ids must be unique")
    return scenarios


def build_variant_runtime_input(
    scenario: ReplacementBenchmarkScenario,
    variant: PromptVariant,
) -> dict[str, object]:
    if variant not in scenario.prompt_variants:
        raise ValueError("prompt variant does not belong to scenario")
    payload: dict[str, object] = {"prompt": variant.prompt}
    if variant.mode == "parsed_spec":
        payload["request"] = scenario.hidden_request.model_dump(mode="json")
    return payload


def score_replacement_run(
    scenario: ReplacementBenchmarkScenario,
    run: ReplacementRun,
) -> ScoredReplacementRun:
    if run.scenario_id != scenario.id:
        raise ValueError("replacement run scenario does not match")
    if run.variant_id not in {variant.id for variant in scenario.prompt_variants}:
        raise ValueError("replacement run variant does not match scenario")
    judgments = {key.upper(): grade for key, grade in scenario.relevance_judgments.items()}
    accessions = [accession.strip().upper() for accession in run.selected_project_accessions if accession.strip()]
    grades = [judgments.get(accession, 0) for accession in accessions[:5]]
    relevant = {accession for accession, grade in judgments.items() if grade >= 2}
    found = {accession for accession in accessions if judgments.get(accession, 0) >= 2}
    recall = len(found) / max(1, len(relevant))
    ndcg = _ndcg_at_5(grades, list(judgments.values()))
    quality = (
        0.55 * ndcg
        + 0.25 * recall
        + 0.10 * run.task_ready_precision
        + 0.10 * run.evidence_completeness
    )
    return ScoredReplacementRun(
        **run.model_dump(mode="json"),
        ndcg_at_5=ndcg,
        high_relevance_recall=recall,
        quality_score=quality,
        relevance_grades=grades,
    )


def replacement_run_from_record(
    *,
    scenario: ReplacementBenchmarkScenario,
    variant: PromptVariant,
    runtime: ReplacementRuntime,
    budget_tier: BudgetTier,
    record: dict[str, object],
    elapsed_seconds: float,
    repeat: int = 0,
    repository_requests: int | None = None,
) -> ReplacementRun:
    files = [item for item in (record.get("files") or []) if isinstance(item, dict)]
    projects = [item for item in (record.get("projects") or []) if isinstance(item, dict)]
    summary = record.get("summary") if isinstance(record.get("summary"), dict) else {}
    agent = record.get("agent") if isinstance(record.get("agent"), dict) else {}
    accessions: list[str] = []
    for item in [*projects, *files]:
        accession = str(item.get("project_accession") or "").strip().upper()
        if accession and accession not in accessions:
            accessions.append(accession)
    task_ready_scores = [
        _task_readiness_value(item)
        for item in files
    ]
    evidence_values = [
        max(0.0, min(1.0, float(item.get("evidence_completeness") or 0.0)))
        for item in files
    ]
    eligible = True
    ineligible_reason = None
    if runtime == "workflow":
        agentic = summary.get("agentic") if isinstance(summary.get("agentic"), dict) else {}
        if agentic.get("requested") is True and agentic.get("enabled") is False:
            eligible = False
            fallback = agentic.get("fallback") if isinstance(agentic.get("fallback"), dict) else {}
            ineligible_reason = str(fallback.get("reason") or "workflow_llm_fallback")
    model_usage = agent.get("model_usage") if isinstance(agent.get("model_usage"), dict) else {}
    return ReplacementRun(
        scenario_id=scenario.id,
        variant_id=variant.id,
        repeat=repeat,
        runtime=runtime,
        budget_tier=budget_tier,
        status=str(record.get("status") or "unknown"),
        eligible_for_comparison=eligible,
        ineligible_reason=ineligible_reason,
        selected_project_accessions=accessions,
        hard_constraint_violations=_count_hard_constraint_violations(
            scenario.hidden_request,
            files,
            hard_constraint_fields=(
                variant.hard_constraint_fields
                or scenario.hidden_request.hard_constraint_fields
                if variant.mode == "parsed_spec"
                else variant.hard_constraint_fields
            ),
        ),
        task_ready_precision=sum(task_ready_scores) / max(1, len(task_ready_scores)),
        evidence_completeness=sum(evidence_values) / max(1, len(evidence_values)),
        repository_requests=max(
            0,
            int(
                repository_requests
                if repository_requests is not None
                else agent.get("repository_requests") or 0
            ),
        ),
        query_units=max(0, int(agent.get("query_units") or 0)),
        model_input_tokens=max(0, int(model_usage.get("input_tokens") or 0)),
        model_output_tokens=max(0, int(model_usage.get("output_tokens") or 0)),
        elapsed_seconds=max(0.0, elapsed_seconds),
    )


def _task_readiness_value(item: dict[str, object]) -> float:
    score = item.get("task_ai_readiness_score")
    if score is not None:
        return max(0.0, min(1.0, float(score)))
    status = str(item.get("task_readiness_status") or "")
    return 1.0 if status == "ready" else 0.6 if status == "weak_ready" else 0.0


def evaluate_replacement(
    *,
    scenarios: Sequence[ReplacementBenchmarkScenario],
    workflow: Sequence[ReplacementRun],
    agent: Sequence[ReplacementRun],
    gate: ReplacementGate | None = None,
) -> ReplacementBenchmarkReport:
    gate = gate or ReplacementGate()
    scenarios_by_id = {scenario.id: scenario for scenario in scenarios}
    if not scenarios_by_id or len(scenarios_by_id) != len(scenarios):
        raise ValueError("replacement scenarios must be non-empty and unique")
    workflow_index = _index_runs(workflow, expected_runtime="workflow", include_tier=False)
    agent_by_tier: dict[BudgetTier, list[ReplacementRun]] = defaultdict(list)
    for run in agent:
        if run.runtime != "openai_agents":
            raise ValueError("agent replacement runs must use openai_agents runtime")
        if run.budget_tier == "baseline":
            raise ValueError("agent replacement run requires a non-baseline budget tier")
        agent_by_tier[run.budget_tier].append(run)
    if not agent_by_tier:
        raise ValueError("at least one agent replacement run is required")

    tier_reports: list[ReplacementTierReport] = []
    for tier in ("1x", "2x", "max_quality"):
        tier_runs = agent_by_tier.get(tier, [])
        if not tier_runs:
            continue
        agent_index = _index_runs(tier_runs, expected_runtime="openai_agents", include_tier=False)
        if workflow_index.keys() != agent_index.keys():
            raise ValueError(f"workflow and agent runs must be paired for budget tier {tier}")
        pairs = [
            _score_pair(
                scenarios_by_id,
                workflow_index[key],
                agent_index[key],
            )
            for key in workflow_index
        ]
        tier_reports.append(_evaluate_tier(tier, pairs, gate))

    winning = next((report.budget_tier for report in tier_reports if report.replacement_ready), None)
    return ReplacementBenchmarkReport(
        tiers=tier_reports,
        replacement_ready=winning is not None,
        winning_budget_tier=winning,
    )


def _score_pair(
    scenarios: dict[str, ReplacementBenchmarkScenario],
    workflow: ReplacementRun,
    agent: ReplacementRun,
) -> ReplacementPair:
    scenario = scenarios.get(workflow.scenario_id)
    if scenario is None:
        raise ValueError(f"unknown replacement scenario: {workflow.scenario_id}")
    variant = next(item for item in scenario.prompt_variants if item.id == workflow.variant_id)
    scored_workflow = score_replacement_run(scenario, workflow)
    scored_agent = score_replacement_run(scenario, agent)
    delta = scored_agent.quality_score - scored_workflow.quality_score
    outcome = "agent_win" if delta >= 0.03 else "workflow_win" if delta <= -0.03 else "tie"
    return ReplacementPair(
        scenario_id=workflow.scenario_id,
        variant_id=workflow.variant_id,
        repeat=workflow.repeat,
        ambiguity_level=variant.ambiguity_level,
        eligible=workflow.eligible_for_comparison and agent.eligible_for_comparison,
        ineligible_reason=(
            workflow.ineligible_reason
            or agent.ineligible_reason
            if not (workflow.eligible_for_comparison and agent.eligible_for_comparison)
            else None
        ),
        workflow=scored_workflow,
        agent=scored_agent,
        quality_delta=delta,
        outcome=outcome,
        added_hard_constraint_violations=max(
            0,
            agent.hard_constraint_violations - workflow.hard_constraint_violations,
        ),
    )


def _evaluate_tier(
    tier: BudgetTier,
    pairs: list[ReplacementPair],
    gate: ReplacementGate,
) -> ReplacementTierReport:
    eligible_pairs = [pair for pair in pairs if pair.eligible]
    count = len(eligible_pairs)
    wins = sum(pair.outcome == "agent_win" for pair in eligible_pairs)
    ties = sum(pair.outcome == "tie" for pair in eligible_pairs)
    losses = sum(pair.outcome == "workflow_win" for pair in eligible_pairs)
    average_delta = sum(pair.quality_delta for pair in eligible_pairs) / max(1, count)
    vague_pairs = [
        pair
        for pair in eligible_pairs
        if pair.ambiguity_level in {"vague", "ambiguous"}
    ]
    vague_delta = sum(pair.quality_delta for pair in vague_pairs) / max(1, len(vague_pairs))
    workflow_requests = sum(pair.workflow.repository_requests for pair in eligible_pairs)
    agent_requests = sum(pair.agent.repository_requests for pair in eligible_pairs)
    workflow_elapsed = sum(pair.workflow.elapsed_seconds for pair in eligible_pairs)
    agent_elapsed = sum(pair.agent.elapsed_seconds for pair in eligible_pairs)
    added_violations = sum(pair.added_hard_constraint_violations for pair in eligible_pairs)
    reasons: list[str] = []
    if count != len(pairs):
        reasons.append("ineligible paired runs present")
    if count < gate.min_pairs:
        reasons.append("insufficient paired runs")
    if average_delta < gate.min_average_quality_delta:
        reasons.append("average quality delta below gate")
    if wins / max(1, count) < gate.min_win_rate:
        reasons.append("agent win rate below gate")
    if losses / max(1, count) > gate.max_loss_rate:
        reasons.append("workflow win rate exceeds gate")
    if vague_delta < gate.min_vague_quality_delta:
        reasons.append("vague-prompt quality delta below gate")
    if added_violations:
        reasons.append("agent added hard-constraint violations")
    request_ratio = agent_requests / max(1, workflow_requests)
    if request_ratio > gate.max_repository_request_ratio:
        reasons.append("repository request ratio exceeds hard gate")
    return ReplacementTierReport(
        budget_tier=tier,
        pairs=pairs,
        total_pairs=len(pairs),
        pair_count=count,
        agent_wins=wins,
        ties=ties,
        workflow_wins=losses,
        win_rate=wins / max(1, count),
        loss_rate=losses / max(1, count),
        average_quality_delta=average_delta,
        vague_quality_delta=vague_delta,
        repository_request_ratio=request_ratio,
        elapsed_time_ratio=agent_elapsed / max(0.001, workflow_elapsed),
        added_hard_constraint_violations=added_violations,
        replacement_ready=not reasons,
        gate_reasons=reasons,
    )


def _index_runs(
    runs: Sequence[ReplacementRun],
    *,
    expected_runtime: ReplacementRuntime,
    include_tier: bool,
) -> dict[tuple[object, ...], ReplacementRun]:
    indexed: dict[tuple[object, ...], ReplacementRun] = {}
    for run in runs:
        if run.runtime != expected_runtime:
            raise ValueError(f"expected {expected_runtime} replacement run")
        key: tuple[object, ...] = (run.scenario_id, run.variant_id, run.repeat)
        if include_tier:
            key = (*key, run.budget_tier)
        if key in indexed:
            raise ValueError(f"duplicate replacement run: {key}")
        indexed[key] = run
    if not indexed:
        raise ValueError("at least one replacement run is required")
    return indexed


def _ndcg_at_5(selected_grades: Sequence[int], all_grades: Sequence[int]) -> float:
    gains = [_relevance_gain(grade) for grade in selected_grades[:5]]
    ideal = sorted((_relevance_gain(grade) for grade in all_grades), reverse=True)[:5]
    dcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))
    ideal_dcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(ideal))
    return dcg / ideal_dcg if ideal_dcg else 0.0


def _relevance_gain(grade: int) -> float:
    return float((2 ** max(0, grade - 1)) - 1)


def _count_hard_constraint_violations(
    request: DatasetRequest,
    files: Sequence[dict[str, object]],
    *,
    hard_constraint_fields: Sequence[str],
) -> int:
    violations = 0
    hard_fields = {str(value) for value in hard_constraint_fields}
    requested_species = _species_keys([*request.species, *request.canonical_species])
    for item in files:
        if item.get("validity_status") == "exclude":
            violations += 1
        acquisition = str(item.get("acquisition_mode") or "").strip().casefold()
        if (
            "acquisition_mode" in hard_fields
            and
            acquisition
            and acquisition not in {"unknown", "not_specified"}
            and acquisition != request.acquisition_mode.casefold()
        ):
            violations += 1
        if (
            {"species", "species_policy"} & hard_fields
            and request.species_policy == "include_only"
            and requested_species
        ):
            raw_species = [
                *(item.get("species") or []),
                *(item.get("canonical_species") or []),
            ]
            observed_species = _species_keys(raw_species)
            if observed_species and requested_species.isdisjoint(observed_species):
                violations += 1
        labeling = str(item.get("labeling_strategy") or "").strip().casefold()
        if (
            "labeling_strategy" in hard_fields
            and
            labeling
            and labeling not in {"unknown", "not_specified"}
            and labeling != request.labeling_strategy.casefold()
        ):
            violations += 1
    return violations


def _species_keys(values: Sequence[object]) -> set[str]:
    keys: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        keys.add(text.casefold())
        term = normalize_species(text)
        if term is not None:
            keys.update(
                {
                    term.canonical.casefold(),
                    term.scientific_name.casefold(),
                    term.taxon_id.casefold(),
                }
            )
    return keys
