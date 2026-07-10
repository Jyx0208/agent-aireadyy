from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field

from agent.models import JsonModel


AgentRunStatus = Literal[
    "created",
    "running",
    "waiting_approval",
    "completed",
    "completed_with_review",
    "blocked",
    "failed",
    "cancelled",
]
ToolRisk = Literal["read_only", "bounded_write", "expensive", "biological", "forbidden"]
PolicyOutcome = Literal["allow", "approval_required", "deny"]
ToolCallStatus = Literal["started", "completed", "failed", "denied"]


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class AgentBudget(JsonModel):
    max_turns: int = Field(default=8, ge=1, le=50)
    max_tool_calls: int = Field(default=12, ge=1, le=100)
    max_discovery_rounds: int = Field(default=3, ge=1, le=8)
    max_expensive_actions: int = Field(default=0, ge=0, le=20)
    max_download_bytes: int = Field(default=0, ge=0)


class ArtifactReference(JsonModel):
    path: str
    artifact_type: str
    schema_version: str | None = None
    sha256: str | None = None


class PolicyDecision(JsonModel):
    outcome: PolicyOutcome
    risk: ToolRisk
    reason: str
    requires_human: bool = False


class ToolExecutionRecord(JsonModel):
    idempotency_key: str
    run_id: str
    tool_name: str
    status: ToolCallStatus
    arguments: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class AgentEvent(JsonModel):
    sequence: int
    run_id: str
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class AgentRunRecord(JsonModel):
    schema_version: str = "agent-control/v1"
    run_id: str
    runtime: str = "openai_agents"
    workflow: str
    status: AgentRunStatus = "created"
    prompt: str = ""
    request: dict[str, Any] = Field(default_factory=dict)
    budget: AgentBudget = Field(default_factory=AgentBudget)
    tool_call_count: int = 0
    discovery_round_count: int = 0
    expensive_action_count: int = 0
    current_manifest_path: str | None = None
    candidate_pool_manifest_path: str | None = None
    selected_round_index: int | None = None
    selection_rationale: str | None = None
    artifacts: dict[str, ArtifactReference] = Field(default_factory=dict)
    pending_approvals: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    stop_reason: str | None = None
    final_output: str | None = None
    sdk_state_json: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class DiscoveryRoundObservation(JsonModel):
    status: Literal["completed", "blocked", "failed"]
    round_index: int
    queries: list[str] = Field(default_factory=list)
    manifest_path: str | None = None
    selected_projects: int = 0
    selected_files: int = 0
    candidate_pool_manifest_path: str | None = None
    pooled_selected_projects: int = 0
    pooled_selected_files: int = 0
    candidate_projects_seen: int = 0
    validity_status_counts: dict[str, int] = Field(default_factory=dict)
    evidence_level_distribution: dict[str, int] = Field(default_factory=dict)
    instrument_family_distribution: dict[str, int] = Field(default_factory=dict)
    unknown_counts: dict[str, int] = Field(default_factory=dict)
    recommended_action: str = "review_manifest"
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict)


class OpenAIAgentsDiscoveryResult(JsonModel):
    status: AgentRunStatus
    run_id: str
    output_dir: str
    state_db: str
    selected_manifest_path: str | None = None
    selected_round_index: int | None = None
    selection_rationale: str | None = None
    discovery_round_count: int = 0
    final_output: str = ""
    pending_approvals: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict)
