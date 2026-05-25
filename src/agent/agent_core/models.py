from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field

from agent.models import JsonModel


class AgentRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class GateAction(StrEnum):
    AUTO_ACCEPT = "auto_accept"
    EVIDENCE_GATED_ACCEPT = "evidence_gated_accept"
    REVIEW_REQUIRED = "review_required"


class AgentDecisionRecord(JsonModel):
    id: str
    decision_type: str
    selected_value: Any
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    risk_level: AgentRisk
    gate_action: GateAction | str


class AgentObservation(JsonModel):
    schema_version: str = "agent-audit/v1"
    input_file: str
    repository_candidates: list[dict[str, Any]] = Field(default_factory=list)
    selected_project: dict[str, Any] | None = None
    metadata_evidence: dict[str, Any] = Field(default_factory=dict)
    asset_evidence: dict[str, Any] = Field(default_factory=dict)
    resource_state: dict[str, Any] = Field(default_factory=dict)


class AgentPlanSummary(JsonModel):
    schema_version: str = "agent-audit/v1"
    selected_database: dict[str, Any] = Field(default_factory=dict)
    selected_workflow: dict[str, Any] = Field(default_factory=dict)
    search_parameters: dict[str, Any] = Field(default_factory=dict)
    resource_policy: dict[str, Any] = Field(default_factory=dict)
    risk_assessment: dict[str, Any] = Field(default_factory=dict)
    execution_gate: str = "allowed"
    blocking_issues: list[str] = Field(default_factory=list)


class AgentDecisionTrace(JsonModel):
    schema_version: str = "agent-audit/v1"
    decisions: list[AgentDecisionRecord] = Field(default_factory=list)


class AgentAuditArtifactPaths(JsonModel):
    observation: Path
    plan: Path
    decision_trace: Path
