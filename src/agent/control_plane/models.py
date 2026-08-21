from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from agent.discovery.publication import (
    AuthorityEvidenceObservation,
    BuildReadyPackage,
    BusinessCompletionDecision,
    PublicationAuthorityState,
)
from agent.discovery.evidence_store import EvidenceStoreArtifact
from agent.discovery.builder_contract import BuilderDryRunResult
from agent.discovery.project_judgment import ProjectJudgmentInput
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
SearchGrantStatus = Literal["issued", "consumed", "rejected", "expired", "abandoned"]
BudgetReviewOutcome = Literal["granted", "replan", "stopped", "denied"]
DiscoveryAuditStatus = Literal["ready", "repair_required", "blocked"]
DiscoveryAuditSeverity = Literal["info", "warning", "error"]
DiscoveryRepairKind = Literal[
    "search_more",
    "inspect_candidates",
    "rescore_projects",
    "select_manifest",
    "stop_with_limitations",
]


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def minimum_high_relevance_inspections(
    high_relevance_candidate_count: int,
    max_projects: int,
) -> int:
    if high_relevance_candidate_count <= 0:
        return 0
    high = int(high_relevance_candidate_count)
    target = max(1, int(max_projects))
    # Maximize / large harvests: require broad inspection, but never demand inspecting
    # every high-relevance hit before any finalization is allowed.
    if target >= 100:
        return min(high, max(target, min(300, high)))
    return min(high, max(1, target * 2))


def recommended_inspection_rounds(
    target_projects: int,
    *,
    batch_size: int = 25,
    max_rounds: int = 20,
) -> int:
    target = max(1, int(target_projects))
    batch = max(1, int(batch_size))
    # Large maximize targets need more inspection rounds than curated pilots.
    if target >= 100:
        return min(max(8, int(max_rounds)), max(8, (min(target, 500) + batch - 1) // batch))
    return min(max(1, int(max_rounds)), (target + batch - 1) // batch)


class AgentBudget(JsonModel):
    max_turns: int = Field(default=50, ge=1, le=200)
    max_tool_calls: int = Field(default=200, ge=1, le=500)
    max_discovery_rounds: int = Field(default=8, ge=1, le=30)
    max_expensive_actions: int = Field(default=0, ge=0, le=50)
    max_download_bytes: int = Field(default=0, ge=0)


class DynamicBudgetLimits(JsonModel):
    # Generous hard ceilings: broad PRIDE paging + deep inspection should not trip
    # these under normal "越多越好" runs. Override via env only when needed.
    initial_query_units: int = Field(default=200, ge=1, le=10000)
    expanded_query_units: int = Field(default=600, ge=1, le=10000)
    max_query_units: int = Field(default=2000, ge=1, le=10000)
    initial_repository_requests: int = Field(default=3000, ge=1, le=100000)
    expanded_repository_requests: int = Field(default=10000, ge=1, le=100000)
    max_repository_requests: int = Field(default=25000, ge=1, le=100000)
    max_elapsed_seconds: int = Field(default=14400, ge=30, le=172800)
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
    semantic_coverage_gap: float = Field(default=1.0, ge=0.0, le=1.0)
    corpus_term_coverage_gap: float = Field(default=1.0, ge=0.0, le=1.0)
    hard_constraint_evidence_gap: float = Field(default=1.0, ge=0.0, le=1.0)
    n_hard_conjunction_pass: int = Field(default=0, ge=0)
    n_hard_pass_inspected: int = Field(default=0, ge=0)
    unknown_hard_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    candidate_level_conjunction_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    duplicate_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    high_relevance_gain: float = Field(default=0.0, ge=0.0, le=1.0)
    inspection_yield: float = Field(default=0.0, ge=0.0, le=1.0)
    no_gain_streak: int = Field(default=0, ge=0)
    counts: dict[str, int] = Field(default_factory=dict)
    deltas: dict[str, int] = Field(default_factory=dict)


class SearchDiagnosis(JsonModel):
    health: Literal[
        "healthy_yield",
        "selectivity_suspected",
        "repository_unavailable",
        "response_invalid",
        "no_match_after_recovery",
    ]
    strategy: str
    proposed_queries: list[str] = Field(default_factory=list)
    executed_queries: list[str] = Field(default_factory=list)
    consecutive_zero_yield: int = Field(default=0, ge=0)
    recovery_required: bool = False
    recovery_attempted: bool = False
    reason: str = ""


class DiscoveryAuditIssue(JsonModel):
    """One public, evidence-addressable quality finding (never hidden reasoning)."""

    code: str = Field(min_length=1, max_length=120)
    severity: DiscoveryAuditSeverity
    summary: str = Field(min_length=1, max_length=1000)
    project_accessions: list[str] = Field(default_factory=list, max_length=500)
    constraint_ids: list[str] = Field(default_factory=list, max_length=200)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)


class DiscoveryRepairAction(JsonModel):
    """A bounded next operation derived from an audit finding."""

    action: DiscoveryRepairKind
    reason: str = Field(min_length=1, max_length=1000)
    project_accessions: list[str] = Field(default_factory=list, max_length=500)
    constraint_ids: list[str] = Field(default_factory=list, max_length=200)


class DiscoveryQualityAudit(JsonModel):
    """Replayable selection-readiness report for the discovery Agent and UI."""

    schema_version: str = "discovery-quality-audit/v1"
    run_id: str
    status: DiscoveryAuditStatus
    ready_for_selection: bool = False
    counts: dict[str, int] = Field(default_factory=dict)
    requested_inspection_accessions: list[str] = Field(default_factory=list)
    succeeded_inspection_accessions: list[str] = Field(default_factory=list)
    non_assessable_inspection_accessions: list[str] = Field(default_factory=list)
    failed_inspection_accessions: list[str] = Field(default_factory=list)
    selection_backed_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    selection_backed_intent_terms: list[str] = Field(default_factory=list)
    uncovered_intent_terms: list[str] = Field(default_factory=list)
    unsupported_coverage_terms: list[str] = Field(default_factory=list)
    issues: list[DiscoveryAuditIssue] = Field(default_factory=list)
    repair_actions: list[DiscoveryRepairAction] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


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


class RuntimeProvenance(JsonModel):
    """Portable facts that identify the code and dependency runtime for a run."""

    schema_version: str = "runtime-provenance/v2"
    git_sha: str | None = None
    git_dirty: bool | None = None
    git_diff_sha256: str | None = None
    git_fingerprint_complete: bool | None = None
    untracked_source_file_count: int = Field(default=0, ge=0)
    python_version: str
    package_versions: dict[str, str | None] = Field(default_factory=dict)
    loaded_module_paths: dict[str, str | None] = Field(default_factory=dict)


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


class VerifiedProjectBatch(JsonModel):
    batch_index: int = Field(ge=1)
    batch_size: int = Field(ge=1)
    project_count: int = Field(ge=1)
    file_count: int = Field(ge=1)
    cumulative_verified_project_count: int = Field(ge=1)
    cumulative_verified_file_count: int = Field(default=0, ge=0)
    project_accessions: list[str] = Field(min_length=1)
    file_identifiers: list[str] = Field(default_factory=list)
    delivery_unit: Literal["project", "file"] = "project"
    manifest_path: str
    terminal: bool = False
    message: str


class AgentRunRecord(JsonModel):
    schema_version: str = "agent-control/v1"
    run_id: str
    project_id: str | None = None
    runtime: str = "openai_agents"
    runtime_provenance: RuntimeProvenance | None = None
    workflow: str
    status: AgentRunStatus = "created"
    prompt: str = ""
    request: dict[str, Any] = Field(default_factory=dict)
    budget: AgentBudget = Field(default_factory=AgentBudget)
    tool_call_count: int = 0
    discovery_round_count: int = 0
    candidate_search_count: int = 0
    candidate_inspection_count: int = 0
    inspected_candidate_accessions: list[str] = Field(default_factory=list)
    verified_project_accessions: list[str] = Field(default_factory=list)
    verified_project_batch_size: int = Field(default=500, ge=1, le=5000)
    published_verified_project_batches: list[VerifiedProjectBatch] = Field(default_factory=list)
    no_gain_action_count: int = 0
    latest_candidate_search_id: str | None = None
    latest_high_relevance_candidate_count: int = 0
    latest_semantic_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    latest_corpus_term_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    model_requests: int = 0
    sdk_turn_count: int = Field(default=0, ge=0)
    model_input_tokens: int = 0
    model_output_tokens: int = 0
    model_total_tokens: int = 0
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
    latest_discovery_audit: DiscoveryQualityAudit | None = None
    # Compact, replayable portfolio planner state.  The manifest itself remains
    # on disk; this field contains only the contract, coverage, gaps, and actions.
    portfolio_state: dict[str, Any] | None = None
    business_completion: BusinessCompletionDecision | None = None
    builder_dry_run_result: BuilderDryRunResult | None = None
    build_ready_package_material: BuildReadyPackage | None = None
    publication_authority: PublicationAuthorityState | None = None
    publication_evidence_observations: list[AuthorityEvidenceObservation] = Field(
        default_factory=list
    )
    publication_membership_refs: list[str] = Field(default_factory=list)
    publication_evidence_store: EvidenceStoreArtifact | None = None
    publication_builder_entrypoint: str | None = None
    publication_builder_preflight_ref: str | None = None
    publication_builder_preflight_status: str | None = None
    publication_materialization_blockers: list[str] = Field(default_factory=list)
    repair_execution_keys: list[str] = Field(default_factory=list)
    repair_no_progress_signature: str | None = None
    repair_no_progress_count: int = Field(default=0, ge=0)
    auth_refresh_attempts: int = Field(default=0, ge=0)
    project_judgments: dict[str, ProjectJudgmentInput] = Field(default_factory=dict)
    qualified_project_count: int = Field(default=0, ge=0)
    qualified_no_gain_count: int = Field(default=0, ge=0)
    consecutive_zero_yield: int = 0
    search_recovery_required: bool = False
    search_recovery_attempts: int = 0
    last_search_strategy: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    def remaining_model_turn_budget(self) -> int:
        """Return the conservative shared turn budget across SDK and provider counters."""

        consumed = max(int(self.sdk_turn_count), int(self.model_requests))
        return max(0, int(self.budget.max_turns) - consumed)


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
    candidate_search: dict[str, Any] | None = None
    project_assessments: list[dict[str, Any]] = Field(default_factory=list)
    inspection_outcomes: list[dict[str, Any]] = Field(default_factory=list)
    verified_project_count: int = Field(default=0, ge=0)
    published_verified_project_batches: list[VerifiedProjectBatch] = Field(default_factory=list)
    inspected_candidate_count: int = Field(default=0, ge=0)
    minimum_high_relevance_inspections: int = Field(default=0, ge=0)
    selection_ready: bool = False
    recommended_action: str = "review_manifest"
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    metrics: RoundMetrics | None = None
    diagnosis: SearchDiagnosis | None = None
    files: dict[str, str] = Field(default_factory=dict)


class OpenAIAgentsDiscoveryResult(JsonModel):
    status: AgentRunStatus
    run_id: str
    output_dir: str
    state_db: str
    runtime_provenance: RuntimeProvenance | None = None
    sdk_turn_count: int = Field(default=0, ge=0)
    selected_manifest_path: str | None = None
    selected_round_index: int | None = None
    selection_rationale: str | None = None
    discovery_round_count: int = 0
    final_output: str = ""
    latest_discovery_audit: DiscoveryQualityAudit | None = None
    portfolio_state: dict[str, Any] | None = None
    pending_approvals: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict)
