# Agent MVP Closed Loop Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an engineering-grade MVP of the public raw MS standardization Agent: auditable understanding, evidence-gated planning, bounded autonomous recovery, consistent artifacts, and full test coverage.

**Architecture:** Keep the existing repository adapters, DDA planner, bundle materialization, web runner, and MSDT execution. Add a small Agent control layer for decision gating and recovery auditing; allow only low-risk computational recovery automatically, and route biological interpretation changes to review.

**Tech Stack:** Python, Pydantic models, pytest, existing web/orchestrator modules, existing `agent_core` audit package.

---

## Scope And Boundaries

- Do not commit, push, or deploy.
- Do not add free-form shell/LLM execution.
- Do not automatically change high-risk biological facts: species, organism database, DDA/DIA, labeling, PTM experiment type, explicit project modifications, or digestion strategy unless evidence gates pass.
- Keep autonomous recovery limited to allowlisted computational actions: classify failure, write audit, reduce thread count in plan metadata, retry/download/conversion only where the existing preparer already has safe boundaries, and mark review when unsafe.

## File Map

- Create `src/agent/agent_core/recovery.py`: recovery audit schema, failure evidence extraction, action recommendation, writer.
- Create `src/agent/agent_core/recovery_policy.py`: allowlist and gate rules for automatic versus review-required recovery.
- Modify `src/agent/agent_core/models.py`: recovery model exports and optional recovery path in audit artifact paths.
- Modify `src/agent/agent_core/audit.py`: helper for writing `recovery_audit.json`.
- Modify `src/agent/execution/outputs.py`: structured execution failure objects while preserving legacy string output.
- Modify `src/agent/orchestrator/pipeline.py`: write recovery audit on planning/execution failure paths.
- Modify `src/agent/web/app.py`: write recovery audit consistently for full and batch failures.
- Modify `src/agent/agent_core/decision_trace.py` and `src/agent/agent_core/plan.py`: add database/workflow/resource decision records and align gates.
- Modify `src/agent/decision/dda.py`: use project/asset/attribute gates as blocking issues where evidence is insufficient.
- Add/extend tests in `tests/test_agent_recovery.py`, `tests/test_execution_outputs.py`, `tests/test_docker_pipeline.py`, `tests/test_web.py`, `tests/test_agent_core.py`, `tests/test_decision.py`, and `tests/test_repositories.py`.

---

## Milestone 1: Recovery Audit Core

### Task 1: Recovery schema and policy

**Files:**
- Create: `src/agent/agent_core/recovery.py`
- Create: `src/agent/agent_core/recovery_policy.py`
- Modify: `src/agent/agent_core/models.py`
- Test: `tests/test_agent_recovery.py`

- [ ] Write failing tests for:
  - `recovery_audit.json` contains `schema_version`, `task`, `failure`, `recovery`, and `integrity`.
  - output-missing reasons map to `missing_pin` / `missing_msdt_output`.
  - memory marker maps to `fragpipe_oom` or `insufficient_memory`.
  - unsafe biological actions return `manual_required`.
- [ ] Run focused tests and confirm they fail for missing module/API:
  - `.\.venv\Scripts\python.exe -m pytest tests/test_agent_recovery.py -q`
- [ ] Implement minimal schema/policy helpers.
- [ ] Re-run focused tests and keep them green.

### Task 2: Structured execution failure detection

**Files:**
- Modify: `src/agent/execution/outputs.py`
- Test: `tests/test_execution_outputs.py`

- [ ] Write failing tests for structured categories while preserving current `execution_failure_reasons()` strings.
- [ ] Run focused tests and confirm expected failure.
- [ ] Implement `execution_failure_events()` returning category, reason, evidence kind, marker/path.
- [ ] Re-run focused tests.

---

## Milestone 2: Recovery Audit Integration

### Task 3: Orchestrator failure paths

**Files:**
- Modify: `src/agent/orchestrator/pipeline.py`
- Test: `tests/test_docker_pipeline.py`, `tests/test_assets_integration.py`

- [ ] Write failing test: Docker/full execution failure writes `recovery_audit.json` beside manifest/state/review queue.
- [ ] Include missing PIN/MSDT evidence and review-required decision.
- [ ] Implement write helper calls in execution failure and asset-preparation failure paths.
- [ ] Run focused tests.

### Task 4: Web and batch paths

**Files:**
- Modify: `src/agent/web/app.py`
- Test: `tests/test_web.py`

- [ ] Write failing tests for single full failure and batch full failure producing `recovery_audit.json`.
- [ ] Ensure parameter and prepare paths include audit artifacts but do not invent recovery when no failure happened.
- [ ] Implement helper with reporter failure tolerance matching existing audit package behavior.
- [ ] Run focused tests.

---

## Milestone 3: Evidence Gates For Agent Decisions

### Task 5: Decision trace completeness

**Files:**
- Modify: `src/agent/agent_core/decision_trace.py`
- Modify: `src/agent/agent_core/plan.py`
- Test: `tests/test_agent_core.py`

- [ ] Write failing tests requiring decision types: `file_matching`, `database_selection`, `workflow_selection`, `resource_policy_selection`.
- [ ] Write failing test that any `review_required` decision makes `agent_plan.execution_gate == "review_required"`.
- [ ] Implement minimal decision records using existing resolution/asset/plan/attributes evidence.
- [ ] Run focused tests.

### Task 6: Planner gate enforcement

**Files:**
- Modify: `src/agent/decision/dda.py`
- Test: `tests/test_decision.py`, `tests/test_repositories.py`

- [ ] Write failing tests:
  - `ProjectResolution.needs_review=True` blocks execution.
  - low asset confidence or unknown asset blocks.
  - workflow/database hints with weak or conflicting source require review.
  - FASTA/species conflict requires review.
- [ ] Implement conservative gate checks with clear blocking issue messages.
- [ ] Run focused tests.

---

## Milestone 4: Engineering Hardening And Docs

### Task 7: Audit manifest consistency

**Files:**
- Modify: bundle/manifest writing paths as needed.
- Tests: affected web/orchestrator tests.

- [ ] Verify parameter, prepare, full, and batch outputs list only existing audit artifacts.
- [ ] Add regression tests for missing optional audit files.
- [ ] Run affected suites.

### Task 8: Documentation

**Files:**
- Create or update: `docs/agent_mvp.md`

- [ ] Document autonomy levels L0-L3, recovery allowlist, `recovery_audit.json`, and manual review boundaries.
- [ ] Document known MVP limitations: no arbitrary biological overrides, no tool installation, no unlimited retries.

---

## Final Verification

- [ ] Run focused suites after each milestone.
- [ ] Run affected suites:
  - `.\.venv\Scripts\python.exe -m pytest tests/test_agent_core.py tests/test_agent_recovery.py tests/test_execution_outputs.py tests/test_docker_pipeline.py tests/test_assets_integration.py tests/test_decision.py tests/test_repositories.py tests/test_web.py -q`
- [ ] Run full suite:
  - `.\.venv\Scripts\python.exe -m pytest -q`
- [ ] Inspect `git status --short` and summarize changes without committing.
