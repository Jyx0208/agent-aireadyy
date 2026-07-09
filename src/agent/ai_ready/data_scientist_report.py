from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import Field

from agent.ai_ready.model_informed_discovery import model_informed_repository_plan
from agent.models import JsonModel
from agent.utils import write_json


class DataScientistAgentReportResult(JsonModel):
    status: str
    recipe_dir: str
    output_dir: str
    selected_count: int = 0
    excluded_count: int = 0
    leakage_status: str = "not_evaluated"
    hard_benchmark_count: int = 0
    curation_queue_count: int = 0
    model_loop_status: str = "not_available"
    guidance_alignment_status: str = "not_available"
    gap_action_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict)


def make_data_scientist_agent_report(
    *,
    recipe_dir: str | Path,
    output_dir: str | Path | None = None,
    model_loop_dir: str | Path | None = None,
    benchmark_dir: str | Path | None = None,
    discovery_manifest: str | Path | None = None,
    guidance_alignment_dir: str | Path | None = None,
) -> DataScientistAgentReportResult:
    recipe_dir = Path(recipe_dir)
    output_dir = Path(output_dir) if output_dir is not None else recipe_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    model_loop_dir = Path(model_loop_dir) if model_loop_dir is not None else recipe_dir
    benchmark_dir = Path(benchmark_dir) if benchmark_dir is not None else None
    guidance_alignment_dir = Path(guidance_alignment_dir) if guidance_alignment_dir is not None else None
    discovery_manifest_path = Path(discovery_manifest) if discovery_manifest is not None else None
    discovery_dir = discovery_manifest_path.parent if discovery_manifest_path is not None else None

    recipe = _read_json(recipe_dir / "dataset_recipe.json")
    discovery_payload = _read_json(discovery_manifest_path) if discovery_manifest_path is not None else {}
    task_readiness_matrix = _read_json(discovery_dir / "task_ai_readiness_matrix.json") if discovery_dir is not None else {}
    data_value_ranking = _read_json(discovery_dir / "data_value_ranking.json") if discovery_dir is not None else {}
    data_value_strategy_eval = _read_json(discovery_dir / "data_value_strategy_eval.json") if discovery_dir is not None else {}
    repository_audit = _read_json(discovery_dir / "repository_audit.json") if discovery_dir is not None else {}
    split_plan = _read_json(recipe_dir / "dataset_split_plan.json")
    leakage = _read_json(recipe_dir / "leakage_risk_report.json")
    split_baseline = _read_json(recipe_dir / "split_baseline_evaluation.json")
    hard = _read_json(recipe_dir / "hard_benchmark_manifest.json")
    counterfactual = _read_json(recipe_dir / "counterfactual_benchmark_manifest.json")
    curation = _read_json(recipe_dir / "curation_queue.json")
    curation_efficiency = _read_json(recipe_dir / "curation_efficiency_report.json")
    curation_memory_update = _read_json(recipe_dir / "curation_memory_update.json")
    coverage_gap = _read_json(recipe_dir / "coverage_gap_report.json")
    expansion_plan = _read_json(recipe_dir / "agent_expansion_plan.json")
    evidence_graph = _read_json(recipe_dir / "evidence_graph.json")
    model_eval = _read_json(model_loop_dir / "model_eval_summary.json")
    model_adapter_contract = _read_json(model_loop_dir / "model_adapter_contract.json")
    model_adapter_input = _read_json(model_loop_dir / "model_adapter_input_manifest.json")
    model_failure_modes = _read_json(model_loop_dir / "model_failure_modes.json")
    model_gap = _read_json(model_loop_dir / "model_informed_gap_report.json")
    model_expansion = _read_json(model_loop_dir / "model_informed_expansion_plan.json")
    model_discovery_requests = _read_json(model_loop_dir / "model_informed_discovery_requests.json")
    model_discovery_payloads = _read_json(model_loop_dir / "model_informed_discovery_payloads.json")
    model_discovery_payload_queue = _read_json(model_loop_dir / "model_informed_discovery_payload_queue.json")
    model_informed_curation_queue = _read_json(model_loop_dir / "model_informed_curation_queue.json")
    model_strategy_comparison = _read_json(model_loop_dir / "model_strategy_comparison.json")
    benchmark_summary = _read_json(benchmark_dir / "benchmark_summary.json") if benchmark_dir else {}
    guidance_alignment = _read_json(guidance_alignment_dir / "guidance_alignment_report.json") if guidance_alignment_dir else {}

    warnings = _missing_artifact_warnings(
        {
            "dataset_recipe": recipe,
            "dataset_split_plan": split_plan,
            "leakage_risk_report": leakage,
            "split_baseline_evaluation": split_baseline,
            "hard_benchmark_manifest": hard,
            "counterfactual_benchmark_manifest": counterfactual,
            "curation_queue": curation,
            "curation_efficiency_report": curation_efficiency,
            "coverage_gap_report": coverage_gap,
            "agent_expansion_plan": expansion_plan,
            "evidence_graph": evidence_graph,
        }
    )
    if not model_eval:
        warnings.append("model_loop_outputs_missing")
    elif not model_adapter_contract:
        warnings.append("model_adapter_contract_missing")
    if guidance_alignment_dir is not None and not guidance_alignment:
        warnings.append("guidance_alignment_report_missing")
    if discovery_manifest is not None and not Path(discovery_manifest).exists():
        warnings.append(f"discovery_manifest_missing:{discovery_manifest}")

    selected = recipe.get("selected_files") if isinstance(recipe.get("selected_files"), list) else []
    excluded = recipe.get("excluded_files") if isinstance(recipe.get("excluded_files"), list) else []
    hard_rows = hard.get("rows") if isinstance(hard.get("rows"), list) else []
    counterfactual_rows = counterfactual.get("rows") if isinstance(counterfactual.get("rows"), list) else []
    curation_rows = curation.get("rows") if isinstance(curation.get("rows"), list) else []
    model_gaps = model_gap.get("gaps") if isinstance(model_gap.get("gaps"), list) else []
    model_actions = model_expansion.get("actions") if isinstance(model_expansion.get("actions"), list) else []
    model_requests = model_discovery_requests.get("requests") if isinstance(model_discovery_requests.get("requests"), list) else []
    model_payloads = model_discovery_payloads.get("payloads") if isinstance(model_discovery_payloads.get("payloads"), list) else []
    model_queue_items = model_discovery_payload_queue.get("items") if isinstance(model_discovery_payload_queue.get("items"), list) else []
    model_repository_plan = model_informed_repository_plan(model_discovery_payloads, model_discovery_payload_queue)
    model_curation_rows = (
        model_informed_curation_queue.get("rows")
        if isinstance(model_informed_curation_queue.get("rows"), list)
        else _model_discovery_request_curation_rows(model_requests)
    )
    combined_curation_rows = sorted(
        [*curation_rows, *model_curation_rows],
        key=lambda row: (-_safe_float(row.get("priority_score")), str(row.get("curation_type") or "")),
    )
    recipe_actions = expansion_plan.get("actions") if isinstance(expansion_plan.get("actions"), list) else []
    summary = {
        "status": "ready" if recipe else "partial",
        "recipe_dir": str(recipe_dir),
        "output_dir": str(output_dir),
        "benchmark_dir": str(benchmark_dir) if benchmark_dir else "",
        "model_loop_dir": str(model_loop_dir),
        "selected_count": len(selected),
        "excluded_count": len(excluded),
        "repository_summary": recipe.get("repository_summary") or {},
        "discovery": _discovery_summary(
            discovery_payload,
            task_readiness_matrix,
            data_value_ranking,
            data_value_strategy_eval,
            repository_audit,
        ),
        "split": {
            "requested_strategy": recipe.get("split_strategy_requested") or split_plan.get("split_strategy_requested"),
            "resolved_strategy": recipe.get("split_strategy_resolved") or split_plan.get("split_policy"),
            "counts": recipe.get("split_counts") or split_plan.get("split_counts") or {},
        },
        "leakage": {
            "status": leakage.get("status") or "not_evaluated",
            "issue_counts": leakage.get("issue_counts") or {},
            "recommendations": leakage.get("recommendations") or [],
        },
        "split_baseline_evaluation": {
            "status": split_baseline.get("status") or "not_evaluated",
            "best_strategy_by_leakage": split_baseline.get("best_strategy_by_leakage") or "",
            "agent_total_leakage_issue_count": split_baseline.get("agent_total_leakage_issue_count") or 0,
            "random_total_leakage_issue_count": split_baseline.get("random_total_leakage_issue_count") or 0,
            "agent_minus_random_leakage": split_baseline.get("agent_minus_random_leakage") or 0,
            "interpretation": split_baseline.get("interpretation") or "not_evaluated",
        },
        "hard_benchmark": {
            "row_count": len(hard_rows),
            "tag_counts": _count_hard_tags(hard_rows),
        },
        "counterfactual_benchmark": {
            "row_count": len(counterfactual_rows),
            "case_type_counts": counterfactual.get("case_type_counts") or _count_values([str(row.get("case_type") or "unknown") for row in counterfactual_rows]),
            "tag_counts": counterfactual.get("tag_counts") or _count_hard_tags(counterfactual_rows),
        },
        "coverage_gap": {
            "gap_count": len(coverage_gap.get("gaps") or []),
            "actions": recipe_actions[:20],
        },
        "model_loop": {
            "status": model_eval.get("status") or "not_available",
            "adapter": model_eval.get("adapter") or "",
            "adapter_status": model_eval.get("adapter_status") or "",
            "metric_status": model_eval.get("metric_status") or "not_available",
            "metrics": model_eval.get("metrics") or {},
            "adapter_contract": {
                "available": bool(model_adapter_contract),
                "schema_version": model_adapter_contract.get("schema_version") or "",
                "input_schema_version": model_adapter_input.get("schema_version") or "",
                "input_selected_count": (model_adapter_input.get("summary") or {}).get("selected_count") if model_adapter_input else None,
                "expected_metrics_path": model_eval.get("expected_metrics_path") or (model_adapter_contract.get("required_output") or {}).get("path") or "",
                "warnings": model_eval.get("adapter_contract_warnings") or [],
            },
            "failure_mode_count": int(model_failure_modes.get("failure_mode_count") or len(model_failure_modes.get("failure_modes") or [])),
            "model_gap_count": len(model_gaps),
            "model_informed_actions": model_actions[:20],
            "model_informed_discovery_request_count": len(model_requests),
            "model_informed_discovery_requests": model_requests[:20],
            "model_informed_discovery_payload_count": len(model_payloads),
            "model_informed_discovery_payloads": model_payloads[:20],
            "model_informed_payload_queue_count": len(model_queue_items),
            "model_informed_payload_queue_ready_count": model_discovery_payload_queue.get("ready_count") or 0,
            "model_informed_payload_queue_review_count": model_discovery_payload_queue.get("review_count") or 0,
            "model_informed_payload_queue_blocked_count": model_discovery_payload_queue.get("blocked_count") or 0,
            "model_informed_payload_queue": model_queue_items[:20],
            "model_informed_repository_plan": model_repository_plan,
            "model_informed_curation_item_count": len(model_curation_rows),
            "model_informed_curation_queue": model_curation_rows[:20],
        },
        "model_strategy_comparison": {
            "status": model_strategy_comparison.get("status") or "not_available",
            "primary_metric": model_strategy_comparison.get("primary_metric") or "",
            "best_baseline_strategy": model_strategy_comparison.get("best_baseline_strategy") or "",
            "agent_minus_best_baseline": model_strategy_comparison.get("agent_minus_best_baseline"),
            "interpretation": model_strategy_comparison.get("interpretation") or "not_available",
        },
        "active_curation": {
            "row_count": len(combined_curation_rows),
            "recipe_row_count": len(curation_rows),
            "model_informed_row_count": len(model_curation_rows),
            "top_items": combined_curation_rows[:20],
            "efficiency": {
                "status": curation_efficiency.get("status") or "not_available",
                "manual_only_review_count": curation_efficiency.get("manual_only_review_count") or 0,
                "agent_assisted_review_count": curation_efficiency.get("agent_assisted_review_count") or 0,
                "review_reduction_rate": curation_efficiency.get("review_reduction_rate") or 0,
                "critical_issue_coverage": curation_efficiency.get("critical_issue_coverage") or {},
            },
            "memory_update": {
                "status": curation_memory_update.get("status") or "not_available",
                "imported_decision_count": curation_memory_update.get("imported_decision_count") or 0,
                "skipped_count": curation_memory_update.get("skipped_count") or 0,
                "memory_summary": curation_memory_update.get("memory_summary") or {},
            },
        },
        "evidence_graph": {
            "node_count": len(evidence_graph.get("nodes") or []),
            "edge_count": len(evidence_graph.get("edges") or []),
            "node_type_counts": _node_type_counts(evidence_graph),
            "model_informed_request_node_count": len(model_requests),
            "model_informed_curation_edge_count": len(model_curation_rows),
        },
        "benchmark_summary": benchmark_summary,
        "guidance_alignment": _guidance_alignment_summary(guidance_alignment),
        "warnings": warnings,
    }

    files = {
        "real_data_scientist_agent_report_md": str(output_dir / "real_data_scientist_agent_report.md"),
        "real_data_scientist_agent_summary_json": str(output_dir / "real_data_scientist_agent_summary.json"),
    }
    write_json(files["real_data_scientist_agent_summary_json"], summary)
    Path(files["real_data_scientist_agent_report_md"]).write_text(_markdown_report(summary), encoding="utf-8")
    return DataScientistAgentReportResult(
        status=str(summary["status"]),
        recipe_dir=str(recipe_dir),
        output_dir=str(output_dir),
        selected_count=len(selected),
        excluded_count=len(excluded),
        leakage_status=str(summary["leakage"]["status"]),
        hard_benchmark_count=len(hard_rows),
        curation_queue_count=len(combined_curation_rows),
        model_loop_status=str(summary["model_loop"]["status"]),
        guidance_alignment_status=str(summary["guidance_alignment"]["status"]),
        gap_action_count=len(recipe_actions) + len(model_actions),
        warnings=warnings,
        files=files,
    )


def _missing_artifact_warnings(artifacts: dict[str, dict[str, Any]]) -> list[str]:
    return [f"{name}_missing" for name, payload in artifacts.items() if not payload]


def _count_hard_tags(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for tag in row.get("tags") or []:
            key = str(tag)
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _count_values(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _model_discovery_request_curation_rows(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for request in requests:
        if not isinstance(request, dict):
            continue
        priority = _model_request_priority_score(str(request.get("priority") or "medium"))
        constraints = request.get("constraints") if isinstance(request.get("constraints"), dict) else {}
        rows.append(
            {
                "curation_type": "review_model_informed_discovery_request",
                "action": "review_or_run_discovery",
                "reason": "model_failure_mode_requires_data_expansion",
                "selection": "model_informed_expansion",
                "priority_score": priority,
                "request_id": request.get("request_id") or "",
                "dimension": request.get("dimension") or "",
                "target": request.get("target") or "",
                "query": request.get("query") or "",
                "repository": request.get("repository") or "auto",
                "repositories": ";".join(map(str, request.get("repositories") or [])),
                "task_type": request.get("task_type") or "",
                "requires_user_confirmation": bool(request.get("requires_user_confirmation", True)),
                "max_file_size_mb": constraints.get("max_file_size_mb"),
                "species_policy": constraints.get("species_policy") or "",
                "suggested_cli": request.get("suggested_cli") or "",
            }
        )
    return rows


def _model_request_priority_score(priority: str) -> float:
    key = priority.casefold()
    if key == "high":
        return 0.92
    if key == "medium":
        return 0.78
    if key == "low":
        return 0.55
    return 0.7


def _safe_float(value: Any) -> float:
    try:
        if value in {"", None}:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _node_type_counts(evidence_graph: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for node in evidence_graph.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        key = str(node.get("type") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _discovery_summary(
    discovery_payload: dict[str, Any],
    task_readiness_matrix: dict[str, Any],
    data_value_ranking: dict[str, Any],
    data_value_strategy_eval: dict[str, Any],
    repository_audit: dict[str, Any],
) -> dict[str, Any]:
    files = discovery_payload.get("files") if isinstance(discovery_payload.get("files"), list) else []
    rows = task_readiness_matrix.get("rows") if isinstance(task_readiness_matrix.get("rows"), list) else []
    value_rows = data_value_ranking.get("rows") if isinstance(data_value_ranking.get("rows"), list) else []
    audit_rows = repository_audit.get("rows") if isinstance(repository_audit.get("rows"), list) else []
    readiness_by_task: dict[str, dict[str, int]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        task = str(row.get("task_type") or "unknown")
        band = str(row.get("task_ai_readiness_band") or row.get("task_readiness_status") or "unknown")
        readiness_by_task.setdefault(task, {})
        readiness_by_task[task][band] = readiness_by_task[task].get(band, 0) + 1
    top_value = [
        {
            "project_accession": row.get("project_accession") or "",
            "file_name": row.get("file_name") or "",
            "task_type": row.get("task_type") or "",
            "data_value_score": row.get("data_value_score") or "",
            "data_value_action": row.get("data_value_action") or "",
        }
        for row in value_rows[:10]
        if isinstance(row, dict)
    ]
    return {
        "available": bool(discovery_payload),
        "candidate_count": len(files),
        "task_readiness_matrix_available": bool(rows),
        "task_types": task_readiness_matrix.get("task_types") or [],
        "readiness_by_task": readiness_by_task,
        "data_value_ranking_available": bool(value_rows),
        "top_data_value_candidates": top_value,
        "data_value_strategy_eval": {
            "available": bool(data_value_strategy_eval),
            "interpretation": data_value_strategy_eval.get("interpretation") or "",
            "agent_minus_best_baseline": data_value_strategy_eval.get("agent_minus_best_baseline"),
            "best_baseline_strategy": data_value_strategy_eval.get("best_baseline_strategy") or "",
        },
        "repository_audit": {
            "available": bool(repository_audit),
            "repositories_attempted": repository_audit.get("repositories_attempted") or [],
            "repository_counts": repository_audit.get("repository_counts") or {},
            "status_counts": _count_values([str(row.get("status") or "unknown") for row in audit_rows if isinstance(row, dict)]),
            "blocked_repositories": [
                {
                    "repository": row.get("repository") or "unknown",
                    "blocker": row.get("blocker") or "",
                    "next_step": row.get("next_step") or "",
                }
                for row in audit_rows
                if isinstance(row, dict) and str(row.get("status") or "") == "blocked"
            ],
            "rows": audit_rows[:20],
        },
    }


def _guidance_alignment_summary(guidance_alignment: dict[str, Any]) -> dict[str, Any]:
    summary = guidance_alignment.get("summary") if isinstance(guidance_alignment.get("summary"), dict) else {}
    requirements = guidance_alignment.get("requirements") if isinstance(guidance_alignment.get("requirements"), list) else []
    requirement_rows = [row for row in requirements if isinstance(row, dict)]
    partial_or_missing = [
        {
            "name": row.get("name") or "",
            "status": row.get("status") or "missing",
            "remaining_work": row.get("remaining_work") or [],
        }
        for row in requirement_rows
        if str(row.get("status") or "missing") != "achieved"
    ]
    return {
        "available": bool(guidance_alignment),
        "status": guidance_alignment.get("status") or "not_available",
        "achieved_count": int(summary.get("achieved") or 0),
        "partial_count": int(summary.get("partial") or 0),
        "missing_count": int(summary.get("missing") or 0),
        "requirement_count": len(requirement_rows),
        "partial_or_missing_requirements": partial_or_missing[:20],
    }


def _markdown_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Real Data Scientist Agent Report",
        "",
        f"- Status: `{summary.get('status')}`",
        f"- Selected task outputs: {summary.get('selected_count', 0)}",
        f"- Excluded task outputs: {summary.get('excluded_count', 0)}",
        f"- Repository summary: `{json.dumps(summary.get('repository_summary') or {}, ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Guidance Alignment",
        "",
        f"- Alignment status: `{(summary.get('guidance_alignment') or {}).get('status')}`",
        f"- Achieved / partial / missing: "
        f"{(summary.get('guidance_alignment') or {}).get('achieved_count', 0)} / "
        f"{(summary.get('guidance_alignment') or {}).get('partial_count', 0)} / "
        f"{(summary.get('guidance_alignment') or {}).get('missing_count', 0)}",
        "",
        "## Discovery And Data Value",
        "",
        f"- Discovery candidates: {(summary.get('discovery') or {}).get('candidate_count', 0)}",
        f"- Task types scored: `{', '.join(map(str, (summary.get('discovery') or {}).get('task_types') or []))}`",
        f"- Data-value strategy eval: `{((summary.get('discovery') or {}).get('data_value_strategy_eval') or {}).get('interpretation') or 'not_available'}`",
        f"- Agent data-value delta: `{((summary.get('discovery') or {}).get('data_value_strategy_eval') or {}).get('agent_minus_best_baseline')}`",
        f"- Repository audit: `{json.dumps(((summary.get('discovery') or {}).get('repository_audit') or {}).get('status_counts') or {}, ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Split And Leakage",
        "",
        f"- Split: `{(summary.get('split') or {}).get('resolved_strategy')}`",
        f"- Split counts: `{json.dumps((summary.get('split') or {}).get('counts') or {}, ensure_ascii=False, sort_keys=True)}`",
        f"- Leakage status: `{(summary.get('leakage') or {}).get('status')}`",
        f"- Leakage issues: `{json.dumps((summary.get('leakage') or {}).get('issue_counts') or {}, ensure_ascii=False, sort_keys=True)}`",
        f"- Split baseline: `{(summary.get('split_baseline_evaluation') or {}).get('interpretation')}`",
        f"- Agent vs random leakage delta: {(summary.get('split_baseline_evaluation') or {}).get('agent_minus_random_leakage', 0)}",
        "",
        "## Hard Benchmark",
        "",
        f"- Hard rows: {(summary.get('hard_benchmark') or {}).get('row_count', 0)}",
        f"- Tags: `{json.dumps((summary.get('hard_benchmark') or {}).get('tag_counts') or {}, ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Counterfactual Benchmark",
        "",
        f"- Counterfactual rows: {(summary.get('counterfactual_benchmark') or {}).get('row_count', 0)}",
        f"- Case types: `{json.dumps((summary.get('counterfactual_benchmark') or {}).get('case_type_counts') or {}, ensure_ascii=False, sort_keys=True)}`",
        f"- Tags: `{json.dumps((summary.get('counterfactual_benchmark') or {}).get('tag_counts') or {}, ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Model Loop",
        "",
        f"- Model-loop status: `{(summary.get('model_loop') or {}).get('status')}`",
        f"- Adapter: `{(summary.get('model_loop') or {}).get('adapter')}`",
        f"- Adapter status: `{(summary.get('model_loop') or {}).get('adapter_status')}`",
        f"- Metric status: `{(summary.get('model_loop') or {}).get('metric_status')}`",
        f"- Adapter contract: `{(((summary.get('model_loop') or {}).get('adapter_contract') or {}).get('schema_version') or 'missing')}`",
        f"- Adapter input rows: `{(((summary.get('model_loop') or {}).get('adapter_contract') or {}).get('input_selected_count'))}`",
        f"- Failure modes: {(summary.get('model_loop') or {}).get('failure_mode_count', 0)}",
        f"- Model-informed gaps: {(summary.get('model_loop') or {}).get('model_gap_count', 0)}",
        f"- Model-informed discovery requests: {(summary.get('model_loop') or {}).get('model_informed_discovery_request_count', 0)}",
        f"- Ready discovery payloads: {(summary.get('model_loop') or {}).get('model_informed_discovery_payload_count', 0)}",
        f"- Discovery payload queue: ready `{(summary.get('model_loop') or {}).get('model_informed_payload_queue_ready_count', 0)}`, review `{(summary.get('model_loop') or {}).get('model_informed_payload_queue_review_count', 0)}`, blocked `{(summary.get('model_loop') or {}).get('model_informed_payload_queue_blocked_count', 0)}`",
        f"- Model-informed repositories: `{', '.join(map(str, ((summary.get('model_loop') or {}).get('model_informed_repository_plan') or {}).get('planned_repositories') or [])) or 'not_available'}`",
        f"- Strategy comparison: `{(summary.get('model_strategy_comparison') or {}).get('interpretation')}`",
        f"- Agent vs best baseline metric delta: `{(summary.get('model_strategy_comparison') or {}).get('agent_minus_best_baseline')}`",
        "",
        "## Gap-Aware Expansion",
        "",
    ]
    actions = (summary.get("coverage_gap") or {}).get("actions") or []
    model_actions = (summary.get("model_loop") or {}).get("model_informed_actions") or []
    model_requests = (summary.get("model_loop") or {}).get("model_informed_discovery_requests") or []
    blocked_repositories = ((summary.get("discovery") or {}).get("repository_audit") or {}).get("blocked_repositories") or []
    if blocked_repositories:
        lines.extend(["", "### Repository Blockers", ""])
        for row in blocked_repositories:
            lines.append(
                f"- `{row.get('repository') or 'unknown'}` blocked by `{row.get('blocker') or 'unknown'}`; "
                f"next step: `{row.get('next_step') or 'review_repository_discovery_failure'}`"
            )
    if not actions and not model_actions:
        lines.append("- No expansion action proposed.")
    for action in actions:
        lines.append(f"- Dataset gap: `{action.get('action')}` {action.get('dimension') or action.get('reason') or ''} {action.get('query_hint') or ''}")
    for action in model_actions:
        lines.append(f"- Model-informed gap: `{action.get('action')}` {action.get('target') or action.get('query_hint') or ''}")
    for request in model_requests[:10]:
        lines.append(f"- Discovery request: `{request.get('request_id')}` {request.get('query')} ({request.get('repository')})")
    lines.extend(["", "## Active Curation", ""])
    efficiency = (summary.get("active_curation") or {}).get("efficiency") or {}
    lines.append(f"- Review reduction rate: `{efficiency.get('review_reduction_rate', 0)}`")
    lines.append(f"- Agent-assisted review count: {efficiency.get('agent_assisted_review_count', 0)} / manual-only {efficiency.get('manual_only_review_count', 0)}")
    lines.append(f"- Critical issue coverage: `{json.dumps(efficiency.get('critical_issue_coverage') or {}, ensure_ascii=False, sort_keys=True)}`")
    memory_update = (summary.get("active_curation") or {}).get("memory_update") or {}
    lines.append(
        f"- Memory write-back: `{memory_update.get('status') or 'not_available'}`, "
        f"imported {memory_update.get('imported_decision_count', 0)}, skipped {memory_update.get('skipped_count', 0)}"
    )
    for item in (summary.get("active_curation") or {}).get("top_items") or []:
        subject = item.get("source_file") or item.get("run_name") or item.get("request_id") or item.get("query") or "curation_item"
        lines.append(
            f"- `{item.get('curation_type')}` priority {item.get('priority_score')}: "
            f"{subject} ({item.get('reason')})"
        )
    if not (summary.get("active_curation") or {}).get("top_items"):
        lines.append("- No curation item.")
    lines.extend(["", "## Evidence Graph", ""])
    lines.append(f"- Nodes: {(summary.get('evidence_graph') or {}).get('node_count', 0)}")
    lines.append(f"- Edges: {(summary.get('evidence_graph') or {}).get('edge_count', 0)}")
    lines.append(f"- Model-informed request nodes: {(summary.get('evidence_graph') or {}).get('model_informed_request_node_count', 0)}")
    lines.append(f"- Node types: `{json.dumps((summary.get('evidence_graph') or {}).get('node_type_counts') or {}, ensure_ascii=False, sort_keys=True)}`")
    warnings = summary.get("warnings") or []
    if warnings:
        lines.extend(["", "## Warnings", ""])
        for warning in warnings:
            lines.append(f"- {warning}")
    alignment_gaps = (summary.get("guidance_alignment") or {}).get("partial_or_missing_requirements") or []
    if alignment_gaps:
        lines.extend(["", "## Remaining Alignment Work", ""])
        for item in alignment_gaps:
            remaining = "; ".join(map(str, item.get("remaining_work") or [])) or "review requirement evidence"
            lines.append(f"- `{item.get('name')}` is `{item.get('status')}`: {remaining}")
    return "\n".join(lines) + "\n"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}
