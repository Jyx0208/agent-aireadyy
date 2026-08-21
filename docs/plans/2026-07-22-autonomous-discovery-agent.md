# Plan: Autonomous Proteomics Discovery Agent (Codex-class)

Date: 2026-07-22  
Status: draft for joint review with Codex (`gpt-5.6-sol` high)  
Worktree: `E:\ai-agent-already\github-publish\agent-aireadyy\.claude\worktrees\benchmark-review-planning`  
Supervisor policy: Grok audits; Codex implements; no Claude Code; no Gemini

## 1. North star

Build a **reliable, flexible, self-healing Discovery Agent** that feels closer to
Codex than to a questionnaire:

- Understands *how far* the user wants to go (horizon), not only *what* topic.
- Distinguishes **hard / soft / open** constraints without keyword hacks.
- Uses **OpenAI Agents SDK** primitives for multi-agent work, sessions, tools,
  tracing — and keeps **business authority outside the model**.
- When execution fails quality checks, runs a **convergent repair controller**
  (diagnose → allowed action → measure delta → stop honestly), not a one-shot
  “please fix yourself” prompt.
- Generalizes across tasks (immuno, PTM, RT, de novo, browse…). The failing
  immunopeptidomics run is a **regression fixture**, not a special case.

## 2. What already exists (keep)

| Layer | What works | Source of truth |
| --- | --- | --- |
| Dialogue authority | One Dialogue Manager writes strategy; specialists/critic read-only | ADR 0001/0002 |
| Option contracts | Numeric/id/label selection applies stored `strategy_patch` | ADR 0002 |
| Confirm ≠ search | Fingerprint-bound confirmation separate from job start | ADR 0002, guidance |
| Execution control plane | Budgets, grants, audits, repair *kinds*, event log | `control_plane/` |
| Project judgment | 0–3 grades, hard_gate, evidence stage | `project_judgment.py` |
| Agents SDK usage | Manager tools, OpenAI-compatible paths, discovery runner | `web/app.py`, discovery agents |

Do **not** throw away one-writer dialogue or fail-closed confirmation.

## 3. Failure pattern classes (generic, not one case)

Use the 32/0/2408 run only to name *classes*:

| Class ID | Failure class | Symptom family | Root mechanism |
| --- | --- | --- | --- |
| H1 | Horizon/ruler mismatch | Reviewed job scored with AI-ready file gate | Single publication contract |
| H2 | Soft→hard corruption | Preference becomes hard constraint in payload | Frontend/backend strength inference |
| H3 | Evidence scope mismatch | Project-level fact demanded on every file | Gate ignores scope |
| H4 | Dual quality definitions | Judgment pass ≠ delivery pass, no materialization | Judgment evidence not canonicalized |
| H5 | Non-convergent repair | “Repair completed” + zero delta | LLM essay instead of controller |
| H6 | Dishonest telemetry | Green UI on failed outcome | Event naming / UI mapping |
| H7 | Stale capability tokens | Reinspect with expired search id | No automatic refresh |
| H8 | Agenda under-specified | Task feasibility questions skipped | Guidance/agenda incomplete |

Any fix must register as a **class handler**, tested by ≥1 fixture that is not
immunopeptidomics-only (add a second synthetic fixture later).

## 4. Target architecture (SDK-aligned)

### 4.1 Principles from OpenAI Agents SDK docs

Official concepts we must map explicitly
([multi-agent](https://openai.github.io/openai-agents-python/multi_agent/),
[agents-as-tools](https://openai.github.io/openai-agents-python/tools/#agents-as-tools),
[sessions](https://openai.github.io/openai-agents-python/sessions/),
[tracing](https://openai.github.io/openai-agents-python/tracing/),
[guardrails](https://openai.github.io/openai-agents-python/guardrails/),
[handoffs](https://openai.github.io/openai-agents-python/handoffs/)):

1. **Manager retains the user conversation** (agents-as-tools), specialists get
   *explicit* context — already ADR 0002; extend to execution specialists.
2. **Handoffs** only for *user-visible phase changes* (dialogue → discovery
   workbench; discovery → repair mode banner), never for silent second writers.
3. **Sessions** hold dialogue continuity; they are **not** authorization.
4. **Tracing** records workflow; UI must map trace/events to honest stages.
5. **Tools** are the only side effects; tool surface is versioned and policy-gated.
6. **Structured outputs** for audits, repair plans, judgments — not free prose
   as authority.
7. Prefer **deterministic control loops around agents**, like Codex: model
   proposes/acts within tools; runtime enforces budgets, idempotency, stop rules.

### 4.2 Runtime shape (target)

```text
                    ┌──────────────────────────────┐
   User ───────────►│ Dialogue Manager Agent (SDK) │
                    │ tools: update/confirm/respond│
                    │ as_tool: Scientific Advisor  │
                    │ as_tool: Critic (read-only)  │
                    └──────────────┬───────────────┘
                                   │ confirmed StrategySnapshot
                                   ▼
                    ┌──────────────────────────────┐
                    │ Discovery Orchestrator       │
                    │ (deterministic state machine)│
                    │ states: search|inspect|judge │
                    │         audit|repair|publish │
                    └──────┬───────────┬───────────┘
                           │           │
              SDK Worker Agents        │
              (tool-bound, no card     │
               write authority)        │
                           │           ▼
                           │  Publication contracts
                           │  by horizon (pure code)
                           ▼
                    Artifacts + QualityAudit + UI events
```

**Key split (Codex-like):**

- **LLM agents**: propose queries, inspect reasoning, project judgments,
  explain limitations to the user.
- **Deterministic orchestrator**: when to search/inspect/select/repair/stop;
  which repair actions are legal; whether a horizon is satisfied.
- **Never** let an LLM invent “repair succeeded”.

### 4.3 Horizon publication contracts (generic)

```text
candidates_only
  publish: ranked projects + provenance
  gate: relevance/search-stage only; no file AI-ready requirement

candidates_reviewed
  publish: project judgments + reasons + confidence + unresolved evidence
           + ranked follow-ups
  gate: inspection-backed judgment policy; hard conflicts fail-closed at
        correct evidence scope; may include partial unresolved set

ai_ready_* / training horizons
  publish: file/spectrum delivery rows
  gate: strict validity, role, URL/size, evidence level as today
```

Horizon is part of `StrategySnapshot` and `DatasetRequest`; **every gate and
UI metric branches on it**.

### 4.4 Constraint strength + evidence scope (generic)

Each strategy dimension:

```text
ConstraintBinding {
  dimension: str
  value: ...
  strength: hard | soft | open
  evidence_scope: project | assay | file | spectrum
  source: user_option | user_nl | system_default
}
```

Rules:

- Concrete value **does not** imply hard.
- Soft affects ranking/repair priority, not automatic exclusion (unless later
  user hardens it).
- Hard fails only when evidence at `evidence_scope` contradicts or is missing
  *when the horizon requires that knowledge*.
- Judgment-verified observations **materialize** into canonical evidence store
  so delivery gates and UI share one truth.

### 4.5 Autonomous repair controller (the “self-healing” core)

Replace one-shot repair runner with:

```text
loop (bounded: max_steps, max_no_progress, budget):
  audit = deterministic_quality_audit(state, horizon)
  if audit.ready_for_publication(horizon): publish; break
  if audit.status == blocked and no_legal_actions: publish_partial_or_fail; break

  action = select_repair_action(audit)  # pure policy table, not free LLM
  # optional: LLM only to *fill parameters* of a chosen action kind

  pre = metrics_snapshot(state)
  result = dispatch(action)             # tools only; refresh stale ids
  post = metrics_snapshot(state)

  if not improved(pre, post, action.success_metric):
     record no_progress_signature
  if repeated no_progress: stop_with_limitations

emit events:
  repair_attempt_started | repair_attempt_finished
  repair_progressed | repair_no_progress
  repair_succeeded | repair_incomplete | repair_blocked
```

**Action catalog (extensible, class-driven):**

| Action kind | Fixes class | Deterministic effects |
| --- | --- | --- |
| `refresh_search_grant` | H7 | new search id / grant |
| `inspect_candidates` | H1/H4 partial | deeper evidence |
| `materialize_judgment_evidence` | H4 | write canonical evidence from judgments |
| `recompute_validity` | H3/H4 | re-run scoped gates |
| `search_more` | coverage gaps | budgeted queries |
| `select_manifest` | only if audit allows | publish |
| `stop_with_limitations` | no progress | honest terminal |

**Forbidden:** `rescore_projects` as the only fix for manifest field problems;
`select_manifest` when audit says `repair_required` without allow.

LLM may *prioritize which accessions to inspect* inside an allowed kind;
it may not invent new kinds or declare success.

### 4.6 Dialogue Manager upgrades (still SDK-native)

Keep Manager-as-tools. Add:

1. **Strength-aware patches** in every option `strategy_patch`.
2. **Horizon-first critical agenda** before optional prefs when training-ish.
3. Task packs (e.g. chimeric): feasibility decisions (label provenance /
   relabel tolerance, isolation window) as agenda items — data-driven, not
   hard-coded only for immuno.
4. Guardrail: reject patches that upgrade soft→hard without user language
   markers / explicit strength field.

### 4.7 Observability (honest, Codex-like progress)

Map control-plane events → UI stages:

```text
searched → inspected → judged → horizon_ready → unresolved
```

Metrics always dual-track:

- `judgment_qualified_projects`
- `delivery_eligible_for_horizon` (name includes horizon)

Never render `repair_attempt_finished` as success.

Tracing: one SDK trace per dialogue turn; one per discovery run; repair steps
as nested spans with action kind + delta metrics.

## 5. Implementation waves (Codex executes, Grok supervises)

### Wave 0 — SDK alignment memo (1 short doc + inventory)
- Inventory every `Agent(`, `Runner`, tool, session, handoff in repo.
- Map each to target diagram; list gaps vs official patterns.
- Read current Agents SDK docs (multi_agent, tools, sessions, tracing,
  guardrails, results) before coding.

### Wave 1 — Generic regression seam
- Fixture from real audit summary (offline).
- Second synthetic fixture (different task/horizon) encoding H1–H6.
- Red tests for horizon publication, not string matching on immuno terms.

### Wave 2 — Contracts (H1–H4)
- `ConstraintBinding` + payload builders (frontend + backend).
- Horizon publication modules (pure functions, heavily tested).
- Evidence materialization path judgment → canonical store → gate.

### Wave 3 — Repair controller (H5–H7)
- Deterministic loop + action table + stale grant refresh.
- Delete/disable “one-shot repair completed” success path.
- Property tests: no progress ⇒ stop; allow-list dispatch only.

### Wave 4 — UI honesty (H6)
- Event renames; dual metrics; aggregate unresolved by project/cause.

### Wave 5 — Agenda packs (H8)
- Task-agnostic agenda engine; chimeric pack as first plugin.

### Wave 6 — Hardening
- Preserve numeric option memory, fingerprint confirm, one-writer tests.
- Grok live turn + DeepSeek fallback turn if credentials available.
- No secrets in git; no PRIDE in unit loops.

## 6. What “done” means (product)

User can say almost anything scientific; Agent:

1. Builds a strategy with visible hard/soft/open.
2. Confirms, then executes with live honest stages.
3. Returns a **horizon-correct package** (not empty because the wrong ruler).
4. If blocked: explains *error class*, what it tried, what remains.
5. Same machinery works for browse, reviewed candidates, and AI-ready.

Not done if: only the immuno fixture greens; soft still becomes hard elsewhere;
repair still means “ran an LLM once”.

## 7. Risks

| Risk | Mitigation |
| --- | --- |
| Over-refactor of dialogue | Keep ADR 0002; change execution/publish first |
| Weakening fail-closed | Separate tests: hard conflict still blocks |
| LLM-driven repair creep | Action allow-list in code; LLM only parametrizes |
| Provider tool_choice flakiness | Existing JSON compatibility path; don’t add writers |
| Scope explosion | Waves gateable; Grok rejects unplanned refactors |

## 8. Discussion questions for Codex

Please answer explicitly in the joint review:

1. Should the **Discovery Orchestrator** live as pure Python state machine that
   *calls* SDK agents as tools, or as an SDK Agent whose tools are exclusively
   deterministic services? (Recommend: pure SM + worker agents-as-tools.)
2. Where should `ConstraintBinding` live — shared pydantic in
   `agent/discovery/constraints.py` with frontend mirror types, or codegen?
3. Minimal materialization API: extend `DiscoveredProject` evidence maps vs new
   `EvidenceStore` artifact?
4. How to version `DiscoveryRepairKind` without breaking old run replays?
5. Which existing tests are sacred green (must not regress) vs rewrite?

## 9. Non-goals (this program)

- Replacing PRIDE client wholesale
- Training learned ranking models
- Multi-user SaaS auth
- Making critic a second strategy writer
- Case-specific “if immuno and 32 candidates then pass”

## 10. Immediate next step after plan PASS

Codex implements Wave 1 red fixture under TDD; Grok verifies the fixture
encodes classes H1–H6 without network; only then Wave 2 code.
