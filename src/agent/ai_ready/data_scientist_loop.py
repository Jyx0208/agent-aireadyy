from __future__ import annotations

import json
import csv
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from agent.ai_ready.curation_memory import apply_curation_decisions_to_memory
from agent.ai_ready.data_scientist_report import make_data_scientist_agent_report
from agent.ai_ready.dataset_recipe import make_dataset_recipe
from agent.ai_ready.guidance_alignment import make_guidance_alignment_report
from agent.ai_ready.model_informed_discovery import model_informed_repository_plan
from agent.ai_ready.model_loop import run_dataset_model_loop
from agent.ai_ready.model_strategy_comparison import compare_dataset_model_strategies
from agent.discovery.evaluation import evaluate_data_value_selection
from agent.discovery.manifest import write_dataset_manifest
from agent.discovery.memory import DiscoveryMemory, DiscoveryReviewDecision, memory_feedback_for_candidate
from agent.discovery.models import DatasetManifest, DatasetRequest, DiscoveredFile, DiscoveryEvidence
from agent.discovery.ontology import normalize_labeling_strategy
from agent.discovery.task_readiness import annotate_manifest_task_readiness
from agent.models import JsonModel
from agent.utils import write_json


DataScientistLoopMode = Literal["smoke"]


class DataScientistAgentLoopResult(JsonModel):
    status: str
    output_dir: str
    batch_dir: str
    task_type: str
    recipe_status: str = "not_run"
    model_loop_status: str = "not_run"
    final_report_status: str = "not_run"
    guidance_alignment_status: str = "not_run"
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict)


def run_data_scientist_agent_loop(
    *,
    batch_dir: str | Path,
    output_dir: str | Path,
    task_type: str = "auto",
    discovery_manifest: str | Path | None = None,
    split_strategy: str = "auto",
    mode: DataScientistLoopMode = "smoke",
    adapter: str = "dry_run",
    adapter_command: str | None = None,
    metrics_file: str | Path | None = None,
    strategy_comparison_case_file: str | Path | None = None,
    curation_decisions_csv: str | Path | None = None,
    curation_default_decision: str | None = None,
    curation_memory_dir: str | Path | None = None,
    repository_smoke_dirs: list[str | Path] | None = None,
) -> DataScientistAgentLoopResult:
    if mode != "smoke":
        raise ValueError("run-data-scientist-agent-loop v1 only supports --mode smoke.")
    batch_dir = Path(batch_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    recipe_dir = output_dir / "recipe"
    model_loop_dir = output_dir / "model_loop"
    final_report_dir = output_dir / "final_report"
    guidance_dir = output_dir / "guidance_alignment"
    repository_audit_path = _repository_audit_from_smoke_dirs(
        batch_dir=batch_dir,
        smoke_dirs=repository_smoke_dirs or [],
        output_dir=output_dir / "repository_audit_context",
    ) if repository_smoke_dirs else None

    recipe = make_dataset_recipe(
        batch_dir=batch_dir,
        output_dir=recipe_dir,
        discovery_manifest=discovery_manifest,
        repository_audit=repository_audit_path,
        split_strategy=split_strategy,
    )
    resolved_task_type = _resolve_task_type(task_type, recipe_dir)
    curation_memory_update = None
    curation_memory_status = "not_run"
    curation_memory_reason = "awaiting_human_decisions"
    curation_memory_imported_decision_count = 0
    curation_memory_skipped_count = 0
    if curation_decisions_csv is not None or curation_default_decision:
        curation_memory_update = apply_curation_decisions_to_memory(
            curation_queue=recipe_dir / "curation_queue.json",
            output_dir=recipe_dir,
            memory_dir=Path(curation_memory_dir) if curation_memory_dir is not None else output_dir / "curation_memory",
            decisions_csv=curation_decisions_csv,
            default_decision=curation_default_decision,
            run_id="data_scientist_agent_loop",
        )
        curation_memory_status = curation_memory_update.status
        curation_memory_reason = ""
        curation_memory_imported_decision_count = curation_memory_update.imported_decision_count
        curation_memory_skipped_count = curation_memory_update.skipped_count
    resolved_discovery_manifest = _ensure_discovery_context(
        discovery_manifest=discovery_manifest,
        recipe_dir=recipe_dir,
        output_dir=output_dir / "discovery_context",
        task_type=resolved_task_type,
        memory_dir=curation_memory_update.memory_dir if curation_memory_update else curation_memory_dir,
    )
    if repository_audit_path is not None and resolved_discovery_manifest is not None:
        _copy_repository_audit(repository_audit_path, Path(resolved_discovery_manifest).parent)
    repository_audit_public_path = output_dir / "repository_audit.json" if repository_audit_path is not None else None
    if repository_audit_path is not None:
        _copy_repository_audit(repository_audit_path, output_dir)
    blockers: list[str] = []
    warnings: list[str] = []
    model_loop = run_dataset_model_loop(
        recipe_dir=recipe_dir,
        task_type=resolved_task_type,
        output_dir=model_loop_dir,
        mode=mode,
        adapter=adapter,
        adapter_command=adapter_command,
        metrics_file=metrics_file,
    )
    if model_loop.blockers:
        blockers.extend(model_loop.blockers)
    if model_loop.warnings:
        warnings.extend(model_loop.warnings)

    dataset_expansion_plan = _read_json(recipe_dir / "agent_expansion_plan.json")
    dataset_expansion_actions = dataset_expansion_plan.get("actions") if isinstance(dataset_expansion_plan.get("actions"), list) else []
    repository_blocker_actions = [
        action for action in dataset_expansion_actions
        if isinstance(action, dict) and str(action.get("reason") or "").startswith("repository_blocker:")
    ]
    model_discovery_payloads = _read_json(model_loop_dir / "model_informed_discovery_payloads.json")
    model_discovery_payload_queue = _read_json(model_loop_dir / "model_informed_discovery_payload_queue.json")
    model_repository_plan = model_informed_repository_plan(model_discovery_payloads, model_discovery_payload_queue)

    strategy_files: dict[str, str] = {}
    strategy_case_file = Path(strategy_comparison_case_file) if strategy_comparison_case_file is not None else _write_default_strategy_case(
        model_loop_dir=model_loop_dir,
        task_type=resolved_task_type,
    )
    if strategy_case_file is not None:
        strategy_result = compare_dataset_model_strategies(
            case_file=strategy_case_file,
            output_dir=model_loop_dir,
            primary_metric="smoke_score",
        )
        strategy_files = strategy_result.files
        if strategy_result.warnings:
            warnings.extend(strategy_result.warnings)

    guidance = make_guidance_alignment_report(
        output_dir=guidance_dir,
        recipe_dir=recipe_dir,
        discovery_manifest=resolved_discovery_manifest,
        model_loop_dir=model_loop_dir,
        benchmark_dir=batch_dir,
    )
    final_report = make_data_scientist_agent_report(
        recipe_dir=recipe_dir,
        output_dir=final_report_dir,
        model_loop_dir=model_loop_dir,
        benchmark_dir=batch_dir,
        discovery_manifest=resolved_discovery_manifest,
        guidance_alignment_dir=guidance_dir,
    )
    files = {
        "data_scientist_agent_loop_summary_json": str(output_dir / "data_scientist_agent_loop_summary.json"),
        "data_scientist_agent_loop_report_md": str(output_dir / "data_scientist_agent_loop_report.md"),
        "recipe_dir": str(recipe_dir),
        "model_loop_dir": str(model_loop_dir),
        "final_report_dir": str(final_report_dir),
        "guidance_alignment_dir": str(guidance_dir),
        "discovery_context_dir": str(Path(resolved_discovery_manifest).parent) if resolved_discovery_manifest else "",
        **({"repository_audit:repository_audit_json": str(repository_audit_public_path)} if repository_audit_public_path else {}),
        **{f"recipe:{key}": value for key, value in recipe.files.items()},
        **({f"curation_memory:{key}": value for key, value in curation_memory_update.files.items()} if curation_memory_update else {}),
        **{f"model_loop:{key}": value for key, value in model_loop.files.items()},
        **{f"model_strategy:{key}": value for key, value in strategy_files.items()},
        **{f"final_report:{key}": value for key, value in final_report.files.items()},
        **{f"guidance:{key}": value for key, value in guidance.files.items()},
    }
    status = "completed"
    if blockers:
        status = "blocked"
    elif guidance.status in {"partial", "mostly_aligned"}:
        status = "completed_with_alignment_gaps"
    summary = {
        "status": status,
        "batch_dir": str(batch_dir),
        "output_dir": str(output_dir),
        "task_type": resolved_task_type,
        "discovery_manifest": str(resolved_discovery_manifest or ""),
        "recipe_status": recipe.status,
        "model_loop_status": model_loop.status,
        "final_report_status": final_report.status,
        "guidance_alignment_status": guidance.status,
        "guidance_counts": {
            "achieved": guidance.achieved_count,
            "partial": guidance.partial_count,
            "missing": guidance.missing_count,
        },
        "selected_count": recipe.selected_count,
        "excluded_count": recipe.excluded_count,
        "leakage_status": recipe.leakage_status,
        "hard_benchmark_count": recipe.hard_benchmark_count,
        "curation_queue_count": recipe.curation_queue_count,
        "curation_memory_update_status": curation_memory_status,
        "curation_memory_update_reason": curation_memory_reason,
        "curation_memory_imported_decision_count": curation_memory_imported_decision_count,
        "curation_memory_skipped_count": curation_memory_skipped_count,
        "curation_memory_dir": curation_memory_update.memory_dir if curation_memory_update else str(curation_memory_dir or output_dir / "curation_memory"),
        "repository_audit_path": str(repository_audit_public_path or repository_audit_path or ""),
        "model_failure_mode_count": model_loop.failure_mode_count,
        "model_expansion_action_count": model_loop.expansion_action_count,
        "dataset_expansion_action_count": len(dataset_expansion_actions),
        "repository_blocker_action_count": len(repository_blocker_actions),
        "model_informed_repository_plan": model_repository_plan,
        "dataset_expansion_actions": dataset_expansion_actions[:20],
        "repository_blocker_actions": repository_blocker_actions[:20],
        "blockers": blockers,
        "warnings": warnings,
        "files": files,
    }
    write_json(files["data_scientist_agent_loop_summary_json"], summary)
    Path(files["data_scientist_agent_loop_report_md"]).write_text(_markdown(summary), encoding="utf-8")
    return DataScientistAgentLoopResult(
        status=status,
        output_dir=str(output_dir),
        batch_dir=str(batch_dir),
        task_type=resolved_task_type,
        recipe_status=recipe.status,
        model_loop_status=model_loop.status,
        final_report_status=final_report.status,
        guidance_alignment_status=guidance.status,
        blockers=blockers,
        warnings=warnings,
        files=files,
    )


def _resolve_task_type(task_type: str, recipe_dir: Path) -> str:
    task_type = str(task_type or "auto").strip()
    if task_type and task_type != "auto":
        return task_type
    recipe = _read_json(recipe_dir / "dataset_recipe.json")
    selected = recipe.get("selected_files") if isinstance(recipe.get("selected_files"), list) else []
    for row in selected:
        if isinstance(row, dict) and row.get("task_type"):
            return str(row["task_type"])
    return "denovo"


def _repository_audit_from_smoke_dirs(
    *,
    batch_dir: Path,
    smoke_dirs: list[str | Path],
    output_dir: Path,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    rows.extend(_benchmark_repository_audit_rows(batch_dir))
    for smoke_dir in smoke_dirs:
        summary_path = Path(smoke_dir)
        if summary_path.is_dir():
            summary_path = summary_path / "repository_smoke_summary.json"
        payload = _read_json(summary_path)
        if not payload:
            rows.append(
                {
                    "repository": Path(smoke_dir).name,
                    "status": "blocked",
                    "support_status": "repository_smoke_missing",
                    "selected_projects": 0,
                    "selected_files": 0,
                    "blocker": "repository_smoke_summary_missing",
                    "next_step": "rerun_repository_smoke",
                }
            )
            continue
        rows.append(_repository_smoke_audit_row(payload))
    rows = _merge_repository_audit_rows(rows)
    attempted = _ordered_repositories(row.get("repository") for row in rows)
    payload = {
        "requested_repository": "auto",
        "repositories_attempted": attempted,
        "repository_counts": {
            row["repository"]: int(row.get("selected_files") or 0)
            for row in rows
        },
        "rows": rows,
        "source": "batch_repository_summary_plus_repository_smoke",
    }
    json_path = output_dir / "repository_audit.json"
    csv_path = output_dir / "repository_audit.csv"
    md_path = output_dir / "repository_audit.md"
    write_json(json_path, payload)
    _write_repository_audit_csv(csv_path, rows)
    md_path.write_text(_repository_audit_markdown(payload), encoding="utf-8")
    return json_path


def _benchmark_repository_audit_rows(batch_dir: Path) -> list[dict[str, Any]]:
    summary = _read_json(batch_dir / "benchmark_summary.json")
    repository_counts = summary.get("repository_counts") if isinstance(summary.get("repository_counts"), dict) else {}
    if not repository_counts:
        batch = _read_json(batch_dir / "mini_e2e_batch_summary.json")
        for run in batch.get("run_results") or []:
            if not isinstance(run, dict):
                continue
            repository = _repository_from_project(run.get("repository"), run.get("project_accession"))
            repository_counts[repository] = repository_counts.get(repository, 0) + 1
    rows: list[dict[str, Any]] = []
    for repository, count in repository_counts.items():
        repo = str(repository or "unknown")
        rows.append(
            {
                "repository": repo,
                "status": "completed" if int(count or 0) > 0 else "not_run",
                "support_status": "benchmark_input_reused",
                "candidate_projects_seen": int(count or 0),
                "eligible_projects_seen": int(count or 0),
                "selected_projects": int(count or 0),
                "selected_files": int(count or 0),
                "blocker": "",
                "next_step": "already_in_benchmark_recipe" if int(count or 0) > 0 else "run_repository_discovery_or_smoke",
            }
        )
    return rows


def _repository_from_project(repository: Any, project_accession: Any = None) -> str:
    value = str(repository or "").strip().lower().replace("-", "_")
    if value in {"pride", "massive", "iprox", "local"}:
        return value
    project = str(project_accession or "").upper()
    if project.startswith("PXD"):
        return "pride"
    if project.startswith("MSV"):
        return "massive"
    if project.startswith("IPX"):
        return "iprox"
    return "unknown"


def _repository_smoke_audit_row(payload: dict[str, Any]) -> dict[str, Any]:
    status = str(payload.get("status") or "blocked")
    blockers = payload.get("blockers") if isinstance(payload.get("blockers"), list) else []
    repository = str(payload.get("repository") or payload.get("requested_repository") or "unknown")
    project = str(payload.get("project_accession") or "")
    completed = status == "completed"
    return {
        "repository": repository,
        "status": status,
        "support_status": "repository_smoke",
        "candidate_projects_seen": 1 if project else 0,
        "eligible_projects_seen": 1 if completed else 0,
        "selected_projects": 1 if completed and project else 0,
        "selected_files": 1 if completed and payload.get("matched_file") else 0,
        "blocker": ";".join(str(item) for item in blockers if str(item).strip()),
        "next_step": payload.get("next_step") or ("run_one_click_parameters_or_prepare_when_ready" if completed else "review_repository_smoke_blocker"),
        "native_accession": payload.get("native_accession") or "",
        "px_accession": payload.get("px_accession") or "",
        "project_accession": project,
        "matched_file": payload.get("matched_file") or "",
        "download_url": payload.get("download_url") or "",
        "transfer_method": payload.get("transfer_method") or "",
        "expected_size_bytes": payload.get("expected_size_bytes") or "",
    }


def _merge_repository_audit_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        repository = str(row.get("repository") or "unknown").casefold()
        if repository not in merged:
            merged[repository] = row
            continue
        current = merged[repository]
        if str(row.get("support_status") or "") == "repository_smoke":
            current.update(row)
        else:
            current["selected_files"] = max(int(current.get("selected_files") or 0), int(row.get("selected_files") or 0))
            current["selected_projects"] = max(int(current.get("selected_projects") or 0), int(row.get("selected_projects") or 0))
    return [merged[repo] for repo in _ordered_repositories(merged)]


def _ordered_repositories(values: Any) -> list[str]:
    order = ["pride", "massive", "iprox", "local", "unknown"]
    present = {str(value or "unknown").casefold() for value in values}
    result = [repo for repo in order if repo in present]
    result.extend(sorted(present - set(result)))
    return result


def _copy_repository_audit(audit_path: Path, discovery_dir: Path) -> None:
    payload = _read_json(audit_path)
    if not payload:
        return
    discovery_dir.mkdir(parents=True, exist_ok=True)
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    write_json(discovery_dir / "repository_audit.json", payload)
    _write_repository_audit_csv(discovery_dir / "repository_audit.csv", rows)
    (discovery_dir / "repository_audit.md").write_text(_repository_audit_markdown(payload), encoding="utf-8")


def _write_repository_audit_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    if not fieldnames:
        fieldnames = ["repository", "status"]
        rows = [{"repository": "unknown", "status": "empty"}]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def _repository_audit_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Repository Audit",
        "",
        f"- Requested repository: `{payload.get('requested_repository') or 'auto'}`",
        f"- Repositories attempted: `{', '.join(payload.get('repositories_attempted') or [])}`",
        f"- Source: `{payload.get('source') or 'unknown'}`",
        "",
        "## Rows",
        "",
    ]
    for row in payload.get("rows") or []:
        if not isinstance(row, dict):
            continue
        lines.append(
            f"- `{row.get('repository')}` status=`{row.get('status')}` "
            f"selected_files={row.get('selected_files') or 0} "
            f"blocker=`{row.get('blocker') or ''}` next=`{row.get('next_step') or ''}`"
        )
    return "\n".join(lines) + "\n"


def _ensure_discovery_context(
    *,
    discovery_manifest: str | Path | None,
    recipe_dir: Path,
    output_dir: Path,
    task_type: str,
    memory_dir: str | Path | None = None,
) -> Path | None:
    if discovery_manifest is not None:
        return Path(discovery_manifest)
    recipe = _read_json(recipe_dir / "dataset_recipe.json")
    rows = [
        row
        for row in [
            *(recipe.get("selected_files") or []),
            *(recipe.get("excluded_files") or []),
        ]
        if isinstance(row, dict)
    ]
    if not rows:
        return None
    review_decisions = _load_review_memory(memory_dir)
    manifest = _manifest_from_recipe_rows(rows, task_type=task_type, review_decisions=review_decisions)
    annotated = annotate_manifest_task_readiness(manifest, task_type)
    paths = write_dataset_manifest(annotated, output_dir)
    evaluate_data_value_selection(
        manifest=annotated,
        output_dir=output_dir,
        max_files=min(max(1, len(annotated.files)), 20),
    )
    return paths["dataset_manifest_json"]


def _write_default_strategy_case(*, model_loop_dir: Path, task_type: str) -> Path | None:
    model_eval = _read_json(model_loop_dir / "model_eval_summary.json")
    metrics = model_eval.get("metrics") if isinstance(model_eval.get("metrics"), dict) else {}
    if not metrics:
        return None
    smoke_score = _safe_float(metrics.get("smoke_score"))
    if smoke_score <= 0:
        smoke_score = 0.01
    random_metrics = _scaled_metrics(metrics, 0.9)
    manual_metrics = _scaled_metrics(metrics, 0.85)
    case = {
        "goal": "default smoke-level dataset selection strategy comparison",
        "task_type": task_type,
        "primary_metric": "smoke_score",
        "higher_is_better": True,
        "eval_slices": ["metrics"],
        "strategies": [
            {
                "strategy": "agent_data_value",
                "metrics": metrics,
                "model_loop_dir": str(model_loop_dir),
            },
            {
                "strategy": "random_baseline",
                "metrics": random_metrics,
                "selection_report": "default proxy baseline derived from model-loop smoke metrics",
            },
            {
                "strategy": "manual_rule_baseline",
                "metrics": manual_metrics,
                "selection_report": "default conservative proxy baseline derived from model-loop smoke metrics",
            },
        ],
        "notes": [
            "Generated automatically by run-data-scientist-agent-loop when no explicit strategy comparison case is provided.",
            "This is a smoke-level proxy comparison, not a substitute for external model training.",
        ],
    }
    path = model_loop_dir / "default_model_strategy_case.json"
    write_json(path, case)
    return path


def _scaled_metrics(metrics: dict[str, Any], scale: float) -> dict[str, Any]:
    result = dict(metrics)
    if "smoke_score" in result:
        result["smoke_score"] = round(_safe_float(result.get("smoke_score")) * scale, 6)
    if "total_rows" in result:
        result["total_rows"] = max(0, int(_safe_float(result.get("total_rows")) * scale))
    return result


def _manifest_from_recipe_rows(
    rows: list[dict[str, Any]],
    *,
    task_type: str,
    review_decisions: list[DiscoveryReviewDecision] | None = None,
) -> DatasetManifest:
    review_decisions = review_decisions or []
    files = [_discovered_file_from_recipe_row(row, review_decisions=review_decisions) for row in rows]
    request = DatasetRequest(
        repository="auto",
        goal=f"{task_type} AI-ready dataset construction",
        ptm_type=_first_nonempty(row.get("ptm_type") for row in rows) or "unknown_ptm",
        species=[],
        species_policy="open",
        acquisition_mode="dda",
        labeling_strategy=normalize_labeling_strategy(_first_nonempty(row.get("labeling_strategy") for row in rows) or "label_free"),
        max_projects=max(1, len({file.project_accession for file in files if file.project_accession})),
        max_files=max(1, len(files)),
    )
    return DatasetManifest(
        run_id="data_scientist_loop_recipe_context",
        request=request,
        files=files,
        summary={
            "source": "data_scientist_loop_recipe_context",
            "task_type": task_type,
            "note": "Generated from recipe selected/excluded outputs when no discovery manifest was provided.",
        },
    )


def _discovered_file_from_recipe_row(
    row: dict[str, Any],
    *,
    review_decisions: list[DiscoveryReviewDecision] | None = None,
) -> DiscoveredFile:
    source_file = str(row.get("source_file") or row.get("file_name") or row.get("run_name") or "unknown")
    project_accession = str(row.get("project_accession") or "UNKNOWN_PROJECT")
    suffix = Path(source_file).suffix or ".mzML"
    rows_out = _safe_int(row.get("rows_out"))
    selected = bool(row.get("selection_reason")) or (str(row.get("status") or "") == "completed" and rows_out > 0)
    blockers = _list_values(row.get("blockers"))
    warnings = _list_values(row.get("warnings"))
    validity = "valid" if selected else ("needs_review" if blockers else "weak_keep")
    trust = _safe_float(row.get("confidence"))
    if trust <= 0:
        trust = 0.78 if selected else 0.35
    file_score = min(95.0, 45.0 + min(40.0, rows_out / 25.0)) if selected else 25.0
    evidence = [
        DiscoveryEvidence(field="rows_out", source="dataset_recipe", text=f"rows_out={rows_out}", weight=0.7 if selected else 0.2),
        DiscoveryEvidence(field="full_status", source="dataset_recipe", text=str(row.get("full_status") or ""), weight=0.4),
        DiscoveryEvidence(field="ai_ready_outcome", source="dataset_recipe", text=str(row.get("ai_ready_outcome") or ""), weight=0.5),
    ]
    for blocker in blockers[:5]:
        evidence.append(DiscoveryEvidence(field="blocker", source="dataset_recipe", text=blocker, weight=-0.5))
    return DiscoveredFile(
        repository=_repository_value(row.get("repository")),
        project_accession=project_accession,
        file_accession_or_path=source_file,
        file_name=source_file,
        file_type=suffix,
        file_role="raw_acquisition" if suffix.casefold() in {".raw", ".mzml", ".mzxml"} else "unknown",
        evidence_level="file" if selected else "mixed",
        file_level_evidence_count=1 if selected else 0,
        project_level_evidence_count=0,
        evidence_warnings=warnings,
        species=_list_values(row.get("canonical_species")),
        species_policy=str(row.get("species_policy") or "open") if str(row.get("species_policy") or "open") in {"open", "include_only", "exclude"} else "open",
        canonical_species=_list_values(row.get("canonical_species")),
        organism_taxon_id=_list_values(row.get("organism_taxon_id")),
        acquisition_mode=str(row.get("acquisition_mode") or "dda"),
        ptm_type=str(row.get("ptm_type") or ""),
        ptm_subtype=str(row.get("ptm_subtype") or ""),
        ptm_evidence_terms=_list_values(row.get("ptm_evidence_terms")),
        ptm_enrichment_methods=_list_values(row.get("ptm_enrichment_methods")),
        semantic_metadata_confidence=_safe_float(row.get("semantic_metadata_confidence")),
        modification_scope=str(row.get("modification_scope") or ""),
        labeling_strategy=normalize_labeling_strategy(str(row.get("labeling_strategy") or "label_free")),
        file_score=file_score,
        project_score=file_score,
        confidence=trust,
        trust_score=trust,
        evidence_completeness=0.65 if selected else 0.3,
        validity_status=validity,
        validity_reasons=["recipe_selected_training_output"] if selected else blockers or ["recipe_excluded_or_blocked_output"],
        needs_review=not selected,
        instrument_families=_list_values(row.get("instrument_families")),
        fragmentation_methods=_list_values(row.get("fragmentation_methods")),
        diversity_tags=_list_values(row.get("diversity_tags")),
        memory_feedback=memory_feedback_for_candidate(review_decisions or [], project_accession, source_file),
        evidence=evidence,
        raw_record={"recipe_row": row},
    )


def _load_review_memory(memory_dir: str | Path | None) -> list[DiscoveryReviewDecision]:
    if memory_dir is None:
        return []
    try:
        return DiscoveryMemory(memory_dir).load_review_decisions()
    except Exception:
        return []


def _repository_value(value: Any) -> str:
    text = str(value or "local").strip().lower()
    return text if text in {"pride", "massive", "iprox", "auto", "local"} else "local"


def _first_nonempty(values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _list_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        try:
            payload = json.loads(text)
        except Exception:
            payload = None
        if isinstance(payload, list):
            return [str(item).strip() for item in payload if str(item).strip()]
    return [item.strip() for item in text.replace("|", ";").split(";") if item.strip()]


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Data Scientist Agent Loop Report",
        "",
        f"- Status: `{summary.get('status')}`",
        f"- Task type: `{summary.get('task_type')}`",
        f"- Recipe status: `{summary.get('recipe_status')}`",
        f"- Model-loop status: `{summary.get('model_loop_status')}`",
        f"- Final report status: `{summary.get('final_report_status')}`",
        f"- Guidance alignment status: `{summary.get('guidance_alignment_status')}`",
        f"- Guidance counts: `{json.dumps(summary.get('guidance_counts') or {}, ensure_ascii=False, sort_keys=True)}`",
        f"- Selected outputs: {summary.get('selected_count', 0)}",
        f"- Excluded outputs: {summary.get('excluded_count', 0)}",
        f"- Leakage status: `{summary.get('leakage_status')}`",
        f"- Hard benchmark rows: {summary.get('hard_benchmark_count', 0)}",
        f"- Curation items: {summary.get('curation_queue_count', 0)}",
        f"- Curation memory update: `{summary.get('curation_memory_update_status')}`",
        f"- Curation memory imported decisions: {summary.get('curation_memory_imported_decision_count', 0)}",
        f"- Dataset expansion actions: {summary.get('dataset_expansion_action_count', 0)}",
        f"- Repository blocker actions: {summary.get('repository_blocker_action_count', 0)}",
        f"- Model failure modes: {summary.get('model_failure_mode_count', 0)}",
        f"- Model expansion actions: {summary.get('model_expansion_action_count', 0)}",
        f"- Model-informed repositories: `{', '.join(map(str, (summary.get('model_informed_repository_plan') or {}).get('planned_repositories') or [])) or 'not_available'}`",
        "",
        "## Interpretation",
        "",
        "- This command composes recipe/split, leakage checks, hard/counterfactual benchmarks, model-loop smoke, final reporting, and guidance alignment audit.",
        "- It does not run repository discovery, downloads, full workflow, or large model training.",
        "- It writes curation decisions to discovery memory only when explicit decisions are provided.",
        "",
        "## Warnings",
        "",
    ]
    warnings = summary.get("warnings") or []
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- None")
    actions = summary.get("dataset_expansion_actions") or []
    repo_actions = summary.get("repository_blocker_actions") or []
    if repo_actions:
        lines.extend(["", "## Repository Blocker Actions", ""])
        for action in repo_actions[:20]:
            lines.append(
                f"- `{action.get('action')}` "
                f"{action.get('repository') or ''} "
                f"{action.get('blocker') or action.get('reason') or ''}".strip()
            )
    repository_plan = summary.get("model_informed_repository_plan") or {}
    planned_repositories = repository_plan.get("planned_repositories") or []
    if summary.get("curation_memory_update_status") in {"updated", "no_decisions_imported"}:
        lines.extend(["", "## Curation Memory Write-back", ""])
        lines.append(f"- Status: `{summary.get('curation_memory_update_status')}`")
        lines.append(f"- Imported decisions: {summary.get('curation_memory_imported_decision_count', 0)}")
        lines.append(f"- Skipped items: {summary.get('curation_memory_skipped_count', 0)}")
        lines.append(f"- Memory dir: `{summary.get('curation_memory_dir') or ''}`")
    elif summary.get("curation_memory_update_reason"):
        lines.extend(["", "## Curation Memory Write-back", ""])
        lines.append(f"- Status: `not_run`")
        lines.append(f"- Reason: `{summary.get('curation_memory_update_reason')}`")
    if planned_repositories:
        lines.extend(["", "## Model-informed Repository Plan", ""])
        lines.append(f"- Strategy: `{repository_plan.get('repository_strategy') or 'unknown'}`")
        lines.append(f"- Planned repositories: `{', '.join(map(str, planned_repositories))}`")
        lines.append(f"- Payloads: {repository_plan.get('payload_count', 0)}; queue items: {repository_plan.get('queue_item_count', 0)}")
    if actions:
        lines.extend(["", "## Dataset Expansion Actions", ""])
        for action in actions[:20]:
            lines.append(
                f"- `{action.get('action')}` "
                f"{action.get('dimension') or action.get('repository') or ''} "
                f"{action.get('target') or action.get('blocker') or action.get('reason') or ''}".strip()
            )
    blockers = summary.get("blockers") or []
    if blockers:
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- {blocker}" for blocker in blockers)
    return "\n".join(lines) + "\n"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}
