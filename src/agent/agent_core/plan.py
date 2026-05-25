from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.agent_core.models import AgentDecisionRecord, AgentPlanSummary, GateAction
from agent.models import AttributeSet, DdaExecutionPlan


def _field(obj: Any, name: str, default: Any = None) -> Any:
    return getattr(obj, name, default)


def _attr_value(attributes: AttributeSet | Any, name: str, default: Any = None) -> Any:
    attr = getattr(attributes, name, None)
    return getattr(attr, "value", default) if attr is not None else default


def _hints(attributes: AttributeSet | Any) -> dict[str, Any]:
    value = _attr_value(attributes, "search_parameter_hints", {})
    return dict(value) if isinstance(value, dict) else {}


def _name(path: Any) -> str:
    return Path(str(path)).name if path not in (None, "") else ""


def build_agent_plan_summary(
    plan: DdaExecutionPlan,
    attributes: AttributeSet,
    *,
    decisions: list[AgentDecisionRecord] | None = None,
) -> AgentPlanSummary:
    hints = _hints(attributes)
    needs_review = bool(_field(plan, "needs_review", False))
    blocking_issues = list(_field(plan, "blocking_issues", []) or [])
    review_decisions = [
        decision
        for decision in (decisions or [])
        if str(decision.gate_action) == str(GateAction.REVIEW_REQUIRED)
    ]
    if review_decisions:
        needs_review = True
        existing = set(blocking_issues)
        for decision in review_decisions:
            message = f"Agent decision requires review: {decision.decision_type}"
            if message not in existing:
                blocking_issues.append(message)
                existing.add(message)
    fasta_path = _field(plan, "fasta_path")
    workflow_path = _field(plan, "fragpipe_workflow_path")
    output_paths = _field(plan, "output_paths", {}) or {}
    return AgentPlanSummary(
        selected_database={
            "fasta_path": fasta_path,
            "fasta_name": _name(fasta_path),
            "fasta_selection_mode": _field(plan, "fasta_selection_mode"),
            "fasta_download_url": _field(plan, "fasta_download_url"),
            "recommended_fasta_name": hints.get("recommended_fasta_name"),
            "recommended_fasta_source": hints.get("recommended_fasta_source"),
        },
        selected_workflow={
            "path": workflow_path,
            "name": _name(workflow_path),
            "recommended_workflow_name": hints.get("recommended_workflow_name"),
            "workflow_parameter_overrides": hints.get("workflow_parameter_overrides")
            or hints.get("fragpipe_workflow_overrides")
            or hints.get("msfragger_parameter_overrides")
            or {},
        },
        search_parameters={
            "acquisition_mode": _attr_value(attributes, "acquisition_mode"),
            "species": _attr_value(attributes, "species"),
            "instrument": _attr_value(attributes, "instrument_name"),
            "instrument_family": _attr_value(attributes, "instrument_family"),
            "enzyme": _attr_value(attributes, "enzyme"),
            "labeling_strategy": _attr_value(attributes, "labeling_strategy"),
            "fixed_mods": _attr_value(attributes, "fixed_mods"),
            "variable_mods": _attr_value(attributes, "variable_mods"),
            "precursor_tol": hints.get("precursor_tol") or hints.get("precursor_tolerance"),
            "fragment_tol": hints.get("fragment_tol") or hints.get("fragment_tolerance"),
            "missed_cleavages": hints.get("missed_cleavages"),
            "min_peaks": hints.get("min_peaks"),
            "max_variable_mods": hints.get("max_variable_mods"),
        },
        resource_policy={
            "thread_num": _field(plan, "thread_num"),
            "raw_data_type": _field(plan, "raw_data_type"),
            "source_data_path": _field(plan, "source_data_path"),
        },
        risk_assessment={
            "needs_review": needs_review,
            "blocking_issue_count": len(blocking_issues),
            "expected_outputs": {
                "rawspectrum": _field(plan, "rawspectrum_output_path"),
                "fp_pin": _field(plan, "expected_pin_path"),
                "fp_msdt": output_paths.get("fp_msdt") if isinstance(output_paths, dict) else None,
            },
        },
        execution_gate="review_required" if needs_review else "allowed",
        blocking_issues=blocking_issues,
    )
