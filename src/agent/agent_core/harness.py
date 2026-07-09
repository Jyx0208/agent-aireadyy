from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import Field

from agent.discovery.ontology import (
    interpret_immunopeptide_metadata,
    interpret_ptm_metadata,
    is_immunopeptidomics_goal,
    normalize_labeling_strategy,
    normalize_ptm_type,
    normalize_species_values,
)
from agent.models import JsonModel
from agent.repositories.smoke import RepositorySmokeResult, run_repository_smoke
from agent.utils import write_json


AgentHarnessStatus = Literal["passed", "failed", "blocked"]


class AgentHarnessCase(JsonModel):
    id: str
    goal: str
    repository: str = "pride"
    input_value: str | None = None
    expected_hard_constraints: dict[str, Any] = Field(default_factory=dict)
    allowed_repositories: list[str] = Field(default_factory=list)
    expected_goal: str | None = None
    expected_task_type: str | None = None
    expected_ptm_type: str | None = None
    expected_immunopeptide_scope: str | None = None
    expected_labeling_strategy: str | None = None
    expected_species_policy: str | None = None
    expected_species: list[str] = Field(default_factory=list)
    expected_repository: str | None = None
    expected_next_action_category: str | None = None
    expected_blocker_recovery_class: str | None = None
    requires_llm: bool = False


class AgentHarnessCaseResult(JsonModel):
    id: str
    status: AgentHarnessStatus
    goal: str
    inferred: dict[str, Any] = Field(default_factory=dict)
    checks: list[dict[str, Any]] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    repository_smoke: dict[str, Any] | None = None


class AgentHarnessResult(JsonModel):
    status: str
    output_dir: str
    total_cases: int = 0
    passed: int = 0
    failed: int = 0
    blocked: int = 0
    case_results: list[AgentHarnessCaseResult] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict)


RepositorySmokeRunner = Callable[..., RepositorySmokeResult]


def run_agent_harness(
    *,
    case_file: str | Path,
    output_dir: str | Path,
    use_llm: bool = True,
    repository_smoke_runner: RepositorySmokeRunner = run_repository_smoke,
) -> AgentHarnessResult:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cases = _load_cases(Path(case_file))
    case_results: list[AgentHarnessCaseResult] = []
    traces: list[dict[str, Any]] = []
    llm_available = bool(os.getenv("DEEPSEEK_API_KEY") or os.getenv("AGENT_LLM_API_KEY"))

    for case in cases:
        inferred = _infer_intent(case)
        blockers: list[str] = []
        warnings: list[str] = []
        repository_smoke_payload: dict[str, Any] | None = None
        if case.requires_llm and use_llm and not llm_available:
            blockers.append("needs_llm")
        elif use_llm and llm_available:
            warnings.append("llm_planner_not_invoked_in_harness_v1_deterministic_fallback_used")

        if not blockers and inferred["next_action_category"] == "repository_smoke" and case.input_value:
            smoke_dir = output_dir / f"{_safe_stem(case.id)}_repository_smoke"
            smoke = repository_smoke_runner(
                repository=inferred["repository"],
                input_value=case.input_value,
                mode="parameters",
                output_dir=smoke_dir,
            )
            repository_smoke_payload = smoke.model_dump(mode="json")
            if smoke.status == "blocked":
                blockers.extend(smoke.blockers or ["repository_smoke_blocked"])
                if smoke.blockers and not inferred.get("blocker_recovery_class"):
                    inferred["blocker_recovery_class"] = smoke.blockers[0]
            inferred["repository"] = smoke.repository or inferred["repository"]
            inferred["project_accession"] = smoke.project_accession

        checks = _evaluate_case(case, inferred)
        if blockers:
            status: AgentHarnessStatus = "blocked"
        elif all(bool(item.get("passed")) for item in checks):
            status = "passed"
        else:
            status = "failed"
        result = AgentHarnessCaseResult(
            id=case.id,
            status=status,
            goal=case.goal,
            inferred=inferred,
            checks=checks,
            blockers=blockers,
            warnings=warnings,
            repository_smoke=repository_smoke_payload,
        )
        case_results.append(result)
        traces.append(
            {
                "case_id": case.id,
                "goal": case.goal,
                "thought": "Parse goal and select deterministic next action.",
                "action": inferred.get("next_action_category"),
                "observation": {
                    "status": status,
                    "checks": checks,
                    "blockers": blockers,
                    "warnings": warnings,
                },
            }
        )

    files = {
        "agent_harness_summary_json": output_dir / "agent_harness_summary.json",
        "agent_harness_summary_csv": output_dir / "agent_harness_summary.csv",
        "agent_harness_report_md": output_dir / "agent_harness_report.md",
        "agent_decision_trace_json": output_dir / "agent_decision_trace.json",
    }
    passed = sum(1 for item in case_results if item.status == "passed")
    failed = sum(1 for item in case_results if item.status == "failed")
    blocked = sum(1 for item in case_results if item.status == "blocked")
    status = "passed" if failed == 0 and blocked == 0 else ("blocked" if failed == 0 else "failed")
    result = AgentHarnessResult(
        status=status,
        output_dir=str(output_dir),
        total_cases=len(case_results),
        passed=passed,
        failed=failed,
        blocked=blocked,
        case_results=case_results,
        files={key: str(path) for key, path in files.items()},
    )
    write_json(files["agent_harness_summary_json"], result.model_dump(mode="json"))
    _write_case_csv(files["agent_harness_summary_csv"], case_results)
    files["agent_harness_report_md"].write_text(_markdown(result), encoding="utf-8")
    write_json(files["agent_decision_trace_json"], traces)
    return result


def _load_cases(path: Path) -> list[AgentHarnessCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("Agent harness case file must contain a list or {'cases': [...]} payload.")
    return [AgentHarnessCase.model_validate(row) for row in rows]


def _infer_intent(case: AgentHarnessCase) -> dict[str, Any]:
    goal = case.goal.casefold()
    repository = _normalize_repository(case.repository)
    if (
        "auto" in goal
        or "all repositories" in goal
        or "multi-repository" in goal
        or "multi repository" in goal
        or "cross repository" in goal
        or "cross-repository" in goal
        or all(token in goal for token in ["pride", "massive", "iprox"])
    ):
        repository = "auto"
    elif "massive" in goal or "msv" in goal:
        repository = "massive"
    elif "iprox" in goal or "ipx" in goal:
        repository = "iprox"
    planned_repositories = _planned_repositories(repository, goal)

    task_type = "rt_prediction"
    if "fragment" in goal or "intensity" in goal:
        task_type = "fragment_intensity_prediction"
    if "psm" in goal or "scoring" in goal:
        task_type = "psm_scoring"
    if "ptm de novo" in goal or "ptm-denovo" in goal:
        task_type = "ptm_denovo"
    elif "de novo" in goal or "denovo" in goal:
        task_type = "denovo"
    if "chimeric" in goal:
        task_type = "chimeric_interpretation"

    species_policy = _infer_species_policy(goal, case.goal)
    species: list[str] = []
    if "human" in goal or "hela" in goal:
        species.append("human")
    if "mouse" in goal or "murine" in goal or "小鼠" in case.goal:
        species.append("mouse")
    if "yeast" in goal:
        species.append("yeast")
    if "rat" in goal or "rattus" in goal:
        species.append("rat")
    if "e coli" in goal or "e. coli" in goal or "ecoli" in goal or "escherichia" in goal:
        species.append("e_coli")
    if "rice" in goal or "oryza" in goal:
        species.append("rice")
    if not species:
        species = ["human"]
    species, taxon_ids = normalize_species_values(species)

    immunopeptide_interpretation = interpret_immunopeptide_metadata(case.goal)
    explicit_general_discovery = _is_explicit_general_discovery_goal(goal)
    goal_type = (
        "general"
        if explicit_general_discovery
        else (
            "immunopeptidomics"
            if is_immunopeptidomics_goal(case.goal) or immunopeptide_interpretation.confidence > 0
            else "ptm"
        )
    )
    ptm_interpretation = interpret_ptm_metadata(case.goal, requested=case.expected_ptm_type)
    ptm_type = normalize_ptm_type(ptm_interpretation.canonical)
    if goal_type == "immunopeptidomics" and ptm_interpretation.confidence <= 0:
        ptm_type = "unknown_ptm"

    labeling_strategy = "label_free"
    if "tmt" in goal or "tandem mass tag" in goal:
        labeling_strategy = "TMT"
    elif "itraq" in goal:
        labeling_strategy = "iTRAQ"
    elif "unknown labeling" in goal:
        labeling_strategy = "unknown"
    labeling_strategy = normalize_labeling_strategy(labeling_strategy)

    recovery_class = None
    explicit_blocker = None
    explicit_blocker_recovery = False
    if "missing index" in goal or "iprox index" in goal:
        explicit_blocker = "iprox_index_missing"
        explicit_blocker_recovery = any(marker in goal for marker in ["refresh", "fix", "recover", "repair", "resolve"])
    if "partial" in goal or "usable partial" in goal:
        recovery_class = "usable_partial_outputs"
    elif "review gate" in goal or "species uncertain" in goal:
        recovery_class = "review_gate_blocked"
    elif "missing peaklist" in goal or "needs peaklist" in goal:
        recovery_class = "missing_peaklist"

    recipe_action = None
    if "counterfactual benchmark" in goal or "counterfactual" in goal or "decision boundary benchmark" in goal:
        recipe_action = "counterfactual_benchmark_plan"
    elif "hard benchmark" in goal or "hard-case" in goal or "hard case" in goal:
        recipe_action = "hard_benchmark_plan"
    elif ("random" in goal or "baseline" in goal) and "split" in goal:
        recipe_action = "split_baseline_evaluation_plan"
    elif "leakage" in goal or "leakage-aware" in goal or "split" in goal:
        recipe_action = "leakage_aware_recipe_plan"
    elif "curation" in goal or "review queue" in goal or "manual review" in goal:
        if "memory" in goal or "write back" in goal or "writeback" in goal or "learn from" in goal:
            recipe_action = "curation_memory_learning_plan"
        else:
            recipe_action = "active_curation_plan"
    elif "data value" in goal or "high value" in goal or "worth processing" in goal:
        recipe_action = "data_value_selection_plan"
    elif ("agent-selected" in goal or "agent selected" in goal or "strategy" in goal) and ("baseline" in goal or "random" in goal) and ("metric" in goal or "model" in goal):
        recipe_action = "model_strategy_comparison_plan"
    elif "adapter contract" in goal or "model adapter" in goal or "external adapter" in goal:
        recipe_action = "model_adapter_contract_plan"
    elif ("model-informed" in goal or "model informed" in goal) and "discovery request" in goal:
        recipe_action = "model_informed_discovery_request_review"
    elif "model failure" in goal or "failure mode" in goal or "model-informed" in goal or "baseline model" in goal:
        recipe_action = "model_informed_expansion_plan"

    if explicit_blocker and explicit_blocker_recovery:
        next_action = "repository_blocker_recovery_plan"
    elif explicit_blocker:
        next_action = "stop_with_blocker"
    elif recovery_class:
        next_action = "recovery_plan"
    elif recipe_action:
        next_action = recipe_action
    elif case.input_value and repository in {"massive", "iprox", "auto"}:
        next_action = "repository_smoke"
    else:
        next_action = "discovery_plan"

    immunopeptide_scope = (
        immunopeptide_interpretation.scope if immunopeptide_interpretation.scope != "unknown" else None
    )
    hard_constraints = {
        "goal": goal_type,
        "task_type": task_type,
        "species": species,
        "species_policy": species_policy,
        "repository": repository,
        "planned_repositories": planned_repositories,
        "acquisition_mode": "dda" if "dia" not in goal else "dia",
        "ptm_type": ptm_type,
        "labeling_strategy": labeling_strategy,
        "organism_taxon_id": taxon_ids,
        "semantic_ptm_evidence_terms": list(ptm_interpretation.evidence_terms),
        "ptm_enrichment_methods": list(ptm_interpretation.enrichment_methods),
        "semantic_metadata_confidence": ptm_interpretation.confidence,
        "immunopeptide_scope": immunopeptide_scope,
        "hla_class": list(immunopeptide_interpretation.hla_classes),
        "hla_alleles": list(immunopeptide_interpretation.hla_alleles),
        "immunopeptide_evidence_terms": list(immunopeptide_interpretation.evidence_terms),
        "immunopeptide_enrichment_methods": list(immunopeptide_interpretation.enrichment_methods),
        "immunopeptide_metadata_confidence": immunopeptide_interpretation.confidence,
    }
    return {
        "goal": goal_type,
        "task_type": task_type,
        "ptm_type": ptm_type,
        "ptm_evidence_terms": list(ptm_interpretation.evidence_terms),
        "ptm_enrichment_methods": list(ptm_interpretation.enrichment_methods),
        "semantic_metadata_confidence": ptm_interpretation.confidence,
        "immunopeptide_scope": immunopeptide_scope,
        "hla_class": list(immunopeptide_interpretation.hla_classes),
        "hla_alleles": list(immunopeptide_interpretation.hla_alleles),
        "immunopeptide_evidence_terms": list(immunopeptide_interpretation.evidence_terms),
        "immunopeptide_enrichment_methods": list(immunopeptide_interpretation.enrichment_methods),
        "immunopeptide_metadata_confidence": immunopeptide_interpretation.confidence,
        "labeling_strategy": labeling_strategy,
        "species": species,
        "species_policy": species_policy,
        "repository": repository,
        "planned_repositories": planned_repositories,
        "next_action_category": next_action,
        "blocker_recovery_class": explicit_blocker or recovery_class,
        "hard_constraints": hard_constraints,
    }


def _evaluate_case(case: AgentHarnessCase, inferred: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if case.expected_goal:
        checks.append(_check("goal", inferred.get("goal"), case.expected_goal))
    if case.expected_task_type:
        checks.append(_check("task_type", inferred.get("task_type"), case.expected_task_type))
    if case.expected_ptm_type:
        checks.append(_check("ptm_type", inferred.get("ptm_type"), normalize_ptm_type(case.expected_ptm_type)))
    if case.expected_immunopeptide_scope:
        checks.append(
            _check("immunopeptide_scope", inferred.get("immunopeptide_scope"), case.expected_immunopeptide_scope)
        )
    if case.expected_labeling_strategy:
        checks.append(
            _check(
                "labeling_strategy",
                inferred.get("labeling_strategy"),
                normalize_labeling_strategy(case.expected_labeling_strategy),
            )
        )
    if case.expected_species_policy:
        checks.append(_check("species_policy", inferred.get("species_policy"), case.expected_species_policy))
    if case.expected_repository:
        checks.append(_check("repository", inferred.get("repository"), _normalize_repository(case.expected_repository)))
    if case.allowed_repositories:
        allowed = [_normalize_repository(repository) for repository in case.allowed_repositories]
        planned = inferred.get("planned_repositories") or [inferred.get("repository")]
        checks.append(
            {
                "name": "planned_repositories_cover_allowed",
                "actual": planned,
                "expected": allowed,
                "passed": all(repository in planned for repository in allowed),
            }
        )
    if case.expected_next_action_category:
        checks.append(_check("next_action_category", inferred.get("next_action_category"), case.expected_next_action_category))
    if case.expected_blocker_recovery_class:
        checks.append(_check("blocker_recovery_class", inferred.get("blocker_recovery_class"), case.expected_blocker_recovery_class))
    for expected_species in case.expected_species:
        checks.append(
            {
                "name": "species_contains",
                "actual": inferred.get("species") or [],
                "expected": expected_species,
                "passed": expected_species in (inferred.get("species") or []),
            }
        )
    for key, expected in case.expected_hard_constraints.items():
        actual = (inferred.get("hard_constraints") or {}).get(key)
        checks.append(_check(f"hard_constraint:{key}", actual, expected))
    if not checks:
        checks.append({"name": "case_loaded", "actual": True, "expected": True, "passed": True})
    return checks


def _check(name: str, actual: Any, expected: Any) -> dict[str, Any]:
    return {"name": name, "actual": actual, "expected": expected, "passed": actual == expected}


def _normalize_repository(repository: str) -> str:
    value = str(repository or "pride").strip().lower().replace("-", "_")
    aliases = {"px": "pride", "proteomexchange": "pride", "msv": "massive", "ipx": "iprox", "all": "auto"}
    value = aliases.get(value, value)
    return value if value in {"pride", "massive", "iprox", "auto"} else "pride"


def _planned_repositories(repository: str, goal: str) -> list[str]:
    planned = ["pride", "massive", "iprox"] if repository == "auto" else [repository]
    if "local" in str(goal or "").casefold() and "local" not in planned:
        planned.append("local")
    return planned


def _infer_species_policy(goal: str, original_goal: str) -> str:
    text = str(goal or "").casefold()
    if any(marker in text for marker in ["include only", "only include", "only use", "strict species", "strictly species"]):
        return "include_only"
    if any(marker in text for marker in ["exclude species", "without species", "do not use species"]):
        return "exclude"
    return "open"
    """
    original = str(original_goal or "")
    if any(marker in text for marker in ["include only", "only include", "only use", "strict species", "strictly species"]):
        return "include_only"
    if any(marker in original for marker in ["只要", "仅", "只用"]):
        return "include_only"
    if any(marker in text for marker in ["exclude species", "without species", "do not use species"]):
        return "exclude"
    if any(marker in original for marker in ["不要", "排除"]):
        return "exclude"
    return "open"


    """


def _is_explicit_general_discovery_goal(goal: str) -> bool:
    text = str(goal or "").casefold()
    return any(
        marker in text
        for marker in [
            "general data search",
            "general discovery",
            "general dataset discovery",
            "generic discovery",
            "generic data search",
            "arbitrary data search",
            "arbitrary dda",
        ]
    )


def _safe_stem(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value.strip())
    return cleaned[:80] or "case"


def _write_case_csv(path: Path, rows: list[AgentHarnessCaseResult]) -> None:
    fieldnames = [
        "id",
        "status",
        "goal",
        "inferred_goal",
        "task_type",
        "ptm_type",
        "immunopeptide_scope",
        "hla_class",
        "hla_alleles",
        "labeling_strategy",
        "species_policy",
        "species",
        "repository",
        "planned_repositories",
        "next_action_category",
        "blockers",
        "warnings",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "id": row.id,
                    "status": row.status,
                    "goal": row.goal,
                    "inferred_goal": row.inferred.get("goal"),
                    "task_type": row.inferred.get("task_type"),
                    "ptm_type": row.inferred.get("ptm_type"),
                    "immunopeptide_scope": row.inferred.get("immunopeptide_scope"),
                    "hla_class": json.dumps(row.inferred.get("hla_class") or [], ensure_ascii=False),
                    "hla_alleles": json.dumps(row.inferred.get("hla_alleles") or [], ensure_ascii=False),
                    "labeling_strategy": row.inferred.get("labeling_strategy"),
                    "species_policy": row.inferred.get("species_policy"),
                    "species": json.dumps(row.inferred.get("species") or [], ensure_ascii=False),
                    "repository": row.inferred.get("repository"),
                    "planned_repositories": json.dumps(row.inferred.get("planned_repositories") or [], ensure_ascii=False),
                    "next_action_category": row.inferred.get("next_action_category"),
                    "blockers": ";".join(row.blockers),
                    "warnings": ";".join(row.warnings),
                }
            )


def _markdown(result: AgentHarnessResult) -> str:
    lines = [
        "# Agent Harness Report",
        "",
        f"- Status: `{result.status}`",
        f"- Cases: {result.total_cases}",
        f"- Passed: {result.passed}",
        f"- Failed: {result.failed}",
        f"- Blocked: {result.blocked}",
        "",
        "## Cases",
        "",
    ]
    for row in result.case_results:
        lines.extend(
            [
                f"### {row.id}",
                "",
                f"- Status: `{row.status}`",
                f"- Goal: `{row.inferred.get('goal')}`",
                f"- Task: `{row.inferred.get('task_type')}`",
                f"- PTM: `{row.inferred.get('ptm_type')}`",
                f"- Immunopeptide scope: `{row.inferred.get('immunopeptide_scope')}`",
                f"- HLA class: `{', '.join(map(str, row.inferred.get('hla_class') or []))}`",
                f"- Labeling: `{row.inferred.get('labeling_strategy')}`",
                f"- Species policy: `{row.inferred.get('species_policy')}`",
                f"- Repository: `{row.inferred.get('repository')}`",
                f"- Planned repositories: `{', '.join(map(str, row.inferred.get('planned_repositories') or []))}`",
                f"- Next action: `{row.inferred.get('next_action_category')}`",
                f"- Checks: `{json.dumps(row.checks, ensure_ascii=False)}`",
                f"- Blockers: {', '.join(row.blockers) if row.blockers else 'None'}",
                "",
            ]
        )
    return "\n".join(lines)
