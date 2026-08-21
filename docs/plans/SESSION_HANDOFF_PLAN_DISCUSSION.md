# Session handoff: Plan discussion with Codex (no implementation yet)

Date: 2026-07-22 (Asia/Shanghai)  
Mode: **discussion only** until user says the plan is locked.

## Goal of this Codex session

Work **with** the human (via orchestrator notes below) to **criticize, revise, and lock** an architecture/implementation plan for a Codex-class autonomous proteomics Discovery Agent.

**Do not write product code.** You may only create/update planning docs under `docs/plans/` if the user explicitly asks to save the agreed plan. Default: analysis in chat only.

## User decisions already made

- Fix **generic failure classes**, not one immunopeptidomics case.
- Prefer **OpenAI Agents SDK** mature patterns; deterministic control plane owns graduation and repair success.
- Implementer model: **Codex gpt-5.6-sol, high reasoning**.
- Supervisor later: **Grok (strict)**; not for this planning round unless asked.
- **No Claude Code. No Gemini.**
- Shell on this machine is fixed: Git Bash `E:\Git\bin\bash.exe` (MINGW64). Do not reinstall WSL for this.

## North star (one sentence)

A reliable, flexible, self-healing Discovery Agent: hears **horizon**, separates **hard/soft/open**, scopes **evidence**, publishes the right package, and **converges repair** honestly—like Codex (tools + budgets + stop rules), not a questionnaire.

## Failure classes to solve (H1–H8)

| ID | Class |
| --- | --- |
| H1 | Horizon/ruler mismatch (reviewed scored as AI-ready) |
| H2 | Soft preference → hard constraint |
| H3 | Evidence scope mismatch (project fact demanded per file) |
| H4 | Dual quality defs (judgment pass ≠ delivery; no materialization) |
| H5 | Non-convergent repair (one-shot LLM “fix”) |
| H6 | Dishonest UI/events (green “repair completed” at 0 delivery) |
| H7 | Stale search/grant ids |
| H8 | Weak task agenda (e.g. chimeric label feasibility under-asked) |

Regression fixture exists conceptually from run `discovery_job_20260722_133827_*` (32 / 0 / 2408). Use as exam, not as `if immuno` special case.

## Draft plan to review

Primary: `docs/plans/2026-07-22-autonomous-discovery-agent.md`  
Brief format: `docs/plans/CODEX_PLANNING_BRIEF.md`  
Handoff facts: `E:\TEMP\proteomics-discovery-agent-handoff-20260722.md`  
ADRs: `docs/adr/0001-*.md`, `docs/adr/0002-*.md`  
Guidance: `docs/discovery-agent-guidance.md`

## What Codex must deliver this turn

Structure the reply as:

### A. SDK fit
What we should use from OpenAI Agents SDK (multi-agent, agents-as-tools, sessions, tracing, guardrails, handoffs, structured outputs) vs what must stay deterministic outside the model.

### B. Critique of the draft plan
Agree/disagree; cut over-scope; fill gaps for “Codex-like” autonomy.

### C. Architecture decisions (answer plan §8)
1. Orchestrator = pure state machine + worker agents-as-tools, or SDK agent wrapping services?
2. Where `ConstraintBinding` lives (shared pydantic vs codegen).
3. Evidence materialization: extend models vs `EvidenceStore`.
4. Versioning `DiscoveryRepairKind` for old run replay.
5. Sacred green tests vs rewritable tests.

### D. Locked wave plan
Ordered waves with **entry/exit criteria**, file touch list (paths), and test strategy (fixtures ≥2 scenarios).

### E. Explicit non-goals and anti-patterns
Especially case patches, second strategy writers, fake repair success.

End with exactly one of:

- `PLAN_STATUS: NEEDS_HUMAN_CHOICES` + numbered questions for the user  
- `PLAN_STATUS: READY_TO_LOCK` + short “I recommend we lock X”

## Discussion rules

- Challenge the draft; do not rubber-stamp.
- Prefer smallest vertical slice that proves H1–H6 without boiling the ocean.
- Preserve ADR 0002 one-writer dialogue and fail-closed hard constraints.
- Cite concrete repo paths when claiming current behavior.
- Read current Agents SDK docs if needed (openai.github.io/openai-agents-python/…).

## After plan lock (not this turn)

Grok audits the locked plan; then IMPLEMENT WAVE 1 (red fixtures) under Grok supervision.
