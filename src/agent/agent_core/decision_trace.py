from __future__ import annotations

import re
from typing import Any

from agent.agent_core.models import AgentDecisionRecord, AgentRisk, GateAction
from pathlib import Path

from agent.models import AttributeSet, AttributeValue, DdaExecutionPlan, FileAsset, ProjectResolution


def _field(obj: Any, name: str, default: Any = None) -> Any:
    return getattr(obj, name, default)


def _attribute_value(attributes: AttributeSet | Any, name: str, default: Any = None) -> Any:
    return getattr(attributes, name, default)


def _as_evidence(*values: Any) -> list[str]:
    evidence: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, list):
            evidence.extend(str(item) for item in value if str(item).strip())
        elif str(value).strip():
            evidence.append(str(value))
    return evidence


def _hint_mapping(attributes: AttributeSet | Any) -> dict[str, Any]:
    hints = _attribute_value(attributes, "search_parameter_hints")
    value = _field(hints, "value", {})
    return dict(value) if isinstance(value, dict) else {}


def _proteome_ids_from_hints(hints: dict[str, Any]) -> list[str]:
    text = " ".join(
        str(value)
        for key, value in hints.items()
        if key in {"recommended_fasta_name", "recommended_fasta_url", "recommended_fasta_source", "database"}
    )
    seen: set[str] = set()
    ids: list[str] = []
    for match in re.finditer(r"\bUP\d{9}\b", text, flags=re.IGNORECASE):
        proteome_id = match.group(0).upper()
        if proteome_id not in seen:
            seen.add(proteome_id)
            ids.append(proteome_id)
    return ids


def _risk_for_attribute(decision_type: str, attribute: AttributeValue | Any) -> AgentRisk:
    confidence = float(_field(attribute, "confidence", 0.0) or 0.0)
    if bool(_field(attribute, "conflict_flag", False)) or confidence < 0.7:
        return AgentRisk.HIGH
    if decision_type in {"species_inference", "acquisition_mode_inference", "enzyme_inference"} and confidence < 0.95:
        return AgentRisk.MEDIUM
    if decision_type == "enzyme_inference" and any(token in str(_field(attribute, "value", "")).lower() for token in ["/", "+", ";"]):
        return AgentRisk.MEDIUM
    return AgentRisk.LOW


def _gate_for_risk(risk: AgentRisk, confidence: float) -> GateAction:
    if risk == AgentRisk.HIGH:
        return GateAction.REVIEW_REQUIRED
    if risk == AgentRisk.MEDIUM:
        return GateAction.EVIDENCE_GATED_ACCEPT if confidence >= 0.85 else GateAction.REVIEW_REQUIRED
    return GateAction.AUTO_ACCEPT


def _attribute_decision(index: int, decision_type: str, attribute: AttributeValue | Any) -> AgentDecisionRecord:
    confidence = float(_field(attribute, "confidence", 0.0) or 0.0)
    risk = _risk_for_attribute(decision_type, attribute)
    return AgentDecisionRecord(
        id=f"D{index:03d}",
        decision_type=decision_type,
        selected_value=_field(attribute, "value"),
        confidence=confidence,
        evidence=_as_evidence(_field(attribute, "source"), _field(attribute, "evidence_excerpt")),
        alternatives=[],
        risk_level=risk,
        gate_action=_gate_for_risk(risk, confidence),
    )


def build_agent_decision_trace(
    resolution: ProjectResolution,
    attributes: AttributeSet,
    *,
    asset: FileAsset | Any | None = None,
    plan: DdaExecutionPlan | Any | None = None,
) -> list[AgentDecisionRecord]:
    decisions: list[AgentDecisionRecord] = []
    primary = _field(resolution, "primary_project")
    resolution_confidence = float(_field(resolution, "resolution_confidence", 0.0) or 0.0)
    project_risk = AgentRisk.HIGH if bool(_field(resolution, "needs_review", False)) or resolution_confidence < 0.85 else AgentRisk.LOW
    decisions.append(
        AgentDecisionRecord(
            id="D001",
            decision_type="project_selection",
            selected_value=_field(primary, "project_accession"),
            confidence=resolution_confidence,
            evidence=_as_evidence(_field(resolution, "resolution_reason"), _field(primary, "evidence", [])),
            alternatives=[
                {
                    "value": _field(candidate, "project_accession"),
                    "repository": _field(candidate, "repository"),
                    "matched_file": _field(candidate, "matched_file"),
                    "match_type": _field(candidate, "match_type"),
                    "match_score": _field(candidate, "match_score"),
                    "metadata_consistency": _field(candidate, "metadata_consistency"),
                    "reason_rejected": "not selected as primary project",
                }
                for candidate in list(_field(resolution, "alternative_projects", []) or [])
            ],
            risk_level=project_risk,
            gate_action=_gate_for_risk(project_risk, resolution_confidence),
        )
    )

    next_index = 2
    if asset is not None:
        asset_confidence = float(_field(asset, "asset_confidence", 0.0) or 0.0)
        asset_type = _field(asset, "resolved_asset_type")
        asset_risk = AgentRisk.HIGH if asset_type in {None, "", "unknown"} or asset_confidence < 0.75 else AgentRisk.LOW
        decisions.append(
            AgentDecisionRecord(
                id=f"D{next_index:03d}",
                decision_type="file_matching",
                selected_value=_field(asset, "matched_project_file") or _field(asset, "original_file_name"),
                confidence=asset_confidence,
                evidence=_as_evidence(
                    _field(asset, "match_type"),
                    _field(asset, "logical_path"),
                    _field(asset, "download_url"),
                ),
                alternatives=[],
                risk_level=asset_risk,
                gate_action=_gate_for_risk(asset_risk, asset_confidence),
            )
        )
        next_index += 1

    if plan is not None:
        hints = _hint_mapping(attributes)
        fasta_path = _field(plan, "fasta_path")
        fasta_mode = _field(plan, "fasta_selection_mode")
        fasta_url = _field(plan, "fasta_download_url")
        plan_needs_review = bool(_field(plan, "needs_review", False))
        database_risk = AgentRisk.HIGH if plan_needs_review or fasta_mode == "defaulted" else AgentRisk.LOW
        rejected_fasta_hints = [
            {
                "value": proteome_id,
                "source": "search_parameter_hints",
                "recommended_fasta_name": hints.get("recommended_fasta_name"),
                "recommended_fasta_url": hints.get("recommended_fasta_url"),
                "reason_rejected": (
                    "Rejected because the selected FASTA was canonicalized from stronger species evidence."
                ),
            }
            for proteome_id in _proteome_ids_from_hints(hints)
            if proteome_id not in f"{fasta_path} {fasta_url}"
        ]
        decisions.append(
            AgentDecisionRecord(
                id=f"D{next_index:03d}",
                decision_type="database_selection",
                selected_value={
                    "fasta_name": Path(str(fasta_path)).name if fasta_path else "",
                    "fasta_selection_mode": fasta_mode,
                    "fasta_download_url": fasta_url,
                },
                confidence=0.6 if database_risk == AgentRisk.HIGH else 0.9,
                evidence=_as_evidence(fasta_mode, fasta_url, hints.get("recommended_fasta_source"), hints.get("database")),
                alternatives=rejected_fasta_hints,
                risk_level=database_risk,
                gate_action=_gate_for_risk(database_risk, 0.6 if database_risk == AgentRisk.HIGH else 0.9),
            )
        )
        next_index += 1

        workflow_path = _field(plan, "fragpipe_workflow_path")
        workflow_name = Path(str(workflow_path)).name if workflow_path else ""
        workflow_risk = AgentRisk.HIGH if plan_needs_review or not workflow_name else AgentRisk.LOW
        decisions.append(
            AgentDecisionRecord(
                id=f"D{next_index:03d}",
                decision_type="workflow_selection",
                selected_value=workflow_name,
                confidence=0.6 if workflow_risk == AgentRisk.HIGH else 0.9,
                evidence=_as_evidence(workflow_path),
                alternatives=[],
                risk_level=workflow_risk,
                gate_action=_gate_for_risk(workflow_risk, 0.6 if workflow_risk == AgentRisk.HIGH else 0.9),
            )
        )
        next_index += 1

        decisions.append(
            AgentDecisionRecord(
                id=f"D{next_index:03d}",
                decision_type="resource_policy_selection",
                selected_value={
                    "thread_num": _field(plan, "thread_num"),
                    "raw_data_type": _field(plan, "raw_data_type"),
                },
                confidence=0.9,
                evidence=_as_evidence("Configured execution resource policy"),
                alternatives=[],
                risk_level=AgentRisk.LOW,
                gate_action=GateAction.AUTO_ACCEPT,
            )
        )
        next_index += 1

    attribute_map: list[tuple[str, Any]] = [
        ("acquisition_mode_inference", _attribute_value(attributes, "acquisition_mode")),
        ("species_inference", _attribute_value(attributes, "species")),
        ("instrument_inference", _attribute_value(attributes, "instrument_name")),
        ("enzyme_inference", _attribute_value(attributes, "enzyme")),
        ("labeling_inference", _attribute_value(attributes, "labeling_strategy")),
        ("search_parameter_selection", _attribute_value(attributes, "search_parameter_hints")),
    ]
    for offset, (decision_type, attribute) in enumerate(attribute_map, start=next_index):
        if attribute is not None:
            decisions.append(_attribute_decision(offset, decision_type, attribute))
    return decisions
