from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import Field

from agent.models import JsonModel
from agent.utils import write_json


TASK_PARQUET_NAMES = {
    "rt_prediction": "rt_train.parquet",
    "fragment_intensity_prediction": "fragment_intensity_train.parquet",
    "psm_scoring": "psm_scoring_train.parquet",
    "denovo": "denovo_train.parquet",
    "ptm_denovo": "ptm_denovo_train.parquet",
    "chimeric_interpretation": "chimeric_train.parquet",
}

PEPTIDE_COLUMNS = ["peptide_sequence", "sequence"]
MODIFIED_SEQUENCE_COLUMNS = ["modified_sequence", "modified_peptide", "modified_peptide_sequence"]
PROTEIN_COLUMNS = ["protein_accession", "protein_id", "protein", "proteins", "protein_ids", "mapped_proteins"]

SPLIT_STRATEGIES = {
    "auto",
    "project_disjoint",
    "file_disjoint",
    "sample_disjoint",
    "lab_disjoint",
    "instrument_disjoint",
    "organism_disjoint",
    "peptide_disjoint",
    "protein_disjoint",
    "modification_disjoint",
    "acquisition_disjoint",
}


class DatasetRecipeResult(JsonModel):
    status: str
    batch_dir: str
    output_dir: str
    selected_count: int = 0
    excluded_count: int = 0
    split_level: str = "unknown"
    split_policy: str = "none"
    split_strategy: str = "auto"
    split_counts: dict[str, int] = Field(default_factory=dict)
    leakage_status: str = "not_evaluated"
    hard_benchmark_count: int = 0
    curation_queue_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict)


def make_dataset_recipe(
    *,
    batch_dir: str | Path,
    output_dir: str | Path,
    discovery_manifest: str | Path | None = None,
    repository_audit: str | Path | dict[str, Any] | None = None,
    split_strategy: str = "auto",
) -> DatasetRecipeResult:
    batch_dir = Path(batch_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    batch_summary = _load_batch_summary(batch_dir)
    benchmark_summary = _read_json(batch_dir / "benchmark_summary.json")
    discovery_manifest_path = Path(discovery_manifest) if discovery_manifest else None
    discovery_rows = _load_discovery_rows(discovery_manifest_path) if discovery_manifest_path else []
    repository_audit = _load_repository_audit_payload(repository_audit) if repository_audit is not None else (
        _load_repository_audit(discovery_manifest_path) if discovery_manifest_path else {}
    )
    split_strategy = _normalize_split_strategy(split_strategy)
    selected, excluded = _select_rows(batch_summary, discovery_rows)
    repository_summary = _repository_summary(selected, excluded)
    split_rows, split_level, split_policy, split_rationale = _make_split_rows(selected, split_strategy=split_strategy)
    leakage_report = _leakage_check(split_rows)
    leakage_risk_report = _leakage_risk_report(leakage_report, split_rows)
    split_baseline_evaluation = _split_baseline_evaluation(
        selected,
        agent_split_rows=split_rows,
        agent_split_policy=split_policy,
    )
    hard_benchmark_rows = _hard_benchmark_rows(selected, excluded)
    counterfactual_rows = _counterfactual_benchmark_rows(selected, excluded, hard_benchmark_rows)
    coverage_gap_report = _coverage_gap_report(selected, excluded, repository_audit=repository_audit)
    expansion_plan = _agent_expansion_plan(coverage_gap_report)
    curation_queue = _curation_queue(selected, excluded, leakage_risk_report, repository_audit=repository_audit)
    curation_efficiency_report = _curation_efficiency_report(selected, excluded, curation_queue, leakage_risk_report)
    evidence_graph = _evidence_graph(
        selected,
        excluded,
        leakage_risk_report,
        split_rows=split_rows,
        hard_benchmark_rows=hard_benchmark_rows,
        counterfactual_rows=counterfactual_rows,
        curation_queue=curation_queue,
        repository_audit=repository_audit,
    )
    split_counts = _counts([row["split"] for row in split_rows])
    warnings = list(leakage_report.get("warnings") or [])
    recipe = {
        "status": "ready" if selected else "blocked",
        "batch_dir": str(batch_dir),
        "benchmark_summary": benchmark_summary,
        "selected_files": selected,
        "excluded_files": excluded,
        "repository_summary": repository_summary,
        "repository_audit": repository_audit,
        "split_strategy_requested": split_strategy,
        "split_strategy_resolved": split_policy,
        "split_rationale": split_rationale,
        "split_level": split_level,
        "split_policy": split_policy,
        "split_counts": split_counts,
        "leakage_check": leakage_report,
        "leakage_risk": leakage_risk_report,
        "split_baseline_evaluation": split_baseline_evaluation,
        "hard_benchmark": {
            "rows": hard_benchmark_rows,
            "row_count": len(hard_benchmark_rows),
            "tag_counts": _hard_benchmark_tag_counts(hard_benchmark_rows),
        },
        "counterfactual_benchmark": {
            "rows": counterfactual_rows,
            "row_count": len(counterfactual_rows),
            "case_type_counts": _counts([str(row.get("case_type") or "unknown") for row in counterfactual_rows]),
            "tag_counts": _counterfactual_tag_counts(counterfactual_rows),
        },
        "coverage_gap_report": coverage_gap_report,
        "agent_expansion_plan": expansion_plan,
        "active_curation_efficiency": curation_efficiency_report,
        "reproduction": {
            "validate_batch": "python -m agent.cli validate-agent-runs-ai-ready-batch --agent-run-dir <run> --task-type <task> --output-dir <batch_dir>",
            "make_recipe": "python -m agent.cli make-dataset-recipe --batch-dir <batch_dir> --output-dir <recipe_dir>",
            "repository_inputs": {
                "pride": "Use PRIDE accession/file names or cached local acquisition files from the benchmark manifest.",
                "massive": "Use MassIVE MSV/native paths through repository-smoke or original one-click repository mode.",
                "iprox": "Use iProX IPX/native paths through repository-smoke or original one-click repository mode.",
            },
            "note": "Recipe v1 records parquet paths and split metadata; it does not copy large parquet files.",
        },
    }
    files = {
        "dataset_recipe_json": str(output_dir / "dataset_recipe.json"),
        "dataset_recipe_md": str(output_dir / "dataset_recipe.md"),
        "selected_files_csv": str(output_dir / "selected_files.csv"),
        "excluded_files_csv": str(output_dir / "excluded_files.csv"),
        "dataset_split_manifest_json": str(output_dir / "dataset_split_manifest.json"),
        "dataset_split_manifest_csv": str(output_dir / "dataset_split_manifest.csv"),
        "leakage_check_report_json": str(output_dir / "leakage_check_report.json"),
        "dataset_split_plan_json": str(output_dir / "dataset_split_plan.json"),
        "dataset_split_plan_csv": str(output_dir / "dataset_split_plan.csv"),
        "split_rationale_md": str(output_dir / "split_rationale.md"),
        "leakage_risk_report_json": str(output_dir / "leakage_risk_report.json"),
        "leakage_risk_report_md": str(output_dir / "leakage_risk_report.md"),
        "split_baseline_evaluation_json": str(output_dir / "split_baseline_evaluation.json"),
        "split_baseline_evaluation_csv": str(output_dir / "split_baseline_evaluation.csv"),
        "split_baseline_evaluation_md": str(output_dir / "split_baseline_evaluation.md"),
        "hard_benchmark_manifest_json": str(output_dir / "hard_benchmark_manifest.json"),
        "hard_benchmark_manifest_csv": str(output_dir / "hard_benchmark_manifest.csv"),
        "counterfactual_benchmark_manifest_json": str(output_dir / "counterfactual_benchmark_manifest.json"),
        "counterfactual_benchmark_manifest_csv": str(output_dir / "counterfactual_benchmark_manifest.csv"),
        "counterfactual_benchmark_report_md": str(output_dir / "counterfactual_benchmark_report.md"),
        "coverage_gap_report_json": str(output_dir / "coverage_gap_report.json"),
        "coverage_gap_report_md": str(output_dir / "coverage_gap_report.md"),
        "agent_expansion_plan_json": str(output_dir / "agent_expansion_plan.json"),
        "evidence_graph_json": str(output_dir / "evidence_graph.json"),
        "evidence_graph_summary_md": str(output_dir / "evidence_graph_summary.md"),
        "curation_queue_csv": str(output_dir / "curation_queue.csv"),
        "curation_queue_json": str(output_dir / "curation_queue.json"),
        "curation_efficiency_report_json": str(output_dir / "curation_efficiency_report.json"),
        "curation_efficiency_report_csv": str(output_dir / "curation_efficiency_report.csv"),
        "curation_efficiency_report_md": str(output_dir / "curation_efficiency_report.md"),
    }
    write_json(files["dataset_recipe_json"], recipe)
    _write_csv(Path(files["selected_files_csv"]), selected)
    _write_csv(Path(files["excluded_files_csv"]), excluded)
    split_plan = {
        "split_level": split_level,
        "split_policy": split_policy,
        "split_strategy_requested": split_strategy,
        "split_rationale": split_rationale,
        "split_counts": split_counts,
        "rows": split_rows,
    }
    write_json(files["dataset_split_manifest_json"], {"split_level": split_level, "rows": split_rows})
    _write_csv(Path(files["dataset_split_manifest_csv"]), split_rows)
    write_json(files["leakage_check_report_json"], leakage_report)
    write_json(files["dataset_split_plan_json"], split_plan)
    _write_csv(Path(files["dataset_split_plan_csv"]), split_rows)
    Path(files["split_rationale_md"]).write_text(_markdown_split_rationale(split_rationale), encoding="utf-8")
    write_json(files["leakage_risk_report_json"], leakage_risk_report)
    Path(files["leakage_risk_report_md"]).write_text(_markdown_leakage_risk(leakage_risk_report), encoding="utf-8")
    write_json(files["split_baseline_evaluation_json"], split_baseline_evaluation)
    _write_csv(Path(files["split_baseline_evaluation_csv"]), split_baseline_evaluation.get("strategy_rows") or [])
    Path(files["split_baseline_evaluation_md"]).write_text(_markdown_split_baseline_evaluation(split_baseline_evaluation), encoding="utf-8")
    write_json(files["hard_benchmark_manifest_json"], {"rows": hard_benchmark_rows})
    _write_csv(Path(files["hard_benchmark_manifest_csv"]), hard_benchmark_rows)
    counterfactual_payload = {
        "rows": counterfactual_rows,
        "row_count": len(counterfactual_rows),
        "case_type_counts": recipe["counterfactual_benchmark"]["case_type_counts"],
        "tag_counts": recipe["counterfactual_benchmark"]["tag_counts"],
        "notes": [
            "Counterfactual benchmark rows are evaluation and review candidates, not additional training labels.",
            "They organize positive, hard-positive, negative/blocked, low-yield, and uncertainty cases for agent decision assessment.",
        ],
    }
    write_json(files["counterfactual_benchmark_manifest_json"], counterfactual_payload)
    _write_csv(Path(files["counterfactual_benchmark_manifest_csv"]), counterfactual_rows)
    Path(files["counterfactual_benchmark_report_md"]).write_text(_markdown_counterfactual_benchmark(counterfactual_payload), encoding="utf-8")
    write_json(files["coverage_gap_report_json"], coverage_gap_report)
    Path(files["coverage_gap_report_md"]).write_text(_markdown_coverage_gap(coverage_gap_report, expansion_plan), encoding="utf-8")
    write_json(files["agent_expansion_plan_json"], expansion_plan)
    write_json(files["evidence_graph_json"], evidence_graph)
    Path(files["evidence_graph_summary_md"]).write_text(
        _markdown_evidence_graph_summary(evidence_graph, selected, excluded, hard_benchmark_rows, counterfactual_rows, curation_queue),
        encoding="utf-8",
    )
    _write_csv(Path(files["curation_queue_csv"]), curation_queue)
    write_json(files["curation_queue_json"], {"rows": curation_queue, "row_count": len(curation_queue)})
    write_json(files["curation_efficiency_report_json"], curation_efficiency_report)
    _write_csv(Path(files["curation_efficiency_report_csv"]), curation_efficiency_report.get("rows") or [])
    Path(files["curation_efficiency_report_md"]).write_text(_markdown_curation_efficiency(curation_efficiency_report), encoding="utf-8")
    Path(files["dataset_recipe_md"]).write_text(_markdown_recipe(recipe), encoding="utf-8")
    return DatasetRecipeResult(
        status=str(recipe["status"]),
        batch_dir=str(batch_dir),
        output_dir=str(output_dir),
        selected_count=len(selected),
        excluded_count=len(excluded),
        split_level=split_level,
        split_policy=split_policy,
        split_strategy=split_strategy,
        split_counts=split_counts,
        leakage_status=str(leakage_report.get("status") or "not_evaluated"),
        hard_benchmark_count=len(hard_benchmark_rows),
        curation_queue_count=len(curation_queue),
        warnings=warnings,
        files=files,
    )


def _load_batch_summary(batch_dir: Path) -> dict[str, Any]:
    path = batch_dir / "mini_e2e_batch_summary.json"
    if not path.exists():
        raise ValueError(f"mini_e2e_batch_summary.json not found: {path}")
    payload = _read_json(path)
    if not isinstance(payload.get("run_results"), list):
        raise ValueError(f"Invalid mini E2E batch summary: {path}")
    return payload


def _load_repository_audit(discovery_manifest: Path | None) -> dict[str, Any]:
    if discovery_manifest is None:
        return {}
    audit_path = discovery_manifest.parent / "repository_audit.json"
    payload = _read_json(audit_path)
    return payload if isinstance(payload.get("rows"), list) else {}


def _load_repository_audit_payload(value: str | Path | dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value if isinstance(value.get("rows"), list) else {}
    payload = _read_json(Path(value))
    return payload if isinstance(payload.get("rows"), list) else {}


def _select_rows(
    batch_summary: dict[str, Any],
    discovery_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    discovery_by_file = {
        str(row.get("file_name") or row.get("file") or row.get("source_file") or "").casefold(): row
        for row in discovery_rows
    }
    for run in batch_summary.get("run_results") or []:
        if not isinstance(run, dict):
            continue
        task_statuses = run.get("task_statuses") if isinstance(run.get("task_statuses"), dict) else {}
        rows_out = run.get("rows_out") if isinstance(run.get("rows_out"), dict) else {}
        task_files = run.get("task_files") if isinstance(run.get("task_files"), dict) else {}
        run_name = str(run.get("run_name") or Path(str(run.get("agent_run_dir") or "")).name)
        project = str(run.get("project_accession") or "") or _guess_project(run)
        repository = _normalize_repository(run.get("repository"), project)
        source_file = str(run.get("source_file") or run_name)
        discovery = discovery_by_file.get(source_file.casefold(), {})
        score_source = "discovery_manifest" if discovery else "not_scored_existing_run"
        repository = _normalize_repository(discovery.get("repository") or repository, project)
        completed_task = False
        for task_type, status in task_statuses.items():
            task_type = str(task_type)
            rows = int(rows_out.get(task_type) or 0)
            parquet_path = _task_parquet_path(task_type, task_files.get(task_type), run)
            base = {
                "run_name": run_name,
                "agent_run_dir": run.get("agent_run_dir"),
                "repository": repository,
                "project_accession": project,
                "source_file": source_file,
                "task_type": task_type,
                "status": status,
                "rows_out": rows,
                "parquet_path": parquet_path,
                "sample_class": run.get("sample_class"),
                "full_status": run.get("full_status"),
                "ai_ready_outcome": run.get("ai_ready_outcome"),
                "metadata_quality": run.get("metadata_quality"),
                "sample_name": discovery.get("sample_name") or discovery.get("sample") or run.get("sample_name") or run.get("sample") or "",
                "condition": discovery.get("condition") or run.get("condition") or run.get("sample_condition") or "",
                "lab": discovery.get("lab") or discovery.get("submitter_lab") or run.get("lab") or run.get("submitter_lab") or run.get("submitter") or "",
                "submitter": discovery.get("submitter") or run.get("submitter") or "",
                "enzyme": discovery.get("enzyme") or run.get("enzyme") or "",
                "database": discovery.get("database") or run.get("database") or "",
                "workflow": discovery.get("workflow") or run.get("workflow") or run.get("search_workflow") or "",
                "search_engine": discovery.get("search_engine") or run.get("search_engine") or "",
                "acquisition_mode": discovery.get("acquisition_mode") or run.get("acquisition_mode") or "",
                "confidence": discovery.get("trust_score") or discovery.get("score") or "",
                "task_readiness": discovery.get("task_readiness_status") or "",
                "task_ai_readiness_score": discovery.get("task_ai_readiness_score") or "",
                "task_ai_readiness_band": discovery.get("task_ai_readiness_band") or "",
                "data_value_score": discovery.get("data_value_score") or "",
                "data_value_action": discovery.get("data_value_action") or "",
                "score_source": score_source,
                "species_policy": discovery.get("species_policy") or run.get("species_policy") or "open",
                "canonical_species": discovery.get("canonical_species") or run.get("canonical_species") or "",
                "organism_taxon_id": discovery.get("organism_taxon_id") or run.get("organism_taxon_id") or "",
                "instrument_families": discovery.get("instrument_families") or run.get("instrument_families") or "",
                "fragmentation_methods": discovery.get("fragmentation_methods") or run.get("fragmentation_methods") or "",
                "ptm_type": discovery.get("ptm_type") or run.get("ptm_type") or "",
                "ptm_subtype": discovery.get("ptm_subtype") or run.get("ptm_subtype") or "",
                "ptm_evidence_terms": discovery.get("ptm_evidence_terms") or run.get("ptm_evidence_terms") or "",
                "ptm_enrichment_methods": discovery.get("ptm_enrichment_methods") or run.get("ptm_enrichment_methods") or "",
                "semantic_metadata_confidence": discovery.get("semantic_metadata_confidence") or run.get("semantic_metadata_confidence") or "",
                "modification_scope": discovery.get("modification_scope") or run.get("modification_scope") or "",
                "labeling_strategy": discovery.get("labeling_strategy") or run.get("labeling_strategy") or "",
                "diversity_tags": discovery.get("diversity_tags") or run.get("diversity_tags") or "",
                "warnings": run.get("warnings") or [],
                "blockers": run.get("blockers") or [],
            }
            if str(status) == "completed" and rows > 0 and parquet_path:
                completed_task = True
                selected.append({**base, "selection_reason": "completed_task_with_rows"})
            else:
                excluded.append({**base, "exclusion_reason": _exclusion_reason(status, rows, parquet_path, run)})
        if not task_statuses:
            excluded.append(
                {
                    "run_name": run_name,
                    "agent_run_dir": run.get("agent_run_dir"),
                    "repository": repository,
                    "project_accession": project,
                    "source_file": source_file,
                    "task_type": "",
                    "status": run.get("status"),
                    "rows_out": 0,
                    "parquet_path": "",
                    "exclusion_reason": "no_task_results",
                    "warnings": run.get("warnings") or [],
                    "blockers": run.get("blockers") or [],
                }
            )
        elif not completed_task and all(str(status) != "completed" for status in task_statuses.values()):
            # Run-level exclusion already represented by task rows; keep task-level detail only.
            pass
    return selected, excluded


def _repository_summary(selected: list[dict[str, Any]], excluded: list[dict[str, Any]]) -> dict[str, Any]:
    selected_counts = _counts([str(row.get("repository") or "unknown") for row in selected])
    excluded_counts = _counts([str(row.get("repository") or "unknown") for row in excluded])
    task_rows_by_repository: dict[str, dict[str, int]] = {}
    for row in selected:
        repository = str(row.get("repository") or "unknown")
        task_type = str(row.get("task_type") or "unknown")
        task_rows_by_repository.setdefault(repository, {})
        task_rows_by_repository[repository][task_type] = task_rows_by_repository[repository].get(task_type, 0) + int(row.get("rows_out") or 0)
    return {
        "selected_counts": selected_counts,
        "excluded_counts": excluded_counts,
        "task_rows_by_repository": {
            repository: dict(sorted(rows.items()))
            for repository, rows in sorted(task_rows_by_repository.items())
        },
    }


def _task_parquet_path(task_type: str, files: Any, run: dict[str, Any]) -> str:
    if isinstance(files, dict):
        for key, value in files.items():
            if str(key).endswith("_parquet") or str(value).endswith(".parquet"):
                return str(value)
    output_dir = Path(str(run.get("output_dir") or ""))
    expected_name = TASK_PARQUET_NAMES.get(task_type)
    if expected_name:
        candidates = list(output_dir.rglob(expected_name)) if output_dir.exists() else []
        if candidates:
            return str(candidates[0])
    return ""


def _exclusion_reason(status: Any, rows: int, parquet_path: str, run: dict[str, Any]) -> str:
    if str(status) == "completed" and rows <= 0:
        return "zero_rows_out"
    if str(status) == "completed" and not parquet_path:
        return "parquet_missing"
    blockers = run.get("blockers") if isinstance(run.get("blockers"), list) else []
    return str(blockers[0]) if blockers else f"task_{status}"


def _normalize_split_strategy(value: str | None) -> str:
    strategy = str(value or "auto").strip().lower()
    if strategy not in SPLIT_STRATEGIES:
        raise ValueError(f"Unsupported split_strategy: {value}. Expected one of {sorted(SPLIT_STRATEGIES)}")
    return strategy


def _make_split_rows(
    selected: list[dict[str, Any]],
    *,
    split_strategy: str,
) -> tuple[list[dict[str, Any]], str, str, dict[str, Any]]:
    if not selected:
        rationale = {
            "requested_strategy": split_strategy,
            "resolved_strategy": "none",
            "split_level": "none",
            "key_field": "",
            "fallback_used_count": 0,
            "warnings": ["no_selected_outputs"],
            "notes": ["No selected task outputs were available for splitting."],
        }
        return [], "none", "none", rationale

    resolved_strategy = split_strategy
    notes: list[str] = []
    warnings: list[str] = []
    if split_strategy == "auto":
        projects = sorted({str(row.get("project_accession") or "") for row in selected if row.get("project_accession")})
        if len(projects) >= 3:
            resolved_strategy = "project_disjoint"
            notes.append("auto selected project_disjoint because at least three projects are available.")
        else:
            resolved_strategy = "file_disjoint"
            notes.append("auto selected file_disjoint because fewer than three projects are available.")

    key_field, split_level = _split_strategy_key(resolved_strategy)
    groups: list[str] = []
    fallback_used_count = 0
    row_keys: list[str] = []
    for row in selected:
        key = _split_key_for_row(row, resolved_strategy)
        if not key:
            key = str(row.get("source_file") or row.get("run_name") or row.get("project_accession") or "unknown")
            fallback_used_count += 1
        row_keys.append(key)
        groups.append(key)
    groups = sorted(set(groups))
    assignments = _assign_groups(groups)
    split_rows = [
        {
            **row,
            "split": assignments.get(row_keys[index], "train"),
            "split_key": row_keys[index],
            "split_strategy": resolved_strategy,
        }
        for index, row in enumerate(selected)
    ]
    if fallback_used_count:
        warnings.append(f"split_key_missing_fallback_used:{fallback_used_count}")
    if len(groups) < 2:
        warnings.append("single_split_only")
    rationale = {
        "requested_strategy": split_strategy,
        "resolved_strategy": resolved_strategy,
        "split_level": split_level,
        "split_policy": resolved_strategy,
        "key_field": key_field,
        "group_count": len(groups),
        "fallback_used_count": fallback_used_count,
        "warnings": warnings,
        "notes": notes or [f"Used {resolved_strategy} split based on `{key_field}`."],
    }
    return split_rows, split_level, resolved_strategy, rationale


def _split_strategy_key(strategy: str) -> tuple[str, str]:
    mapping = {
        "project_disjoint": ("project_accession", "project"),
        "file_disjoint": ("source_file", "file"),
        "sample_disjoint": ("sample_name", "sample"),
        "lab_disjoint": ("lab", "lab"),
        "instrument_disjoint": ("instrument_families", "instrument"),
        "organism_disjoint": ("canonical_species", "organism"),
        "peptide_disjoint": ("peptide_sequence", "peptide"),
        "protein_disjoint": ("protein_accession", "protein"),
        "modification_disjoint": ("modified_sequence", "modification"),
        "acquisition_disjoint": ("fragmentation_methods", "acquisition"),
    }
    return mapping.get(strategy, ("", "none"))


def _split_key_for_row(row: dict[str, Any], strategy: str) -> str:
    if strategy == "project_disjoint":
        return str(row.get("project_accession") or "").strip()
    if strategy == "file_disjoint":
        return str(row.get("source_file") or row.get("run_name") or "").strip()
    if strategy == "sample_disjoint":
        return str(row.get("sample_name") or row.get("source_file") or row.get("run_name") or "").strip()
    if strategy == "lab_disjoint":
        return _first_split_value(row.get("lab") or row.get("submitter"))
    if strategy == "instrument_disjoint":
        return _first_split_value(row.get("instrument_families"))
    if strategy == "organism_disjoint":
        return _first_split_value(row.get("canonical_species"))
    if strategy == "acquisition_disjoint":
        return _first_split_value(row.get("fragmentation_methods"))
    if strategy == "peptide_disjoint":
        return _first_parquet_value(row, ["peptide_sequence", "sequence"])
    if strategy == "protein_disjoint":
        return _first_parquet_value(row, ["protein_accession", "protein_id", "protein", "proteins", "protein_ids", "mapped_proteins"])
    if strategy == "modification_disjoint":
        return _first_parquet_value(row, ["modified_sequence", "modified_peptide", "modified_peptide_sequence"])
    return ""


def _first_split_value(value: Any) -> str:
    values = _split_values(value)
    return values[0] if values else ""


def _first_parquet_value(row: dict[str, Any], columns: list[str]) -> str:
    frame, _warning = _read_parquet_preview(row, columns=columns, max_rows=1000)
    if frame is None or frame.empty:
        return ""
    column = _first_column(frame, columns)
    if column is None:
        return ""
    values = frame[column].dropna()
    if values.empty:
        return ""
    return str(values.iloc[0])


def _legacy_make_split_rows(selected: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    projects = sorted({str(row.get("project_accession") or "") for row in selected if row.get("project_accession")})
    if len(projects) >= 3:
        assignments = _assign_groups(projects)
        return [{**row, "split": assignments.get(str(row.get("project_accession")), "train")} for row in selected], "project"
    files = sorted({str(row.get("source_file") or row.get("run_name") or "") for row in selected})
    assignments = _assign_groups(files)
    return [{**row, "split": assignments.get(str(row.get("source_file") or row.get("run_name")), "train")} for row in selected], "file"


def _assign_groups(groups: list[str]) -> dict[str, str]:
    if not groups:
        return {}
    if len(groups) == 1:
        return {groups[0]: "train"}
    if len(groups) == 2:
        return {groups[0]: "train", groups[1]: "val"}
    assignments: dict[str, str] = {}
    for index, group in enumerate(groups):
        if index == len(groups) - 1:
            split = "test"
        elif index == len(groups) - 2:
            split = "val"
        else:
            split = "train"
        assignments[group] = split
    return assignments


def _split_baseline_evaluation(
    selected: list[dict[str, Any]],
    *,
    agent_split_rows: list[dict[str, Any]],
    agent_split_policy: str,
) -> dict[str, Any]:
    strategies: list[tuple[str, list[dict[str, Any]], str]] = [
        ("agent_designed_split", agent_split_rows, agent_split_policy),
        ("random_row_split", _random_row_split_rows(selected), "row_random"),
    ]
    for strategy in [
        "file_disjoint",
        "project_disjoint",
        "lab_disjoint",
        "instrument_disjoint",
        "organism_disjoint",
        "peptide_disjoint",
        "modification_disjoint",
        "protein_disjoint",
        "acquisition_disjoint",
    ]:
        if strategy == agent_split_policy:
            continue
        try:
            rows, _level, policy, _rationale = _make_split_rows(selected, split_strategy=strategy)
        except Exception:
            continue
        strategies.append((strategy, rows, policy))

    strategy_rows = [
        _split_strategy_eval_row(name, rows, policy)
        for name, rows, policy in strategies
    ]
    agent_row = next((row for row in strategy_rows if row["strategy"] == "agent_designed_split"), None)
    random_row = next((row for row in strategy_rows if row["strategy"] == "random_row_split"), None)
    best_row = min(
        strategy_rows,
        key=lambda row: (
            int(row.get("total_leakage_issue_count") or 0),
            1 if str(row.get("status") or "") == "warn" else 0,
            -int(row.get("split_count") or 0),
            str(row.get("strategy") or ""),
        ),
        default=None,
    )
    return {
        "status": "ready" if selected else "not_evaluated",
        "selected_count": len(selected),
        "agent_strategy": agent_split_policy,
        "random_baseline_strategy": "random_row_split",
        "best_strategy_by_leakage": best_row.get("strategy") if best_row else "",
        "agent_total_leakage_issue_count": agent_row.get("total_leakage_issue_count") if agent_row else 0,
        "random_total_leakage_issue_count": random_row.get("total_leakage_issue_count") if random_row else 0,
        "agent_minus_random_leakage": int(agent_row.get("total_leakage_issue_count") or 0) - int(random_row.get("total_leakage_issue_count") or 0) if agent_row and random_row else 0,
        "interpretation": _split_eval_interpretation(agent_row, random_row, best_row),
        "strategy_rows": strategy_rows,
        "notes": [
            "This compares split strategies using leakage checks over recipe metadata and parquet previews.",
            "It is a split-design diagnostic, not a substitute for held-out model evaluation.",
        ],
    }


def _random_row_split_rows(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    splits = ["train", "val", "test"]
    if len(selected) == 1:
        splits = ["train"]
    elif len(selected) == 2:
        splits = ["train", "val"]
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(selected):
        rows.append(
            {
                **row,
                "split": splits[index % len(splits)],
                "split_key": f"row:{index}",
                "split_strategy": "random_row_split",
            }
        )
    return rows


def _split_strategy_eval_row(strategy: str, rows: list[dict[str, Any]], policy: str) -> dict[str, Any]:
    leakage = _leakage_check(rows)
    issue_counts = {
        "project": int(leakage.get("project_leak_count") or 0),
        "source_file": int(leakage.get("source_file_leak_count") or 0),
        "lab": int(leakage.get("lab_leak_count") or 0),
        "peptide_charge": int(leakage.get("peptide_charge_leak_count") or 0),
        "modified_sequence_charge": int(leakage.get("modified_sequence_charge_leak_count") or 0),
        "protein": int(leakage.get("protein_leak_count") or 0),
    }
    total = sum(issue_counts.values())
    split_counts = _counts([str(row.get("split") or "unknown") for row in rows])
    status = "pass"
    if total > 0:
        status = "fail"
    elif rows and len(split_counts) < 2:
        status = "warn"
    elif not rows:
        status = "not_evaluated"
    return {
        "strategy": strategy,
        "policy": policy,
        "status": status,
        "split_counts": split_counts,
        "split_count": len(split_counts),
        "rows_scanned": leakage.get("rows_scanned") or 0,
        "total_leakage_issue_count": total,
        "project_leak_count": issue_counts["project"],
        "source_file_leak_count": issue_counts["source_file"],
        "lab_leak_count": issue_counts["lab"],
        "peptide_charge_leak_count": issue_counts["peptide_charge"],
        "modified_sequence_charge_leak_count": issue_counts["modified_sequence_charge"],
        "protein_leak_count": issue_counts["protein"],
        "warnings": leakage.get("warnings") or [],
        "recommendation": _split_strategy_recommendation(status, issue_counts, split_counts),
    }


def _split_strategy_recommendation(status: str, issue_counts: dict[str, int], split_counts: dict[str, int]) -> str:
    if status == "pass":
        return "usable_split_strategy"
    if status == "warn" and len(split_counts) < 2:
        return "leakage_safe_but_not_a_true_holdout_split"
    if issue_counts.get("project"):
        return "prefer_project_disjoint_or_exclude_project_overlap"
    if issue_counts.get("source_file"):
        return "prefer_file_disjoint"
    if issue_counts.get("protein"):
        return "consider_protein_disjoint_for_family_generalization"
    if issue_counts.get("peptide_charge") or issue_counts.get("modified_sequence_charge"):
        return "consider_peptide_or_modification_disjoint_when_enough_data"
    if issue_counts.get("lab"):
        return "consider_lab_disjoint_when_lab_metadata_is_reliable"
    return "review_split_design"


def _split_eval_interpretation(
    agent_row: dict[str, Any] | None,
    random_row: dict[str, Any] | None,
    best_row: dict[str, Any] | None,
) -> str:
    if not agent_row:
        return "agent_split_missing"
    if not random_row:
        return "random_baseline_missing"
    agent_issues = int(agent_row.get("total_leakage_issue_count") or 0)
    random_issues = int(random_row.get("total_leakage_issue_count") or 0)
    if agent_issues < random_issues:
        return "agent_split_reduces_leakage_vs_random_baseline"
    if agent_issues == random_issues:
        if best_row and best_row.get("strategy") != "agent_designed_split" and int(best_row.get("total_leakage_issue_count") or 0) < agent_issues:
            return "alternative_split_has_lower_leakage_than_agent_default"
        return "agent_split_matches_random_leakage_proxy"
    return "agent_split_has_more_leakage_than_random_baseline"


def _split_policy(split_level: str) -> str:
    if split_level == "project":
        return "project_disjoint"
    if split_level == "file":
        return "file_disjoint"
    return "none"


def _leakage_risk_report(leakage_report: dict[str, Any], split_rows: list[dict[str, Any]]) -> dict[str, Any]:
    issue_counts = {
        "project": int(leakage_report.get("project_leak_count") or 0),
        "source_file": int(leakage_report.get("source_file_leak_count") or 0),
        "lab": int(leakage_report.get("lab_leak_count") or 0),
        "peptide_charge": int(leakage_report.get("peptide_charge_leak_count") or 0),
        "modified_sequence_charge": int(leakage_report.get("modified_sequence_charge_leak_count") or 0),
        "protein": int(leakage_report.get("protein_leak_count") or 0),
    }
    metadata_distribution = {
        "canonical_species": _split_distribution(split_rows, "canonical_species"),
        "species_policy": _split_distribution(split_rows, "species_policy"),
        "lab": _split_distribution(split_rows, "lab"),
        "sample_name": _split_distribution(split_rows, "sample_name"),
        "condition": _split_distribution(split_rows, "condition"),
        "instrument_families": _split_distribution(split_rows, "instrument_families"),
        "acquisition_mode": _split_distribution(split_rows, "acquisition_mode"),
        "fragmentation_methods": _split_distribution(split_rows, "fragmentation_methods"),
        "enzyme": _split_distribution(split_rows, "enzyme"),
        "database": _split_distribution(split_rows, "database"),
        "workflow": _split_distribution(split_rows, "workflow"),
        "search_engine": _split_distribution(split_rows, "search_engine"),
        "ptm_type": _split_distribution(split_rows, "ptm_type"),
        "ptm_subtype": _split_distribution(split_rows, "ptm_subtype"),
        "ptm_enrichment_methods": _split_distribution(split_rows, "ptm_enrichment_methods"),
        "labeling_strategy": _split_distribution(split_rows, "labeling_strategy"),
    }
    status = "pass"
    if any(value > 0 for value in issue_counts.values()):
        status = "fail"
    elif split_rows and len({row.get("split") for row in split_rows}) < 2:
        status = "warn"
    return {
        "status": status,
        "issue_counts": issue_counts,
        "leakage_check_status": leakage_report.get("status"),
        "metadata_distribution_by_split": metadata_distribution,
        "warnings": leakage_report.get("warnings") or [],
        "recommendations": _leakage_recommendations(status, issue_counts),
    }


def _hard_benchmark_rows(selected: list[dict[str, Any]], excluded: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in selected:
        tags, evidence = _hard_tags(row, selected=True)
        if tags:
            rows.append(_hard_row(row, tags, "selected", evidence))
    for row in excluded:
        tags, evidence = _hard_tags(row, selected=False)
        if tags:
            rows.append(_hard_row(row, tags, "excluded", evidence))
    return rows


def _hard_tags(row: dict[str, Any], *, selected: bool) -> tuple[list[str], list[str]]:
    tags: list[str] = []
    evidence: list[str] = []
    rows_out = _safe_int(row.get("rows_out"))
    if rows_out == 0:
        tags.append("zero_label_yield")
        evidence.append("rows_out=0")
    elif rows_out < 10:
        tags.append("low_label_yield")
        evidence.append(f"rows_out={rows_out}")
    if row.get("full_status") == "failed_with_usable_partial_outputs" or row.get("ai_ready_outcome") == "completed_from_usable_partial_outputs":
        tags.append("partial_output_recovery")
        evidence.append("usable partial outputs were used")
    if row.get("task_type") in {"ptm_denovo", "chimeric_interpretation"}:
        tags.append(f"specialized_task:{row.get('task_type')}")
    if _safe_float(row.get("semantic_metadata_confidence")) < 0.35:
        tags.append("low_semantic_metadata_confidence")
        evidence.append("semantic_metadata_confidence<0.35")
    if _split_values(row.get("ptm_enrichment_methods")):
        tags.append("ptm_enrichment_evidence")
        evidence.append("ptm enrichment method metadata present")
    if _split_values(row.get("blockers")):
        tags.append("blocked_but_informative")
        evidence.append("blockers present")
    if _split_values(row.get("warnings")):
        tags.append("warning_case")
    if not selected:
        tags.append("excluded_case")
    if _safe_float(row.get("data_value_score")) >= 0.5 and not selected:
        tags.append("high_value_needs_curation")
    parquet_tags, parquet_evidence = _parquet_hard_tags(row)
    tags.extend(parquet_tags)
    evidence.extend(parquet_evidence)
    return sorted(set(tags)), _dedupe(evidence)


def _parquet_hard_tags(row: dict[str, Any]) -> tuple[list[str], list[str]]:
    task_type = str(row.get("task_type") or "")
    tags: list[str] = []
    evidence: list[str] = []
    frame, warning = _read_parquet_preview(row, max_rows=5000)
    if warning:
        tags.append("hard_case_evidence_missing")
        evidence.append(warning)
        return tags, evidence
    if frame is None or frame.empty:
        tags.append("hard_case_evidence_missing")
        evidence.append("parquet_empty_or_unavailable")
        return tags, evidence

    charge_col = _first_column(frame, ["charge", "precursor_charge"])
    peptide_col = _first_column(frame, ["peptide_sequence", "sequence"])
    modified_col = _first_column(frame, ["modified_sequence", "modified_peptide", "modified_peptide_sequence"])
    q_col = _first_column(frame, ["q_value", "psm_q_value", "q-value", "psm q-value", "pep"])
    target_col = _first_column(frame, ["target_decoy", "is_decoy", "label", "target"])
    score_cols = [col for col in ["score", "hyperscore", "expectation", "probability", "psm_probability"] if _first_column(frame, [col])]
    intensity_col = _first_column(frame, ["spectrum_intensity_json", "intensity_array", "intensities"])
    localization_col = _first_column(frame, ["localization_confidence", "ptm_localization_confidence", "site_probability"])
    ptm_cols = [col for col in [modified_col, _first_column(frame, ["ptm_type", "ptm_subtype", "ptm_evidence_terms"])] if col]

    if charge_col is not None:
        charges = pd.to_numeric(frame[charge_col], errors="coerce").dropna()
        if not charges.empty and (charges >= 4).any():
            tags.append("high_charge_peptide")
            evidence.append("charge>=4")
    elif task_type in {"denovo", "ptm_denovo"}:
        tags.append("hard_case_evidence_missing:charge")

    if peptide_col and modified_col:
        peptides = frame[[peptide_col, modified_col]].dropna(how="any").head(5000)
        if not peptides.empty and any(str(item[peptide_col]) != str(item[modified_col]) for _, item in peptides.iterrows()):
            tags.append("modified_peptide")
            evidence.append("modified_sequence differs from peptide_sequence")
    elif task_type in {"denovo", "ptm_denovo"}:
        tags.append("hard_case_evidence_missing:modified_sequence")

    if task_type == "psm_scoring":
        if q_col is not None:
            q_values = pd.to_numeric(frame[q_col], errors="coerce").dropna()
            if not q_values.empty and ((q_values >= 0.005) & (q_values <= 0.02)).any():
                tags.append("target_decoy_boundary")
                evidence.append("q_value near 0.01 threshold")
        else:
            tags.append("hard_case_evidence_missing:q_value")
        if target_col is None:
            tags.append("hard_case_evidence_missing:target_decoy")
        if not score_cols:
            tags.append("hard_case_evidence_missing:score")
        elif q_col is not None and target_col is not None:
            tags.append("score_conflict_review_candidate")
            evidence.append("score and target/decoy fields available for conflict review")

    if task_type in {"denovo", "fragment_intensity_prediction"}:
        if intensity_col is None:
            tags.append("hard_case_evidence_missing:spectrum_intensity")
        elif _has_low_intensity(frame[intensity_col]):
            tags.append("low_spectrum_intensity")
            evidence.append("low spectrum intensity signal")
        if task_type == "denovo" and peptide_col is None:
            tags.append("hard_case_evidence_missing:peptide_sequence")

    if task_type == "ptm_denovo":
        text = " ".join(
            str(value)
            for col in ptm_cols
            for value in frame[col].dropna().astype(str).head(1000).tolist()
        ).casefold()
        if any(marker in text for marker in ["ptyr", "phosphotyrosine", "py", "y]"]):
            tags.append("phosphotyrosine_case")
            evidence.append("phosphotyrosine-like modified sequence or metadata")
        if localization_col is None:
            tags.append("ptm_localization_evidence_missing")
        elif pd.to_numeric(frame[localization_col], errors="coerce").dropna().lt(0.75).any():
            tags.append("low_ptm_localization_confidence")

    if task_type == "rt_prediction":
        if row.get("project_accession") and row.get("instrument_families"):
            tags.append("rt_project_instrument_shift_candidate")
            evidence.append("project/instrument metadata available for shift benchmark")
        elif not row.get("project_accession") or not row.get("instrument_families"):
            tags.append("hard_case_evidence_missing:rt_shift_metadata")

    if task_type == "fragment_intensity_prediction":
        if not _split_values(row.get("fragmentation_methods")):
            tags.append("fragmentation_evidence_weak")
        if not row.get("instrument_families"):
            tags.append("hard_case_evidence_missing:instrument")

    return tags, evidence


def _has_low_intensity(series: pd.Series) -> bool:
    checked = 0
    for value in series.dropna().head(100):
        numbers: list[float] = []
        if isinstance(value, list):
            raw_values = value
        else:
            try:
                raw_values = json.loads(str(value))
            except Exception:
                raw_values = []
        for item in raw_values[:1000] if isinstance(raw_values, list) else []:
            try:
                numbers.append(float(item))
            except (TypeError, ValueError):
                continue
        if numbers:
            checked += 1
            if sum(numbers) / len(numbers) < 100.0:
                return True
    return False


def _read_parquet_preview(
    row: dict[str, Any],
    *,
    columns: list[str] | None = None,
    max_rows: int = 5000,
) -> tuple[pd.DataFrame | None, str | None]:
    parquet_path = str(row.get("parquet_path") or "")
    if not parquet_path:
        return None, "parquet_path_missing"
    path = Path(parquet_path)
    if not path.exists():
        return None, f"parquet_missing:{parquet_path}"
    try:
        frame = pd.read_parquet(path)
    except Exception as exc:
        return None, f"parquet_unreadable:{path.name}:{exc}"
    if columns:
        wanted = [col for col in frame.columns if str(col).casefold() in {item.casefold() for item in columns}]
        if wanted:
            frame = frame[wanted]
    return frame.head(max_rows), None


def _hard_row(row: dict[str, Any], tags: list[str], selection: str, evidence: list[str]) -> dict[str, Any]:
    return {
        "selection": selection,
        "run_name": row.get("run_name") or "",
        "repository": row.get("repository") or "unknown",
        "project_accession": row.get("project_accession") or "",
        "source_file": row.get("source_file") or "",
        "task_type": row.get("task_type") or "",
        "rows_out": row.get("rows_out") or 0,
        "tags": tags,
        "hard_case_evidence_status": "missing" if any(str(tag).startswith("hard_case_evidence_missing") for tag in tags) else "available",
        "hard_case_evidence": evidence,
        "blockers": row.get("blockers") or [],
        "warnings": row.get("warnings") or [],
    }


def _hard_benchmark_tag_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for tag in row.get("tags") or []:
            counts[str(tag)] = counts.get(str(tag), 0) + 1
    return dict(sorted(counts.items()))


def _counterfactual_benchmark_rows(
    selected: list[dict[str, Any]],
    excluded: list[dict[str, Any]],
    hard_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    hard_by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {
        _counterfactual_key(row): row for row in hard_rows
    }
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()

    def add_case(row: dict[str, Any], selection: str, case_type: str, tags: list[str], evidence: list[str]) -> None:
        key = (*_counterfactual_key({**row, "selection": selection}), case_type)
        if key in seen:
            return
        seen.add(key)
        rows.append(_counterfactual_row(row, selection, case_type, tags, evidence, len(rows) + 1))

    for row in selected:
        rows_out = _safe_int(row.get("rows_out"))
        hard = hard_by_key.get(_counterfactual_key({**row, "selection": "selected"}), {})
        if rows_out > 0:
            add_case(
                row,
                "selected",
                "positive_training_case",
                ["selected", "label_available"],
                [f"rows_out={rows_out}", "agent selected this output for training recipe"],
            )
        if hard.get("tags"):
            add_case(
                row,
                "selected",
                "hard_positive_case",
                _split_values(hard.get("tags")) or ["hard_case"],
                _split_values(hard.get("hard_case_evidence")) or ["hard benchmark evidence present"],
            )
        if 0 < rows_out < 10:
            add_case(
                row,
                "selected",
                "low_yield_counterfactual",
                ["low_label_yield", "selected"],
                [f"rows_out={rows_out}", "selected despite low yield; compare against higher-yield alternatives"],
            )
        if _metadata_uncertain(row):
            add_case(
                row,
                "selected",
                "metadata_uncertainty_counterfactual",
                ["metadata_uncertain", "selected"],
                _metadata_uncertainty_evidence(row),
            )

    for row in excluded:
        rows_out = _safe_int(row.get("rows_out"))
        blockers = _split_values(row.get("blockers"))
        exclusion = _clean_reason(row.get("exclusion_reason") or row.get("status"))
        hard = hard_by_key.get(_counterfactual_key({**row, "selection": "excluded"}), {})
        if blockers or exclusion not in {"unknown", "completed"}:
            add_case(
                row,
                "excluded",
                "negative_or_blocked_case",
                ["excluded", *(blockers or [exclusion])],
                blockers or [f"exclusion_reason={exclusion}"],
            )
        if _safe_float(row.get("data_value_score")) >= 0.5:
            add_case(
                row,
                "excluded",
                "high_value_blocked_counterfactual",
                ["high_value", "blocked_or_excluded"],
                [f"data_value_score={row.get('data_value_score')}", f"exclusion_reason={exclusion}"],
            )
        if rows_out == 0:
            add_case(
                row,
                "excluded",
                "zero_label_yield_counterfactual",
                ["zero_label_yield", "excluded"],
                ["rows_out=0"],
            )
        if hard.get("tags"):
            add_case(
                row,
                "excluded",
                "hard_negative_case",
                _split_values(hard.get("tags")) or ["hard_case"],
                _split_values(hard.get("hard_case_evidence")) or ["hard benchmark evidence present"],
            )
        if _metadata_uncertain(row):
            add_case(
                row,
                "excluded",
                "metadata_uncertainty_counterfactual",
                ["metadata_uncertain", "excluded"],
                _metadata_uncertainty_evidence(row),
            )

    return rows


def _counterfactual_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("selection") or ""),
        str(row.get("run_name") or row.get("source_file") or ""),
        str(row.get("source_file") or ""),
        str(row.get("task_type") or ""),
    )


def _counterfactual_row(
    row: dict[str, Any],
    selection: str,
    case_type: str,
    tags: list[str],
    evidence: list[str],
    index: int,
) -> dict[str, Any]:
    return {
        "case_id": f"cf_{index:04d}",
        "case_type": case_type,
        "selection": selection,
        "repository": row.get("repository") or "unknown",
        "project_accession": row.get("project_accession") or "",
        "source_file": row.get("source_file") or "",
        "run_name": row.get("run_name") or "",
        "task_type": row.get("task_type") or "",
        "rows_out": row.get("rows_out") or 0,
        "expected_model_behavior": _counterfactual_expected_behavior(case_type),
        "counterfactual_question": _counterfactual_question(row, case_type),
        "tags": _dedupe(tags),
        "evidence": _dedupe(evidence),
        "blockers": row.get("blockers") or [],
        "warnings": row.get("warnings") or [],
        "data_value_score": row.get("data_value_score") or "",
        "task_ai_readiness_score": row.get("task_ai_readiness_score") or "",
        "semantic_metadata_confidence": row.get("semantic_metadata_confidence") or "",
        "ptm_type": row.get("ptm_type") or "",
        "labeling_strategy": row.get("labeling_strategy") or "",
        "canonical_species": row.get("canonical_species") or "",
    }


def _counterfactual_expected_behavior(case_type: str) -> str:
    mapping = {
        "positive_training_case": "model should learn from this selected, label-bearing case",
        "hard_positive_case": "model should remain robust on this selected hard case",
        "low_yield_counterfactual": "agent should justify retaining or replacing this low-yield case",
        "metadata_uncertainty_counterfactual": "agent should request curation or lower confidence before relying on this case",
        "negative_or_blocked_case": "agent should not use this as training-ready without resolving blockers",
        "high_value_blocked_counterfactual": "agent should prioritize review because value may justify recovery",
        "zero_label_yield_counterfactual": "agent should skip or recover before training use",
        "hard_negative_case": "agent should keep this as diagnostic evidence, not training data",
    }
    return mapping.get(case_type, "agent should explain the decision boundary for this case")


def _counterfactual_question(row: dict[str, Any], case_type: str) -> str:
    task = row.get("task_type") or "task"
    source = row.get("source_file") or row.get("run_name") or "this file"
    if case_type == "positive_training_case":
        return f"Would the agent still select `{source}` for `{task}` if a higher-diversity alternative had the same label yield?"
    if case_type == "hard_positive_case":
        return f"Does `{source}` expose a meaningful hard slice for `{task}` rather than ordinary training data?"
    if case_type == "low_yield_counterfactual":
        return f"Should `{source}` remain selected for `{task}` despite low rows_out?"
    if case_type == "metadata_uncertainty_counterfactual":
        return f"Should `{source}` be curated before using its metadata-dependent `{task}` labels?"
    if case_type == "high_value_blocked_counterfactual":
        return f"Is recovery worth attempting for high-value blocked `{source}`?"
    if case_type == "zero_label_yield_counterfactual":
        return f"Should `{source}` be skipped or rerun before considering `{task}`?"
    return f"Can the agent correctly keep `{source}` out of training-ready `{task}` data?"


def _metadata_uncertain(row: dict[str, Any]) -> bool:
    confidence = _safe_float(row.get("semantic_metadata_confidence"))
    return (
        0 < confidence < 0.35
        or _metadata_missing(row)
        or bool(_split_values(row.get("warnings")))
    )


def _metadata_uncertainty_evidence(row: dict[str, Any]) -> list[str]:
    evidence: list[str] = []
    confidence = _safe_float(row.get("semantic_metadata_confidence"))
    if 0 < confidence < 0.35:
        evidence.append("semantic_metadata_confidence<0.35")
    for field in ["canonical_species", "instrument_families", "fragmentation_methods", "sample_name"]:
        if not row.get(field):
            evidence.append(f"{field}_missing")
    for warning in _split_values(row.get("warnings"))[:5]:
        evidence.append(f"warning:{warning}")
    return evidence or ["metadata uncertainty detected"]


def _counterfactual_tag_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for tag in row.get("tags") or []:
            counts[str(tag)] = counts.get(str(tag), 0) + 1
    return dict(sorted(counts.items()))


def _coverage_gap_report(
    selected: list[dict[str, Any]],
    excluded: list[dict[str, Any]],
    *,
    repository_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    targets = {
        "instrument_families": ["orbitrap", "qtof", "tims", "ion_trap"],
        "fragmentation_methods": ["HCD", "CID", "ETD", "EThcD"],
        "ptm_type": ["phospho", "acetyl", "ubiquitin", "glyco", "methyl"],
        "labeling_strategy": ["label_free", "TMT", "iTRAQ"],
        "repository": ["pride", "massive", "iprox"],
    }
    distributions = {
        dimension: _distribution(selected, dimension)
        for dimension in [
            *targets,
            "canonical_species",
            "species_policy",
            "lab",
            "sample_name",
            "condition",
            "enzyme",
            "database",
            "workflow",
            "search_engine",
            "acquisition_mode",
            "ptm_enrichment_methods",
        ]
    }
    distributions["task_rows"] = _task_rows(selected)
    distributions["charge"] = _charge_distribution(selected)
    gaps = []
    for dimension, expected in targets.items():
        present = {value.casefold() for value in distributions.get(dimension, {})}
        missing = [value for value in expected if value.casefold() not in present]
        if missing:
            gaps.append({"dimension": dimension, "missing": missing, "priority": _gap_priority(dimension)})
    blocked_reasons = _blocked_reason_counts(excluded)
    if blocked_reasons:
        gaps.append({"dimension": "blocked_reason", "missing": list(blocked_reasons)[:10], "priority": "diagnostic"})
    if selected and len(distributions.get("canonical_species", {})) <= 1:
        gaps.append({"dimension": "canonical_species", "missing": ["additional_species_diversity"], "priority": "medium"})
    repository_blockers = _repository_blockers(repository_audit or {})
    for row in repository_blockers:
        gaps.append(
            {
                "dimension": "repository_blocker",
                "repository": row.get("repository") or "unknown",
                "blocker": row.get("blocker") or row.get("status") or "unknown",
                "next_step": row.get("next_step") or "review_repository_discovery_failure",
                "missing": [row.get("blocker") or row.get("status") or "unknown"],
                "priority": "high",
            }
        )
    return {
        "selected_count": len(selected),
        "excluded_count": len(excluded),
        "distributions": distributions,
        "gaps": gaps,
        "blocked_reason_counts": blocked_reasons,
        "repository_blockers": repository_blockers,
    }


def _agent_expansion_plan(coverage_gap_report: dict[str, Any]) -> dict[str, Any]:
    actions = []
    for gap in coverage_gap_report.get("gaps") or []:
        dimension = gap.get("dimension")
        if dimension == "repository_blocker":
            actions.append(
                {
                    "action": gap.get("next_step") or "review_repository_discovery_failure",
                    "dimension": "repository",
                    "repository": gap.get("repository") or "unknown",
                    "blocker": gap.get("blocker") or "unknown",
                    "reason": f"repository_blocker:{gap.get('repository') or 'unknown'}:{gap.get('blocker') or 'unknown'}",
                    "query_hint": "",
                    "requires_user_confirmation": True,
                }
            )
            continue
        for value in gap.get("missing") or []:
            if dimension == "blocked_reason":
                actions.append(
                    {
                        "action": "review_failure_taxonomy",
                        "reason": value,
                        "query_hint": "",
                        "requires_user_confirmation": False,
                    }
                )
                continue
            actions.append(
                {
                    "action": "plan_discovery_query",
                    "dimension": dimension,
                    "target": value,
                    "query_hint": _query_hint(dimension, str(value)),
                    "requires_user_confirmation": True,
                }
            )
    return {
        "status": "ready" if actions else "no_major_gap_detected",
        "actions": actions,
        "notes": [
            "V1 only plans expansion; it does not download or run full workflow automatically.",
            "Use data_value_score and task_ai_readiness_score before selecting new candidates.",
        ],
    }


def _repository_blockers(repository_audit: dict[str, Any]) -> list[dict[str, Any]]:
    rows = repository_audit.get("rows") if isinstance(repository_audit.get("rows"), list) else []
    blockers: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "")
        blocker = str(row.get("blocker") or "")
        if status not in {"blocked", "no_selected_files"} and not blocker:
            continue
        blockers.append(
            {
                "repository": row.get("repository") or "unknown",
                "status": status or "unknown",
                "blocker": blocker or status or "unknown",
                "next_step": row.get("next_step") or "review_repository_discovery_failure",
                "support_status": row.get("support_status") or "",
            }
        )
    return blockers


def _evidence_graph(
    selected: list[dict[str, Any]],
    excluded: list[dict[str, Any]],
    leakage_risk_report: dict[str, Any],
    *,
    split_rows: list[dict[str, Any]],
    hard_benchmark_rows: list[dict[str, Any]],
    counterfactual_rows: list[dict[str, Any]],
    curation_queue: list[dict[str, Any]],
    repository_audit: dict[str, Any],
) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []

    def add_node(node_id: str, node_type: str, **attrs: Any) -> None:
        nodes.setdefault(node_id, {"id": node_id, "type": node_type, **attrs})

    def add_edge(source: str, target: str, relation: str) -> None:
        edges.append({"source": source, "target": target, "relation": relation})

    audit_rows = repository_audit.get("rows") if isinstance(repository_audit.get("rows"), list) else []
    for row in audit_rows:
        if not isinstance(row, dict):
            continue
        repository = str(row.get("repository") or "unknown")
        attempt_id = f"repository_attempt:{repository}"
        add_node(
            attempt_id,
            "repository_attempt",
            repository=repository,
            status=row.get("status") or "",
            support_status=row.get("support_status") or "",
            selected_files=row.get("selected_files") or 0,
            blocker=row.get("blocker") or "",
            next_step=row.get("next_step") or "",
        )
        if row.get("blocker"):
            blocker_id = f"blocker:{row.get('blocker')}"
            add_node(blocker_id, "blocker", label=row.get("blocker"))
            add_edge(blocker_id, attempt_id, "blocks")

    for selection, rows in [("selected", selected), ("excluded", excluded)]:
        for row in rows:
            project_id = f"project:{row.get('repository') or 'unknown'}:{row.get('project_accession') or 'unknown'}"
            file_id = f"file:{row.get('source_file') or row.get('run_name') or 'unknown'}"
            sample_id = f"sample:{row.get('run_name') or row.get('source_file') or 'unknown'}"
            task_id = f"task:{row.get('task_type') or 'unknown'}"
            output_id = f"output:{selection}:{row.get('run_name') or row.get('source_file') or 'unknown'}:{row.get('task_type') or 'unknown'}"
            add_node(project_id, "project", repository=row.get("repository"), accession=row.get("project_accession"))
            attempt_id = f"repository_attempt:{row.get('repository') or 'unknown'}"
            if attempt_id in nodes:
                add_edge(attempt_id, project_id, "supports" if selection == "selected" else "blocks")
            add_node(file_id, "file", source_file=row.get("source_file"), selection=selection)
            add_node(sample_id, "sample", run_name=row.get("run_name"), metadata_quality=row.get("metadata_quality"))
            add_node(task_id, "task", task_type=row.get("task_type"))
            add_node(output_id, "output", status=row.get("status"), rows_out=row.get("rows_out"))
            add_edge(file_id, project_id, "belongs_to")
            add_edge(sample_id, file_id, "belongs_to")
            add_edge(output_id, file_id, "generated_from")
            add_edge(output_id, task_id, "supports" if selection == "selected" else "blocks")
            decision_reason = row.get("selection_reason") if selection == "selected" else row.get("exclusion_reason")
            if decision_reason:
                decision_id = f"decision:{selection}:{decision_reason}"
                add_node(decision_id, "decision", selection=selection, reason=decision_reason)
                add_edge(output_id, decision_id, "selected_because" if selection == "selected" else "excluded_because")
            if row.get("parquet_path"):
                parquet_id = f"parquet:{row.get('parquet_path')}"
                add_node(parquet_id, "parquet", path=row.get("parquet_path"), task_type=row.get("task_type"))
                add_edge(parquet_id, output_id, "generates")
                for context_type, values in _parquet_context_values(row).items():
                    for value in values:
                        context_id = f"{context_type}:{value}"
                        add_node(context_id, context_type, value=value)
                        add_edge(context_id, parquet_id, "supports")
            _add_project_context_nodes(row, add_node, add_edge, project_id, file_id, sample_id, output_id)
            for field in [
                "canonical_species",
                "instrument_families",
                "fragmentation_methods",
                "ptm_type",
                "labeling_strategy",
                "semantic_metadata_confidence",
            ]:
                for value in _split_values(row.get(field)) or ([] if row.get(field) in {None, ""} else [str(row.get(field))]):
                    evidence_id = f"metadata:{field}:{value}"
                    add_node(evidence_id, "metadata_evidence", field=field, value=value)
                    add_edge(evidence_id, output_id, "supports")
            for blocker in _split_values(row.get("blockers")):
                blocker_id = f"blocker:{blocker}"
                add_node(blocker_id, "blocker", label=blocker)
                add_edge(blocker_id, output_id, "blocks")
    for row in split_rows:
        split_id = f"split:{row.get('split') or 'unknown'}"
        output_id = f"output:selected:{row.get('run_name') or row.get('source_file') or 'unknown'}:{row.get('task_type') or 'unknown'}"
        add_node(split_id, "split", split=row.get("split"), strategy=row.get("split_strategy"), key=row.get("split_key"))
        add_edge(output_id, split_id, "assigned_to_split")
    for row in hard_benchmark_rows:
        hard_id = f"hard:{row.get('selection')}:{row.get('run_name') or row.get('source_file') or 'unknown'}:{row.get('task_type') or 'unknown'}"
        output_id = f"output:{row.get('selection')}:{row.get('run_name') or row.get('source_file') or 'unknown'}:{row.get('task_type') or 'unknown'}"
        add_node(hard_id, "hard_case", tags=row.get("tags"), evidence_status=row.get("hard_case_evidence_status"))
        add_edge(hard_id, output_id, "supports")
    for row in counterfactual_rows:
        case_id = f"counterfactual:{row.get('case_id') or row.get('case_type')}"
        output_id = f"output:{row.get('selection')}:{row.get('run_name') or row.get('source_file') or 'unknown'}:{row.get('task_type') or 'unknown'}"
        add_node(
            case_id,
            "counterfactual_case",
            case_type=row.get("case_type"),
            expected_model_behavior=row.get("expected_model_behavior"),
            tags=row.get("tags"),
        )
        add_edge(case_id, output_id, "evaluates")
    for index, row in enumerate(curation_queue):
        curation_id = str(row.get("curation_id") or f"curation:{index}:{row.get('curation_type') or row.get('reason')}")
        output_id = f"output:{row.get('selection') or 'selected'}:{row.get('run_name') or row.get('source_file') or 'unknown'}:{row.get('task_type') or 'unknown'}"
        add_node(curation_id, "curation_item", **row)
        add_edge(output_id, curation_id, "needs_curation")
    leakage_id = "evidence:leakage_risk"
    add_node(leakage_id, "evidence", status=leakage_risk_report.get("status"))
    for row in selected:
        output_id = f"output:selected:{row.get('run_name') or row.get('source_file') or 'unknown'}:{row.get('task_type') or 'unknown'}"
        add_edge(leakage_id, output_id, "evaluates")
    return {"nodes": list(nodes.values()), "edges": edges}


def _add_project_context_nodes(
    row: dict[str, Any],
    add_node: Any,
    add_edge: Any,
    project_id: str,
    file_id: str,
    sample_id: str,
    output_id: str,
) -> None:
    for value in _split_values(row.get("canonical_species") or row.get("species")):
        node_id = f"organism:{value}"
        add_node(node_id, "organism", value=value, taxon_id=row.get("organism_taxon_id") or "")
        add_edge(sample_id, node_id, "has_organism")
        add_edge(node_id, output_id, "supports")
    for value in _split_values(row.get("instrument_families")):
        node_id = f"instrument:{value}"
        add_node(node_id, "instrument", value=value)
        add_edge(file_id, node_id, "acquired_by")
        add_edge(node_id, output_id, "supports")
    for field, node_type, relation in [
        ("fragmentation_methods", "acquisition", "uses_acquisition"),
        ("acquisition_mode", "acquisition", "uses_acquisition"),
        ("enzyme", "enzyme", "uses_enzyme"),
        ("database", "database", "uses_database"),
        ("workflow", "workflow", "generated_by_workflow"),
        ("search_engine", "workflow", "generated_by_workflow"),
        ("labeling_strategy", "labeling", "uses_labeling"),
    ]:
        for value in _split_values(row.get(field)):
            node_id = f"{node_type}:{field}:{value}"
            add_node(node_id, node_type, field=field, value=value)
            add_edge(output_id, node_id, relation)
    for field, node_type in [
        ("ptm_type", "ptm"),
        ("ptm_subtype", "ptm"),
        ("ptm_enrichment_methods", "ptm"),
    ]:
        for value in _split_values(row.get(field)):
            node_id = f"{node_type}:{field}:{value}"
            add_node(node_id, node_type, field=field, value=value)
            add_edge(node_id, output_id, "supports")
    for value in _split_values(row.get("condition")):
        node_id = f"condition:{value}"
        add_node(node_id, "condition", value=value)
        add_edge(sample_id, node_id, "has_condition")
    for value in _split_values(row.get("lab") or row.get("submitter")):
        node_id = f"lab:{value}"
        add_node(node_id, "lab", value=value)
        add_edge(project_id, node_id, "submitted_by")
    for field in ["full_status", "ai_ready_outcome", "metadata_quality"]:
        value = str(row.get(field) or "").strip()
        if not value:
            continue
        node_id = f"qc:{field}:{value}"
        add_node(node_id, "qc", field=field, value=value)
        add_edge(node_id, output_id, "supports" if "blocked" not in value and "failed" not in value else "blocks")


def _parquet_context_values(row: dict[str, Any]) -> dict[str, list[str]]:
    frame, warning = _read_parquet_preview(row, max_rows=500)
    if warning or frame is None or frame.empty:
        return {}
    context: dict[str, list[str]] = {}
    column_groups = {
        "peptide": PEPTIDE_COLUMNS,
        "modified_peptide": MODIFIED_SEQUENCE_COLUMNS,
        "protein": PROTEIN_COLUMNS,
    }
    for node_type, candidates in column_groups.items():
        column = _first_column(frame, candidates)
        if column is None:
            continue
        values: list[str] = []
        for value in frame[column].dropna().astype(str).head(200):
            values.extend(_split_values(value))
        context[node_type] = _dedupe(values)[:25]
    return context


def _curation_queue(
    selected: list[dict[str, Any]],
    excluded: list[dict[str, Any]],
    leakage_risk_report: dict[str, Any],
    *,
    repository_audit: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    leakage_failed = leakage_risk_report.get("status") == "fail"
    for row in excluded:
        value = _safe_float(row.get("data_value_score"))
        readiness = _safe_float(row.get("task_ai_readiness_score"))
        semantic_confidence = _safe_float(row.get("semantic_metadata_confidence"))
        if value >= 0.45 or readiness >= 0.55:
            rows.append(_curation_row(row, "review_high_value_blocked", "review", "high_value_blocked", selection="excluded"))
        elif semantic_confidence and semantic_confidence < 0.35:
            rows.append(_curation_row(row, "confirm_ptm_semantics", "review_metadata", "semantic_metadata_low_confidence", selection="excluded"))
        elif _metadata_missing(row):
            rows.append(_curation_row(row, "review_metadata_missing", "review_metadata", "metadata_missing", selection="excluded"))
        elif value <= 0.2 and (_split_values(row.get("blockers")) or row.get("exclusion_reason")):
            rows.append(_curation_row(row, "exclude_low_value_high_risk", "skip", "low_value_high_risk", selection="excluded"))
    if leakage_failed:
        for row in selected:
            rows.append(_curation_row(row, "check_leakage_risk", "review_split", "potential_leakage_risk", selection="selected"))
    for row in selected:
        if _safe_int(row.get("rows_out")) < 10:
            rows.append(_curation_row(row, "inspect_low_yield", "review_output", "low_label_yield", selection="selected"))
        if str(row.get("species_policy") or "") in {"include_only", "exclude"} and not row.get("canonical_species"):
            rows.append(_curation_row(row, "confirm_species_policy", "review_metadata", "species_policy_without_species_evidence", selection="selected"))
        if not row.get("sample_name") and not row.get("condition"):
            rows.append(_curation_row(row, "review_metadata_missing", "review_metadata", "sample_condition_context_missing", selection="selected"))
    rows.extend(_repository_curation_rows(repository_audit or {}))
    rows.sort(key=lambda item: (-_safe_float(item.get("priority_score")), str(item.get("curation_type") or "")))
    return rows


def _repository_curation_rows(repository_audit: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for blocker in _repository_blockers(repository_audit):
        repository = str(blocker.get("repository") or "unknown")
        reason = str(blocker.get("blocker") or "repository_blocked")
        next_step = str(blocker.get("next_step") or "review_repository_discovery_failure")
        rows.append(
            {
                "curation_id": f"curation:repository:{repository}:{reason}".replace(":", "_", 1),
                "action": next_step,
                "curation_type": "review_repository_blocker",
                "priority_score": 0.88 if reason == "iprox_index_missing" else 0.74,
                "reason": reason,
                "selection": "repository_audit",
                "repository": repository,
                "project_accession": "",
                "source_file": "",
                "task_type": "",
                "sample_name": "",
                "condition": "",
                "lab": "",
                "data_value_score": "",
                "task_ai_readiness_score": "",
                "blockers": [reason],
                "warnings": [],
                "next_step": next_step,
            }
        )
    return rows


def _metadata_missing(row: dict[str, Any]) -> bool:
    return (
        not row.get("canonical_species")
        or not row.get("instrument_families")
        or not row.get("fragmentation_methods")
        or not row.get("sample_name")
    )


def _curation_row(row: dict[str, Any], curation_type: str, action: str, reason: str, *, selection: str) -> dict[str, Any]:
    priority = _curation_priority(row, curation_type, action)
    return {
        "curation_id": _curation_id(row, curation_type, selection),
        "action": action,
        "curation_type": curation_type,
        "priority_score": round(priority, 3),
        "reason": reason,
        "selection": selection,
        "run_name": row.get("run_name") or "",
        "repository": row.get("repository") or "unknown",
        "project_accession": row.get("project_accession") or "",
        "source_file": row.get("source_file") or "",
        "task_type": row.get("task_type") or "",
        "sample_name": row.get("sample_name") or "",
        "condition": row.get("condition") or "",
        "lab": row.get("lab") or "",
        "data_value_score": row.get("data_value_score") or "",
        "task_ai_readiness_score": row.get("task_ai_readiness_score") or "",
        "blockers": row.get("blockers") or [],
        "warnings": row.get("warnings") or [],
    }


def _curation_id(row: dict[str, Any], curation_type: str, selection: str) -> str:
    values = [
        selection,
        row.get("repository") or "unknown",
        row.get("project_accession") or "unknown_project",
        row.get("source_file") or row.get("run_name") or "unknown_file",
        row.get("task_type") or "unknown_task",
        curation_type,
    ]
    return "curation:" + ":".join(
        str(value or "").strip().replace(":", "_").replace("|", "_").replace("\\", "/")
        for value in values
    )


def _curation_priority(row: dict[str, Any], curation_type: str, action: str) -> float:
    value = _safe_float(row.get("data_value_score"))
    readiness = _safe_float(row.get("task_ai_readiness_score"))
    uncertainty = 1.0 - min(1.0, max(_safe_float(row.get("semantic_metadata_confidence")), 0.0))
    base = max(value, readiness, 0.25)
    if curation_type == "check_leakage_risk":
        base = max(base, 0.9)
    elif curation_type == "review_high_value_blocked":
        base = max(base, 0.75)
    elif curation_type in {"confirm_ptm_semantics", "review_metadata_missing", "confirm_species_policy"}:
        base = max(base, 0.45) + min(0.25, uncertainty * 0.25)
    elif curation_type == "inspect_low_yield":
        base = max(base, 0.4)
    elif curation_type == "exclude_low_value_high_risk" or action == "skip":
        base = min(base, 0.1)
    return min(1.0, base)


def _curation_efficiency_report(
    selected: list[dict[str, Any]],
    excluded: list[dict[str, Any]],
    curation_queue: list[dict[str, Any]],
    leakage_risk_report: dict[str, Any],
) -> dict[str, Any]:
    total_items = len(selected) + len(excluded)
    queue_count = len(curation_queue)
    auto_selected = len([row for row in selected if not _requires_curation(row, curation_queue)])
    auto_skipped = len([row for row in excluded if not _requires_curation(row, curation_queue)])
    review_reduction = 0.0 if total_items == 0 else round(max(0, total_items - queue_count) / total_items, 6)
    critical_types = {
        "check_leakage_risk",
        "review_high_value_blocked",
        "confirm_ptm_semantics",
        "confirm_species_policy",
        "review_repository_blocker",
    }
    critical_items = [row for row in curation_queue if row.get("curation_type") in critical_types]
    leakage_covered = any(row.get("curation_type") == "check_leakage_risk" for row in curation_queue)
    high_value_blocked_covered = any(row.get("curation_type") == "review_high_value_blocked" for row in curation_queue)
    ptm_uncertainty_covered = any(row.get("curation_type") == "confirm_ptm_semantics" for row in curation_queue)
    rows = [
        {"metric": "manual_only_review_count", "value": total_items},
        {"metric": "agent_assisted_review_count", "value": queue_count},
        {"metric": "auto_process_count", "value": auto_selected},
        {"metric": "auto_skip_count", "value": auto_skipped},
        {"metric": "review_reduction_rate", "value": review_reduction},
        {"metric": "critical_curation_item_count", "value": len(critical_items)},
        {"metric": "leakage_risk_covered", "value": leakage_covered},
        {"metric": "high_value_blocked_covered", "value": high_value_blocked_covered},
        {"metric": "ptm_uncertainty_covered", "value": ptm_uncertainty_covered},
    ]
    return {
        "status": "ready" if total_items else "not_evaluated",
        "manual_only_review_count": total_items,
        "agent_assisted_review_count": queue_count,
        "auto_process_count": auto_selected,
        "auto_skip_count": auto_skipped,
        "review_reduction_rate": review_reduction,
        "critical_curation_item_count": len(critical_items),
        "curation_type_counts": _counts([str(row.get("curation_type") or "unknown") for row in curation_queue]),
        "action_counts": _counts([str(row.get("action") or "unknown") for row in curation_queue]),
        "priority_summary": _curation_priority_summary(curation_queue),
        "critical_issue_coverage": {
            "leakage_risk": leakage_covered,
            "high_value_blocked": high_value_blocked_covered,
            "ptm_semantic_uncertainty": ptm_uncertainty_covered,
            "leakage_status": leakage_risk_report.get("status") or "not_evaluated",
        },
        "rows": rows,
        "top_review_items": curation_queue[:20],
        "notes": [
            "manual_only_review_count assumes every selected/excluded task output would be manually inspected.",
            "agent_assisted_review_count is the prioritized active curation queue size.",
            "Low-value high-risk items can be auto-skipped instead of consuming expert review time.",
        ],
    }


def _requires_curation(row: dict[str, Any], curation_queue: list[dict[str, Any]]) -> bool:
    key = (
        str(row.get("run_name") or ""),
        str(row.get("source_file") or ""),
        str(row.get("task_type") or ""),
    )
    for item in curation_queue:
        item_key = (
            str(item.get("run_name") or ""),
            str(item.get("source_file") or ""),
            str(item.get("task_type") or ""),
        )
        if key == item_key:
            return True
    return False


def _curation_priority_summary(curation_queue: list[dict[str, Any]]) -> dict[str, Any]:
    values = sorted([_safe_float(row.get("priority_score")) for row in curation_queue], reverse=True)
    if not values:
        return {"max": 0.0, "mean": 0.0, "high_priority_count": 0}
    return {
        "max": round(values[0], 3),
        "mean": round(sum(values) / len(values), 3),
        "high_priority_count": len([value for value in values if value >= 0.75]),
    }


def _leakage_check(split_rows: list[dict[str, Any]]) -> dict[str, Any]:
    warnings: list[str] = []
    peptide_splits: dict[str, set[str]] = {}
    modified_splits: dict[str, set[str]] = {}
    source_splits: dict[str, set[str]] = {}
    project_splits: dict[str, set[str]] = {}
    lab_splits: dict[str, set[str]] = {}
    protein_splits: dict[str, set[str]] = {}
    rows_scanned = 0
    for row in split_rows:
        split = str(row.get("split") or "")
        parquet_path = str(row.get("parquet_path") or "")
        if row.get("source_file"):
            source_splits.setdefault(str(row["source_file"]), set()).add(split)
        if row.get("project_accession"):
            project_splits.setdefault(str(row["project_accession"]), set()).add(split)
        for lab in _split_values(row.get("lab") or row.get("submitter")):
            lab_splits.setdefault(lab, set()).add(split)
        if not parquet_path:
            continue
        path = Path(parquet_path)
        if not path.exists():
            warnings.append(f"parquet_missing:{parquet_path}")
            continue
        try:
            frame = pd.read_parquet(path)
        except Exception as exc:
            warnings.append(f"parquet_unreadable:{path.name}:{exc}")
            continue
        rows_scanned += len(frame)
        modified_col = _first_column(frame, MODIFIED_SEQUENCE_COLUMNS)
        peptide_col = _first_column(frame, PEPTIDE_COLUMNS)
        protein_col = _first_column(frame, PROTEIN_COLUMNS)
        charge_col = _first_column(frame, ["charge", "precursor_charge"])
        if peptide_col is None and modified_col is None and protein_col is None:
            warnings.append(f"sequence_or_protein_column_missing:{path.name}")
            continue
        columns = [col for col in [peptide_col, modified_col, protein_col, charge_col] if col is not None]
        for _, item in frame[columns].dropna(how="all").head(200000).iterrows():
            charge = str(item[charge_col]) if charge_col is not None else ""
            if peptide_col is not None and not pd.isna(item.get(peptide_col)):
                peptide_splits.setdefault(f"{item[peptide_col]}|{charge}", set()).add(split)
            if modified_col is not None and not pd.isna(item.get(modified_col)):
                modified_splits.setdefault(f"{item[modified_col]}|{charge}", set()).add(split)
            if protein_col is not None and not pd.isna(item.get(protein_col)):
                for protein in _split_values(item.get(protein_col)):
                    protein_splits.setdefault(protein, set()).add(split)
    peptide_leaks = sorted(key for key, splits in peptide_splits.items() if len(splits) > 1)
    modified_leaks = sorted(key for key, splits in modified_splits.items() if len(splits) > 1)
    source_leaks = sorted(key for key, splits in source_splits.items() if len(splits) > 1)
    project_leaks = sorted(key for key, splits in project_splits.items() if len(splits) > 1)
    lab_leaks = sorted(key for key, splits in lab_splits.items() if len(splits) > 1)
    protein_leaks = sorted(key for key, splits in protein_splits.items() if len(splits) > 1)
    status = "passed"
    if peptide_leaks or modified_leaks or source_leaks or project_leaks or lab_leaks or protein_leaks:
        status = "leakage_detected"
    elif not split_rows:
        status = "not_evaluated"
    return {
        "status": status,
        "rows_scanned": rows_scanned,
        "peptide_charge_leak_count": len(peptide_leaks),
        "modified_sequence_charge_leak_count": len(modified_leaks),
        "source_file_leak_count": len(source_leaks),
        "project_leak_count": len(project_leaks),
        "lab_leak_count": len(lab_leaks),
        "protein_leak_count": len(protein_leaks),
        "peptide_charge_leaks_preview": peptide_leaks[:20],
        "modified_sequence_charge_leaks_preview": modified_leaks[:20],
        "source_file_leaks_preview": source_leaks[:20],
        "project_leaks_preview": project_leaks[:20],
        "lab_leaks_preview": lab_leaks[:20],
        "protein_leaks_preview": protein_leaks[:20],
        "warnings": sorted(set(warnings)),
    }


def _first_column(frame: pd.DataFrame, candidates: list[str]) -> str | None:
    by_lower = {str(column).casefold(): str(column) for column in frame.columns}
    for candidate in candidates:
        column = by_lower.get(candidate.casefold())
        if column is not None:
            return column
    return None


def _split_distribution(rows: list[dict[str, Any]], field: str) -> dict[str, dict[str, int]]:
    distribution: dict[str, dict[str, int]] = {}
    for row in rows:
        split = str(row.get("split") or "unknown")
        distribution.setdefault(split, {})
        for value in _split_values(row.get(field)) or ["unknown"]:
            distribution[split][value] = distribution[split].get(value, 0) + 1
    return {split: dict(sorted(values.items())) for split, values in sorted(distribution.items())}


def _distribution(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for value in _split_values(row.get(field)) or ["unknown"]:
            counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _task_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        task = str(row.get("task_type") or "unknown")
        counts[task] = counts.get(task, 0) + _safe_int(row.get("rows_out"))
    return dict(sorted(counts.items()))


def _charge_distribution(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        parquet_path = str(row.get("parquet_path") or "")
        if not parquet_path:
            continue
        path = Path(parquet_path)
        if not path.exists():
            continue
        try:
            frame = pd.read_parquet(path, columns=None)
        except Exception:
            continue
        charge_col = _first_column(frame, ["charge", "precursor_charge"])
        if charge_col is None:
            continue
        for value in frame[charge_col].dropna().head(200000):
            key = str(value)
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _blocked_reason_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        reasons = _split_values(row.get("blockers")) or [_clean_reason(row.get("exclusion_reason"))]
        for reason in reasons:
            if not reason:
                continue
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _gap_priority(dimension: str) -> str:
    if dimension in {"canonical_species", "ptm_type", "fragmentation_methods"}:
        return "high"
    if dimension in {"instrument_families", "labeling_strategy", "repository"}:
        return "medium"
    return "low"


def _query_hint(dimension: str, value: str) -> str:
    mapping = {
        "canonical_species": value.replace("_", " "),
        "instrument_families": value,
        "fragmentation_methods": value,
        "ptm_type": value,
        "labeling_strategy": value,
        "repository": f"repository:{value}",
    }
    return f"{mapping.get(dimension, value)} DDA mzML small file"


def _leakage_recommendations(status: str, issue_counts: dict[str, int]) -> list[str]:
    if status == "pass":
        return ["split_is_usable_for_v1_recipe"]
    recommendations: list[str] = []
    if issue_counts.get("project"):
        recommendations.append("use_project_disjoint_split_or_reduce_project_overlap")
    if issue_counts.get("lab"):
        recommendations.append("use_lab_disjoint_split_when_submitter_or_lab_metadata_is_available")
    if issue_counts.get("source_file"):
        recommendations.append("keep_source_file_in_single_split")
    if issue_counts.get("protein"):
        recommendations.append("inspect_protein_level_overlap_or_use_protein_disjoint_split_for_protein_family_benchmarks")
    if issue_counts.get("peptide_charge") or issue_counts.get("modified_sequence_charge"):
        recommendations.append("use_peptide_or_modified_sequence_disjoint_split_when_enough_data")
    if not recommendations:
        recommendations.append("review_split_balance_before_training")
    return recommendations


def _markdown_leakage_risk(report: dict[str, Any]) -> str:
    lines = [
        "# Leakage Risk Report",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Issue counts: `{json.dumps(report.get('issue_counts') or {}, ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Recommendations",
        "",
    ]
    for item in report.get("recommendations") or []:
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def _markdown_split_baseline_evaluation(report: dict[str, Any]) -> str:
    lines = [
        "# Split Baseline Evaluation",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Agent strategy: `{report.get('agent_strategy')}`",
        f"- Best strategy by leakage: `{report.get('best_strategy_by_leakage')}`",
        f"- Agent leakage issues: {report.get('agent_total_leakage_issue_count', 0)}",
        f"- Random leakage issues: {report.get('random_total_leakage_issue_count', 0)}",
        f"- Agent minus random leakage: {report.get('agent_minus_random_leakage', 0)}",
        f"- Interpretation: `{report.get('interpretation')}`",
        "",
        "## Strategy Rows",
        "",
    ]
    rows = report.get("strategy_rows") or []
    if not rows:
        lines.append("- No split strategies evaluated.")
    for row in rows:
        lines.append(
            f"- `{row.get('strategy')}` status={row.get('status')} "
            f"issues={row.get('total_leakage_issue_count')} "
            f"splits=`{json.dumps(row.get('split_counts') or {}, ensure_ascii=False, sort_keys=True)}` "
            f"recommendation=`{row.get('recommendation')}`"
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
        ]
    )
    for item in report.get("notes") or []:
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def _markdown_coverage_gap(report: dict[str, Any], plan: dict[str, Any]) -> str:
    lines = [
        "# Coverage Gap Report",
        "",
        f"- Selected outputs: {report.get('selected_count', 0)}",
        f"- Excluded outputs: {report.get('excluded_count', 0)}",
        f"- Expansion status: `{plan.get('status')}`",
        "",
        "## Gaps",
        "",
    ]
    gaps = report.get("gaps") or []
    if not gaps:
        lines.append("- No major coverage gap detected.")
    for gap in gaps:
        lines.append(f"- `{gap.get('dimension')}` missing `{', '.join(map(str, gap.get('missing') or []))}` ({gap.get('priority')})")
    lines.extend(["", "## Planned Actions", ""])
    for action in plan.get("actions") or []:
        lines.append(f"- `{action.get('action')}` {action.get('dimension') or action.get('reason') or ''}: {action.get('query_hint') or action.get('target') or ''}")
    return "\n".join(lines) + "\n"


def _markdown_split_rationale(rationale: dict[str, Any]) -> str:
    lines = [
        "# Split Rationale",
        "",
        f"- Requested strategy: `{rationale.get('requested_strategy')}`",
        f"- Resolved strategy: `{rationale.get('resolved_strategy')}`",
        f"- Split level: `{rationale.get('split_level')}`",
        f"- Key field: `{rationale.get('key_field')}`",
        f"- Group count: {rationale.get('group_count', 0)}",
        f"- Fallback rows: {rationale.get('fallback_used_count', 0)}",
        "",
        "## Notes",
        "",
    ]
    for item in rationale.get("notes") or []:
        lines.append(f"- {item}")
    warnings = rationale.get("warnings") or []
    if warnings:
        lines.extend(["", "## Warnings", ""])
        for item in warnings:
            lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def _markdown_counterfactual_benchmark(payload: dict[str, Any]) -> str:
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    lines = [
        "# Counterfactual Benchmark Report",
        "",
        f"- Rows: {payload.get('row_count', len(rows))}",
        f"- Case types: `{json.dumps(payload.get('case_type_counts') or {}, ensure_ascii=False, sort_keys=True)}`",
        f"- Tags: `{json.dumps(payload.get('tag_counts') or {}, ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Interpretation",
        "",
        "- These rows are evaluation and review candidates, not additional training labels.",
        "- They help test whether the agent can distinguish selected positives, hard positives, blocked negatives, low-yield cases, and metadata-uncertain cases.",
        "",
        "## Top Cases",
        "",
    ]
    if not rows:
        lines.append("- No counterfactual benchmark row.")
    for row in rows[:50]:
        lines.append(
            f"- `{row.get('case_type')}` / `{row.get('task_type')}` / `{row.get('source_file')}`: "
            f"{row.get('counterfactual_question')} "
            f"Expected: {row.get('expected_model_behavior')}"
        )
    notes = payload.get("notes") or []
    if notes:
        lines.extend(["", "## Notes", ""])
        for item in notes:
            lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def _markdown_evidence_graph_summary(
    graph: dict[str, Any],
    selected: list[dict[str, Any]],
    excluded: list[dict[str, Any]],
    hard_rows: list[dict[str, Any]],
    counterfactual_rows: list[dict[str, Any]],
    curation_queue: list[dict[str, Any]],
) -> str:
    node_counts = _counts([str(node.get("type") or "unknown") for node in graph.get("nodes") or [] if isinstance(node, dict)])
    lines = [
        "# Evidence Graph Summary",
        "",
        f"- Nodes: {len(graph.get('nodes') or [])}",
        f"- Edges: {len(graph.get('edges') or [])}",
        f"- Node types: `{json.dumps(node_counts, ensure_ascii=False, sort_keys=True)}`",
        f"- Repository attempts: {node_counts.get('repository_attempt', 0)}",
        f"- Selected outputs: {len(selected)}",
        f"- Excluded outputs: {len(excluded)}",
        f"- Hard benchmark rows: {len(hard_rows)}",
        f"- Counterfactual benchmark rows: {len(counterfactual_rows)}",
        f"- Curation items: {len(curation_queue)}",
        "",
        "## Why Selected",
        "",
    ]
    for row in selected[:20]:
        lines.append(
            f"- `{row.get('task_type')}` / `{row.get('source_file')}` selected because `{row.get('selection_reason')}` "
            f"with {row.get('rows_out', 0)} rows."
        )
    if not selected:
        lines.append("- No selected outputs.")
    repository_attempts = [
        node for node in graph.get("nodes") or []
        if isinstance(node, dict) and node.get("type") == "repository_attempt"
    ]
    if repository_attempts:
        lines.extend(["", "## Repository Audit Evidence", ""])
        for node in repository_attempts:
            lines.append(
                f"- `{node.get('repository') or 'unknown'}` status `{node.get('status') or 'unknown'}` "
                f"selected_files={node.get('selected_files') or 0} "
                f"blocker=`{node.get('blocker') or ''}` next=`{node.get('next_step') or ''}`"
            )
    lines.extend(["", "## Why Excluded", ""])
    for row in excluded[:20]:
        lines.append(f"- `{row.get('task_type') or 'run'}` / `{row.get('source_file')}` excluded because `{row.get('exclusion_reason')}`.")
    if not excluded:
        lines.append("- No excluded outputs.")
    lines.extend(["", "## Hard Benchmark Evidence", ""])
    for row in hard_rows[:20]:
        lines.append(
            f"- `{row.get('task_type')}` / `{row.get('source_file')}`: "
            f"{', '.join(map(str, row.get('tags') or []))}"
        )
    if not hard_rows:
        lines.append("- No hard benchmark rows.")
    lines.extend(["", "## Counterfactual Benchmark Evidence", ""])
    for row in counterfactual_rows[:20]:
        lines.append(
            f"- `{row.get('case_type')}` / `{row.get('task_type')}` / `{row.get('source_file')}`: "
            f"{row.get('counterfactual_question')}"
        )
    if not counterfactual_rows:
        lines.append("- No counterfactual benchmark rows.")
    lines.extend(["", "## Active Curation", ""])
    for row in curation_queue[:20]:
        lines.append(
            f"- `{row.get('curation_type')}` priority {row.get('priority_score')}: "
            f"`{row.get('task_type')}` / `{row.get('source_file')}` ({row.get('reason')})"
        )
    if not curation_queue:
        lines.append("- No curation items.")
    return "\n".join(lines) + "\n"


def _markdown_curation_efficiency(report: dict[str, Any]) -> str:
    lines = [
        "# Active Curation Efficiency Report",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Manual-only review count: {report.get('manual_only_review_count', 0)}",
        f"- Agent-assisted review count: {report.get('agent_assisted_review_count', 0)}",
        f"- Auto-process count: {report.get('auto_process_count', 0)}",
        f"- Auto-skip count: {report.get('auto_skip_count', 0)}",
        f"- Review reduction rate: `{report.get('review_reduction_rate', 0)}`",
        f"- Critical curation items: {report.get('critical_curation_item_count', 0)}",
        f"- Critical issue coverage: `{json.dumps(report.get('critical_issue_coverage') or {}, ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Curation Types",
        "",
    ]
    for curation_type, count in (report.get("curation_type_counts") or {}).items():
        lines.append(f"- `{curation_type}`: {count}")
    lines.extend(["", "## Top Review Items", ""])
    top_items = report.get("top_review_items") or []
    if not top_items:
        lines.append("- No curation item.")
    for item in top_items[:20]:
        lines.append(
            f"- `{item.get('curation_type')}` priority {item.get('priority_score')}: "
            f"{item.get('source_file') or item.get('run_name')} ({item.get('reason')})"
        )
    lines.extend(["", "## Notes", ""])
    for item in report.get("notes") or []:
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def _split_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_split_values(item))
        return _dedupe(result)
    if isinstance(value, dict):
        return _dedupe([str(key) for key, item in value.items() if item])
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("[") or text.startswith("{"):
        try:
            return _split_values(json.loads(text))
        except Exception:
            pass
    return _dedupe([part.strip() for part in text.replace("|", ";").split(";") if part.strip()])


def _clean_reason(value: Any) -> str:
    text = str(value or "").strip()
    return text if text else "unknown"


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


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _load_discovery_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.suffix.lower() == ".json":
        payload = _read_json(path)
        rows = payload.get("files") or payload.get("items") or payload.get("rows") or payload.get("manifest")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except Exception:
        return []


def _markdown_recipe(recipe: dict[str, Any]) -> str:
    selected = recipe.get("selected_files") or []
    excluded = recipe.get("excluded_files") or []
    leakage = recipe.get("leakage_check") or {}
    leakage_risk = recipe.get("leakage_risk") or {}
    coverage = recipe.get("coverage_gap_report") or {}
    expansion = recipe.get("agent_expansion_plan") or {}
    hard = recipe.get("hard_benchmark") or {}
    counterfactual = recipe.get("counterfactual_benchmark") or {}
    split_eval = recipe.get("split_baseline_evaluation") or {}
    lines = [
        "# Dataset Recipe",
        "",
        f"- Status: `{recipe.get('status')}`",
        f"- Batch dir: `{recipe.get('batch_dir')}`",
        f"- Selected task outputs: {len(selected)}",
        f"- Excluded task outputs: {len(excluded)}",
        f"- Requested split strategy: `{recipe.get('split_strategy_requested')}`",
        f"- Resolved split strategy: `{recipe.get('split_strategy_resolved')}`",
        f"- Split policy: `{recipe.get('split_policy') or recipe.get('split_level')}`",
        f"- Split counts: `{json.dumps(recipe.get('split_counts') or {}, ensure_ascii=False, sort_keys=True)}`",
        f"- Repository summary: `{json.dumps(recipe.get('repository_summary') or {}, ensure_ascii=False, sort_keys=True)}`",
        f"- Leakage status: `{leakage.get('status', 'not_evaluated')}`",
        f"- Leakage risk: `{leakage_risk.get('status', 'not_evaluated')}`",
        f"- Split baseline interpretation: `{split_eval.get('interpretation', 'not_evaluated')}`",
        f"- Hard benchmark rows: {hard.get('row_count', 0)}",
        f"- Hard benchmark tags: `{json.dumps(hard.get('tag_counts') or {}, ensure_ascii=False, sort_keys=True)}`",
        f"- Counterfactual benchmark rows: {counterfactual.get('row_count', 0)}",
        f"- Counterfactual case types: `{json.dumps(counterfactual.get('case_type_counts') or {}, ensure_ascii=False, sort_keys=True)}`",
        f"- Coverage gaps: {len(coverage.get('gaps') or [])}",
        "",
        "## Selected Outputs",
        "",
    ]
    if not selected:
        lines.append("- None")
    for row in selected:
        lines.append(
            f"- `{row.get('task_type')}` / `{row.get('repository') or 'unknown'}` / `{row.get('project_accession') or 'unknown'}` / "
            f"`{row.get('source_file')}`: {row.get('rows_out')} rows"
        )
    lines.extend(["", "## Exclusions", ""])
    for row in excluded[:50]:
        lines.append(
            f"- `{row.get('task_type') or 'run'}` / `{row.get('source_file')}`: "
            f"{row.get('exclusion_reason')} ({row.get('repository') or 'unknown'})"
        )
    lines.extend(["", "## Gap-Aware Expansion", ""])
    for action in (expansion.get("actions") or [])[:20]:
        lines.append(
            f"- `{action.get('action')}` {action.get('dimension') or action.get('reason') or ''}: "
            f"{action.get('query_hint') or action.get('target') or ''}"
        )
    if not expansion.get("actions"):
        lines.append("- No major expansion action proposed.")
    lines.extend(
        [
            "",
            "## Reproduction",
            "",
            "- Re-run mini E2E batch with the same agent run directories.",
            "- Re-run `make-dataset-recipe` against that batch output directory.",
            "- For PRIDE, reproduce from PXD/file names or cached local acquisitions.",
            "- For MassIVE/iProX, first run `repository-smoke` with the native accession/path, then use the original one-click repository mode.",
            "- Recipe v1 records paths and split metadata only; parquet files are not copied.",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    if not fieldnames:
        fieldnames = ["status"]
        rows = [{"status": "empty"}]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(row.get(key), ensure_ascii=False, sort_keys=True)
                    if isinstance(row.get(key), (dict, list))
                    else row.get(key, "")
                    for key in fieldnames
                }
            )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _guess_project(run: dict[str, Any]) -> str:
    text = " ".join(str(run.get(key) or "") for key in ["agent_run_dir", "run_name", "source_file"])
    for token in text.replace("\\", "/").replace("_", "/").split("/"):
        if token.upper().startswith("PXD") and len(token) >= 9:
            return token.upper()[:9]
    return ""


def _normalize_repository(repository: Any, project_accession: str | None = None) -> str:
    value = str(repository or "").strip().lower().replace("-", "_")
    if value in {"pride", "massive", "iprox"}:
        return value
    project = str(project_accession or "").upper()
    if project.startswith("MSV"):
        return "massive"
    if project.startswith("IPX"):
        return "iprox"
    if project.startswith("PXD"):
        return "pride"
    return "unknown"


def _counts(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))
