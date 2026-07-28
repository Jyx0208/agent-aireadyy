# -*- coding: utf-8 -*-
"""Fail-closed release predicates for AI-ready exports (not Registry build_ready)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agent.models import JsonModel

ReleaseHorizon = Literal[
    "ai_ready_table",
    "pre_release",
    "full_release",
    "training_preview",
]


class ReleaseDecision(JsonModel):
    ok: bool = False
    status: str = "blocked"
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    horizon: str = "ai_ready_table"


def evaluate_export_nonempty(*, rows_out: int, parquet_exists: bool = True) -> list[str]:
    blockers: list[str] = []
    if int(rows_out or 0) <= 0:
        blockers.append("zero_rows")
    if not parquet_exists:
        blockers.append("parquet_missing")
    return blockers


def evaluate_leakage_gate(
    leakage_risk: dict[str, Any] | None,
    *,
    horizon: ReleaseHorizon,
) -> list[str]:
    blockers: list[str] = []
    if not isinstance(leakage_risk, dict):
        if horizon in {"pre_release", "full_release"}:
            blockers.append("leakage_not_evaluated")
        return blockers
    status = str(leakage_risk.get("status") or leakage_risk.get("risk") or "not_evaluated").casefold()
    if status in {"", "not_evaluated", "unknown", "none"}:
        if horizon in {"pre_release", "full_release", "ai_ready_table"}:
            blockers.append("leakage_not_evaluated")
        return blockers
    if status in {"fail", "failed", "high"}:
        blockers.append("leakage_failed")
        return blockers
    if status in {"warn", "warning", "medium"} and horizon in {"pre_release", "full_release"}:
        blockers.append("leakage_warn_blocks_pre_release")
    return blockers


def evaluate_rt_science(export_report: dict[str, Any] | None) -> list[str]:
    blockers: list[str] = []
    report = export_report or {}
    unit = str(report.get("rt_unit") or report.get("retention_time_unit") or "").casefold()
    unit_source = str(report.get("rt_unit_source") or report.get("retention_time_unit_source") or "").casefold()
    allow = bool(report.get("allow_unfiltered_rt"))
    # Provenance is authoritative: only column_explicit/user_supplied pass without opt-in.
    if unit_source in {"column_explicit", "user_supplied"}:
        pass
    elif unit_source in {"unknown", "inferred_default", "mixed", ""} or unit in {"", "unknown", "rt_unit_unknown"}:
        if not allow:
            blockers.append("rt_unit_unknown")
    # Release default: require confidence unless allow_unfiltered_rt
    has_conf = report.get("has_confidence_column")
    if has_conf is False and not allow:
        blockers.append("rt_confidence_required")
    if report.get("require_confidence") and int(report.get("rows_missing_confidence") or 0) > 0:
        blockers.append("rt_confidence_missing")
    return blockers


def evaluate_psm_science(export_report: dict[str, Any] | None) -> list[str]:
    blockers: list[str] = []
    report = export_report or {}
    target = int(report.get("target_count") or 0)
    decoy = int(report.get("decoy_count") or 0)
    rows = int(report.get("rows_out") or 0)
    if rows > 0 and decoy <= 0:
        blockers.append("psm_no_decoy")
    if rows > 0 and target <= 0:
        blockers.append("psm_no_target")
    if target > 0 and decoy > 0:
        fraction = decoy / max(1, target + decoy)
        low = float(report.get("decoy_fraction_min") or 0.05)
        high = float(report.get("decoy_fraction_max") or 0.95)
        if fraction < low or fraction > high:
            blockers.append("psm_decoy_fraction_out_of_band")
    return blockers



class ScienceGate(JsonModel):
    ok: bool = False
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def evaluate_export_science(
    task_type: str | None,
    export_report: dict[str, Any] | None = None,
    validation_row: dict[str, Any] | None = None,
) -> ScienceGate:
    """Task-specific science contract for an export/validation row."""
    report = dict(export_report or {})
    if isinstance(validation_row, dict):
        # validation row may carry science_contract fields already merged by caller
        for key, value in validation_row.items():
            report.setdefault(key, value)
    blockers: list[str] = []
    warnings: list[str] = []
    rows_out = int(report.get("rows_out") or 0)
    parquet_exists = report.get("parquet_exists")
    if parquet_exists is None and report.get("parquet_path"):
        from pathlib import Path as _Path
        parquet_exists = _Path(str(report.get("parquet_path"))).exists()
    blockers.extend(
        evaluate_export_nonempty(
            rows_out=rows_out,
            parquet_exists=True if parquet_exists is None else bool(parquet_exists),
        )
    )
    tt = str(task_type or "").casefold()
    if tt in {"rt_prediction", "rt", "retention_time"}:
        blockers.extend(evaluate_rt_science(report))
    if tt in {"psm_scoring", "psm", "rescoring"}:
        blockers.extend(evaluate_psm_science(report))
    # de-dupe
    blockers = list(dict.fromkeys(blockers))
    return ScienceGate(ok=not blockers, blockers=blockers, warnings=warnings)


def evaluate_release(
    *,
    horizon: ReleaseHorizon,
    rows_out: int,
    parquet_exists: bool = True,
    leakage_risk: dict[str, Any] | None = None,
    task_type: str | None = None,
    export_report: dict[str, Any] | None = None,
    integrity_status: str | None = None,
) -> ReleaseDecision:
    blockers: list[str] = []
    warnings: list[str] = []
    blockers.extend(evaluate_export_nonempty(rows_out=rows_out, parquet_exists=parquet_exists))
    blockers.extend(evaluate_leakage_gate(leakage_risk, horizon=horizon))
    tt = str(task_type or "").casefold()
    if tt in {"rt_prediction", "rt", "retention_time"}:
        blockers.extend(evaluate_rt_science(export_report))
    if tt in {"psm_scoring", "psm", "rescoring"}:
        blockers.extend(evaluate_psm_science(export_report))
    integrity = str(integrity_status or "unknown").casefold()
    if integrity in {"failed", "fail"}:
        blockers.append("artifact_integrity_failed")
    elif integrity in {"unknown", "checksum_unknown", ""} and horizon in {
        "pre_release",
        "full_release",
    }:
        blockers.append("artifact_integrity_unknown")
    ok = not blockers
    status = "ready" if ok else "blocked"
    if ok and horizon == "training_preview":
        status = "ready_for_training_preview"
    return ReleaseDecision(
        ok=ok,
        status=status,
        blockers=blockers,
        warnings=warnings,
        horizon=horizon,
    )


def export_status_for_rows(rows_out: int) -> str:
    """Canonical exporter status: never completed on zero rows."""
    return "completed" if int(rows_out or 0) > 0 else "export_empty"


def exporter_status_from_rows(rows_out: int) -> str:
    """Alias used by exporters."""
    return export_status_for_rows(rows_out)
