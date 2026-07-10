from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field, model_validator

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
BudgetDecisionKind = Literal["grant", "shrink", "replan", "stop"]
SearchGrantStatus = Literal["issued", "consumed", "rejected", "expired"]
BudgetReviewOutcome = Literal["granted", "replan", "stopped", "denied"]


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class AgentBudget(JsonModel):
    max_turns: int = Field(default=8, ge=1, le=50)
    max_tool_calls: int = Field(default=12, ge=1, le=100)
    max_discovery_rounds: int = Field(default=3, ge=1, le=8)
    max_expensive_actions: int = Field(default=0, ge=0, le=20)
    max_download_bytes: int = Field(default=0, ge=0)


class DynamicBudgetLimits(JsonModel):
    max_query_units: int = Field(default=30, ge=1, le=500)
    max_repository_requests: int = Field(default=200, ge=1, le=5000)
    max_elapsed_seconds: int = Field(default=1200, ge=30, le=86400)
    budget_agent_max_turns: int = Field(default=3, ge=2, le=10)


class DynamicBudgetUsage(JsonModel):
    query_units: int = Field(default=0, ge=0)
    repository_requests: int = Field(default=0, ge=0)
    search_batches: int = Field(default=0, ge=0)
    budget_reviews: int = Field(default=0, ge=0)
    started_at: str = Field(default_factory=utc_now_iso)


class SearchProposalInput(JsonModel):
    objective: str = Field(min_length=1, max_length=500)
    reasoning_summary: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=50)
    queries: list[str] = Field(min_length=1, max_length=40)
    expected_gain_dimensions: list[str] = Field(default_factory=list, max_length=20)
    expected_gain: str = Field(min_length=1, max_length=1000)
    alternatives_considered: list[str] = Field(default_factory=list, max_length=20)
    stop_condition: str = Field(min_length=1, max_length=1000)


class SearchProposalRecord(SearchProposalInput):
    proposal_id: str
    run_id: str
    query_hash: str
    created_at: str = Field(default_factory=utc_now_iso)


class BudgetDecisionInput(JsonModel):
    proposal_id: str
    decision: BudgetDecisionKind
    approved_query_indexes: list[int] = Field(default_factory=list)
    rejected_query_indexes: list[int] = Field(default_factory=list)
    observed_gaps: list[str] = Field(default_factory=list)
    expected_value: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasoning_summary: str = Field(min_length=1, max_length=2000)
    stop_after_execution_if: str = Field(default="", max_length=1000)
    unresolved_gaps: list[str] = Field(default_factory=list)
    unexplored_strategies: list[str] = Field(default_factory=list)
    why_not_continue: str = Field(default="", max_length=2000)


class BudgetDecision(BudgetDecisionInput):

    @model_validator(mode="after")
    def validate_shape(self) -> "BudgetDecision":
        if self.decision == "grant" and not self.approved_query_indexes:
            raise ValueError("grant requires approved_query_indexes")
        if self.decision == "shrink" and not self.approved_query_indexes:
            raise ValueError("shrink requires a non-empty approved subset")
        if self.decision in {"replan", "stop"} and self.approved_query_indexes:
            raise ValueError("replan and stop cannot approve queries")
        if self.decision == "stop" and (
            not self.unresolved_gaps
            or not self.unexplored_strategies
            or not self.why_not_continue.strip()
        ):
            raise ValueError("stop requires counterfactual fields")
        return self


class SearchGrant(JsonModel):
    grant_id: str
    run_id: str
    proposal_id: str
    approved_queries: list[str] = Field(min_length=1)
    query_hash: str
    query_units: int = Field(ge=1)
    status: SearchGrantStatus = "issued"
    single_use: bool = True
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class RoundMetrics(JsonModel):
    round_index: int = Field(default=0, ge=0)
    candidate_shortfall: float = Field(ge=0.0, le=1.0)
    quality_gap: float = Field(ge=0.0, le=1.0)
    metadata_gap: float = Field(ge=0.0, le=1.0)
    diversity_gap: float = Field(ge=0.0, le=1.0)
    strategy_novelty: float = Field(ge=0.0, le=1.0)
    last_round_yield: float = Field(ge=0.0, le=1.0)
    query_repetition: float = Field(ge=0.0, le=1.0)
    budget_pressure: float = Field(ge=0.0, le=1.0)
    counts: dict[str, int] = Field(default_factory=dict)
    deltas: dict[str, int] = Field(default_factory=dict)


class BudgetReviewResult(JsonModel):
    outcome: BudgetReviewOutcome
    decision: BudgetDecision
    grant: SearchGrant | None = None
    reason: str


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
    dynamic_budget_enabled: bool = False
    dynamic_limits: DynamicBudgetLimits = Field(default_factory=DynamicBudgetLimits)
    dynamic_usage: DynamicBudgetUsage = Field(default_factory=DynamicBudgetUsage)
    active_grant_id: str | None = None
    search_stopped: bool = False
    search_stop_reason: str | None = None
    latest_metrics: RoundMetrics | None = None
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
    metrics: RoundMetrics | None = None
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
