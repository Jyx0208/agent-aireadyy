from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import Field

from agent.ai_ready.model_informed_discovery import model_informed_repository_plan
from agent.models import JsonModel
from agent.utils import write_json


class GuidanceAlignmentResult(JsonModel):
    status: str
    output_dir: str
    achieved_count: int = 0
    partial_count: int = 0
    missing_count: int = 0
    files: dict[str, str] = Field(default_factory=dict)


def make_guidance_alignment_report(
    *,
    output_dir: str | Path,
    recipe_dir: str | Path | None = None,
    discovery_dir: str | Path | None = None,
    discovery_manifest: str | Path | None = None,
    model_loop_dir: str | Path | None = None,
    benchmark_dir: str | Path | None = None,
) -> GuidanceAlignmentResult:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    recipe_dir = Path(recipe_dir) if recipe_dir is not None else output_dir
    if discovery_manifest is not None and discovery_dir is None:
        discovery_dir = Path(discovery_manifest).parent
    discovery_dir = Path(discovery_dir) if discovery_dir is not None else None
    model_loop_dir = Path(model_loop_dir) if model_loop_dir is not None else recipe_dir
    benchmark_dir = Path(benchmark_dir) if benchmark_dir is not None else recipe_dir

    artifacts = {
        "dataset_recipe": _read_json(recipe_dir / "dataset_recipe.json") if recipe_dir else {},
        "task_ai_readiness_matrix": _read_json(discovery_dir / "task_ai_readiness_matrix.json") if discovery_dir else {},
        "quality_report": _read_json(discovery_dir / "quality_report.json") if discovery_dir else {},
        "data_value_ranking": _read_json(discovery_dir / "data_value_ranking.json") if discovery_dir else {},
        "data_value_strategy_eval": _read_json(discovery_dir / "data_value_strategy_eval.json") if discovery_dir else {},
        "repository_audit": _read_json(discovery_dir / "repository_audit.json") if discovery_dir else {},
        "dataset_split_plan": _read_json(recipe_dir / "dataset_split_plan.json") if recipe_dir else {},
        "leakage_risk_report": _read_json(recipe_dir / "leakage_risk_report.json") if recipe_dir else {},
        "split_baseline_evaluation": _read_json(recipe_dir / "split_baseline_evaluation.json") if recipe_dir else {},
        "hard_benchmark_manifest": _read_json(recipe_dir / "hard_benchmark_manifest.json") if recipe_dir else {},
        "counterfactual_benchmark_manifest": _read_json(recipe_dir / "counterfactual_benchmark_manifest.json") if recipe_dir else {},
        "coverage_gap_report": _read_json(recipe_dir / "coverage_gap_report.json") if recipe_dir else {},
        "agent_expansion_plan": _read_json(recipe_dir / "agent_expansion_plan.json") if recipe_dir else {},
        "model_eval_summary": _read_json(model_loop_dir / "model_eval_summary.json") if model_loop_dir else {},
        "model_failure_modes": _read_json(model_loop_dir / "model_failure_modes.json") if model_loop_dir else {},
        "model_informed_gap_report": _read_json(model_loop_dir / "model_informed_gap_report.json") if model_loop_dir else {},
        "model_informed_expansion_plan": _read_json(model_loop_dir / "model_informed_expansion_plan.json") if model_loop_dir else {},
        "model_informed_discovery_payloads": _read_json(model_loop_dir / "model_informed_discovery_payloads.json") if model_loop_dir else {},
        "model_informed_discovery_payload_queue": _read_json(model_loop_dir / "model_informed_discovery_payload_queue.json") if model_loop_dir else {},
        "model_strategy_comparison": _read_json(model_loop_dir / "model_strategy_comparison.json") if model_loop_dir else {},
        "evidence_graph": _read_json(recipe_dir / "evidence_graph.json") if recipe_dir else {},
        "curation_queue": _read_json(recipe_dir / "curation_queue.json") if recipe_dir else {},
        "curation_efficiency_report": _read_json(recipe_dir / "curation_efficiency_report.json") if recipe_dir else {},
        "curation_memory_update": _read_json(recipe_dir / "curation_memory_update.json") if recipe_dir else {},
        "benchmark_summary": _read_json(benchmark_dir / "benchmark_summary.json") if benchmark_dir else {},
    }
    artifacts["model_informed_repository_plan"] = model_informed_repository_plan(
        artifacts["model_informed_discovery_payloads"],
        artifacts["model_informed_discovery_payload_queue"],
    )

    requirements = _requirements(artifacts)
    status_counts = _count_statuses(requirements)
    overall_status = "aligned" if status_counts["missing"] == 0 and status_counts["partial"] == 0 else (
        "mostly_aligned" if status_counts["missing"] == 0 else "partial"
    )
    payload = {
        "status": overall_status,
        "recipe_dir": str(recipe_dir) if recipe_dir else "",
        "discovery_dir": str(discovery_dir) if discovery_dir else "",
        "model_loop_dir": str(model_loop_dir) if model_loop_dir else "",
        "benchmark_dir": str(benchmark_dir) if benchmark_dir else "",
        "summary": status_counts,
        "requirements": requirements,
        "notes": [
            "This is an evidence-based alignment audit against docs/260617_to do.md.",
            "Synthetic/unit tests prove module behavior; real benchmark evidence requires benchmark_summary.json with 3-5 small runs.",
            "Missing or partial items should be treated as remaining work, not as implicit completion.",
        ],
    }
    files = {
        "guidance_alignment_report_json": str(output_dir / "guidance_alignment_report.json"),
        "guidance_alignment_report_md": str(output_dir / "guidance_alignment_report.md"),
    }
    write_json(files["guidance_alignment_report_json"], payload)
    Path(files["guidance_alignment_report_md"]).write_text(_markdown(payload), encoding="utf-8")
    return GuidanceAlignmentResult(
        status=overall_status,
        output_dir=str(output_dir),
        achieved_count=status_counts["achieved"],
        partial_count=status_counts["partial"],
        missing_count=status_counts["missing"],
        files=files,
    )


def _requirements(artifacts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _semantic_metadata_requirement(artifacts),
        _task_readiness_requirement(artifacts),
        _data_value_requirement(artifacts),
        _multi_repository_requirement(artifacts),
        _local_existing_input_requirement(artifacts),
        _leakage_requirement(artifacts),
        _hard_counterfactual_requirement(artifacts),
        _gap_expansion_requirement(artifacts),
        _closed_loop_requirement(artifacts),
        _evidence_graph_requirement(artifacts),
        _active_curation_requirement(artifacts),
        _curation_memory_learning_requirement(artifacts),
        _real_benchmark_requirement(artifacts),
    ]


def _semantic_metadata_requirement(artifacts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    matrix = artifacts["task_ai_readiness_matrix"]
    quality = artifacts["quality_report"]
    rows = _rows(matrix)
    semantic_rows = [
        row
        for row in rows
        if _float(row.get("semantic_metadata_confidence")) > 0
        or str(row.get("ptm_evidence_terms") or "").strip()
        or str(row.get("ptm_enrichment_methods") or "").strip()
    ]
    species_policy = quality.get("species_policy_distribution") if isinstance(quality.get("species_policy_distribution"), dict) else {}
    labeling_distribution = quality.get("labeling_strategy_distribution") if isinstance(quality.get("labeling_strategy_distribution"), dict) else {}
    matrix_text = " ".join(
        str(row.get("warnings") or "") + " " + str(row.get("reasons") or "")
        for row in rows
    ).casefold()
    has_semantic_ptm = bool(semantic_rows) or _float(quality.get("semantic_metadata_confidence_mean")) > 0
    has_open_species = bool(species_policy.get("open")) or "species_open_diversity_gain" in matrix_text
    has_isobaric_policy = (
        bool(labeling_distribution.get("TMT") or labeling_distribution.get("iTRAQ"))
        or "isobaric_labeling_not_first_choice_for_task" in matrix_text
        or "labeling_weak_for_task" in matrix_text
    )
    achieved_parts = sum(1 for item in [has_semantic_ptm, has_open_species, has_isobaric_policy] if item)
    status = "achieved" if achieved_parts == 3 else ("partial" if achieved_parts else "missing")
    return _item(
        "semantic_metadata_interpretation_and_policy",
        status,
        "Normalizes PTM/enrichment semantics, keeps species open for diversity unless constrained, and treats TMT/iTRAQ as weak-but-allowed evidence.",
        evidence=[
            f"semantic_ptm_rows={len(semantic_rows)}",
            f"semantic_metadata_confidence_mean={quality.get('semantic_metadata_confidence_mean') or 0}",
            f"species_policy_distribution={species_policy}",
            f"labeling_strategy_distribution={labeling_distribution}",
            f"has_isobaric_weak_policy={has_isobaric_policy}",
        ],
        remaining=[] if status == "achieved" else [
            "Generate discovery outputs with semantic PTM evidence, open species policy evidence, and TMT/iTRAQ weak-but-allowed warnings."
        ],
    )


def _task_readiness_requirement(artifacts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    matrix = artifacts["task_ai_readiness_matrix"]
    rows = _rows(matrix)
    task_types = sorted({str(row.get("task_type") or "") for row in rows if row.get("task_type")})
    if rows and len(task_types) >= 2:
        status = "achieved"
    elif rows:
        status = "partial"
    else:
        recipe = artifacts["dataset_recipe"]
        status = "partial" if recipe.get("selected_files") else "missing"
    return _item(
        "task_specific_ai_readiness_score",
        status,
        "Scores every candidate against task-specific AI readiness rather than generic format compliance.",
        evidence=[
            f"matrix_rows={len(rows)}",
            f"task_types={task_types}",
            _artifact_evidence("task_ai_readiness_matrix.json", matrix),
        ],
        remaining=[] if status == "achieved" else ["Generate task_ai_readiness_matrix.json from discovery output."],
    )


def _data_value_requirement(artifacts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ranking = artifacts["data_value_ranking"]
    eval_report = artifacts["data_value_strategy_eval"]
    rows = _rows(ranking)
    has_eval = bool(eval_report.get("strategy_rows") or eval_report.get("interpretation"))
    status = "achieved" if rows and has_eval else ("partial" if rows or has_eval else "missing")
    return _item(
        "data_value_prediction",
        status,
        "Ranks public projects/files by expected marginal data value before expensive processing.",
        evidence=[
            f"ranking_rows={len(rows)}",
            f"strategy_eval={bool(has_eval)}",
            _artifact_evidence("data_value_ranking.json", ranking),
            _artifact_evidence("data_value_strategy_eval.json", eval_report),
        ],
        remaining=[] if status == "achieved" else ["Run discovery value scoring and eval-data-value-selection."],
    )


def _multi_repository_requirement(artifacts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    audit = artifacts["repository_audit"]
    graph = artifacts["evidence_graph"]
    rows = _rows(audit)
    repositories = sorted({str(row.get("repository") or "") for row in rows if row.get("repository")})
    attempted = audit.get("repositories_attempted") if isinstance(audit.get("repositories_attempted"), list) else repositories
    node_types = {
        str(node.get("type") or "")
        for node in (graph.get("nodes") if isinstance(graph.get("nodes"), list) else [])
        if isinstance(node, dict)
    }
    has_core_repositories = {"pride", "massive", "iprox"} <= {str(repo).casefold() for repo in attempted}
    has_next_steps = all(str(row.get("status") or "") and str(row.get("next_step") or row.get("status") or "") for row in rows)
    has_graph_evidence = "repository_attempt" in node_types
    status = "achieved" if rows and has_core_repositories and has_next_steps and has_graph_evidence else (
        "partial" if rows or attempted else "missing"
    )
    return _item(
        "multi_repository_discovery_and_audit",
        status,
        "Tracks PRIDE/MassIVE/iProX discovery attempts, blockers, next steps, and repository provenance in the evidence graph.",
        evidence=[
            f"repositories_attempted={attempted}",
            f"repository_audit_rows={len(rows)}",
            f"has_repository_attempt_nodes={has_graph_evidence}",
            f"repository_status_counts={_status_counts(rows)}",
        ],
        remaining=[] if status == "achieved" else [
            "Generate repository_audit.json for PRIDE/MassIVE/iProX discovery and include repository_attempt nodes in evidence_graph.json."
        ],
    )


def _local_existing_input_requirement(artifacts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    recipe = artifacts["dataset_recipe"]
    graph = artifacts["evidence_graph"]
    benchmark = artifacts["benchmark_summary"]
    recipe_rows = _recipe_file_rows(recipe)
    benchmark_rows = _benchmark_run_rows(benchmark)
    graph_nodes = [
        node
        for node in (graph.get("nodes") if isinstance(graph.get("nodes"), list) else [])
        if isinstance(node, dict)
    ]

    local_recipe_rows = [row for row in recipe_rows if _has_local_or_existing_signal(row)]
    usable_recipe_rows = [
        row for row in local_recipe_rows
        if _has_usable_existing_output_signal(row)
    ]
    local_benchmark_rows = [row for row in benchmark_rows if _has_local_or_existing_signal(row)]
    usable_benchmark_rows = [
        row for row in local_benchmark_rows
        if _has_usable_existing_output_signal(row)
    ]
    local_graph_nodes = [node for node in graph_nodes if _has_local_or_existing_signal(node)]

    local_signal_count = len(local_recipe_rows) + len(local_benchmark_rows) + len(local_graph_nodes)
    usable_signal_count = len(usable_recipe_rows) + len(usable_benchmark_rows)
    status = "achieved" if local_signal_count > 0 and usable_signal_count > 0 else (
        "partial" if local_signal_count > 0 else "missing"
    )
    return _item(
        "local_and_existing_result_reuse",
        status,
        "Supports local acquisitions, existing search-result directories, and original-agent partial outputs as first-class AI-ready inputs.",
        evidence=[
            f"local_or_existing_recipe_rows={len(local_recipe_rows)}",
            f"usable_existing_recipe_rows={len(usable_recipe_rows)}",
            f"local_or_existing_benchmark_runs={len(local_benchmark_rows)}",
            f"usable_existing_benchmark_runs={len(usable_benchmark_rows)}",
            f"local_or_existing_graph_nodes={len(local_graph_nodes)}",
        ],
        remaining=[] if status == "achieved" else [
            "Include local/search-dir/agent-run reuse evidence in dataset_recipe.json or benchmark_summary.json, with usable AI-ready output evidence."
        ],
    )


def _leakage_requirement(artifacts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    split = artifacts["dataset_split_plan"]
    leakage = artifacts["leakage_risk_report"]
    split_eval = artifacts["split_baseline_evaluation"]
    status = "achieved" if split and leakage and split_eval else ("partial" if split or leakage else "missing")
    return _item(
        "leakage_aware_split_and_benchmark_construction",
        status,
        "Creates fair train/val/test split plans and checks leakage against random/baseline alternatives.",
        evidence=[
            f"split_policy={split.get('split_policy') or split.get('split_level') or ''}",
            f"leakage_status={leakage.get('status') or ''}",
            f"split_baseline={split_eval.get('interpretation') or split_eval.get('status') or ''}",
        ],
        remaining=[] if status == "achieved" else ["Run make-dataset-recipe to produce split/leakage/baseline reports."],
    )


def _hard_counterfactual_requirement(artifacts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    hard = artifacts["hard_benchmark_manifest"]
    counterfactual = artifacts["counterfactual_benchmark_manifest"]
    hard_rows = _rows(hard)
    cf_rows = _rows(counterfactual)
    status = "achieved" if hard_rows and cf_rows else ("partial" if hard_rows or cf_rows else "missing")
    return _item(
        "counterfactual_and_hard_benchmark_generation",
        status,
        "Builds hard cases plus positive/negative/blocked counterfactual decision-boundary cases.",
        evidence=[
            f"hard_rows={len(hard_rows)}",
            f"counterfactual_rows={len(cf_rows)}",
            f"counterfactual_case_types={counterfactual.get('case_type_counts') or {}}",
        ],
        remaining=[] if status == "achieved" else ["Generate hard_benchmark_manifest and counterfactual_benchmark_manifest from recipe."],
    )


def _gap_expansion_requirement(artifacts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    coverage = artifacts["coverage_gap_report"]
    plan = artifacts["agent_expansion_plan"]
    actions = plan.get("actions") if isinstance(plan.get("actions"), list) else []
    status = "achieved" if coverage and plan else "missing"
    return _item(
        "gap_aware_dataset_expansion",
        status,
        "Diagnoses under-covered species/instrument/PTM/repository/task slices and proposes next discovery actions.",
        evidence=[
            f"gap_count={len(coverage.get('gaps') or [])}",
            f"action_count={len(actions)}",
            _artifact_evidence("agent_expansion_plan.json", plan),
        ],
        remaining=[] if status == "achieved" else ["Run make-dataset-recipe to produce coverage_gap_report and agent_expansion_plan."],
    )


def _closed_loop_requirement(artifacts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    model_eval = artifacts["model_eval_summary"]
    failure_modes = artifacts["model_failure_modes"]
    model_gap = artifacts["model_informed_gap_report"]
    model_plan = artifacts["model_informed_expansion_plan"]
    strategy = artifacts["model_strategy_comparison"]
    repository_plan = artifacts.get("model_informed_repository_plan") or {}
    planned_repositories = {
        str(repository).casefold()
        for repository in (repository_plan.get("planned_repositories") or [])
        if str(repository).strip()
    }
    has_model_informed_repository_plan = {"pride", "massive", "iprox"} <= planned_repositories
    base = bool(model_eval and failure_modes and model_gap and model_plan)
    status = "achieved" if base and strategy and has_model_informed_repository_plan else (
        "partial" if base or model_eval or strategy or repository_plan.get("status") == "ready" else "missing"
    )
    return _item(
        "closed_loop_dataset_model_co_optimization",
        status,
        "Runs safe model-loop smoke, diagnoses failure modes, and translates model gaps into cross-repository dataset expansion plans.",
        evidence=[
            f"model_loop_status={model_eval.get('status') or ''}",
            f"failure_mode_count={failure_modes.get('failure_mode_count') or len(failure_modes.get('failure_modes') or [])}",
            f"model_gap_count={len(model_gap.get('gaps') or [])}",
            f"strategy_comparison={strategy.get('interpretation') or strategy.get('status') or ''}",
            f"model_informed_repositories={sorted(planned_repositories)}",
            f"model_informed_repository_strategy={repository_plan.get('repository_strategy') or ''}",
        ],
        remaining=[] if status == "achieved" else [
            "Run run-dataset-model-loop, compare-dataset-model-strategies, and generate model-informed discovery payloads with PRIDE/MassIVE/iProX planned repositories."
        ],
    )


def _evidence_graph_requirement(artifacts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    graph = artifacts["evidence_graph"]
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    node_types = {str(node.get("type") or "") for node in nodes if isinstance(node, dict)}
    required = {"project", "file", "sample", "task", "parquet", "split", "hard_case", "counterfactual_case", "curation_item"}
    missing_types = sorted(required - node_types)
    status = "achieved" if graph and not missing_types else ("partial" if graph else "missing")
    return _item(
        "auditable_evidence_graph",
        status,
        "Converts project/file/task/output/decision evidence into an auditable graph.",
        evidence=[
            f"node_count={len(nodes)}",
            f"edge_count={len(graph.get('edges') or [])}",
            f"missing_node_types={missing_types}",
        ],
        remaining=[] if status == "achieved" else [f"Populate evidence graph node types: {', '.join(missing_types) or 'all required types'}."],
    )


def _active_curation_requirement(artifacts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    queue = artifacts["curation_queue"]
    efficiency = artifacts["curation_efficiency_report"]
    rows = _rows(queue)
    status = "achieved" if queue and efficiency else ("partial" if queue else "missing")
    return _item(
        "human_in_the_loop_active_curation",
        status,
        "Prioritizes only high-value uncertain or leakage-risk cases for expert review.",
        evidence=[
            f"curation_rows={len(rows)}",
            f"review_reduction_rate={efficiency.get('review_reduction_rate')}",
            f"critical_issue_coverage={efficiency.get('critical_issue_coverage') or {}}",
        ],
        remaining=[] if status == "achieved" else ["Run make-dataset-recipe to produce curation queue and efficiency report."],
    )


def _curation_memory_learning_requirement(artifacts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    quality = artifacts["quality_report"]
    ranking = artifacts["data_value_ranking"]
    curation_memory_update = artifacts["curation_memory_update"]
    memory_summary = quality.get("memory_feedback_summary") if isinstance(quality.get("memory_feedback_summary"), dict) else {}
    ranking_rows = _rows(ranking)
    feedback_rows = [row for row in ranking_rows if row.get("memory_recommended_action")]
    feedback_count = int(memory_summary.get("files_with_memory_feedback") or 0)
    action_counts = memory_summary.get("action_counts") if isinstance(memory_summary.get("action_counts"), dict) else {}
    imported_decisions = int(curation_memory_update.get("imported_decision_count") or 0)
    update_status = str(curation_memory_update.get("status") or "")
    status = "achieved" if feedback_count > 0 and feedback_rows else (
        "partial" if imported_decisions > 0 or feedback_count > 0 or feedback_rows else "missing"
    )
    return _item(
        "curation_memory_feedback_loop",
        status,
        "Writes expert/model-informed curation decisions back to discovery memory and uses them to influence future value ranking.",
        evidence=[
            f"curation_memory_update_status={update_status or 'not_available'}",
            f"curation_memory_imported_decisions={imported_decisions}",
            f"files_with_memory_feedback={feedback_count}",
            f"memory_action_counts={action_counts}",
            f"ranking_rows_with_memory_action={len(feedback_rows)}",
            _artifact_evidence("curation_memory_update.json", curation_memory_update),
            _artifact_evidence("quality_report.json", quality),
            _artifact_evidence("data_value_ranking.json", ranking),
        ],
        remaining=[] if status == "achieved" else (
            [
                "Rerun discovery/value scoring with memory enabled and verify memory_recommended_action appears in data_value_ranking."
            ]
            if imported_decisions > 0
            else [
                "Apply curation decisions to discovery memory, rerun discovery/value scoring with memory enabled, and verify memory_recommended_action appears in data_value_ranking."
            ]
        ),
    )


def _real_benchmark_requirement(artifacts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    benchmark = artifacts["benchmark_summary"]
    acceptance = benchmark.get("acceptance") if isinstance(benchmark.get("acceptance"), dict) else {}
    run_count = int(benchmark.get("run_count") or 0)
    complete = bool(acceptance.get("benchmark_complete"))
    status = "achieved" if complete else ("partial" if run_count > 0 else "missing")
    return _item(
        "real_3_5_sample_benchmark_evidence",
        status,
        "Validates the agent on 3-5 small real samples including clean, partial-recovery, and blocked cases.",
        evidence=[
            f"run_count={run_count}",
            f"acceptance={acceptance}",
            f"benchmark_status={benchmark.get('status') or ''}",
        ],
        remaining=[] if status == "achieved" else ["Run validate-agent-runs-ai-ready-batch with 3-5 real small samples and include clean/partial/blocked cases."],
    )


def _item(name: str, status: str, requirement: str, *, evidence: list[str], remaining: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "requirement": requirement,
        "evidence": evidence,
        "remaining_work": remaining,
    }


def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("rows") or payload.get("files") or payload.get("items") or payload.get("samples")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _recipe_file_rows(recipe: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ["selected_files", "excluded_files", "files", "rows"]:
        value = recipe.get(key)
        if isinstance(value, list):
            rows.extend(row for row in value if isinstance(row, dict))
    return rows


def _benchmark_run_rows(benchmark: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _rows(benchmark)
    for key in ["run_results", "runs", "results"]:
        value = benchmark.get(key)
        if isinstance(value, list):
            rows.extend(row for row in value if isinstance(row, dict))
    return rows


def _has_local_or_existing_signal(row: dict[str, Any]) -> bool:
    repository = str(row.get("repository") or "").casefold()
    source = str(row.get("source") or row.get("source_type") or row.get("input_source") or "").casefold()
    row_type = str(row.get("type") or "").casefold()
    if repository == "local" or source in {"local", "local_path", "search_dir", "agent_run", "existing_search_results"}:
        return True
    if row_type in {"local_input", "agent_run", "search_result", "peaklist", "partial_output", "existing_result"}:
        return True
    return any(
        row.get(key)
        for key in [
            "agent_run_dir",
            "search_dir",
            "search_result_path",
            "search_result_paths",
            "peaklist_path",
            "peaklist_paths",
            "local_path",
            "input_profile_path",
            "agent_run_input_locations",
        ]
    )


def _has_usable_existing_output_signal(row: dict[str, Any]) -> bool:
    status_text = " ".join(
        str(row.get(key) or "")
        for key in ["status", "ai_ready_outcome", "full_status", "outcome", "build_status"]
    ).casefold()
    if "completed_from_existing_search_results" in status_text or "completed_from_usable_partial_outputs" in status_text:
        return True
    if "completed" in status_text and "blocked" not in status_text and "failed" not in status_text:
        return True
    if row.get("generic_ai_ready_available") is True:
        return True
    row_keys = list(row)
    for key in ["rows_out", "denovo_rows_out", "rt_rows_out", "fragment_intensity_rows_out", "psm_scoring_rows_out"]:
        if _float(row.get(key)) > 0:
            return True
    return any(key.endswith("_rows_out") and _float(row.get(key)) > 0 for key in row_keys)


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _artifact_evidence(name: str, payload: dict[str, Any]) -> str:
    return f"{name}={'present' if payload else 'missing'}"


def _count_statuses(requirements: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"achieved": 0, "partial": 0, "missing": 0}
    for item in requirements:
        status = str(item.get("status") or "missing")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get("status") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    lines = [
        "# AI-ready Data Agent Guidance Alignment Report",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Achieved: {summary.get('achieved', 0)}",
        f"- Partial: {summary.get('partial', 0)}",
        f"- Missing: {summary.get('missing', 0)}",
        f"- Recipe dir: `{payload.get('recipe_dir') or ''}`",
        f"- Discovery dir: `{payload.get('discovery_dir') or ''}`",
        f"- Model loop dir: `{payload.get('model_loop_dir') or ''}`",
        f"- Benchmark dir: `{payload.get('benchmark_dir') or ''}`",
        "",
        "## Requirement Audit",
        "",
    ]
    for item in payload.get("requirements") or []:
        lines.extend(
            [
                f"### {item.get('name')}",
                "",
                f"- Status: `{item.get('status')}`",
                f"- Requirement: {item.get('requirement')}",
                "- Evidence:",
            ]
        )
        for evidence in item.get("evidence") or []:
            lines.append(f"  - {evidence}")
        remaining = item.get("remaining_work") or []
        if remaining:
            lines.append("- Remaining work:")
            for action in remaining:
                lines.append(f"  - {action}")
        lines.append("")
    lines.extend(["## Notes", ""])
    for note in payload.get("notes") or []:
        lines.append(f"- {note}")
    return "\n".join(lines) + "\n"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}
