from __future__ import annotations

import csv
import os
import json
import subprocess
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import Field

from agent.ai_ready.model_adapters import load_model_metrics_file
from agent.ai_ready.model_informed_discovery import (
    write_model_informed_curation_queue,
    write_model_informed_discovery_payload_queue,
    write_model_informed_discovery_payloads,
)
from agent.models import JsonModel
from agent.utils import write_json


ModelLoopMode = Literal["smoke"]


class DatasetModelLoopResult(JsonModel):
    status: str
    recipe_dir: str
    output_dir: str
    task_type: str
    mode: ModelLoopMode = "smoke"
    adapter: str = "dry_run"
    metric_status: str = "not_evaluated"
    failure_mode_count: int = 0
    expansion_action_count: int = 0
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict)


def run_dataset_model_loop(
    *,
    recipe_dir: str | Path,
    task_type: str,
    output_dir: str | Path,
    mode: ModelLoopMode = "smoke",
    adapter: str = "dry_run",
    adapter_command: str | None = None,
    metrics_file: str | Path | None = None,
) -> DatasetModelLoopResult:
    if mode != "smoke":
        raise ValueError("run-dataset-model-loop v1 only supports --mode smoke.")
    recipe_dir = Path(recipe_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    recipe = _read_json(recipe_dir / "dataset_recipe.json")
    split_plan = _read_json(recipe_dir / "dataset_split_plan.json")
    leakage_risk = _read_json(recipe_dir / "leakage_risk_report.json")
    hard_benchmark = _read_json(recipe_dir / "hard_benchmark_manifest.json")
    curation_queue = _read_json(recipe_dir / "curation_queue.json")
    if not recipe:
        raise ValueError(f"dataset_recipe.json not found or invalid: {recipe_dir}")

    selected = [
        row
        for row in recipe.get("selected_files") or []
        if isinstance(row, dict) and (not task_type or str(row.get("task_type") or "") == task_type)
    ]
    split_rows = [
        row
        for row in split_plan.get("rows") or []
        if isinstance(row, dict) and (not task_type or str(row.get("task_type") or "") == task_type)
    ]
    validation = _validate_model_loop_inputs(selected, split_rows, leakage_risk, recipe=recipe, recipe_dir=recipe_dir)
    adapter_contract = _write_adapter_contract(
        output_dir=output_dir,
        recipe_dir=recipe_dir,
        task_type=task_type,
        mode=mode,
        adapter=adapter,
    )
    adapter_input = _write_adapter_input_manifest(
        output_dir=output_dir,
        recipe=recipe,
        recipe_dir=recipe_dir,
        selected=selected,
        split_rows=split_rows,
        leakage_risk=leakage_risk,
        task_type=task_type,
    )
    adapter_result = _run_adapter(
        selected=selected,
        split_rows=split_rows,
        task_type=task_type,
        adapter=adapter,
        adapter_command=adapter_command,
        output_dir=output_dir,
        validation=validation,
        metrics_file=Path(metrics_file) if metrics_file else None,
        recipe=recipe,
        recipe_dir=recipe_dir,
        adapter_contract=adapter_contract,
        adapter_input=adapter_input,
    )
    eval_summary = _model_eval_summary(
        recipe=recipe,
        task_type=task_type,
        selected=selected,
        split_rows=split_rows,
        validation=validation,
        adapter_result=adapter_result,
    )
    adapter_warnings = [
        str(item)
        for item in adapter_result.get("contract_warnings", [])
        if str(item).strip()
    ]
    failure_modes = _failure_modes(
        eval_summary=eval_summary,
        validation=validation,
        hard_benchmark=hard_benchmark,
        curation_queue=curation_queue,
    )
    gap_report = _model_informed_gap_report(eval_summary, failure_modes, recipe)
    expansion_plan = _model_informed_expansion_plan(gap_report)
    discovery_requests = _model_informed_discovery_requests(
        gap_report,
        task_type=task_type,
        output_dir=output_dir,
    )
    adapter_status = str(adapter_result.get("status") or "")
    if validation["blockers"]:
        status = "blocked"
    elif adapter_status and adapter_status != "completed":
        status = "failed"
    else:
        status = "completed"
    files = {
        "model_adapter_contract_json": str(output_dir / "model_adapter_contract.json"),
        "model_adapter_contract_md": str(output_dir / "model_adapter_contract.md"),
        "model_adapter_input_manifest_json": str(output_dir / "model_adapter_input_manifest.json"),
        "model_adapter_input_manifest_csv": str(output_dir / "model_adapter_input_manifest.csv"),
        "model_eval_summary_json": str(output_dir / "model_eval_summary.json"),
        "model_failure_modes_json": str(output_dir / "model_failure_modes.json"),
        "model_loop_report_md": str(output_dir / "model_loop_report.md"),
        "model_informed_gap_report_json": str(output_dir / "model_informed_gap_report.json"),
        "model_informed_gap_report_md": str(output_dir / "model_informed_gap_report.md"),
        "model_informed_expansion_plan_json": str(output_dir / "model_informed_expansion_plan.json"),
        "model_informed_discovery_requests_json": str(output_dir / "model_informed_discovery_requests.json"),
        "model_informed_discovery_requests_csv": str(output_dir / "model_informed_discovery_requests.csv"),
        "model_informed_discovery_requests_md": str(output_dir / "model_informed_discovery_requests.md"),
        "model_informed_discovery_payloads_json": str(output_dir / "model_informed_discovery_payloads.json"),
        "model_informed_discovery_payloads_csv": str(output_dir / "model_informed_discovery_payloads.csv"),
        "model_informed_discovery_payloads_md": str(output_dir / "model_informed_discovery_payloads.md"),
        "model_informed_discovery_payload_queue_json": str(output_dir / "model_informed_discovery_payload_queue.json"),
        "model_informed_discovery_payload_queue_csv": str(output_dir / "model_informed_discovery_payload_queue.csv"),
        "model_informed_discovery_payload_queue_md": str(output_dir / "model_informed_discovery_payload_queue.md"),
        "model_informed_curation_queue_json": str(output_dir / "model_informed_curation_queue.json"),
        "model_informed_curation_queue_csv": str(output_dir / "model_informed_curation_queue.csv"),
        "model_informed_curation_queue_md": str(output_dir / "model_informed_curation_queue.md"),
    }
    write_json(files["model_eval_summary_json"], eval_summary)
    write_json(files["model_failure_modes_json"], failure_modes)
    Path(files["model_loop_report_md"]).write_text(
        _markdown_model_loop(eval_summary, failure_modes, gap_report, expansion_plan),
        encoding="utf-8",
    )
    write_json(files["model_informed_gap_report_json"], gap_report)
    Path(files["model_informed_gap_report_md"]).write_text(_markdown_gap_report(gap_report), encoding="utf-8")
    write_json(files["model_informed_expansion_plan_json"], expansion_plan)
    write_json(files["model_informed_discovery_requests_json"], discovery_requests)
    _write_discovery_requests_csv(files["model_informed_discovery_requests_csv"], discovery_requests)
    Path(files["model_informed_discovery_requests_md"]).write_text(
        _markdown_discovery_requests(discovery_requests),
        encoding="utf-8",
    )
    discovery_payloads = write_model_informed_discovery_payloads(
        output_json=files["model_informed_discovery_payloads_json"],
        output_csv=files["model_informed_discovery_payloads_csv"],
        output_md=files["model_informed_discovery_payloads_md"],
        discovery_requests=discovery_requests,
    )
    payload_queue = write_model_informed_discovery_payload_queue(
        output_json=files["model_informed_discovery_payload_queue_json"],
        output_csv=files["model_informed_discovery_payload_queue_csv"],
        output_md=files["model_informed_discovery_payload_queue_md"],
        payloads=discovery_payloads,
    )
    write_model_informed_curation_queue(
        output_json=files["model_informed_curation_queue_json"],
        output_csv=files["model_informed_curation_queue_csv"],
        output_md=files["model_informed_curation_queue_md"],
        discovery_requests=discovery_requests,
        payload_queue=payload_queue,
    )
    return DatasetModelLoopResult(
        status=status,
        recipe_dir=str(recipe_dir),
        output_dir=str(output_dir),
        task_type=task_type,
        mode=mode,
        adapter=str(eval_summary.get("adapter") or adapter),
        metric_status=str(eval_summary.get("metric_status") or "not_evaluated"),
        failure_mode_count=len(failure_modes.get("failure_modes") or []),
        expansion_action_count=len(expansion_plan.get("actions") or []),
        blockers=validation["blockers"],
        warnings=[*validation["warnings"], *adapter_warnings],
        files=files,
    )


def _validate_model_loop_inputs(
    selected: list[dict[str, Any]],
    split_rows: list[dict[str, Any]],
    leakage_risk: dict[str, Any],
    *,
    recipe: dict[str, Any],
    recipe_dir: Path,
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    if not selected:
        blockers.append("no_selected_task_outputs")
    split_counts: dict[str, int] = {}
    for row in split_rows:
        split = str(row.get("split") or "unknown")
        split_counts[split] = split_counts.get(split, 0) + 1
    if not {"train", "val"}.issubset(set(split_counts)):
        warnings.append("train_val_split_incomplete_for_smoke")
    if leakage_risk.get("status") == "fail":
        warnings.append("leakage_risk_detected")
    missing_parquet = [
        row
        for row in selected
        if not row.get("parquet_path") or _resolve_existing_parquet_path(row, recipe=recipe, recipe_dir=recipe_dir) is None
    ]
    if missing_parquet:
        blockers.append("selected_parquet_missing")
    return {
        "status": "blocked" if blockers else "ready",
        "blockers": blockers,
        "warnings": warnings,
        "split_counts": split_counts,
        "selected_count": len(selected),
    }


def _run_adapter(
    *,
    selected: list[dict[str, Any]],
    split_rows: list[dict[str, Any]],
    task_type: str,
    adapter: str,
    adapter_command: str | None,
    output_dir: Path,
    validation: dict[str, Any],
    metrics_file: Path | None,
    recipe: dict[str, Any],
    recipe_dir: Path,
    adapter_contract: dict[str, Any],
    adapter_input: dict[str, Any],
) -> dict[str, Any]:
    if metrics_file and metrics_file.exists():
        metrics = load_model_metrics_file(metrics_file, adapter=adapter, task_type=task_type)
        warnings = []
        if not _adapter_metrics_contract_ok(metrics):
            warnings.append("metrics_file_schema_incomplete")
        return {
            "adapter": "metrics_file",
            "status": "completed" if metrics else "failed",
            "metrics": metrics,
            "command": "",
            "contract_warnings": warnings,
            "expected_metrics_path": str(Path(metrics_file).resolve()),
        }
    if adapter != "dry_run" and adapter_command:
        return _run_external_adapter(
            adapter_command,
            output_dir,
            adapter_contract=adapter_contract,
            adapter_input=adapter_input,
            task_type=task_type,
            recipe_dir=recipe_dir,
        )
    metrics = _dry_run_metrics(selected, split_rows, task_type, validation, recipe=recipe, recipe_dir=recipe_dir)
    return {"adapter": "dry_run", "status": "completed" if not validation["blockers"] else "blocked", "metrics": metrics, "command": ""}


def _run_external_adapter(
    command: str,
    output_dir: Path,
    *,
    adapter_contract: dict[str, Any],
    adapter_input: dict[str, Any],
    task_type: str,
    recipe_dir: Path,
) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    recipe_dir = recipe_dir.resolve()
    log_path = output_dir / "model_adapter.log"
    expected_metrics_path = output_dir / "external_model_metrics.json"
    env = os.environ.copy()
    env.update(
        {
            "AGENT_MODEL_ADAPTER_CONTRACT": str(output_dir / "model_adapter_contract.json"),
            "AGENT_MODEL_ADAPTER_INPUT": str(output_dir / "model_adapter_input_manifest.json"),
            "AGENT_MODEL_ADAPTER_OUTPUT": str(expected_metrics_path),
            "AGENT_MODEL_ADAPTER_OUTPUT_DIR": str(output_dir),
            "AGENT_MODEL_ADAPTER_TASK_TYPE": task_type,
            "AGENT_MODEL_ADAPTER_RECIPE_DIR": str(recipe_dir),
            "AGENT_MODEL_ADAPTER_SCHEMA_VERSION": str(adapter_contract.get("schema_version") or ""),
            "AGENT_MODEL_ADAPTER_SELECTED_COUNT": str((adapter_input.get("summary") or {}).get("selected_count") or 0),
        }
    )
    completed = subprocess.run(
        command,
        shell=True,
        cwd=output_dir,
        text=True,
        capture_output=True,
        timeout=600,
        env=env,
    )
    log_path.write_text((completed.stdout or "") + "\n" + (completed.stderr or ""), encoding="utf-8")
    metrics = _read_json(expected_metrics_path) if expected_metrics_path.exists() else {}
    contract_warnings: list[str] = []
    if completed.returncode != 0:
        contract_warnings.append("external_adapter_failed")
    if completed.returncode == 0 and not metrics:
        contract_warnings.append("external_adapter_metrics_missing")
    if metrics and not _adapter_metrics_contract_ok(metrics):
        contract_warnings.append("external_adapter_metrics_schema_incomplete")
    status = "completed" if completed.returncode == 0 and metrics else "failed"
    return {
        "adapter": "external_command",
        "status": status,
        "returncode": completed.returncode,
        "metrics": metrics,
        "command": command,
        "log_path": str(log_path),
        "contract_warnings": contract_warnings,
        "expected_metrics_path": str(expected_metrics_path),
    }


def _write_adapter_contract(
    *,
    output_dir: Path,
    recipe_dir: Path,
    task_type: str,
    mode: ModelLoopMode,
    adapter: str,
) -> dict[str, Any]:
    resolved_output_dir = output_dir.resolve()
    resolved_recipe_dir = recipe_dir.resolve()
    contract = {
        "schema_version": "model-adapter-contract/v1",
        "task_type": task_type,
        "mode": mode,
        "adapter": adapter,
        "inputs": {
            "recipe_dir": str(resolved_recipe_dir),
            "input_manifest_json": str(resolved_output_dir / "model_adapter_input_manifest.json"),
            "input_manifest_csv": str(resolved_output_dir / "model_adapter_input_manifest.csv"),
        },
        "environment": {
            "AGENT_MODEL_ADAPTER_CONTRACT": str(resolved_output_dir / "model_adapter_contract.json"),
            "AGENT_MODEL_ADAPTER_INPUT": str(resolved_output_dir / "model_adapter_input_manifest.json"),
            "AGENT_MODEL_ADAPTER_OUTPUT": str(resolved_output_dir / "external_model_metrics.json"),
            "AGENT_MODEL_ADAPTER_OUTPUT_DIR": str(resolved_output_dir),
            "AGENT_MODEL_ADAPTER_TASK_TYPE": task_type,
            "AGENT_MODEL_ADAPTER_RECIPE_DIR": str(resolved_recipe_dir),
        },
        "required_output": {
            "path": str(resolved_output_dir / "external_model_metrics.json"),
            "format": "json",
            "minimum_fields": ["primary_metric"],
            "recommended_fields": [
                "primary_metric",
                "higher_is_better",
                "thresholds",
                "train",
                "val",
                "test",
                "heldout_project",
                "heldout_instrument",
                "heldout_organism",
                "slices",
            ],
        },
        "metrics_schema": {
            "flat_example": {
                "primary_metric": "sequence_accuracy",
                "higher_is_better": True,
                "sequence_accuracy": 0.72,
            },
            "slice_example": {
                "train": {"sequence_accuracy": 0.88},
                "heldout_project": {"sequence_accuracy": 0.69},
                "slices": {"phosphotyrosine": {"sequence_accuracy": 0.55, "n": 20}},
                "thresholds": {"sequence_accuracy": 0.7, "phosphotyrosine.sequence_accuracy": 0.6},
            },
        },
        "notes": [
            "External adapters should read AGENT_MODEL_ADAPTER_INPUT and write AGENT_MODEL_ADAPTER_OUTPUT.",
            "The agent does not require adapters to train; dry-run and metrics-file modes remain valid.",
            "Metrics are deterministic evidence only after this contract validates their schema.",
        ],
    }
    write_json(output_dir / "model_adapter_contract.json", contract)
    (output_dir / "model_adapter_contract.md").write_text(_markdown_adapter_contract(contract), encoding="utf-8")
    return contract


def _write_adapter_input_manifest(
    *,
    output_dir: Path,
    recipe: dict[str, Any],
    recipe_dir: Path,
    selected: list[dict[str, Any]],
    split_rows: list[dict[str, Any]],
    leakage_risk: dict[str, Any],
    task_type: str,
) -> dict[str, Any]:
    split_by_key = {
        _split_key(row): row
        for row in split_rows
        if isinstance(row, dict)
    }
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(selected, start=1):
        resolved = _resolve_existing_parquet_path(row, recipe=recipe, recipe_dir=recipe_dir)
        split_row = split_by_key.get(_split_key(row), {})
        rows.append(
            {
                "index": index,
                "task_type": task_type,
                "split": split_row.get("split") or row.get("split") or "unknown",
                "project_accession": row.get("project_accession") or "",
                "source_file": row.get("source_file") or "",
                "repository": row.get("repository") or "",
                "parquet_path": row.get("parquet_path") or "",
                "resolved_parquet_path": str(resolved.resolve()) if resolved else "",
                "rows_out": _safe_int(row.get("rows_out")),
                "full_status": row.get("full_status") or "",
                "ai_ready_outcome": row.get("ai_ready_outcome") or "",
                "labeling_strategy": row.get("labeling_strategy") or "",
                "ptm_type": row.get("ptm_type") or "",
                "canonical_species": row.get("canonical_species") or "",
            }
        )
    manifest = {
        "schema_version": "model-adapter-input/v1",
        "task_type": task_type,
        "recipe_dir": str(recipe_dir.resolve()),
        "batch_dir": recipe.get("batch_dir") or "",
        "leakage_status": leakage_risk.get("status") or "unknown",
        "summary": {
            "selected_count": len(rows),
            "split_counts": _counts([str(row.get("split") or "unknown") for row in rows]),
            "total_rows_out": sum(_safe_int(row.get("rows_out")) for row in rows),
        },
        "rows": rows,
    }
    write_json(output_dir / "model_adapter_input_manifest.json", manifest)
    _write_adapter_input_csv(output_dir / "model_adapter_input_manifest.csv", rows)
    return manifest


def _dry_run_metrics(
    selected: list[dict[str, Any]],
    split_rows: list[dict[str, Any]],
    task_type: str,
    validation: dict[str, Any],
    *,
    recipe: dict[str, Any],
    recipe_dir: Path,
) -> dict[str, Any]:
    total_rows = sum(_safe_int(row.get("rows_out")) for row in selected)
    split_counts = validation.get("split_counts") or {}
    parquet_rows_scanned = 0
    charge_4plus = 0
    modified_rows = 0
    for row in selected:
        path = _resolve_existing_parquet_path(row, recipe=recipe, recipe_dir=recipe_dir)
        if path is None:
            continue
        try:
            frame = pd.read_parquet(path).head(20000)
        except Exception:
            continue
        parquet_rows_scanned += len(frame)
        charge_col = _first_column(frame, ["charge", "precursor_charge"])
        if charge_col:
            charges = pd.to_numeric(frame[charge_col], errors="coerce").dropna()
            charge_4plus += int((charges >= 4).sum())
        peptide_col = _first_column(frame, ["peptide_sequence", "sequence"])
        modified_col = _first_column(frame, ["modified_sequence", "modified_peptide", "modified_peptide_sequence"])
        if peptide_col and modified_col:
            modified_rows += int((frame[peptide_col].astype(str) != frame[modified_col].astype(str)).sum())
    coverage_score = min(1.0, len(split_counts) / 3.0)
    sample_efficiency = min(1.0, total_rows / 1000.0)
    smoke_score = round(0.55 * sample_efficiency + 0.25 * coverage_score + 0.20 * (0 if validation["blockers"] else 1), 4)
    return {
        "task_type": task_type,
        "total_rows": total_rows,
        "parquet_rows_scanned": parquet_rows_scanned,
        "split_counts": split_counts,
        "charge_4plus_rows": charge_4plus,
        "modified_rows": modified_rows,
        "smoke_score": smoke_score,
        "metric_schema_version": "model_loop_smoke_v1",
    }


def _adapter_metrics_contract_ok(metrics: dict[str, Any]) -> bool:
    primary_metric = str(metrics.get("primary_metric") or "").strip()
    if primary_metric:
        return _metric_value(metrics, "metrics", primary_metric) is not None or any(
            _metric_value(metrics, slice_name, primary_metric) is not None
            for slice_name in _metric_slices(metrics)
        )
    inferred = _infer_primary_metric(metrics, task_type="")
    return bool(inferred)


def _split_key(row: dict[str, Any]) -> str:
    for key in ("parquet_path", "source_file", "run_name", "project_accession"):
        value = str(row.get(key) or "").strip()
        if value:
            return value.replace("\\", "/")
    return json.dumps(row, ensure_ascii=False, sort_keys=True)


def _write_adapter_input_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "index",
        "task_type",
        "split",
        "project_accession",
        "source_file",
        "repository",
        "parquet_path",
        "resolved_parquet_path",
        "rows_out",
        "full_status",
        "ai_ready_outcome",
        "labeling_strategy",
        "ptm_type",
        "canonical_species",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def _markdown_adapter_contract(contract: dict[str, Any]) -> str:
    env = contract.get("environment") if isinstance(contract.get("environment"), dict) else {}
    required = contract.get("required_output") if isinstance(contract.get("required_output"), dict) else {}
    lines = [
        "# Model Adapter Contract",
        "",
        f"- Schema: `{contract.get('schema_version')}`",
        f"- Task type: `{contract.get('task_type')}`",
        f"- Mode: `{contract.get('mode')}`",
        f"- Adapter: `{contract.get('adapter')}`",
        "",
        "## Adapter Environment",
        "",
    ]
    for key, value in env.items():
        lines.append(f"- `{key}` = `{value}`")
    lines.extend(
        [
            "",
            "## Required Output",
            "",
            f"- Path: `{required.get('path')}`",
            "- Format: JSON",
            "- Minimum: provide `primary_metric` and a corresponding metric value, or provide a known metric that the agent can infer.",
            "",
            "## Notes",
            "",
        ]
    )
    for note in contract.get("notes") or []:
        lines.append(f"- {note}")
    return "\n".join(lines) + "\n"


def _resolve_existing_parquet_path(row: dict[str, Any], *, recipe: dict[str, Any], recipe_dir: Path) -> Path | None:
    raw = str(row.get("parquet_path") or "").strip()
    if not raw:
        return None
    normalized_raw = raw.replace("\\", "/")
    path = Path(normalized_raw)
    candidates: list[Path] = [path]
    batch_dir_text = str(recipe.get("batch_dir") or "").strip()
    batch_dir = Path(batch_dir_text) if batch_dir_text else None
    if not path.is_absolute():
        candidates.extend(
            [
                Path.cwd() / path,
                recipe_dir / path,
                recipe_dir.parent / path,
            ]
        )
        if batch_dir is not None:
            candidates.extend([batch_dir / path, batch_dir.parent / path])
        parts = list(path.parts)
        lower_parts = [part.casefold() for part in parts]
        if "runs" in lower_parts:
            runs_index = lower_parts.index("runs")
            suffix = Path(*parts[runs_index:])
            candidates.extend([Path.cwd() / suffix, recipe_dir.parent / suffix])
            if batch_dir is not None:
                candidates.extend([batch_dir.parent / suffix, batch_dir / suffix])
    for candidate in candidates:
        if candidate.exists():
            return candidate
    if batch_dir is not None and batch_dir.exists():
        expected_name = path.name
        matches = list(batch_dir.rglob(expected_name))
        if matches:
            return matches[0]
    return None


def _model_eval_summary(
    *,
    recipe: dict[str, Any],
    task_type: str,
    selected: list[dict[str, Any]],
    split_rows: list[dict[str, Any]],
    validation: dict[str, Any],
    adapter_result: dict[str, Any],
) -> dict[str, Any]:
    raw_metrics = adapter_result.get("metrics") if isinstance(adapter_result.get("metrics"), dict) else {}
    metrics = _normalize_model_metrics(raw_metrics, task_type=task_type)
    status = "blocked" if validation["blockers"] else adapter_result.get("status", "completed")
    return {
        "status": status,
        "metric_status": "available" if metrics else "missing",
        "task_type": task_type,
        "adapter": adapter_result.get("adapter"),
        "adapter_status": adapter_result.get("status"),
        "adapter_returncode": adapter_result.get("returncode"),
        "adapter_contract_warnings": adapter_result.get("contract_warnings") or [],
        "expected_metrics_path": adapter_result.get("expected_metrics_path") or "",
        "selected_outputs": len(selected),
        "split_rows": len(split_rows),
        "split_counts": validation.get("split_counts") or {},
        "recipe_status": recipe.get("status"),
        "validation": validation,
        "metrics": metrics,
        "adapter_command": adapter_result.get("command") or "",
    }


def _failure_modes(
    *,
    eval_summary: dict[str, Any],
    validation: dict[str, Any],
    hard_benchmark: dict[str, Any],
    curation_queue: dict[str, Any],
) -> dict[str, Any]:
    rows = hard_benchmark.get("rows") if isinstance(hard_benchmark.get("rows"), list) else []
    curation_rows = curation_queue.get("rows") if isinstance(curation_queue.get("rows"), list) else []
    metrics = eval_summary.get("metrics") if isinstance(eval_summary.get("metrics"), dict) else {}
    modes: list[dict[str, Any]] = []
    for blocker in validation.get("blockers") or []:
        modes.append({"failure_mode": blocker, "severity": "blocking", "evidence": "model_loop_validation"})
    for warning in validation.get("warnings") or []:
        modes.append({"failure_mode": warning, "severity": "warning", "evidence": "model_loop_validation"})
    adapter_failed = str(eval_summary.get("adapter_status") or "") not in {"", "completed"}
    if adapter_failed:
        modes.append({"failure_mode": "external_adapter_failed", "severity": "blocking", "evidence": "model_adapter"})
    for warning in eval_summary.get("adapter_contract_warnings") or []:
        if adapter_failed and str(warning) == "external_adapter_failed":
            continue
        modes.append({"failure_mode": str(warning), "severity": "warning", "evidence": "model_adapter_contract"})
    modes.extend(_model_metric_failure_modes(metrics, task_type=str(eval_summary.get("task_type") or "")))
    tag_counts: dict[str, int] = {}
    for row in rows:
        for tag in row.get("tags") or []:
            tag_counts[str(tag)] = tag_counts.get(str(tag), 0) + 1
    for tag, count in sorted(tag_counts.items(), key=lambda item: (-item[1], item[0]))[:20]:
        modes.append({"failure_mode": tag, "severity": _hard_tag_severity(tag), "count": count, "evidence": "hard_benchmark"})
    for row in curation_rows[:20]:
        modes.append(
            {
                "failure_mode": str(row.get("curation_type") or row.get("reason") or "curation_item"),
                "severity": "review",
                "priority_score": row.get("priority_score"),
                "evidence": "active_curation",
            }
        )
    if _safe_int(metrics.get("total_rows")) < 100:
        modes.append({"failure_mode": "low_training_rows", "severity": "warning", "evidence": "dry_run_metrics"})
    if _safe_int(metrics.get("charge_4plus_rows")) == 0:
        modes.append({"failure_mode": "high_charge_underrepresented", "severity": "diagnostic", "evidence": "dry_run_metrics"})
    if _safe_int(metrics.get("modified_rows")) == 0 and str(eval_summary.get("task_type")) in {"ptm_denovo", "denovo"}:
        modes.append({"failure_mode": "modified_peptides_underrepresented", "severity": "diagnostic", "evidence": "dry_run_metrics"})
    return {"failure_modes": modes, "failure_mode_counts": _counts([str(item["failure_mode"]) for item in modes])}


def _model_informed_gap_report(
    eval_summary: dict[str, Any],
    failure_modes: dict[str, Any],
    recipe: dict[str, Any],
) -> dict[str, Any]:
    gaps: list[dict[str, Any]] = []
    mode_names = {str(item.get("failure_mode")) for item in failure_modes.get("failure_modes") or []}
    if "high_charge_underrepresented" in mode_names:
        gaps.append({"dimension": "charge", "target": "charge_4plus", "reason": "model smoke found no high-charge rows", "priority": "medium"})
    if "modified_peptides_underrepresented" in mode_names:
        gaps.append({"dimension": "ptm", "target": "modified_peptides", "reason": "PTM/de novo smoke found no modified rows", "priority": "high"})
    if "low_training_rows" in mode_names:
        gaps.append({"dimension": "label_yield", "target": "more_training_rows", "reason": "training rows below smoke threshold", "priority": "high"})
    if "leakage_risk_detected" in mode_names:
        gaps.append({"dimension": "split", "target": "lower_leakage_split", "reason": "leakage risk was detected before model smoke", "priority": "high"})
    if "model_primary_metric_below_threshold" in mode_names:
        gaps.append({"dimension": "model_quality", "target": "improve_primary_metric", "reason": "external model metric is below the configured threshold", "priority": "high"})
    if "model_generalization_gap" in mode_names:
        gaps.append({"dimension": "diversity", "target": "harder_project_or_instrument_split", "reason": "training metric is substantially better than heldout metric", "priority": "high"})
    for mode in sorted(mode_names):
        if mode.startswith("model_slice_underperformance:"):
            slice_name = mode.split(":", 1)[1]
            gap = _gap_from_model_slice(slice_name)
            gaps.append(
                {
                    "dimension": gap["dimension"],
                    "target": gap["target"],
                    "reason": f"model underperformed on eval slice `{slice_name}`",
                    "priority": gap["priority"],
                }
            )
    for row in recipe.get("coverage_gap_report", {}).get("gaps", []) if isinstance(recipe.get("coverage_gap_report"), dict) else []:
        if isinstance(row, dict):
            gaps.append({"dimension": row.get("dimension"), "target": ",".join(map(str, row.get("missing") or [])), "reason": "recipe coverage gap", "priority": row.get("priority") or "medium"})
    return {
        "status": "gaps_detected" if gaps else "no_major_model_informed_gap",
        "task_type": eval_summary.get("task_type"),
        "gaps": gaps,
        "metrics": eval_summary.get("metrics") or {},
    }


def _model_informed_expansion_plan(gap_report: dict[str, Any]) -> dict[str, Any]:
    actions = []
    for gap in gap_report.get("gaps") or []:
        dimension = str(gap.get("dimension") or "unknown")
        target = str(gap.get("target") or "unknown")
        actions.append(
            {
                "action": "plan_discovery_query",
                "dimension": dimension,
                "target": target,
                "query_hint": _query_hint(dimension, target),
                "requires_user_confirmation": True,
                "source": "model_loop_smoke",
            }
        )
    return {"status": "ready" if actions else "no_action_needed", "actions": actions}


def _model_informed_discovery_requests(
    gap_report: dict[str, Any],
    *,
    task_type: str,
    output_dir: Path,
) -> dict[str, Any]:
    requests: list[dict[str, Any]] = []
    for index, gap in enumerate(gap_report.get("gaps") or [], start=1):
        if not isinstance(gap, dict):
            continue
        dimension = str(gap.get("dimension") or "unknown")
        target = str(gap.get("target") or "unknown")
        query = _query_hint(dimension, target)
        repositories = _repositories_for_gap(dimension, target)
        constraints = _discovery_constraints_for_gap(
            dimension,
            target,
            task_type=task_type,
            priority=str(gap.get("priority") or "medium"),
        )
        repository = repositories[0] if len(repositories) == 1 else "auto"
        request = {
            "request_id": f"model_gap_{index:03d}",
            "source": "model_loop",
            "schema_version": "model-informed-discovery-request/v1",
            "action": "discover_dataset",
            "task_type": task_type,
            "priority": str(gap.get("priority") or "medium"),
            "dimension": dimension,
            "target": target,
            "reason": str(gap.get("reason") or "model-informed coverage gap"),
            "query": query,
            "repository": repository,
            "repositories": repositories,
            "constraints": constraints,
            "requires_user_confirmation": True,
            "suggested_cli": _discovery_cli_hint(
                query=query,
                repository=repository,
                task_type=task_type,
                output_dir=output_dir,
                request_id=f"model_gap_{index:03d}",
            ),
        }
        requests.append(request)
    return {
        "schema_version": "model-informed-discovery-requests/v1",
        "status": "ready" if requests else "no_action_needed",
        "task_type": task_type,
        "request_count": len(requests),
        "requests": requests,
    }


def _discovery_constraints_for_gap(
    dimension: str,
    target: str,
    *,
    task_type: str,
    priority: str,
) -> dict[str, Any]:
    constraints: dict[str, Any] = {
        "task_type": task_type,
        "acquisition": "DDA",
        "species_policy": "open",
        "max_file_size_mb": 500,
        "prefer_small_files": True,
        "avoid_raw_by_default": True,
        "priority": priority,
    }
    if dimension == "charge":
        constraints["preferred_charge"] = target
    elif dimension == "ptm":
        constraints["modification_scope"] = _ptm_constraint(target)
        constraints["prefer_ptm_enrichment"] = True
    elif dimension == "label_yield":
        constraints["prefer_high_label_yield"] = True
        constraints["minimum_expected_rows"] = 100
    elif dimension == "split":
        constraints["prefer_project_disjoint"] = True
    elif dimension == "instrument":
        constraints["instrument_preference"] = target
    elif dimension == "organism":
        constraints["organism_preference"] = target
        constraints["species_policy"] = "open"
    elif dimension == "project":
        constraints["prefer_new_project"] = True
    elif dimension == "spectrum_quality":
        constraints["prefer_high_signal_spectra"] = True
    elif dimension == "labeling_strategy":
        constraints["labeling_strategy"] = target
    elif dimension == "model_quality":
        constraints["prefer_high_confidence_labels"] = True
    elif dimension == "diversity":
        constraints["prefer_diverse_project_or_instrument"] = True
    return constraints


def _repositories_for_gap(dimension: str, target: str) -> list[str]:
    if dimension in {"project", "diversity", "organism", "instrument"}:
        return ["pride", "massive", "iprox"]
    if "iprox" in target.casefold():
        return ["iprox"]
    if "massive" in target.casefold() or "msv" in target.casefold():
        return ["massive"]
    return ["pride", "massive", "iprox"]


def _ptm_constraint(target: str) -> str:
    key = target.casefold()
    if "modified_peptides" in key or key in {"modified", "ptm", "any_ptm"}:
        return "any_ptm"
    if any(token in key for token in ["phospho", "ptyr", "phosphotyrosine", "phosphosite", "sty"]):
        return "phospho"
    if any(f"_{token}" in key or f"-{token}" in key or f" {token}" in key for token in ["ps", "pt", "py"]):
        return "phospho"
    if any(token in key for token in ["gly", "glyco", "hilic", "lectin"]):
        return "glyco"
    if any(token in key for token in ["glygly", "k-gg", "ubiquitin", "ubiquitylation", "digly"]):
        return "ubiquitin"
    if "acetyl" in key or "kac" in key:
        return "acetyl"
    if "methyl" in key or "kme" in key or "rme" in key:
        return "methyl"
    if "modified" in key:
        return "any_ptm"
    return target or "unknown_ptm"


def _discovery_cli_hint(
    *,
    query: str,
    repository: str,
    task_type: str,
    output_dir: Path,
    request_id: str,
) -> str:
    safe_query = query.replace('"', "'")
    request_output = output_dir / "next_discovery" / request_id
    return (
        "python -m agent.cli discover-dataset "
        f'--goal "{safe_query}" '
        f"--task-type {task_type} "
        f"--repository {repository} "
        f"--output-dir {request_output}"
    )


def _write_discovery_requests_csv(path: str | Path, payload: dict[str, Any]) -> None:
    rows = payload.get("requests") if isinstance(payload.get("requests"), list) else []
    columns = [
        "request_id",
        "priority",
        "dimension",
        "target",
        "repository",
        "repositories",
        "query",
        "reason",
        "requires_user_confirmation",
        "suggested_cli",
    ]
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            if not isinstance(row, dict):
                continue
            out = dict(row)
            out["repositories"] = ";".join(map(str, row.get("repositories") or []))
            writer.writerow(out)


def _query_hint(dimension: str, target: str) -> str:
    if dimension == "charge":
        return "DDA high charge peptide Orbitrap HCD mzML"
    if dimension == "ptm":
        return "PTM enriched modified peptide DDA phosphotyrosine GlyGly acetylome mzML"
    if dimension == "label_yield":
        return "small DDA mzML high PSM yield Orbitrap HCD"
    if dimension == "split":
        return "additional project-disjoint DDA mzML dataset"
    if dimension == "instrument":
        return f"{target} DDA Orbitrap timsTOF HCD mzML"
    if dimension == "organism":
        return f"{target} proteomics DDA mzML"
    if dimension == "project":
        return "additional independent PRIDE MassIVE iProX DDA mzML project"
    if dimension == "spectrum_quality":
        return "high quality HCD DDA mzML high signal peptide spectra"
    if dimension == "labeling_strategy":
        return f"{target} DDA mzML label-free TMT iTRAQ"
    if dimension == "model_quality":
        return "high-confidence DDA mzML search results with high label yield"
    if dimension == "diversity":
        return "project-disjoint instrument-diverse DDA mzML dataset"
    return f"{target} {dimension} DDA mzML"


def _normalize_model_metrics(metrics: dict[str, Any], *, task_type: str) -> dict[str, Any]:
    """Add a stable evaluation schema around external or dry-run metrics.

    External adapters may write flat metrics, nested train/val/test sections, or
    a `slices` object. Keep original keys intact while adding fields that the
    agent can use for deterministic failure diagnosis.
    """
    if not metrics:
        return {}
    normalized = dict(metrics)
    primary_metric = str(metrics.get("primary_metric") or _infer_primary_metric(metrics, task_type=task_type) or "score")
    higher_is_better = _higher_is_better(metrics, primary_metric)
    primary_value = _metric_value(metrics, "metrics", primary_metric)
    slices = _metric_slices(metrics)
    thresholds = metrics.get("thresholds") if isinstance(metrics.get("thresholds"), dict) else {}
    primary_threshold = _threshold_for_metric(thresholds, primary_metric=primary_metric, slice_name="metrics", higher_is_better=higher_is_better, task_type=task_type)
    normalized.setdefault("primary_metric", primary_metric)
    normalized["higher_is_better"] = higher_is_better
    normalized["primary_metric_value"] = primary_value
    normalized["primary_metric_threshold"] = primary_threshold
    normalized["model_metric_schema_version"] = "model_eval_metrics_v2"
    normalized["evaluation_slices"] = slices
    normalized["thresholds_applied"] = {
        "primary": primary_threshold,
        "source": "metrics.thresholds" if thresholds else "default_task_thresholds",
    }
    return normalized


def _model_metric_failure_modes(metrics: dict[str, Any], *, task_type: str) -> list[dict[str, Any]]:
    if not metrics:
        return []
    modes: list[dict[str, Any]] = []
    primary_metric = str(metrics.get("primary_metric") or _infer_primary_metric(metrics, task_type=task_type) or "score")
    higher_is_better = bool(metrics.get("higher_is_better", _higher_is_better(metrics, primary_metric)))
    thresholds = metrics.get("thresholds") if isinstance(metrics.get("thresholds"), dict) else {}
    primary_value = _coerce_float(metrics.get("primary_metric_value"))
    if primary_value is None:
        primary_value = _metric_value(metrics, "metrics", primary_metric)
    primary_threshold = _coerce_float(metrics.get("primary_metric_threshold"))
    if primary_threshold is None:
        primary_threshold = _threshold_for_metric(thresholds, primary_metric=primary_metric, slice_name="metrics", higher_is_better=higher_is_better, task_type=task_type)
    if primary_value is not None and _metric_is_below_threshold(primary_value, primary_threshold, higher_is_better):
        modes.append(
            {
                "failure_mode": "model_primary_metric_below_threshold",
                "severity": "warning",
                "evidence": "model_metrics",
                "metric": primary_metric,
                "value": primary_value,
                "threshold": primary_threshold,
            }
        )

    train_value = _metric_value(metrics, "train", primary_metric)
    heldout_values = [
        value
        for value in [
            _metric_value(metrics, "val", primary_metric),
            _metric_value(metrics, "test", primary_metric),
            _metric_value(metrics, "heldout_project", primary_metric),
            _metric_value(metrics, "heldout_instrument", primary_metric),
            _metric_value(metrics, "heldout_organism", primary_metric),
        ]
        if value is not None
    ]
    if train_value is not None and heldout_values:
        heldout = sum(heldout_values) / len(heldout_values)
        gap = train_value - heldout if higher_is_better else heldout - train_value
        if gap >= 0.15:
            modes.append(
                {
                    "failure_mode": "model_generalization_gap",
                    "severity": "warning",
                    "evidence": "model_metrics",
                    "metric": primary_metric,
                    "train_value": round(train_value, 6),
                    "heldout_value": round(heldout, 6),
                    "gap": round(gap, 6),
                }
            )

    for slice_name, slice_metrics in (metrics.get("evaluation_slices") or {}).items():
        if not isinstance(slice_metrics, dict):
            continue
        value = _metric_value({"slices": {slice_name: slice_metrics}}, slice_name, primary_metric)
        if value is None:
            continue
        threshold = _threshold_for_metric(
            thresholds,
            primary_metric=primary_metric,
            slice_name=str(slice_name),
            higher_is_better=higher_is_better,
            task_type=task_type,
        )
        if _metric_is_below_threshold(value, threshold, higher_is_better):
            modes.append(
                {
                    "failure_mode": f"model_slice_underperformance:{slice_name}",
                    "severity": "hard_case",
                    "evidence": "model_metrics",
                    "metric": primary_metric,
                    "value": value,
                    "threshold": threshold,
                }
            )
    return modes


def _infer_primary_metric(metrics: dict[str, Any], *, task_type: str) -> str | None:
    preferred_by_task = {
        "denovo": ["sequence_accuracy", "peptide_recall", "accuracy", "f1", "smoke_score"],
        "ptm_denovo": ["site_localization_accuracy", "sequence_accuracy", "accuracy", "f1", "smoke_score"],
        "psm_scoring": ["auc", "auroc", "f1", "accuracy", "smoke_score"],
        "fragment_intensity_prediction": ["cosine_similarity", "pearson", "spearman", "smoke_score"],
        "rt_prediction": ["r2", "pearson", "spearman", "mae", "rmse", "smoke_score"],
    }
    candidates = preferred_by_task.get(task_type, ["accuracy", "f1", "auc", "smoke_score", "loss"])
    for candidate in candidates:
        if _metric_value(metrics, "metrics", candidate) is not None:
            return candidate
    for key, value in metrics.items():
        if isinstance(value, (int, float)) and key not in {"total_rows", "parquet_rows_scanned", "charge_4plus_rows", "modified_rows"}:
            return str(key)
    return None


def _higher_is_better(metrics: dict[str, Any], primary_metric: str) -> bool:
    if "higher_is_better" in metrics:
        return bool(metrics.get("higher_is_better"))
    metric = primary_metric.casefold()
    lower_is_better_markers = ("loss", "error", "mae", "rmse", "mse", "perplexity", "cer", "wer")
    return not any(marker in metric for marker in lower_is_better_markers)


def _metric_slices(metrics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    slices: dict[str, dict[str, Any]] = {}
    if isinstance(metrics.get("slices"), dict):
        for name, value in metrics["slices"].items():
            if isinstance(value, dict):
                slices[str(name)] = dict(value)
    for name in [
        "train",
        "val",
        "test",
        "heldout_project",
        "heldout_instrument",
        "heldout_organism",
        "hard_benchmark",
        "phosphotyrosine",
        "high_charge",
        "modified_peptide",
        "low_intensity",
        "tmt",
        "itraq",
    ]:
        if isinstance(metrics.get(name), dict):
            slices.setdefault(name, dict(metrics[name]))
    return slices


def _metric_value(metrics: dict[str, Any], slice_name: str, metric: str) -> float | None:
    candidates: list[Any] = []
    if slice_name == "metrics":
        candidates.extend([metrics.get(metric), metrics.get(metric.lower())])
    if isinstance(metrics.get("slices"), dict):
        section = metrics["slices"].get(slice_name)
        if isinstance(section, dict):
            candidates.extend([section.get(metric), section.get(metric.lower())])
    section = metrics.get(slice_name)
    if isinstance(section, dict):
        candidates.extend([section.get(metric), section.get(metric.lower())])
    candidates.extend(
        [
            metrics.get(f"{slice_name}_{metric}"),
            metrics.get(f"{slice_name}.{metric}"),
            metrics.get(f"{slice_name}:{metric}"),
        ]
    )
    for value in candidates:
        parsed = _coerce_float(value)
        if parsed is not None:
            return parsed
    return None


def _threshold_for_metric(
    thresholds: dict[str, Any],
    *,
    primary_metric: str,
    slice_name: str,
    higher_is_better: bool,
    task_type: str,
) -> float:
    keys = [
        f"{slice_name}.{primary_metric}",
        f"{slice_name}:{primary_metric}",
        f"{slice_name}_{primary_metric}",
        primary_metric,
    ]
    for key in keys:
        value = _coerce_float(thresholds.get(key))
        if value is not None:
            return value
    metric = primary_metric.casefold()
    if not higher_is_better:
        if any(marker in metric for marker in ("mae", "rmse", "mse", "error")):
            return 0.25
        if "loss" in metric:
            return 1.0
        return 0.5
    defaults = {
        "denovo": 0.7,
        "ptm_denovo": 0.65,
        "psm_scoring": 0.8,
        "fragment_intensity_prediction": 0.75,
        "rt_prediction": 0.75,
    }
    if any(marker in metric for marker in ("auc", "auroc")):
        return 0.8
    if any(marker in metric for marker in ("r2", "pearson", "spearman", "cosine")):
        return 0.75
    return defaults.get(task_type, 0.7)


def _metric_is_below_threshold(value: float, threshold: float, higher_is_better: bool) -> bool:
    return value < threshold if higher_is_better else value > threshold


def _gap_from_model_slice(slice_name: str) -> dict[str, str]:
    key = slice_name.casefold()
    if "instrument" in key:
        return {"dimension": "instrument", "target": slice_name, "priority": "high"}
    if "organism" in key or "species" in key:
        return {"dimension": "organism", "target": slice_name, "priority": "high"}
    if "project" in key:
        return {"dimension": "project", "target": "project_disjoint_generalization", "priority": "high"}
    if "phosphotyrosine" in key or "ptyr" in key or "py" == key:
        return {"dimension": "ptm", "target": "phosphotyrosine", "priority": "high"}
    if "glyco" in key:
        return {"dimension": "ptm", "target": "glyco", "priority": "medium"}
    if "ubiquitin" in key or "glygly" in key or "kgg" in key:
        return {"dimension": "ptm", "target": "ubiquitin_glygly", "priority": "medium"}
    if "acetyl" in key or "kac" in key:
        return {"dimension": "ptm", "target": "acetyl", "priority": "medium"}
    if "charge" in key:
        return {"dimension": "charge", "target": slice_name, "priority": "medium"}
    if "intensity" in key or "low_signal" in key:
        return {"dimension": "spectrum_quality", "target": slice_name, "priority": "medium"}
    if "tmt" in key or "itraq" in key:
        return {"dimension": "labeling_strategy", "target": slice_name, "priority": "medium"}
    if "modified" in key or "ptm" in key:
        return {"dimension": "ptm", "target": slice_name, "priority": "medium"}
    return {"dimension": "hard_slice", "target": slice_name, "priority": "medium"}


def _hard_tag_severity(tag: str) -> str:
    if str(tag).startswith("hard_case_evidence_missing"):
        return "diagnostic"
    if tag in {"target_decoy_boundary", "low_spectrum_intensity", "phosphotyrosine_case"}:
        return "hard_case"
    if "blocked" in tag or "leakage" in tag:
        return "warning"
    return "diagnostic"


def _markdown_model_loop(
    eval_summary: dict[str, Any],
    failure_modes: dict[str, Any],
    gap_report: dict[str, Any],
    expansion_plan: dict[str, Any],
) -> str:
    lines = [
        "# Dataset Model Loop Report",
        "",
        f"- Status: `{eval_summary.get('status')}`",
        f"- Task: `{eval_summary.get('task_type')}`",
        f"- Adapter: `{eval_summary.get('adapter')}`",
        f"- Metric status: `{eval_summary.get('metric_status')}`",
        f"- Split counts: `{json.dumps(eval_summary.get('split_counts') or {}, ensure_ascii=False, sort_keys=True)}`",
        f"- Metrics: `{json.dumps(eval_summary.get('metrics') or {}, ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Failure Modes",
        "",
    ]
    for item in failure_modes.get("failure_modes") or []:
        lines.append(f"- `{item.get('failure_mode')}` ({item.get('severity')}) from {item.get('evidence')}")
    if not failure_modes.get("failure_modes"):
        lines.append("- No major failure mode detected.")
    lines.extend(["", "## Model-informed Gaps", ""])
    for gap in gap_report.get("gaps") or []:
        lines.append(f"- `{gap.get('dimension')}` -> `{gap.get('target')}`: {gap.get('reason')}")
    if not gap_report.get("gaps"):
        lines.append("- No major gap detected.")
    lines.extend(["", "## Expansion Plan", ""])
    for action in expansion_plan.get("actions") or []:
        lines.append(f"- `{action.get('action')}` {action.get('dimension')}: {action.get('query_hint')}")
    if not expansion_plan.get("actions"):
        lines.append("- No expansion action needed.")
    return "\n".join(lines) + "\n"


def _markdown_gap_report(report: dict[str, Any]) -> str:
    lines = ["# Model-informed Gap Report", "", f"- Status: `{report.get('status')}`", ""]
    for gap in report.get("gaps") or []:
        lines.append(f"- `{gap.get('dimension')}` / `{gap.get('target')}` ({gap.get('priority')}): {gap.get('reason')}")
    if not report.get("gaps"):
        lines.append("- No major gap detected.")
    return "\n".join(lines) + "\n"


def _markdown_discovery_requests(payload: dict[str, Any]) -> str:
    lines = [
        "# Model-informed Discovery Requests",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Task: `{payload.get('task_type')}`",
        f"- Request count: {payload.get('request_count', 0)}",
        "",
    ]
    requests = payload.get("requests") if isinstance(payload.get("requests"), list) else []
    if not requests:
        lines.append("- No discovery request needed.")
        return "\n".join(lines) + "\n"
    lines.extend(["## Requests", ""])
    for item in requests:
        if not isinstance(item, dict):
            continue
        lines.extend(
            [
                f"### {item.get('request_id')}",
                "",
                f"- Priority: `{item.get('priority')}`",
                f"- Gap: `{item.get('dimension')}` -> `{item.get('target')}`",
                f"- Query: `{item.get('query')}`",
                f"- Repositories: `{', '.join(map(str, item.get('repositories') or []))}`",
                f"- User confirmation required: `{item.get('requires_user_confirmation')}`",
                f"- Suggested CLI: `{item.get('suggested_cli')}`",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def _first_column(frame: pd.DataFrame, candidates: list[str]) -> str | None:
    by_lower = {str(column).casefold(): str(column) for column in frame.columns}
    for candidate in candidates:
        column = by_lower.get(candidate.casefold())
        if column is not None:
            return column
    return None


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _counts(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}
