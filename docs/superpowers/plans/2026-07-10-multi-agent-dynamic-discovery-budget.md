# Multi-Agent Dynamic Discovery Budget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace pre-run Discovery search presets with a Discovery Manager Agent and Budget Agent that allocate search work dynamically under deterministic server hard limits.

**Architecture:** Keep the existing OpenAI Agents SDK runner, SQLite Control Plane, deterministic repository discovery, candidate-pool merge, and manifest selection. Add structured proposal/decision/grant records, a deterministic metrics evaluator and BudgetGovernor, then expose the Budget Agent through a bounded nested `Runner.run()` call. Search requires a one-use grant bound to the exact query list; the Web UI streams sanitized structured events and no longer asks ordinary users to choose a budget preset.

**Tech Stack:** Python 3.11+, Pydantic v2, SQLite, `openai-agents==0.18.1`, FastAPI, Typer, vanilla HTML/CSS/JavaScript, pytest, Playwright browser QA.

## Global Constraints

- Keep `openai-agents==0.18.1`; do not introduce LangGraph.
- Scope is Discovery only; do not make download, conversion, Docker execution, AI-ready Build, or model training agentic in this plan.
- Preserve the deterministic Discovery path and the current single-Agent SDK path until the multi-Agent release gate passes.
- Model turns, query units, repository HTTP requests, elapsed time, and total function-tool calls are server hard ceilings, not user-authored search plans.
- Multi-Agent mode must not use `max_discovery_rounds` as normal stopping logic.
- Discovery Manager owns queries and final manifest selection; Budget Agent may approve only the submitted query list or a subset.
- BudgetGovernor enforces schema, hard limits, exact query binding, idempotency, and grant lifecycle; it does not make scientific value judgments.
- Browser-supplied API keys remain transient and must not appear in SQLite payloads, job state, events, reports, or downloads.
- Show real actions, evidence references, tool inputs/outputs, metrics, and public reasoning summaries; never request, store, or display raw hidden chain-of-thought.
- Use the approved design at `docs/superpowers/specs/2026-07-10-multi-agent-dynamic-discovery-budget-design.md` as the source of truth.

## File Responsibility Map

- `src/agent/control_plane/models.py`: typed dynamic-budget contracts and run-level dynamic state.
- `src/agent/control_plane/store.py`: SQLite persistence and atomic proposal/decision/grant/usage operations.
- `src/agent/control_plane/discovery_metrics.py`: deterministic metric calculation only.
- `src/agent/control_plane/budget_governor.py`: proposal registration, decision validation, grant issuance/consumption, and hard-limit enforcement.
- `src/agent/control_plane/budget_agent.py`: bounded Budget Agent runner and strict decision submission tool.
- `src/agent/repositories/metering.py`: context-local repository HTTP request meter.
- `src/agent/pride/client.py`, `src/agent/repositories/massive_adapter.py`, `src/agent/repositories/iprox_adapter.py`: emit actual network-request meter events immediately before network calls.
- `src/agent/control_plane/discovery.py`: grant-gated repository tool, candidate pool, metrics, and finalization state.
- `src/agent/control_plane/openai_agents.py`: Discovery Manager tools, multi-Agent orchestration, streaming, outputs, and single-Agent compatibility.
- `src/agent/web/app.py`: server hard-limit configuration, event bridge, job payload, downloads, and mode rollout.
- `src/agent/web/templates/index.html`: autonomous-budget status, activity/tool/raw-event tabs, append-only log renderer, and removal of static budget inputs.
- `src/agent/cli.py`: explicit single/multi mode and server-ceiling CLI options while retaining legacy single-Agent flags.
- `scripts/evaluate_dynamic_discovery_budget.py`: baseline-versus-dynamic evaluation report.
- `tests/test_dynamic_budget.py`: contracts, store, metrics, governor, and Budget Agent tests.
- `tests/test_control_plane.py`: Discovery service and full SDK loop regression.
- `tests/test_web_discovery.py`, `tests/test_frontend_template.py`: Web API and template behavior.
- `tests/test_repository_request_metering.py`: HTTP-request instrumentation.
- `tests/fixtures/dynamic_budget_replays.json`: fixed replay scenario definitions.

---

### Task 1: Add Dynamic-Budget Contracts

**Files:**
- Create: `tests/test_dynamic_budget.py`
- Modify: `src/agent/control_plane/models.py:1-117`

**Interfaces:**
- Consumes: existing `JsonModel`, `AgentRunRecord`, `utc_now_iso()`.
- Produces: `DynamicBudgetLimits`, `DynamicBudgetUsage`, `SearchProposalInput`, `SearchProposalRecord`, `BudgetDecisionInput`, `BudgetDecision`, `SearchGrant`, `RoundMetrics`, `BudgetReviewResult`.

- [ ] **Step 1: Write failing contract tests**

```python
import pytest
from pydantic import ValidationError

from agent.control_plane.models import (
    BudgetDecision,
    BudgetDecisionInput,
    DynamicBudgetLimits,
    SearchGrant,
    SearchProposalInput,
)


def test_dynamic_budget_contracts_reject_invalid_decision_shapes() -> None:
    proposal = SearchProposalInput(
        objective="Improve metadata coverage",
        reasoning_summary="Most selected files lack sample metadata.",
        evidence_refs=["metadata_gap:0.7"],
        queries=["human plasma DDA SDRF", "human plasma Orbitrap raw"],
        expected_gain_dimensions=["metadata_completeness"],
        expected_gain="More sample-level metadata",
        alternatives_considered=["broaden generic terms"],
        stop_condition="No new usable files",
    )
    assert len(proposal.queries) == 2
    limits = DynamicBudgetLimits()
    assert limits.max_query_units == 30
    transport = BudgetDecisionInput(
        proposal_id="proposal_1",
        decision="stop",
        reasoning_summary="Stop without counterfactual fields",
    )
    assert transport.decision == "stop"
    with pytest.raises(ValidationError):
        BudgetDecision(
            proposal_id="proposal_1",
            decision="shrink",
            approved_query_indexes=[],
            rejected_query_indexes=[0, 1],
            reasoning_summary="Nothing approved",
        )
    with pytest.raises(ValidationError):
        BudgetDecision(
            proposal_id="proposal_1",
            decision="stop",
            reasoning_summary="Stop",
        )


def test_search_grant_is_single_use_by_contract() -> None:
    grant = SearchGrant(
        grant_id="grant_1",
        run_id="run_1",
        proposal_id="proposal_1",
        approved_queries=["human plasma DDA SDRF"],
        query_hash="abc",
        query_units=1,
    )
    assert grant.status == "issued"
    assert grant.single_use is True
```

- [ ] **Step 2: Run the contract tests and confirm they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dynamic_budget.py -q
```

Expected: collection fails because the dynamic-budget model names do not exist.

- [ ] **Step 3: Add the typed models and validators**

Add `model_validator` to the Pydantic imports and implement these exact public fields:

```python
BudgetDecisionKind = Literal["grant", "shrink", "replan", "stop"]
SearchGrantStatus = Literal["issued", "consumed", "rejected", "expired"]
BudgetReviewOutcome = Literal["granted", "replan", "stopped", "denied"]


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
```

Extend `AgentRunRecord` without removing legacy fields:

```python
dynamic_budget_enabled: bool = False
dynamic_limits: DynamicBudgetLimits = Field(default_factory=DynamicBudgetLimits)
dynamic_usage: DynamicBudgetUsage = Field(default_factory=DynamicBudgetUsage)
active_grant_id: str | None = None
search_stopped: bool = False
search_stop_reason: str | None = None
latest_metrics: RoundMetrics | None = None
```

- [ ] **Step 4: Run the model tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dynamic_budget.py -q
```

Expected: both contract tests pass.

- [ ] **Step 5: Commit the contracts**

```powershell
git add src/agent/control_plane/models.py tests/test_dynamic_budget.py
git commit -m "feat: add dynamic discovery budget contracts"
```

---

### Task 2: Persist Proposals, Decisions, Grants, and Usage Atomically

**Files:**
- Modify: `src/agent/control_plane/store.py:1-212`
- Modify: `tests/test_dynamic_budget.py`

**Interfaces:**
- Consumes: Task 1 model classes.
- Produces: `save_search_proposal`, `load_search_proposal`, `list_search_proposals`, `save_budget_decision`, `load_budget_decision`, `issue_search_grant`, `load_search_grant`, `consume_search_grant`, `increment_dynamic_usage`, `increment_tool_call_count`.

- [ ] **Step 1: Add a failing store lifecycle test**

```python
def test_store_persists_and_consumes_one_use_grant_atomically(tmp_path: Path) -> None:
    store = AgentRunStore(tmp_path / "state.sqlite")
    run = store.save_run(AgentRunRecord(run_id="run_budget", workflow="discovery"))
    proposal = SearchProposalRecord(
        proposal_id="proposal_1",
        run_id=run.run_id,
        query_hash="hash_1",
        objective="Find metadata",
        reasoning_summary="Metadata is missing.",
        queries=["human plasma SDRF"],
        expected_gain="More metadata",
        stop_condition="No gain",
    )
    store.save_search_proposal(proposal)
    decision = BudgetDecision(
        proposal_id=proposal.proposal_id,
        decision="grant",
        approved_query_indexes=[0],
        reasoning_summary="The query is novel.",
    )
    store.save_budget_decision(run.run_id, decision)
    grant = SearchGrant(
        grant_id="grant_1",
        run_id=run.run_id,
        proposal_id=proposal.proposal_id,
        approved_queries=proposal.queries,
        query_hash=proposal.query_hash,
        query_units=1,
    )
    store.issue_search_grant(grant)
    consumed = store.consume_search_grant(run.run_id, grant.grant_id, grant.query_hash)
    assert consumed.status == "consumed"
    with pytest.raises(ValueError, match="grant_already_consumed"):
        store.consume_search_grant(run.run_id, grant.grant_id, grant.query_hash)
```

- [ ] **Step 2: Run the store test and confirm it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dynamic_budget.py::test_store_persists_and_consumes_one_use_grant_atomically -q
```

Expected: FAIL because the store methods do not exist.

- [ ] **Step 3: Add the SQLite tables**

Append these statements to `_initialize()`:

```sql
CREATE TABLE IF NOT EXISTS agent_search_proposals (
    proposal_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    query_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES agent_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_search_proposals_run
ON agent_search_proposals(run_id, created_at);

CREATE TABLE IF NOT EXISTS agent_budget_decisions (
    proposal_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES agent_runs(run_id),
    FOREIGN KEY(proposal_id) REFERENCES agent_search_proposals(proposal_id)
);

CREATE TABLE IF NOT EXISTS agent_search_grants (
    grant_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    proposal_id TEXT NOT NULL,
    query_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES agent_runs(run_id),
    FOREIGN KEY(proposal_id) REFERENCES agent_search_proposals(proposal_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_search_grants_run
ON agent_search_grants(run_id, created_at);
```

- [ ] **Step 4: Implement typed persistence and atomic consumption**

Use `canonical_json()` for every payload and `BEGIN IMMEDIATE` for grant consumption and usage increments. The grant transition must be implemented with this check-before-update shape:

```python
def consume_search_grant(self, run_id: str, grant_id: str, query_hash: str) -> SearchGrant:
    connection = self._connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT payload_json, status, run_id, query_hash FROM agent_search_grants WHERE grant_id = ?",
            (grant_id,),
        ).fetchone()
        if row is None:
            raise ValueError("search_grant_not_found")
        if str(row["run_id"]) != run_id:
            raise ValueError("search_grant_run_mismatch")
        if str(row["query_hash"]) != query_hash:
            raise ValueError("search_grant_query_mismatch")
        if str(row["status"]) != "issued":
            raise ValueError(f"grant_already_{row['status']}")
        grant = SearchGrant.model_validate(json.loads(row["payload_json"]))
        consumed = grant.model_copy(update={"status": "consumed", "updated_at": utc_now_iso()})
        connection.execute(
            "UPDATE agent_search_grants SET status = ?, payload_json = ?, updated_at = ? WHERE grant_id = ?",
            (consumed.status, canonical_json(consumed.model_dump(mode="json")), consumed.updated_at, grant_id),
        )
        connection.commit()
        return consumed
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
```

Implement `increment_dynamic_usage(..., enforce_limits: bool = True)` by loading the run inside the same immediate transaction, applying non-negative deltas, validating against `DynamicBudgetLimits` when enforcement is enabled, and updating `agent_runs.payload_json` before commit. Single-Agent baseline metering passes `enforce_limits=False`; multi-Agent Governor calls use the default. Implement `increment_tool_call_count(run_id)` with the same transaction pattern; reject the increment with `tool_call_budget_exhausted` when the next call exceeds `run.budget.max_tool_calls`.

- [ ] **Step 5: Run dynamic store and existing store tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dynamic_budget.py tests/test_control_plane.py::test_agent_run_store_round_trips_events_and_idempotent_tool_calls -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit persistence**

```powershell
git add src/agent/control_plane/store.py tests/test_dynamic_budget.py
git commit -m "feat: persist dynamic discovery grants"
```

---

### Task 3: Implement Deterministic Round Metrics

**Files:**
- Create: `src/agent/control_plane/discovery_metrics.py`
- Modify: `tests/test_dynamic_budget.py`

**Interfaces:**
- Consumes: `DatasetManifest`, `DatasetRequest`, `DynamicBudgetLimits`, `DynamicBudgetUsage`.
- Produces: `evaluate_round_metrics(current, previous, request, queries, prior_queries, usage, limits, round_index) -> RoundMetrics`, `elapsed_seconds_since(started_at) -> float`.

- [ ] **Step 1: Write failing metric tests**

```python
def _manifest_with_files(
    request: DatasetRequest,
    *,
    valid: int,
    weak_keep: int,
    needs_review: int,
) -> DatasetManifest:
    statuses = ["valid"] * valid + ["weak_keep"] * weak_keep + ["needs_review"] * needs_review
    project = DiscoveredProject(project_accession="PXD_METRICS", project_title="Metrics fixture")
    files = [
        DiscoveredFile(
            project_accession=project.project_accession,
            project_title=project.project_title,
            file_name=f"sample_{index}.raw",
            file_type=".raw",
            validity_status=status,
            evidence_level="file",
        )
        for index, status in enumerate(statuses)
    ]
    return DatasetManifest(
        request=request,
        projects=[project],
        files=files,
        summary={
            "selected_projects": 1,
            "selected_files": len(files),
            "validity_status_counts": {
                "valid": valid,
                "weak_keep": weak_keep,
                "needs_review": needs_review,
            },
            "instrument_family_distribution": {"orbitrap": len(files)},
            "unknown_counts": {"fragmentation_method": needs_review},
        },
    )


def test_round_metrics_reward_new_usable_candidates_and_penalize_repeated_queries() -> None:
    request = DatasetRequest(repository="pride", max_files=50)
    previous = DatasetManifest(request=request, summary={"selected_files": 0})
    current = _manifest_with_files(request, valid=4, weak_keep=1, needs_review=1)
    limits = DynamicBudgetLimits(max_query_units=20, max_repository_requests=100)
    usage = DynamicBudgetUsage(query_units=5, repository_requests=12, search_batches=1)
    novel = evaluate_round_metrics(
        current,
        previous,
        request=request,
        queries=["human plasma DDA SDRF"],
        prior_queries=["mouse liver phosphoproteomics"],
        usage=usage,
        limits=limits,
        round_index=2,
    )
    repeated = evaluate_round_metrics(
        current,
        previous,
        request=request,
        queries=["human plasma DDA SDRF"],
        prior_queries=["human plasma DDA SDRF"],
        usage=usage,
        limits=limits,
        round_index=2,
    )
    assert novel.last_round_yield > 0
    assert novel.strategy_novelty > repeated.strategy_novelty
    assert repeated.query_repetition == 1.0
    assert novel.counts["usable_files"] == 5
```

- [ ] **Step 2: Run the metric test and confirm it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dynamic_budget.py::test_round_metrics_reward_new_usable_candidates_and_penalize_repeated_queries -q
```

Expected: import failure for `evaluate_round_metrics`.

- [ ] **Step 3: Implement bounded deterministic calculations**

Use a ten-file sufficiency floor only as a metric reference, never as a stop rule:

```python
def evaluate_round_metrics(
    current: DatasetManifest,
    previous: DatasetManifest | None,
    *,
    request: DatasetRequest,
    queries: list[str],
    prior_queries: list[str],
    usage: DynamicBudgetUsage,
    limits: DynamicBudgetLimits,
    round_index: int,
) -> RoundMetrics:
    current_counts = _manifest_counts(current)
    previous_counts = _manifest_counts(previous)
    usable = current_counts["usable_files"]
    selected = current_counts["selected_files"]
    sufficiency_floor = max(1, min(int(request.max_files), 10))
    candidate_shortfall = _clamp(1.0 - usable / sufficiency_floor)
    quality_gap = _clamp(1.0 - usable / max(1, selected))
    unknown_total = sum(int(value or 0) for value in (current.summary.get("unknown_counts") or {}).values())
    metadata_gap = _clamp(unknown_total / max(1, selected * 4))
    diversity = current.summary.get("instrument_family_distribution") or {}
    diversity_gap = _clamp(1.0 - len(diversity) / 2.0) if selected >= 2 else 1.0
    novelty = _query_novelty(queries, prior_queries)
    new_usable = max(0, usable - previous_counts["usable_files"])
    last_yield = _clamp(new_usable / max(1, len(queries)))
    pressure = max(
        usage.query_units / limits.max_query_units,
        usage.repository_requests / limits.max_repository_requests,
        elapsed_seconds_since(usage.started_at) / limits.max_elapsed_seconds,
    )
    return RoundMetrics(
        round_index=round_index,
        candidate_shortfall=candidate_shortfall,
        quality_gap=quality_gap,
        metadata_gap=metadata_gap,
        diversity_gap=diversity_gap,
        strategy_novelty=novelty,
        last_round_yield=last_yield,
        query_repetition=_clamp(1.0 - novelty),
        budget_pressure=_clamp(pressure),
        counts=current_counts,
        deltas={key: current_counts[key] - previous_counts[key] for key in current_counts},
    )
```

Implement the helpers exactly enough to keep metrics deterministic:

```python
def _clamp(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


def _manifest_counts(manifest: DatasetManifest | None) -> dict[str, int]:
    if manifest is None:
        return {"selected_files": 0, "usable_files": 0, "valid_files": 0, "review_files": 0}
    files = list(manifest.files)
    return {
        "selected_files": int(manifest.summary.get("selected_files") or len(files)),
        "usable_files": sum(item.validity_status in {"valid", "weak_keep"} for item in files),
        "valid_files": sum(item.validity_status == "valid" for item in files),
        "review_files": sum(item.validity_status == "needs_review" for item in files),
    }


def _query_tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(value).casefold()))


def _query_novelty(queries: list[str], prior_queries: list[str]) -> float:
    if not prior_queries:
        return 1.0
    maximum_similarity = 0.0
    for query in queries:
        current = _query_tokens(query)
        for prior in prior_queries:
            previous = _query_tokens(prior)
            union = current | previous
            similarity = len(current & previous) / len(union) if union else 1.0
            maximum_similarity = max(maximum_similarity, similarity)
    return _clamp(1.0 - maximum_similarity)


def elapsed_seconds_since(started_at: str) -> float:
    started = datetime.fromisoformat(started_at)
    return max(0.0, (datetime.now(UTC) - started.astimezone(UTC)).total_seconds())
```

An exact previous query produces novelty `0.0` and repetition `1.0`.

- [ ] **Step 4: Run metric tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dynamic_budget.py -q
```

Expected: all Task 1-3 tests pass.

- [ ] **Step 5: Commit metrics**

```powershell
git add src/agent/control_plane/discovery_metrics.py tests/test_dynamic_budget.py
git commit -m "feat: evaluate discovery marginal value"
```

---

### Task 4: Add the Deterministic BudgetGovernor

**Files:**
- Create: `src/agent/control_plane/budget_governor.py`
- Modify: `src/agent/control_plane/policy.py:1-70`
- Modify: `tests/test_dynamic_budget.py`

**Interfaces:**
- Consumes: Task 1 models and Task 2 store.
- Produces: `canonicalize_queries`, `hash_queries`, `BudgetGovernor.authorize_tool`, `BudgetGovernor.register_proposal`, `BudgetGovernor.apply_decision`, `BudgetGovernor.consume_grant`, `BudgetGovernor.stop_for_hard_limit`.

- [ ] **Step 1: Write failing governor tests**

```python
def _dynamic_store_and_run(
    tmp_path: Path,
    *,
    max_query_units: int = 30,
) -> tuple[AgentRunStore, AgentRunRecord]:
    store = AgentRunStore(tmp_path / "state.sqlite")
    run = store.save_run(
        AgentRunRecord(
            run_id="dynamic_run",
            workflow="discovery",
            status="running",
            dynamic_budget_enabled=True,
            dynamic_limits=DynamicBudgetLimits(
                max_query_units=max_query_units,
                max_repository_requests=200,
            ),
            budget=AgentBudget(max_turns=50, max_tool_calls=100),
        )
    )
    return store, run


def _proposal(queries: list[str]) -> SearchProposalInput:
    return SearchProposalInput(
        objective="Improve metadata coverage",
        reasoning_summary="The measured metadata gap is high.",
        evidence_refs=["metadata_gap:0.7"],
        queries=queries,
        expected_gain_dimensions=["metadata_completeness"],
        expected_gain="More usable metadata",
        alternatives_considered=["generic broad search"],
        stop_condition="No new usable files",
    )


def _grant_decision(proposal_id: str, indexes: list[int]) -> BudgetDecision:
    return BudgetDecision(
        proposal_id=proposal_id,
        decision="grant",
        approved_query_indexes=indexes,
        reasoning_summary="The approved queries target measured gaps.",
    )


def test_governor_issues_subset_grant_and_rejects_tamper_and_replay(tmp_path: Path) -> None:
    store, run = _dynamic_store_and_run(tmp_path, max_query_units=3)
    governor = BudgetGovernor(store, run.run_id)
    proposal = governor.register_proposal(
        SearchProposalInput(
            objective="Improve metadata",
            reasoning_summary="Metadata gap is high.",
            queries=["human plasma SDRF", "human plasma Orbitrap"],
            expected_gain="More usable metadata",
            stop_condition="No new usable files",
        )
    )
    result = governor.apply_decision(
        BudgetDecision(
            proposal_id=proposal.proposal_id,
            decision="shrink",
            approved_query_indexes=[0],
            rejected_query_indexes=[1],
            reasoning_summary="The first query targets the measured gap.",
        )
    )
    assert result.outcome == "granted"
    assert result.grant is not None
    assert result.grant.approved_queries == ["human plasma SDRF"]
    with pytest.raises(ValueError, match="search_grant_query_mismatch"):
        governor.consume_grant(result.grant.grant_id, ["changed query"])
    governor.consume_grant(result.grant.grant_id, ["human plasma SDRF"])
    with pytest.raises(ValueError, match="grant_already_consumed"):
        governor.consume_grant(result.grant.grant_id, ["human plasma SDRF"])
```

- [ ] **Step 2: Run the governor test and confirm it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dynamic_budget.py::test_governor_issues_subset_grant_and_rejects_tamper_and_replay -q
```

Expected: import failure for `BudgetGovernor`.

- [ ] **Step 3: Implement canonicalization, decision validation, and grants**

`canonicalize_queries()` must preserve first occurrence order, collapse whitespace, case-fold only for duplicate detection, reject strings over 240 characters, and cap a proposal at 40 queries. `hash_queries()` must hash UTF-8 canonical JSON.

Implement this control flow:

```python
class BudgetGovernor:
    def __init__(self, store: AgentRunStore, run_id: str) -> None:
        self.store = store
        self.run_id = run_id

    def authorize_tool(self, tool_name: str) -> None:
        policy = evaluate_tool_policy(tool_name, self._require_run())
        if policy.outcome != "allow":
            raise ValueError(policy.reason)
        self.store.increment_tool_call_count(self.run_id)

    def register_proposal(self, payload: SearchProposalInput) -> SearchProposalRecord:
        self.authorize_tool("request_search_budget")
        queries = canonicalize_queries(payload.queries)
        proposal = SearchProposalRecord(
            **payload.model_dump(exclude={"queries"}),
            queries=queries,
            proposal_id=f"proposal_{uuid.uuid4().hex}",
            run_id=self.run_id,
            query_hash=hash_queries(queries),
        )
        self.store.save_search_proposal(proposal)
        self.store.append_event(self.run_id, "search_plan_proposed", proposal.model_dump(mode="json"))
        return proposal

    def apply_decision(self, decision: BudgetDecision) -> BudgetReviewResult:
        proposal = self.store.load_search_proposal(decision.proposal_id)
        if proposal is None or proposal.run_id != self.run_id:
            raise ValueError("budget_proposal_not_found")
        self._validate_indexes(proposal, decision)
        self.store.save_budget_decision(self.run_id, decision)
        if decision.decision == "replan":
            return BudgetReviewResult(outcome="replan", decision=decision, reason="budget_agent_requested_replan")
        if decision.decision == "stop":
            self._mark_search_stopped("budget_agent_stop")
            return BudgetReviewResult(outcome="stopped", decision=decision, reason="budget_agent_stop")
        approved = [proposal.queries[index] for index in decision.approved_query_indexes]
        denial_reason = self._grant_denial_reason(approved)
        if denial_reason:
            self.store.append_event(
                self.run_id,
                "search_grant_rejected",
                {"proposal_id": proposal.proposal_id, "reason": denial_reason},
            )
            return BudgetReviewResult(
                outcome="denied",
                decision=decision,
                reason=denial_reason,
            )
        grant = SearchGrant(
            grant_id=f"grant_{uuid.uuid4().hex}",
            run_id=self.run_id,
            proposal_id=proposal.proposal_id,
            approved_queries=approved,
            query_hash=hash_queries(approved),
            query_units=len(approved),
        )
        self.store.issue_search_grant(grant)
        self._set_active_grant(grant.grant_id)
        self.store.append_event(self.run_id, "search_grant_issued", grant.model_dump(mode="json"))
        return BudgetReviewResult(outcome="granted", decision=decision, grant=grant, reason="budget_agent_grant")
```

`_grant_denial_reason()` returns `hard_query_unit_limit`, `hard_elapsed_time_limit`, `active_search_grant_exists`, or `duplicate_query_not_authorized`; it returns `None` when the grant can be issued. Parse `run.dynamic_usage.started_at` with `datetime.fromisoformat()` and compare it with `run.dynamic_limits.max_elapsed_seconds` before every grant. Budget application denials are structured results. Grant lookup, tamper, and replay errors during execution are raised as `ValueError` because they represent invalid tool use.

Use these private helpers inside `BudgetGovernor`:

```python
def _require_run(self) -> AgentRunRecord:
    run = self.store.load_run(self.run_id)
    if run is None:
        raise KeyError(f"Unknown agent run: {self.run_id}")
    return run


def _validate_indexes(self, proposal: SearchProposalRecord, decision: BudgetDecision) -> None:
    approved = list(decision.approved_query_indexes)
    rejected = list(decision.rejected_query_indexes)
    if len(approved) != len(set(approved)) or len(rejected) != len(set(rejected)):
        raise ValueError("budget_decision_duplicate_indexes")
    indexes = approved + rejected
    if any(index < 0 or index >= len(proposal.queries) for index in indexes):
        raise ValueError("budget_decision_index_out_of_range")
    if set(approved) & set(rejected):
        raise ValueError("budget_decision_overlapping_indexes")
    if decision.decision == "grant" and set(approved) != set(range(len(proposal.queries))):
        raise ValueError("grant_must_approve_all_queries")
    if decision.decision == "shrink" and set(approved) == set(range(len(proposal.queries))):
        raise ValueError("shrink_requires_true_subset")


def elapsed_seconds(self) -> float:
    return elapsed_seconds_since(self._require_run().dynamic_usage.started_at)


def _grant_denial_reason(self, approved: list[str]) -> str | None:
    run = self._require_run()
    if run.active_grant_id:
        return "active_search_grant_exists"
    if self.elapsed_seconds() >= run.dynamic_limits.max_elapsed_seconds:
        return "hard_elapsed_time_limit"
    if run.dynamic_usage.query_units + len(approved) > run.dynamic_limits.max_query_units:
        return "hard_query_unit_limit"
    consumed_queries = {
        " ".join(query.casefold().split())
        for grant in self.store.list_search_grants(self.run_id)
        if grant.status == "consumed"
        for query in grant.approved_queries
    }
    if any(" ".join(query.casefold().split()) in consumed_queries for query in approved):
        return "duplicate_query_not_authorized"
    return None


def _set_active_grant(self, grant_id: str | None) -> None:
    run = self._require_run()
    self.store.save_run(run.model_copy(update={"active_grant_id": grant_id}))


def _mark_search_stopped(self, reason: str) -> None:
    run = self._require_run()
    self.store.save_run(
        run.model_copy(
            update={"search_stopped": True, "search_stop_reason": reason, "active_grant_id": None}
        )
    )
    self.store.append_event(self.run_id, "dynamic_search_stopped", {"reason": reason})
```

`consume_grant()` clears `active_grant_id` only after the store atomically transitions the matching grant to `consumed`, then increments `query_units` and `search_batches`.

Add `request_search_budget` and `submit_budget_decision` to `TOOL_RISKS` as `read_only`; the final grant write remains an internal bounded Control Plane operation, not an LLM-exposed arbitrary write.

- [ ] **Step 4: Add hard-limit and duplicate-query tests**

```python
def test_governor_denies_grant_over_remaining_query_units(tmp_path: Path) -> None:
    store, run = _dynamic_store_and_run(tmp_path, max_query_units=1)
    governor = BudgetGovernor(store, run.run_id)
    proposal = governor.register_proposal(_proposal(["query one", "query two"]))
    result = governor.apply_decision(_grant_decision(proposal.proposal_id, [0, 1]))
    assert result.outcome == "denied"
    assert result.reason == "hard_query_unit_limit"
    assert result.grant is None


def test_governor_rejects_exact_consumed_query_without_retryable_failure(tmp_path: Path) -> None:
    store, run = _dynamic_store_and_run(tmp_path)
    governor = BudgetGovernor(store, run.run_id)
    first = governor.register_proposal(_proposal(["human plasma SDRF"]))
    first_review = governor.apply_decision(_grant_decision(first.proposal_id, [0]))
    governor.consume_grant(first_review.grant.grant_id, ["human plasma SDRF"])
    second = governor.register_proposal(_proposal(["human   plasma SDRF"]))
    second_review = governor.apply_decision(_grant_decision(second.proposal_id, [0]))
    assert second_review.outcome == "denied"
    assert second_review.reason == "duplicate_query_not_authorized"
```

- [ ] **Step 5: Run governor and policy tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dynamic_budget.py tests/test_control_plane.py::test_control_plane_policy_separates_safe_expensive_biological_and_forbidden_tools -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit the governor**

```powershell
git add src/agent/control_plane/budget_governor.py src/agent/control_plane/policy.py tests/test_dynamic_budget.py
git commit -m "feat: govern dynamic discovery grants"
```

---

### Task 5: Meter Actual Repository Network Requests

**Files:**
- Create: `src/agent/repositories/metering.py`
- Create: `tests/test_repository_request_metering.py`
- Modify: `src/agent/pride/client.py:1-96`
- Modify: `src/agent/repositories/massive_adapter.py:150-220`
- Modify: `src/agent/repositories/iprox_adapter.py:350-380`
- Modify: `src/agent/control_plane/budget_governor.py`

**Interfaces:**
- Consumes: `BudgetGovernor` and `DynamicBudgetUsage`.
- Produces: `meter_repository_requests(callback)`, `record_repository_request(repository, operation)`, `BudgetGovernor.record_repository_request(repository, operation)`.

- [ ] **Step 1: Write a failing context-local metering test**

```python
def test_repository_meter_records_each_http_attempt(monkeypatch) -> None:
    observed: list[tuple[str, str]] = []
    response = httpx.Response(200, json={"_embedded": {"projects": []}}, request=httpx.Request("GET", "https://x.test"))
    client = PrideClient()
    monkeypatch.setattr(client._client, "get", lambda *args, **kwargs: response)
    with meter_repository_requests(lambda repository, operation: observed.append((repository, operation))):
        client.search_projects("human plasma")
    client.close()
    assert observed == [("pride", "search_projects")]
```

- [ ] **Step 2: Run the meter test and confirm it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_repository_request_metering.py -q
```

Expected: import failure for `agent.repositories.metering`.

- [ ] **Step 3: Implement the ContextVar meter**

```python
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator

RepositoryRequestCallback = Callable[[str, str], None]
_request_callback: ContextVar[RepositoryRequestCallback | None] = ContextVar(
    "repository_request_callback", default=None
)


@contextmanager
def meter_repository_requests(callback: RepositoryRequestCallback) -> Iterator[None]:
    token = _request_callback.set(callback)
    try:
        yield
    finally:
        _request_callback.reset(token)


def record_repository_request(repository: str, operation: str) -> None:
    callback = _request_callback.get()
    if callback is not None:
        callback(repository, operation)
```

- [ ] **Step 4: Instrument network calls before dispatch**

Call `record_repository_request()` immediately before every Discovery-relevant HTTP call:

```python
record_repository_request("pride", "search_projects")
response = self._client.get("/search/projects", params=...)
```

Use operation names `search_projects`, `get_project`, `list_project_files`, `get_project_metadata`, and `get_file_metadata`. Instrument MassIVE `httpx.Client.get` calls with repository `massive`; instrument iProX metadata/index refresh `urlopen` calls with repository `iprox`. Do not meter file downloads because download tools are not exposed to the Discovery Agent.

- [ ] **Step 5: Enforce the hard request ceiling before network I/O**

Implement `BudgetGovernor.record_repository_request()` so the usage increment happens before the HTTP call:

```python
class RepositoryRequestBudgetExceeded(RuntimeError):
    pass


def record_repository_request(self, repository: str, operation: str) -> None:
    if self.elapsed_seconds() >= self._require_run().dynamic_limits.max_elapsed_seconds:
        self.stop_for_hard_limit("hard_elapsed_time_limit")
        raise RepositoryRequestBudgetExceeded("hard_elapsed_time_limit")
    try:
        usage = self.store.increment_dynamic_usage(
            self.run_id,
            repository_requests=1,
        )
    except ValueError as exc:
        if str(exc) != "hard_repository_request_limit":
            raise
        self.stop_for_hard_limit("hard_repository_request_limit")
        raise RepositoryRequestBudgetExceeded("hard_repository_request_limit") from exc
    self.store.append_event(
        self.run_id,
        "repository_request_started",
        {
            "repository": repository,
            "operation": operation,
            "repository_requests": usage.repository_requests,
        },
    )
```

When the next request exceeds `max_repository_requests`, the exception must be raised before network dispatch.

- [ ] **Step 6: Run repository and metering tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_repository_request_metering.py tests/test_pride_client.py tests/test_repositories.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit request metering**

```powershell
git add src/agent/repositories/metering.py src/agent/pride/client.py src/agent/repositories/massive_adapter.py src/agent/repositories/iprox_adapter.py src/agent/control_plane/budget_governor.py tests/test_repository_request_metering.py
git commit -m "feat: meter discovery repository requests"
```

---

### Task 6: Implement the Budget Agent Runtime

**Files:**
- Create: `src/agent/control_plane/budget_agent.py`
- Modify: `tests/test_dynamic_budget.py`

**Interfaces:**
- Consumes: `SearchProposalRecord`, `BudgetDecisionInput`, `BudgetDecision`, `RoundMetrics`, `BudgetGovernor`, SDK mapping, and model object.
- Produces: `submit_budget_decision`, `run_budget_agent_review(...) -> BudgetReviewResult`.

- [ ] **Step 1: Write a failing strict-tool and Fake Model test**

```python
from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText


class FakeBudgetDecisionModel(Model):
    def __init__(self, payloads: dict[str, Any] | list[dict[str, Any]]) -> None:
        self.payloads = payloads if isinstance(payloads, list) else [payloads]
        self.calls = 0

    async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
        self.calls += 1
        if self.calls <= len(self.payloads):
            output = [
                ResponseFunctionToolCall(
                    arguments=json.dumps(self.payloads[self.calls - 1]),
                    call_id=f"budget_call_{self.calls}",
                    name="submit_budget_decision",
                    type="function_call",
                    status="completed",
                )
            ]
        else:
            output = [
                ResponseOutputMessage(
                    id="budget_message",
                    content=[
                        ResponseOutputText(
                            annotations=[],
                            text="Budget decision submitted.",
                            type="output_text",
                        )
                    ],
                    role="assistant",
                    status="completed",
                    type="message",
                )
            ]
        return ModelResponse(output=output, usage=Usage(requests=1), response_id=None)

    async def stream_response(self, *args: Any, **kwargs: Any):
        if False:
            yield None


def _metrics() -> RoundMetrics:
    return RoundMetrics(
        candidate_shortfall=0.6,
        quality_gap=0.3,
        metadata_gap=0.7,
        diversity_gap=0.4,
        strategy_novelty=0.8,
        last_round_yield=0.5,
        query_repetition=0.2,
        budget_pressure=0.1,
    )


def test_budget_agent_submits_structured_grant_with_fake_model(tmp_path: Path) -> None:
    store, run = _dynamic_store_and_run(tmp_path)
    governor = BudgetGovernor(store, run.run_id)
    proposal = governor.register_proposal(_proposal(["human plasma SDRF"]))
    model = FakeBudgetDecisionModel(
        {
            "decision": {
                "proposal_id": proposal.proposal_id,
                "decision": "grant",
                "approved_query_indexes": [0],
                "reasoning_summary": "The query targets the measured metadata gap.",
            }
        }
    )
    result = asyncio.run(
        run_budget_agent_review(
            sdk=_load_agents_sdk(),
            model=model,
            proposal=proposal,
            metrics=_metrics(),
            governor=governor,
            max_turns=3,
        )
    )
    assert result.outcome == "granted"
    assert result.grant is not None
    assert model.calls == 2
```

- [ ] **Step 2: Run the Budget Agent test and confirm it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dynamic_budget.py::test_budget_agent_submits_structured_grant_with_fake_model -q
```

Expected: import failure for `budget_agent`.

- [ ] **Step 3: Implement the strict decision tool and bounded nested runner**

```python
from pydantic import ValidationError


@dataclass
class BudgetAgentContext:
    governor: BudgetGovernor
    result: BudgetReviewResult | None = None
    invalid_attempts: int = 0


def submit_budget_decision(
    wrapper: RunContextWrapper[BudgetAgentContext],
    decision: BudgetDecisionInput,
) -> str:
    try:
        wrapper.context.governor.authorize_tool("submit_budget_decision")
    except ValueError as exc:
        return json.dumps({"status": "denied", "reason": str(exc)})
    try:
        validated = BudgetDecision.model_validate(decision.model_dump(mode="json"))
    except ValidationError as exc:
        wrapper.context.invalid_attempts += 1
        wrapper.context.governor.store.append_event(
            wrapper.context.governor.run_id,
            "budget_decision_invalid",
            {"proposal_id": decision.proposal_id, "error": str(exc)},
        )
        return json.dumps(
            {
                "status": "invalid",
                "reason": "budget_decision_invalid",
                "attempt": wrapper.context.invalid_attempts,
            }
        )
    result = wrapper.context.governor.apply_decision(validated)
    wrapper.context.result = result
    return result.model_dump_json()


async def run_budget_agent_review(
    *,
    sdk: dict[str, Any],
    model: Any,
    proposal: SearchProposalRecord,
    metrics: RoundMetrics,
    governor: BudgetGovernor,
    max_turns: int,
) -> BudgetReviewResult:
    context = BudgetAgentContext(governor=governor)
    tool = sdk["function_tool"](submit_budget_decision)
    agent = sdk["Agent"][BudgetAgentContext](
        name="Discovery Budget Agent",
        instructions=_budget_instructions(),
        model=model,
        tools=[tool],
        model_settings=sdk["ModelSettings"](parallel_tool_calls=False),
    )
    await sdk["Runner"].run(
        starting_agent=agent,
        input=_budget_input(proposal, metrics),
        context=context,
        max_turns=max_turns,
        run_config=sdk["RunConfig"](
            workflow_name="proteomics_discovery_budget_review",
            tracing_disabled=True,
        ),
    )
    if context.result is None:
        reason = (
            "budget_decision_invalid"
            if context.invalid_attempts
            else "budget_agent_did_not_submit_decision"
        )
        raise ValueError(reason)
    return context.result
```

Implement the prompt helpers as structured JSON input plus fixed instructions:

```python
def _budget_instructions() -> str:
    return (
        "You are the Discovery Budget Agent. Review only the supplied SearchProposal and "
        "deterministic RoundMetrics. You may approve all proposal indexes, approve a true "
        "subset, request replanning, or stop. Never invent, rewrite, or execute queries. "
        "Never change species, acquisition mode, task type, PTM scope, or repository policy. "
        "Submit one final valid decision with a concise public reasoning_summary; if the tool "
        "returns budget_decision_invalid, correct it once. "
        "A stop decision must include unresolved_gaps, unexplored_strategies, and why_not_continue."
    )


def _budget_input(proposal: SearchProposalRecord, metrics: RoundMetrics) -> str:
    return json.dumps(
        {
            "proposal": proposal.model_dump(mode="json"),
            "metrics": metrics.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
```

- [ ] **Step 4: Add correction and invalid-stop tests**

```python
def test_budget_agent_corrects_invalid_stop_to_replan(tmp_path: Path) -> None:
    store, run = _dynamic_store_and_run(tmp_path)
    governor = BudgetGovernor(store, run.run_id)
    proposal = governor.register_proposal(_proposal(["human plasma SDRF"]))
    model = FakeBudgetDecisionModel(
        [
            {
                "decision": {
                    "proposal_id": proposal.proposal_id,
                    "decision": "stop",
                    "reasoning_summary": "Stop without counterfactual fields",
                }
            },
            {
                "decision": {
                    "proposal_id": proposal.proposal_id,
                    "decision": "replan",
                    "reasoning_summary": "Try a materially different metadata strategy.",
                }
            },
        ]
    )
    result = asyncio.run(
        run_budget_agent_review(
            sdk=_load_agents_sdk(),
            model=model,
            proposal=proposal,
            metrics=_metrics(),
            governor=governor,
            max_turns=3,
        )
    )
    assert result.outcome == "replan"
    assert result.grant is None
    assert model.calls == 3
```

The invalid tool payload must be returned to the model for one correction. If the second submission is also invalid, `run_budget_agent_review` raises `budget_decision_invalid`, no grant is issued, and any existing candidate-pool path remains unchanged.

- [ ] **Step 5: Run Budget Agent tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dynamic_budget.py -q
```

Expected: all dynamic-budget tests pass.

- [ ] **Step 6: Commit the Budget Agent**

```powershell
git add src/agent/control_plane/budget_agent.py tests/test_dynamic_budget.py
git commit -m "feat: add discovery budget agent"
```

---

### Task 7: Gate Discovery Search with One-Use Grants

**Files:**
- Modify: `src/agent/control_plane/discovery.py:22-424`
- Modify: `src/agent/control_plane/models.py`
- Modify: `tests/test_control_plane.py`

**Interfaces:**
- Consumes: `BudgetGovernor`, request metering, and `evaluate_round_metrics`.
- Produces: `DiscoveryToolService.search_repository_datasets(queries, grant_id=None)`, dynamic state summary, metrics events, and finalizing behavior.

- [ ] **Step 1: Write failing grant-gated service tests**

```python
def _dynamic_discovery_service(
    tmp_path: Path,
    *,
    with_valid_candidate: bool = False,
) -> tuple[DiscoveryToolService, BudgetGovernor, list[list[str]]]:
    request = DatasetRequest(repository="pride", max_projects=2, max_files=10)
    calls: list[list[str]] = []

    def fake_discovery(request: DatasetRequest, memory=None, queries=None) -> DatasetManifest:
        calls.append(list(queries or []))
        project = DiscoveredProject(project_accession="PXD_DYNAMIC", project_title="Dynamic fixture")
        file = DiscoveredFile(
            project_accession=project.project_accession,
            project_title=project.project_title,
            file_name="dynamic.raw",
            file_type=".raw",
            validity_status="valid",
            evidence_level="file",
        )
        return DatasetManifest(
            request=request,
            projects=[project],
            files=[file],
            summary={"selected_projects": 1, "selected_files": 1},
        )

    store = AgentRunStore(tmp_path / "state.sqlite")
    run = store.save_run(
        _run("dynamic_service").model_copy(
            update={
                "status": "running",
                "dynamic_budget_enabled": True,
                "dynamic_limits": DynamicBudgetLimits(),
            }
        )
    )
    governor = BudgetGovernor(store, run.run_id)
    service = DiscoveryToolService(
        run_id=run.run_id,
        request=request,
        output_dir=tmp_path / "output",
        store=store,
        discovery_func=fake_discovery,
        dynamic_budget=True,
        budget_governor=governor,
    )
    if with_valid_candidate:
        proposal = governor.register_proposal(
            SearchProposalInput(
                objective="Create a persisted candidate pool",
                reasoning_summary="The pool is empty.",
                queries=["seed query"],
                expected_gain="One valid candidate",
                stop_condition="A valid candidate is found",
            )
        )
        review = governor.apply_decision(
            BudgetDecision(
                proposal_id=proposal.proposal_id,
                decision="grant",
                approved_query_indexes=[0],
                reasoning_summary="The seed query is required.",
            )
        )
        service.search_repository_datasets(["seed query"], grant_id=review.grant.grant_id)
    return service, governor, calls


def test_dynamic_discovery_requires_and_consumes_matching_grant(tmp_path: Path) -> None:
    service, governor, calls = _dynamic_discovery_service(tmp_path)
    denied = service.search_repository_datasets(["human plasma SDRF"])
    assert denied.blockers == ["search_grant_required"]
    proposal = governor.register_proposal(_proposal(["human plasma SDRF"]))
    review = governor.apply_decision(_grant_decision(proposal.proposal_id, [0]))
    observation = service.search_repository_datasets(
        ["human plasma SDRF"],
        grant_id=review.grant.grant_id,
    )
    assert observation.status == "completed"
    assert calls == [["human plasma SDRF"]]
    replay = service.search_repository_datasets(
        ["human plasma SDRF"],
        grant_id=review.grant.grant_id,
    )
    assert replay.blockers == ["grant_already_consumed"]
```

- [ ] **Step 2: Run the new service test and confirm it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_control_plane.py::test_dynamic_discovery_requires_and_consumes_matching_grant -q
```

Expected: FAIL because `grant_id` is not accepted.

- [ ] **Step 3: Add dynamic mode without breaking legacy mode**

Extend the service constructor:

```python
dynamic_budget: bool = False,
budget_governor: BudgetGovernor | None = None,
```

At search entry:

```python
if self.dynamic_budget:
    if self.budget_governor is None:
        raise RuntimeError("dynamic_budget_governor_required")
    if not grant_id:
        return self._blocked_observation(queries, "search_grant_required")
    try:
        consumed = self.budget_governor.consume_grant(grant_id, queries)
    except ValueError as exc:
        return self._blocked_observation(queries, str(exc))
else:
    consumed = None
    if run.discovery_round_count >= run.budget.max_discovery_rounds:
        return self._blocked_observation(queries, "discovery_round_budget_exhausted")
```

Add this service helper so all denied searches have the same schema:

```python
def _blocked_observation(self, queries: list[str], reason: str) -> DiscoveryRoundObservation:
    run = self._require_run()
    self.store.append_event(
        self.run_id,
        "tool_denied",
        {"tool": "search_repository_datasets", "reason": reason, "queries": queries},
    )
    return DiscoveryRoundObservation(
        status="blocked",
        round_index=run.discovery_round_count + 1,
        queries=queries,
        recommended_action="stop" if run.search_stopped else "revise_queries_or_request_budget",
        blockers=[reason],
    )
```

Only the legacy path checks `max_discovery_rounds`. Dynamic mode increments `query_units` by the consumed grant size and `search_batches` by one.

- [ ] **Step 4: Wrap repository discovery in actual request metering and compute metrics**

Before calling `discovery_func`, load the previous candidate pool. Dynamic mode executes within:

```python
with meter_repository_requests(self.budget_governor.record_repository_request):
    manifest = self.discovery_func(self.request, memory=self.memory, queries=queries)
```

Single-Agent mode uses the same meter without enforcement so evaluation has a comparable baseline:

```python
def record_legacy_request(repository: str, operation: str) -> None:
    self.store.increment_dynamic_usage(
        self.run_id,
        repository_requests=1,
        enforce_limits=False,
    )

with meter_repository_requests(record_legacy_request):
    manifest = self.discovery_func(self.request, memory=self.memory, queries=queries)
```

For both modes, increment `query_units` by `len(queries)` and `search_batches` by one. Only multi-Agent mode enforces dynamic ceilings.

After the pool is persisted, call `evaluate_round_metrics`, store `latest_metrics`, append `round_value_evaluated`, and include metrics in `DiscoveryRoundObservation` and `get_discovery_state()`.

Expose the interfaces consumed by the Manager tool:

```python
@property
def dynamic_limits(self) -> DynamicBudgetLimits:
    return self._require_run().dynamic_limits


def current_metrics(self) -> RoundMetrics:
    run = self._require_run()
    if run.latest_metrics is not None:
        return run.latest_metrics
    empty = DatasetManifest(
        run_id=self.run_id,
        request=self.request,
        summary={"selected_projects": 0, "selected_files": 0},
    )
    return evaluate_round_metrics(
        empty,
        None,
        request=self.request,
        queries=[],
        prior_queries=[],
        usage=run.dynamic_usage,
        limits=run.dynamic_limits,
        round_index=0,
    )
```

- [ ] **Step 5: Add finalizing tests**

```python
def test_dynamic_stop_blocks_search_but_allows_final_manifest_selection(tmp_path: Path) -> None:
    service, governor, _ = _dynamic_discovery_service(tmp_path, with_valid_candidate=True)
    run = service.store.load_run(service.run_id)
    service.store.save_run(
        run.model_copy(
            update={"search_stopped": True, "search_stop_reason": "budget_agent_stop"}
        )
    )
    blocked = service.search_repository_datasets(["another query"], grant_id="grant_unused")
    assert blocked.blockers == ["dynamic_search_stopped"]
    selected = service.select_discovery_manifest(
        0,
        "The persisted candidate pool is the strongest available manifest.",
    )
    assert selected["status"] == "completed"
    assert selected["round_index"] == 0
```

Keep the existing non-empty-pool-after-empty-follow-up test unchanged and green.

- [ ] **Step 6: Run focused Control Plane tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_control_plane.py tests/test_dynamic_budget.py -q
```

Expected: all focused tests pass, including legacy round-budget tests.

- [ ] **Step 7: Commit grant-gated search**

```powershell
git add src/agent/control_plane/discovery.py src/agent/control_plane/models.py tests/test_control_plane.py
git commit -m "feat: require grants for dynamic discovery"
```

---

### Task 8: Wire the Discovery Manager and Budget Agent Together

**Files:**
- Modify: `src/agent/control_plane/openai_agents.py:1-493`
- Modify: `src/agent/control_plane/__init__.py`
- Modify: `tests/test_control_plane.py`

**Interfaces:**
- Consumes: Tasks 4-7.
- Produces: `request_search_budget`, `search_repository_datasets_with_grant`, multi-Agent `run_openai_agents_discovery(..., mode, dynamic_limits, budget_model)`.

- [ ] **Step 1: Write a failing full multi-Agent Fake Model loop**

Add this scripted model and run helper to `tests/test_control_plane.py`:

```python
class FakeScriptedToolModel(Model):
    def __init__(self, actions: list[tuple[str, dict[str, Any] | str]]) -> None:
        self.actions = actions
        self.calls = 0

    async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
        action, payload = self.actions[self.calls]
        self.calls += 1
        if action == "final":
            output = [
                ResponseOutputMessage(
                    id=f"message_{self.calls}",
                    content=[
                        ResponseOutputText(
                            annotations=[],
                            text=str(payload),
                            type="output_text",
                        )
                    ],
                    role="assistant",
                    status="completed",
                    type="message",
                )
            ]
        else:
            output = [
                ResponseFunctionToolCall(
                    arguments=json.dumps(payload),
                    call_id=f"call_{self.calls}",
                    name=action,
                    type="function_call",
                    status="completed",
                )
            ]
        return ModelResponse(output=output, usage=Usage(requests=1), response_id=None)

    async def stream_response(self, *args: Any, **kwargs: Any):
        if False:
            yield None


def _run_multi_agent_fake_loop(tmp_path: Path) -> tuple[OpenAIAgentsDiscoveryResult, AgentRunStore]:
    proposal = {
        "objective": "Improve metadata coverage",
        "reasoning_summary": "The current pool lacks sample-level metadata.",
        "evidence_refs": ["metadata_gap:0.7"],
        "queries": ["human plasma SDRF"],
        "expected_gain_dimensions": ["metadata_completeness"],
        "expected_gain": "More sample metadata",
        "alternatives_considered": ["generic broad search"],
        "stop_condition": "No new usable files",
    }
    discovery_model = FakeScriptedToolModel(
        [
            ("request_search_budget", {"proposal": proposal}),
            (
                "search_repository_datasets_with_grant",
                {
                    "grant_id": "grant_11111111111111111111111111111111",
                    "queries": ["human plasma SDRF"],
                },
            ),
            (
                "select_discovery_manifest",
                {"round_index": 0, "rationale": "The pool contains valid candidates."},
            ),
            ("final", "Selected the merged candidate pool."),
        ]
    )
    budget_model = FakeScriptedToolModel(
        [
            (
                "submit_budget_decision",
                {
                    "decision": {
                        "proposal_id": "proposal_11111111111111111111111111111111",
                        "decision": "grant",
                        "approved_query_indexes": [0],
                        "reasoning_summary": "The query targets the measured metadata gap.",
                    }
                },
            ),
            ("final", "Budget decision submitted."),
        ]
    )
    request = DatasetRequest(repository="pride", max_projects=2, max_files=10)

    def fake_discovery(request: DatasetRequest, memory=None, queries=None) -> DatasetManifest:
        project = DiscoveredProject(project_accession="PXD_MULTI", project_title="Multi-agent fixture")
        file = DiscoveredFile(
            project_accession=project.project_accession,
            project_title=project.project_title,
            file_name="multi.raw",
            file_type=".raw",
            validity_status="valid",
            evidence_level="file",
        )
        return DatasetManifest(
            request=request,
            projects=[project],
            files=[file],
            summary={"selected_projects": 1, "selected_files": 1},
        )

    state_db = tmp_path / "agent_control.sqlite"
    result = run_openai_agents_discovery(
        prompt="Find human plasma DDA data",
        request=request,
        output_dir=tmp_path / "output",
        state_db=state_db,
        run_id="multi_agent_run",
        mode="multi_agent",
        dynamic_limits=DynamicBudgetLimits(),
        budget=AgentBudget(max_turns=12, max_tool_calls=20),
        discovery_func=fake_discovery,
        model=discovery_model,
        budget_model=budget_model,
        stream_events=False,
    )
    return result, AgentRunStore(state_db)
```

The Discovery Fake Model must emit this sequence:

```python
[
    (
        "request_search_budget",
        {
            "proposal": {
                "objective": "Improve metadata coverage",
                "reasoning_summary": "The current pool lacks sample-level metadata.",
                "evidence_refs": ["metadata_gap:0.7"],
                "queries": ["human plasma SDRF"],
                "expected_gain_dimensions": ["metadata_completeness"],
                "expected_gain": "More sample metadata",
                "alternatives_considered": ["generic broad search"],
                "stop_condition": "No new usable files"
            }
        },
    ),
    (
        "search_repository_datasets_with_grant",
        {
            "grant_id": "grant_11111111111111111111111111111111",
            "queries": ["human plasma SDRF"]
        },
    ),
    ("select_discovery_manifest", {"round_index": 0, "rationale": "The pool contains valid candidates."}),
    ("final", "Selected the merged candidate pool."),
]
```

Monkeypatch `agent.control_plane.budget_governor.uuid.uuid4` to return an object whose `hex` is `"1" * 32`, so the generated grant ID matches the test sequence. The Budget Fake Model must call `submit_budget_decision` with proposal ID `proposal_11111111111111111111111111111111`, `grant`, and index `[0]`. Assert one budget review, one consumed grant, one search batch, selected pool, and a completed result.

```python
def test_openai_agents_runner_executes_multi_agent_budget_loop(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "agent.control_plane.budget_governor.uuid.uuid4",
        lambda: SimpleNamespace(hex="1" * 32),
    )
    result, store = _run_multi_agent_fake_loop(tmp_path)
    assert result.status == "completed"
    assert result.selected_round_index == 0
    run = store.load_run(result.run_id)
    assert run is not None
    assert run.dynamic_usage.budget_reviews == 1
    assert run.dynamic_usage.search_batches == 1
    assert run.dynamic_usage.query_units == 1
    assert store.list_search_grants(run.run_id)[0].status == "consumed"
    event_types = [event.event_type for event in store.list_events(run.run_id)]
    assert "budget_decision_recorded" in event_types
    assert "search_grant_consumed" in event_types
    assert "manifest_selected" in event_types
```

- [ ] **Step 2: Run the multi-Agent loop test and confirm it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_control_plane.py::test_openai_agents_runner_executes_multi_agent_budget_loop -q
```

Expected: FAIL because the manager tools and runtime arguments do not exist.

- [ ] **Step 3: Extend context and add manager tools**

```python
@dataclass
class DiscoveryAgentContext:
    service: DiscoveryToolService
    sdk: dict[str, Any]
    budget_model: Any
    budget_governor: BudgetGovernor | None = None


async def request_search_budget(
    wrapper: RunContextWrapper[DiscoveryAgentContext],
    proposal: SearchProposalInput,
) -> str:
    if wrapper.context.budget_governor is None:
        return json.dumps({"outcome": "denied", "reason": "dynamic_budget_disabled"})
    record = wrapper.context.budget_governor.register_proposal(proposal)
    metrics = wrapper.context.service.current_metrics()
    result = await run_budget_agent_review(
        sdk=wrapper.context.sdk,
        model=wrapper.context.budget_model,
        proposal=record,
        metrics=metrics,
        governor=wrapper.context.budget_governor,
        max_turns=wrapper.context.service.dynamic_limits.budget_agent_max_turns,
    )
    return result.model_dump_json()


def search_repository_datasets_with_grant(
    wrapper: RunContextWrapper[DiscoveryAgentContext],
    grant_id: str,
    queries: list[str],
) -> str:
    return wrapper.context.service.search_repository_datasets(
        queries, grant_id=grant_id
    ).model_dump_json()
```

- [ ] **Step 4: Make mode selection explicit and preserve single-Agent behavior**

Add keyword arguments:

```python
mode: Literal["single_agent", "multi_agent"] = "single_agent",
dynamic_limits: DynamicBudgetLimits | None = None,
budget_model: Any | None = None,
event_callback: Callable[[AgentEvent], None] | None = None,
stream_events: bool = False,
```

Single mode uses the existing search tool and instructions unchanged. Multi mode creates `BudgetGovernor`, enables dynamic service mode, exposes `request_search_budget` plus granted search, and removes `max_discovery_rounds` from the prompt. Use `budget_model or model` so both Agents share the same configured provider by default. Task 8 keeps the existing synchronous Runner path when `stream_events=False`; Task 9 implements the streamed branch used by Web and CLI production runs.

- [ ] **Step 5: Add manager instructions that enforce the protocol**

The multi-Agent prompt must require this order:

```text
Inspect current state -> submit SearchProposal -> obey BudgetDecision ->
use the exact grant queries -> inspect RoundMetrics -> repeat or select manifest.
```

It must state that direct ungranted search is invalid, `stop` means finalization rather than failure, and `select_discovery_manifest` remains mandatory when candidates exist.

- [ ] **Step 6: Run single- and multi-Agent loop tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_control_plane.py -q
```

Expected: all tests pass; the existing single-Agent Fake Model loop remains green.

- [ ] **Step 7: Commit multi-Agent orchestration**

```powershell
git add src/agent/control_plane/openai_agents.py src/agent/control_plane/__init__.py tests/test_control_plane.py
git commit -m "feat: orchestrate discovery and budget agents"
```

---

### Task 9: Stream Structured Events and Write Dynamic-Budget Artifacts

**Files:**
- Modify: `src/agent/control_plane/store.py`
- Modify: `src/agent/control_plane/openai_agents.py:408-493`
- Modify: `tests/test_control_plane.py`

**Interfaces:**
- Consumes: current `AgentEvent` sequence and SDK streamed run events.
- Produces: optional store event listener, streamed runner consumer, `agents_discovery_budget.json`, schema v2 summary/report sections.

- [ ] **Step 1: Write failing event-listener and artifact tests**

```python
def test_agent_store_listener_receives_committed_events(tmp_path: Path) -> None:
    received: list[AgentEvent] = []
    store = AgentRunStore(tmp_path / "state.sqlite", event_listener=received.append)
    store.save_run(_run("listener_1"))
    event = store.append_event("listener_1", "search_plan_proposed", {"reasoning_summary": "Plan"})
    assert received == [event]


def test_streamed_runner_persists_public_lifecycle_not_raw_deltas(tmp_path: Path) -> None:
    class AgentUpdatedStreamEvent:
        new_agent = SimpleNamespace(name="Proteomics Discovery Agent")

    class RawResponsesStreamEvent:
        data = {"reasoning": "must not persist"}

    class FakeStreamedResult:
        async def stream_events(self):
            yield AgentUpdatedStreamEvent()
            yield RawResponsesStreamEvent()

    class FakeRunner:
        @staticmethod
        def run_streamed(**kwargs: Any) -> FakeStreamedResult:
            return FakeStreamedResult()

    store = AgentRunStore(tmp_path / "state.sqlite")
    store.save_run(_run("stream_1"))
    context = SimpleNamespace(service=SimpleNamespace(run_id="stream_1"))
    asyncio.run(
        _run_streamed_to_completion(
            sdk={"Runner": FakeRunner},
            store=store,
            starting_agent=object(),
            input="test",
            context=context,
        )
    )
    events = store.list_events("stream_1")
    assert [event.event_type for event in events] == ["sdk_agent_updated"]
    assert "reasoning" not in json.dumps(events[0].model_dump(mode="json"))


def test_multi_agent_outputs_include_budget_audit(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "agent.control_plane.budget_governor.uuid.uuid4",
        lambda: SimpleNamespace(hex="1" * 32),
    )
    result, _ = _run_multi_agent_fake_loop(tmp_path)
    budget_path = Path(result.files["agents_discovery_budget_json"])
    payload = json.loads(budget_path.read_text(encoding="utf-8"))
    assert payload["mode"] == "multi_agent_dynamic"
    assert payload["approved_queries"] == 1
    assert payload["search_batches"] == 1
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_control_plane.py -k "listener or budget_audit" -q
```

Expected: failures for constructor argument and missing output file.

- [ ] **Step 3: Add post-commit event delivery**

`AgentRunStore.__init__` accepts `event_listener: Callable[[AgentEvent], None] | None = None`. `append_event()` calls the listener only after the SQLite insert commits:

```python
def __init__(
    self,
    path: str | Path,
    event_listener: Callable[[AgentEvent], None] | None = None,
) -> None:
    self.path = Path(path)
    self.event_listener = event_listener
    self.path.parent.mkdir(parents=True, exist_ok=True)
    self._initialize()


def _notify_event(self, event: AgentEvent) -> None:
    if self.event_listener is None:
        return
    try:
        self.event_listener(event)
    except Exception:
        return
```

Construct the `AgentEvent` after the transaction closes, call `_notify_event(event)`, then return it. Listener exceptions do not roll back persisted state.

- [ ] **Step 4: Consume `Runner.run_streamed()` without changing the sync public API**

Implement an internal async helper:

```python
async def _run_streamed_to_completion(*, sdk: dict[str, Any], store: AgentRunStore, **kwargs: Any) -> Any:
    streamed = sdk["Runner"].run_streamed(**kwargs)
    async for event in streamed.stream_events():
        payload = _public_sdk_event(event)
        if payload is not None:
            store.append_event(kwargs["context"].service.run_id, payload["event_type"], payload["payload"])
    return streamed
```

Map only public SDK lifecycle data; discard raw response-delta events:

```python
def _public_sdk_event(event: Any) -> dict[str, Any] | None:
    event_name = type(event).__name__
    if event_name == "AgentUpdatedStreamEvent":
        agent = getattr(event, "new_agent", None)
        return {
            "event_type": "sdk_agent_updated",
            "payload": {"agent": str(getattr(agent, "name", "") or "")},
        }
    if event_name == "RunItemStreamEvent":
        item = getattr(event, "item", None)
        return {
            "event_type": "sdk_run_item",
            "payload": {
                "item_type": type(item).__name__,
                "name": str(getattr(event, "name", "") or ""),
            },
        }
    return None
```

Do not persist `RawResponsesStreamEvent` payloads because they may contain provider-specific reasoning or sensitive raw model content.

When `stream_events=True`, call the helper from the existing synchronous entry point with `asyncio.run()`. When `stream_events=False`, retain `Runner.run_sync()` for injected Fake Model tests. The Web worker and CLI are synchronous call sites, and `/api/discovery` already moves work to a worker thread. Web and CLI production calls must pass `stream_events=True`.

- [ ] **Step 5: Write budget and report artifacts**

Add `agents_discovery_budget.json` and include:

```json
{
  "mode": "multi_agent_dynamic",
  "proposed_queries": 0,
  "approved_queries": 0,
  "rejected_queries": 0,
  "query_units": 0,
  "repository_requests": 0,
  "search_batches": 0,
  "budget_reviews": 0,
  "stop_decision": "",
  "hard_limits_reached": false
}
```

Populate values from persisted proposals, decisions, grants, events, and `run.dynamic_usage`. Add `agents.discovery_manager`, `agents.budget_agent`, latest metrics, and dynamic limits to summary schema `openai-agents-discovery/v2`. Add separate Plan, Budget Decisions, Resource Use, and Final Selection sections to the Markdown report.

- [ ] **Step 6: Run Control Plane tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_control_plane.py tests/test_dynamic_budget.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit event streaming and artifacts**

```powershell
git add src/agent/control_plane/store.py src/agent/control_plane/openai_agents.py tests/test_control_plane.py
git commit -m "feat: stream discovery agent audit events"
```

---

### Task 10: Replace Web Presets with Autonomous Budget and Rich Logs

**Files:**
- Modify: `src/agent/web/app.py:1121-1180,2162-2365,5478-5609`
- Modify: `src/agent/web/templates/index.html:760-820,1260-1345,1750-1930,2930-3210,3320-3695`
- Modify: `tests/test_web_discovery.py:698-873`
- Modify: `tests/test_frontend_template.py:140-190`

**Interfaces:**
- Consumes: multi-Agent runtime, event callback, dynamic outputs.
- Produces: server-configured hard limits, autonomous-budget Web payload, structured job events, three log views, append-only rendering.

- [ ] **Step 1: Replace static-preset expectations with failing autonomous-budget tests**

Update Web tests to assert:

```python
def test_web_agent_uses_server_dynamic_limits_not_request_presets(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DISCOVERY_MODE", "multi_agent")
    monkeypatch.setenv("AGENT_MAX_MODEL_TURNS", "50")
    monkeypatch.setenv("AGENT_MAX_TOOL_CALLS", "100")
    monkeypatch.setenv("AGENT_MAX_QUERY_UNITS", "24")
    monkeypatch.setenv("AGENT_MAX_REPOSITORY_REQUESTS", "120")
    mode, budget, limits = web_app._agent_discovery_configuration(
        {"agent_budget_mode": "deep", "agent_max_rounds": 8}
    )
    assert mode == "multi_agent"
    assert budget.max_turns == 50
    assert budget.max_tool_calls == 100
    assert limits.max_query_units == 24
    assert limits.max_repository_requests == 120
```

Update template tests to assert static IDs and payload fields are absent, while `discoveryAgentBudgetAutonomous`, `discoveryLogActivity`, `discoveryLogTools`, and `discoveryLogRaw` are present.

- [ ] **Step 2: Run Web tests and confirm they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web_discovery.py tests/test_frontend_template.py -q
```

Expected: failures because presets and inputs are still present.

- [ ] **Step 3: Replace `_AGENT_BUDGET_PRESETS` with server configuration**

Implement:

```python
def _agent_discovery_configuration(
    body: dict[str, Any],
) -> tuple[str, AgentBudget, DynamicBudgetLimits]:
    mode = _clean_text(os.getenv("AGENT_DISCOVERY_MODE") or "single_agent").lower()
    if mode not in {"single_agent", "multi_agent"}:
        mode = "single_agent"
    budget = AgentBudget(
        max_turns=_bounded_int(os.getenv("AGENT_MAX_MODEL_TURNS"), default=50, minimum=1, maximum=50),
        max_tool_calls=_bounded_int(os.getenv("AGENT_MAX_TOOL_CALLS"), default=100, minimum=1, maximum=100),
        max_discovery_rounds=3,
    )
    limits = DynamicBudgetLimits(
        max_query_units=_bounded_int(os.getenv("AGENT_MAX_QUERY_UNITS"), default=30, minimum=1, maximum=500),
        max_repository_requests=_bounded_int(os.getenv("AGENT_MAX_REPOSITORY_REQUESTS"), default=200, minimum=1, maximum=5000),
        max_elapsed_seconds=_bounded_int(os.getenv("AGENT_MAX_ELAPSED_SECONDS"), default=1200, minimum=30, maximum=86400),
        budget_agent_max_turns=_bounded_int(os.getenv("AGENT_BUDGET_AGENT_MAX_TURNS"), default=3, minimum=2, maximum=10),
    )
    return mode, budget, limits
```

Ignore legacy budget fields in multi-Agent request bodies. Pass server-derived `budget`, `mode`, `dynamic_limits`, `event_callback`, and `stream_events=True` to `run_openai_agents_discovery`. In multi-Agent mode, `AgentBudget.max_turns` and `max_tool_calls` are safety ceilings and `max_discovery_rounds` is not consulted. In single-Agent mode, keep the existing legacy budget behavior.

- [ ] **Step 4: Persist structured job entries**

Add `_append_discovery_job_event(job_id, event)` that sanitizes and stores:

```python
{
    "sequence": event.sequence,
    "ts": event.created_at,
    "level": _event_level(event.event_type),
    "actor": _event_actor(event.event_type),
    "type": event.event_type,
    "message": _event_message(event),
    "reasoning_summary": event.payload.get("reasoning_summary", ""),
    "evidence_refs": event.payload.get("evidence_refs", []),
    "metrics": event.payload.get("metrics", {}),
    "payload": _sanitize_log_payload(event.payload),
}
```

Use deterministic actor, level, message, and recursive payload helpers:

```python
def _event_actor(event_type: str) -> str:
    if event_type.startswith("budget_"):
        return "Budget Agent"
    if "grant" in event_type or event_type == "dynamic_search_stopped":
        return "BudgetGovernor"
    if event_type.startswith("sdk_"):
        return "OpenAI Agents SDK"
    if event_type.startswith("tool_") or event_type == "repository_request_started":
        return "Repository tool"
    return "Discovery Agent"


def _event_level(event_type: str) -> str:
    if "invalid" in event_type or "rejected" in event_type or "failed" in event_type:
        return "warning"
    return "info"


def _event_message(event: AgentEvent) -> str:
    payload = event.payload
    return _redact_secrets(
        str(
            payload.get("reasoning_summary")
            or payload.get("reason")
            or payload.get("message")
            or event.event_type.replace("_", " ")
        )
    )


def _sanitize_log_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize_log_payload(item)
            for key, item in value.items()
            if str(key).casefold() not in {"api_key", "authorization", "sdk_state_json"}
        }
    if isinstance(value, list):
        return [_sanitize_log_payload(item) for item in value]
    if isinstance(value, str):
        return _redact_secrets(value)
    return _json_safe(value)
```

Keep `_append_discovery_job_log()` as a wrapper for non-Control-Plane messages. Ensure `_redact_secrets` runs recursively over message and payload.

- [ ] **Step 5: Replace the budget controls in HTML**

Use a compact autonomous status block:

```html
<div class="agent-autonomy-status" id="discoveryAgentBudgetAutonomous">
  <span class="status-pill completed" data-i18n="discoveryAgentAutonomous">Agent autonomous budget</span>
  <span id="discoveryAgentResourceUse"></span>
</div>
```

Remove `discoveryAgentBudgetMode`, `discoveryAgentBudgetAdvanced`, `discoveryAgentMaxRounds`, `discoveryAgentMaxTurns`, `discoveryAgentMaxToolCalls`, related translations, toggles, saved-form fields, and request payload fields.

- [ ] **Step 6: Add Activity, Tools and metrics, and Raw event tabs**

Add tab buttons and three stable log panels. Render sanitized events by category:

```javascript
function discoveryLogCategory(item){
  const type=String(item&&item.type||'');
  if(type.startsWith('sdk_')||type.startsWith('tool_')||type.includes('grant')||type==='round_value_evaluated')return 'tools';
  return 'activity';
}

function appendDiscoveryLogRows(box,items,lastSequence){
  const fresh=items.filter(item=>Number(item.sequence||0)>lastSequence);
  fresh.forEach(item=>box.insertAdjacentHTML('beforeend',UI.discoveryAgentEvent(item)));
  return fresh.reduce((value,item)=>Math.max(value,Number(item.sequence||0)),lastSequence);
}
```

Maintain separate last-sequence counters per tab. Reset only when job ID changes or the server returns a sequence lower than the stored value. Preserve follow-scroll only when the user is within 16px of the bottom.

- [ ] **Step 7: Add budget download and result counters**

Expose `agents_discovery_budget_json` through `_DISCOVERY_DOWNLOAD_FILES`:

```python
"agents_discovery_budget_json": ("agents_discovery_budget.json", "application/json"),
```

Render result metadata from the structured summary:

```javascript
meta.innerHTML=[
  '<span>queries '+esc(String(agent.query_units||0))+'</span>',
  '<span>repository requests '+esc(String(agent.repository_requests||0))+'</span>',
  '<span>batches '+esc(String(agent.search_batches||0))+'</span>',
  '<span>pool files '+esc(String(agent.pooled_selected_files||0))+'</span>',
  '<span>selected '+esc(selectedRound)+'</span>',
  agent.stop_reason?'<span>stop '+esc(String(agent.stop_reason))+'</span>':''
].filter(Boolean).join('');
```

Do not render `fast/standard/deep` labels.

- [ ] **Step 8: Run Web tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web_discovery.py tests/test_frontend_template.py -q
```

Expected: all selected tests pass.

- [ ] **Step 9: Commit Web integration**

```powershell
git add src/agent/web/app.py src/agent/web/templates/index.html tests/test_web_discovery.py tests/test_frontend_template.py
git commit -m "feat: show autonomous discovery agent activity"
```

---

### Task 11: Add Rollout Configuration, CLI Compatibility, and Documentation

**Files:**
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `src/agent/cli.py:657-720`
- Modify: `tests/test_cli_entrypoint.py`
- Modify: `tests/test_deploy_script.py`
- Modify: `README.md`
- Modify: `docs/openai-agents-control-plane.md`
- Modify: `docs/PROJECT_HANDOFF_CN.md`

**Interfaces:**
- Consumes: multi-Agent runtime and hard-limit models.
- Produces: deployment defaults, CLI mode switch, updated operator documentation.

- [ ] **Step 1: Write failing CLI and deployment tests**

```python
def test_agents_discover_cli_passes_dynamic_limits(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run(**kwargs: Any) -> OpenAIAgentsDiscoveryResult:
        captured.update(kwargs)
        return OpenAIAgentsDiscoveryResult(
            status="completed",
            run_id="cli_multi_agent",
            output_dir=str(tmp_path / "out"),
            state_db=str(tmp_path / "state.sqlite"),
        )

    monkeypatch.setattr("agent.cli.run_openai_agents_discovery", fake_run)
    result = CliRunner().invoke(
        app,
        [
            "agents-discover-dataset",
            "--prompt", "Find human plasma DDA data",
            "--output-dir", str(tmp_path / "out"),
            "--discovery-mode", "multi_agent",
            "--max-query-units", "24",
            "--max-repository-requests", "120",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["mode"] == "multi_agent"
    assert captured["dynamic_limits"].max_query_units == 24
    assert captured["dynamic_limits"].max_repository_requests == 120


def test_deploy_files_expose_dynamic_discovery_limits() -> None:
    env_text = Path(".env.example").read_text(encoding="utf-8")
    compose_text = Path("docker-compose.yml").read_text(encoding="utf-8")
    for name in (
        "AGENT_DISCOVERY_MODE",
        "AGENT_MAX_MODEL_TURNS",
        "AGENT_MAX_TOOL_CALLS",
        "AGENT_MAX_QUERY_UNITS",
        "AGENT_MAX_REPOSITORY_REQUESTS",
        "AGENT_MAX_ELAPSED_SECONDS",
        "AGENT_BUDGET_AGENT_MAX_TURNS",
    ):
        assert name in env_text
        assert name in compose_text
```

- [ ] **Step 2: Run CLI/deployment tests and confirm they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cli_entrypoint.py tests/test_deploy_script.py -q
```

Expected: failures because the mode and dynamic hard-limit options do not exist.

- [ ] **Step 3: Add configuration values**

```text
AGENT_DISCOVERY_MODE=single_agent
AGENT_MAX_MODEL_TURNS=50
AGENT_MAX_TOOL_CALLS=100
AGENT_MAX_QUERY_UNITS=30
AGENT_MAX_REPOSITORY_REQUESTS=200
AGENT_MAX_ELAPSED_SECONDS=1200
AGENT_BUDGET_AGENT_MAX_TURNS=3
```

Pass them through `docker-compose.yml`. Keep tracing disabled by default.

- [ ] **Step 4: Update CLI options without breaking legacy scripts**

Add:

```python
discovery_mode: str = typer.Option("single_agent", "--discovery-mode"),
max_query_units: int = typer.Option(30, "--max-query-units", min=1, max=500),
max_repository_requests: int = typer.Option(200, "--max-repository-requests", min=1, max=5000),
max_elapsed_seconds: int = typer.Option(1200, "--max-elapsed-seconds", min=30, max=86400),
budget_agent_max_turns: int = typer.Option(3, "--budget-agent-max-turns", min=2, max=10),
```

Retain `--max-rounds`, `--max-turns`, and `--max-tool-calls` for `single_agent`; document that `--max-rounds` is ignored in `multi_agent`. Pass both `AgentBudget` and `DynamicBudgetLimits`, with the runtime selecting the relevant one, and pass `stream_events=True` for real CLI runs.

- [ ] **Step 5: Update user and handoff documentation**

Document the two-Agent responsibilities, grant protocol, autonomous Web behavior, event tabs, server limits, opt-in rollout, output files, DeepSeek-compatible shared model configuration, and the fact that visible reasoning is a public evidence summary rather than raw hidden chain-of-thought. Remove the stale claim that the SDK path has only two read-only tools.

- [ ] **Step 6: Run CLI/deploy tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cli_entrypoint.py tests/test_deploy_script.py tests/test_frontend_template.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit configuration and docs**

```powershell
git add .env.example docker-compose.yml src/agent/cli.py tests/test_cli_entrypoint.py tests/test_deploy_script.py README.md docs/openai-agents-control-plane.md docs/PROJECT_HANDOFF_CN.md
git commit -m "docs: document multi-agent discovery rollout"
```

---

### Task 12: Add Fixed Replays, Evaluation Gate, and End-to-End Verification

**Files:**
- Create: `tests/fixtures/dynamic_budget_replays.json`
- Create: `scripts/evaluate_dynamic_discovery_budget.py`
- Create: `tests/test_discovery_budget_evaluation.py`
- Modify: `docs/openai-agents-control-plane.md`

**Interfaces:**
- Consumes: exported single-Agent baseline summaries and multi-Agent summaries.
- Produces: `dynamic_budget_evaluation.json` and a release-gate pass/fail exit code.

- [ ] **Step 1: Create eight explicit replay definitions**

The JSON fixture must contain these IDs and expected properties:

```json
[
  {"id":"empty_then_success","expected_usable_min":1,"must_preserve_pool":true},
  {"id":"success_then_empty","expected_usable_min":1,"must_preserve_pool":true},
  {"id":"small_incremental_gains","expected_usable_min":3,"must_stop_before_hard_limit":true},
  {"id":"quantity_good_quality_poor","expected_review_or_valid_min":1,"must_target_quality_gap":true},
  {"id":"review_only","expected_status":"completed_with_review"},
  {"id":"repeated_queries","max_duplicate_query_rate":0.05},
  {"id":"persistent_no_results","expected_status":"blocked"},
  {"id":"cross_repository_success","expected_usable_min":1,"required_repository_count":2}
]
```

- [ ] **Step 2: Write failing evaluation math tests**

```python
from scripts.evaluate_dynamic_discovery_budget import EvaluationRun, evaluate_runs


def test_release_gate_requires_quality_and_cost_targets() -> None:
    report = evaluate_runs(
        baseline=[EvaluationRun(usable=20, search_requests=100)],
        dynamic=[EvaluationRun(usable=19, search_requests=75)],
    )
    assert report.usable_recall == pytest.approx(0.95)
    assert report.tool_reduction == pytest.approx(0.25)
    assert report.release_gate_passed is True


def test_release_gate_fails_false_early_stops() -> None:
    report = evaluate_runs(
        baseline=[EvaluationRun(usable=10, search_requests=20) for _ in range(20)],
        dynamic=[EvaluationRun(usable=0, search_requests=2, false_early_stop=True)]
        + [EvaluationRun(usable=10, search_requests=15) for _ in range(19)],
    )
    assert report.false_early_stop_rate == pytest.approx(0.05)
    assert report.release_gate_passed is False
```

- [ ] **Step 3: Run evaluation tests and confirm they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_discovery_budget_evaluation.py -q
```

Expected: import failure for the evaluation script functions.

- [ ] **Step 4: Implement deterministic evaluation math and CLI**

The report must calculate:

```python
class EvaluationRun(JsonModel):
    usable: int = Field(ge=0)
    search_requests: int = Field(ge=0)
    false_early_stop: bool = False
    quality_regression: bool = False
    hard_constraint_violations: int = Field(default=0, ge=0)


usable_recall = dynamic_usable / max(1, baseline_usable)
tool_reduction = 1.0 - dynamic_search_requests / max(1, baseline_search_requests)
false_early_stop_rate = false_early_stops / max(1, evaluated_runs)
release_gate_passed = (
    usable_recall >= 0.95
    and tool_reduction >= 0.20
    and false_early_stop_rate < 0.05
    and quality_regressions == 0
    and hard_constraint_violations == 0
)
```

The script accepts `--baseline-dir`, `--dynamic-dir`, and `--output`; writes `dynamic_budget_evaluation.json`; exits `0` on pass and `1` on gate failure.

Pair baseline and dynamic runs by replay ID. Build `EvaluationRun` from persisted artifacts using these rules:

```python
usable = validity_counts.get("valid", 0) + validity_counts.get("weak_keep", 0)
search_requests = int((summary.get("dynamic_usage") or {}).get("repository_requests") or 0)
false_early_stop = baseline_usable > 0 and dynamic_usable == 0
baseline_quality = baseline_valid / max(1, baseline_usable)
dynamic_quality = dynamic_valid / max(1, dynamic_usable)
quality_regression = dynamic_quality + 1e-9 < baseline_quality
hard_constraint_violations = sum(
    blocker.startswith("hard_constraint_violation:")
    for blocker in summary.get("blockers", [])
)
```

Missing replay IDs, missing manifests, or malformed summaries are evaluation errors and must make the CLI exit non-zero rather than being treated as zero-quality runs.

- [ ] **Step 5: Run the full local regression suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: all tests pass. Record the exact count in `docs/openai-agents-control-plane.md` instead of copying an older count.

- [ ] **Step 6: Run an explicit DeepSeek multi-Agent smoke**

With `AGENT_LLM_API_KEY`, `AGENT_LLM_BASE_URL`, and `AGENT_LLM_MODEL` supplied through the environment, run:

```powershell
.\.venv\Scripts\python.exe -m agent.cli agents-discover-dataset --discovery-mode multi_agent --prompt "Find human plasma DDA proteomics data with RAW files and sample-level metadata" --repository pride --task-type rt_prediction --max-query-units 12 --max-repository-requests 80 --output-dir runs/discovery/multi_agent_deepseek_smoke
```

Expected: two Agent roles appear in events; at least one proposal, decision, issued/consumed grant, metrics event, and final manifest selection are persisted; no key appears in any output file.

- [ ] **Step 7: Start the Web server and perform browser QA**

Run:

```powershell
.\.venv\Scripts\python.exe -m uvicorn agent.web.app:app --host 127.0.0.1 --port 8000
```

Use the browser skill to verify desktop `1440x900` and mobile `390x844`:

- OpenAI Agent mode shows autonomous budget and no preset/round/query/tool inputs.
- Activity, Tools and metrics, and Raw event tabs render without overlap.
- Logs append without flicker and preserve manual scroll position.
- Query, grant, metrics, stop, and final-selection events are visible and readable.
- API key is not rendered after submission and is absent from downloaded JSON/Markdown.
- Candidate tables and download buttons still work.

- [ ] **Step 8: Run the evaluation gate on captured baseline and dynamic runs**

Run:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_dynamic_discovery_budget.py --baseline-dir runs/discovery/eval_single_agent --dynamic-dir runs/discovery/eval_multi_agent --output runs/discovery/dynamic_budget_evaluation.json
```

Expected: exit `0`; usable recall at least 95%, tool reduction at least 20%, false early-stop rate below 5%, no quality regression, and zero hard-constraint violations.

- [ ] **Step 9: Switch the documented default only after the gate passes**

If Step 8 passes, change `.env.example` and `docker-compose.yml` default from `single_agent` to `multi_agent`, update the release status in `docs/openai-agents-control-plane.md`, and rerun deployment plus Web tests. If the gate does not pass, keep `single_agent` as default and preserve the generated evaluation report for tuning.

- [ ] **Step 10: Commit replay evaluation and verified default**

```powershell
git add tests/fixtures/dynamic_budget_replays.json scripts/evaluate_dynamic_discovery_budget.py tests/test_discovery_budget_evaluation.py docs/openai-agents-control-plane.md .env.example docker-compose.yml
git commit -m "test: gate multi-agent discovery rollout"
```

---

## Final Verification Checklist

- [ ] `git diff --check` reports no whitespace errors.
- [ ] `python -m pytest -q` passes with the exact count recorded.
- [ ] Single-Agent SDK Discovery still passes its original Fake Model loop.
- [ ] Deterministic Discovery remains unchanged.
- [ ] Multi-Agent search cannot run without an exact one-use grant.
- [ ] Budget Agent cannot invent queries, run repository tools, select a manifest, or modify hard scientific constraints.
- [ ] Query units and actual repository network requests are counted independently.
- [ ] A later empty round cannot overwrite an earlier non-empty candidate pool.
- [ ] Search stop still permits final manifest selection.
- [ ] Web no longer asks ordinary users to configure search budgets.
- [ ] Visible reasoning is a persisted public summary tied to evidence and actions, not raw hidden chain-of-thought.
- [ ] Browser-supplied API keys are absent from job persistence, SQLite payloads, reports, events, and downloads.
- [ ] DeepSeek smoke, browser QA, and evaluation gate results are recorded before changing the default mode.
