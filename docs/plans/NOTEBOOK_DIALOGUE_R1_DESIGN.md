# Notebook Dialogue Redesign — DESIGN R1

| Field | Value |
|-------|-------|
| Status | **DESIGN ONLY** — R1 diagnosis + target architecture |
| Room | `notebook-dialogue-plan` |
| Board | `docs/plans/SWARM_NOTEBOOK_DIALOGUE.md` |
| Author | DESIGN committee (main agent synthesis) |
| Date | 2026-07-25 |
| Runtime | OpenAI Agents SDK **stays** |
| Non-goals | Deploy/auth/docker; full discovery portfolio/CEM redesign; batch status bar rewrite |

---

## 0. User ideal (acceptance north star)

1. Free chat (including chitchat) — never scary contract errors on non-mutating turns.
2. Agent extracts commitments from **full dialogue context** and writes the card via tools.
3. Later corrections **overwrite** earlier fields (latest user intent wins).
4. Agent asks only about **real gaps**, not a fixed questionnaire.
5. When the task is clear enough, agent calls `update_strategy` itself (prompt + tools), not form wizard.
6. After discovery fail/success, **same conversation continues**: view / revise / re-search — not dead-end.
7. Thin gates only: confirm before PRIDE search; no fake build-ready; no silent drop of hard asks.
8. Prefer fewer hard rules; agent-led; OpenAI Agents SDK stays.

Boss reject bar: hand-wavy “just prompt better” without file paths; thick questionnaire return; kill confirm-before-search; allow fake green.

---

## 1. Current architecture (file map)

### 1.1 Two-authority split (correct, keep)

```text
User speech
  → Dialogue Manager (OpenAI Agents SDK)
      tools: respond | update_strategy | confirm_strategy
      optional read-only: consult_scientific_advisor (Agent.as_tool)
  → Server publication plane
      schema patch validation
      option-scope / executable option contracts
      compound commitment merge + low-risk verifier skip
      soft/partial reject
      critical agenda + next_decision synthesis
      confirm eligibility (phase + fingerprint)
  → Discovery start gate: grill_confirmed (separate from confirm_strategy)
```

This matches ADR 0002 (`docs/adr/0002-discovery-dialogue-manager-and-option-contracts.md`):
one Manager writer; critic read-only; options are executable contracts; confirm ≠ search start.

### 1.2 Key files

| Area | Path | Notes |
|------|------|-------|
| Grill entry | `src/agent/web/app.py` | `_run_discovery_grill_turn`, system/user prompt contract |
| SDK runtime | same | `_run_discovery_dialogue_agents_sdk`, `tool_choice=required`, `max_turns=2` |
| Provider recovery | same | JSON compatibility path; prose never mutates card |
| Compound assist | same | `_discovery_compound_commitment_hints`, merge, low-risk skip, soft-reject keep |
| Agenda | `src/agent/discovery/agenda.py` + `task_profiles.py` | Deterministic critical agenda, not Q1–Q10 |
| Confirm gate | `app.py` `_discovery_confirmation_context` | phase=awaiting_confirm + fingerprint |
| Search start | discovery job path | requires `grill_confirmed=true` |
| FE turn contract | `frontend/benchmark-review/src/agent-turn.ts` | `reduceAgentTurn`, confirm fingerprint |
| FE UX | `frontend/benchmark-review/src/CarbonAgentChat.tsx` | phases idle/grilling/awaiting_confirm/running/done/failed |
| Prior swarms | `SWARM_AGENTIC_COMPOUND_DIALOGUE.md`, `SWARM_DIALOGUE_SOFT_FAST.md`, `SWARM_GRILL_BUGS_FIX.md` | Compound + soft/fast + grill silence |
| Science WP-E | `SCIENCE_SEMANTICS_MASTER_PLAN.md` §7, `_sp_e_r1.txt`, `_sp_e_r2.txt` | Compound vs grill tension; do not weaken CEM/Release |

---

## 2. Root-cause analysis (why it still feels like a form)

### RC1 — Contract surface is notebook-hostile

**Symptom:** Chitchat / vague / off-topic turns can surface schema/contract noise; user feels scolded by a form engine.

**Why (3 levels):**
1. Turn contract requires rich JSON (`turn_interpretation`, `clause_audit`, full `next_decision` schema) even when action is pure `chat`.
2. Incomplete `next_decision` is dropped and repair paths re-prompt with contract language.
3. Historical questionnaire DNA remains in prompt bulk (clause_audit redundancy, option menus as default recovery) even though ADR says menus are only for blockers.

**Symptom vs cause:** Softening copy alone is a symptom patch. Cause is: **non-mutating turns must short-circuit before heavy contract repair**.

### RC2 — Menu-first recovery after any gap

**Symptom:** After a partial compound write, UI still feels like “pick 1–5” instead of notebook continuation.

**Why:**
1. Server synthesizes `next_decision` from agenda when model omits/breaks it (`_synthesize_discovery_next_decision_from_agenda`) — good for science, bad UX when always menu-shaped.
2. Prompt still says “2–5 options” for critical gaps; free-text ask is allowed but not preferred in practice.
3. FE renders options as primary interaction affordance.

**Design stance:** Keep agenda **server-side** as readiness truth; change **presentation**: free-text first, optional chips, not forced radio quiz.

### RC3 — Write path over-gated for low-risk fields, under-provenanced for hints

**Symptom:** Short topic turns blank; packed NL under-writes; or policy satellites land silently.

**Why:**
1. Semantic verifier + multi-hop Manager path adds latency and hard rejects (partially fixed by low-risk skip + soft-reject keep).
2. Deterministic compound hints fill species_policy / mixed_acquisition_policy without provenance (SP-E RC1 / SP-B collision).
3. `scientific_constraints` correctly force critic — keep — but free-form science still has weak “beyond card” home when enums fail.

### RC4 — Post-discovery conversation is half-alive

**Symptom:** After done/failed, user can re-enter grilling (good: `turnPhase` remaps done/failed → grilling, session preserved), but recovery UX is thin:
- `detectNextStepCommand` only routes to single/batch/ai-ready tabs.
- Failed runs do not offer first-class “revise strategy / re-search with same card / explain failure / view L1”.
- Restart remains the only full reset (correct), but users may not know continue is safe.

**Why:** Product state machine treats terminal job status as UX dead-end mentally, even though code already re-enters dialogue.

### RC5 — Thick prompt fights agent-led notebook

**Symptom:** Model spends capacity on clause_audit / option schema completeness instead of extraction + judgment.

**Why:** Prompt grew as regression armor (option 1 → plan_only, confirm leakage, grill silence). Armor is valuable but currently **inlined into every turn**, including chat.

---

## 3. Target conversation state machine

```text
                    ┌─────────────┐
                    │    idle     │
                    └──────┬──────┘
                           │ first user message
                           v
                    ┌─────────────┐
         ┌─────────│  notebook   │◄────────────┐
         │         │  (grilling) │             │
         │         └──────┬──────┘             │
         │    chat/advise │                    │
         │    (no write)  │ update_strategy    │ revise after
         │         │      v                    │ done/failed
         │         │  ┌──────────┐             │
         │         │  │ card     │──clarify ───┘
         │         │  │ updated  │   (gap only)
         │         │  └────┬─────┘
         │         │       │ server: critical empty
         │         │       v
         │         │  ┌────────────────┐
         │         └──│ awaiting_confirm│
         │            └────────┬───────┘
         │     no/revise       │ confirm_strategy
         │◄────────────────────┤ (fingerprint)
         │                     v
         │            ┌────────────────┐
         │            │ grill_confirmed │  (server flag)
         │            └────────┬───────┘
         │                     │ start discovery
         │                     v
         │            ┌────────────────┐
         │            │    running     │──cancel──► notebook (card kept)
         │            └────────┬───────┘
         │                     │
         │            ┌────────┴────────┐
         │            v                 v
         │     ┌──────────┐      ┌──────────┐
         └─────│   done   │      │  failed  │
               └────┬─────┘      └────┬─────┘
                    │                 │
                    └── notebook ─────┘
                        (same session, history, card)
                        only Restart → idle empty
```

### Phase semantics (product)

| Phase | User can | Card | Search |
|-------|----------|------|--------|
| notebook/grilling | chat, advise, write, ask gap | mutable | no |
| awaiting_confirm | confirm / revise | frozen for fingerprint | no |
| running | stop; limited status chat | frozen | yes in flight |
| done / failed | chat, revise, re-confirm, view artifacts | mutable again after revise | only after new confirm |
| idle (Restart only) | empty | empty | no |

**Invariant:** `confirm_strategy` never starts PRIDE. `grill_confirmed` + fingerprint (+ later durable job store) starts search.

---

## 4. Prompt principles (notebook agent)

Replace “questionnaire armor on every turn” with **layered instructions**:

1. **Identity:** scientific notebook partner for proteomics discovery planning — not a form clerk.
2. **Extract then act:** from full history + latest message, list commitments; if any, one `update_strategy` patch; latest intent wins.
3. **Chat is free:** greetings, definitions, hypotheticals → `chat`/`advise`, `next_decision=null`, no contract scolding.
4. **Gap only:** after write, ask at most one real blocker from server critical agenda; prefer free-text; options optional chips.
5. **No invention:** topic ≠ task_type; recommendations stay advice until accepted.
6. **Honest capability:** confirm never implies training table / full release; horizon limits spoken plainly.
7. **Recovery:** after fail/done, propose revise / re-search / explain — never force Restart.

Implementation shape (later, not this R1 code):
- Slim system prompt for chat/advise path.
- Full mutation contract only when Manager is about to call `update_strategy` or when critical gap remains.
- Keep regression armor as **server predicates + tests**, not only as prompt paragraphs.

---

## 5. Tool contract (thin, keep)

| Tool | Authority | Notes |
|------|-----------|-------|
| `respond` | prose only | chat/advise/clarify/refuse_search |
| `update_strategy` | only mutation | one patch object; schema-validated |
| `confirm_strategy` | confirmation event | only if `confirmation_context.eligible` |
| advisor as_tool | read-only | never writes card |

**Delete/weaken (behavioral, not necessarily delete symbols):**
- Menu-first default for every critical gap → free-text-first.
- Scary incomplete-contract user copy on pure chat.
- Silent policy satellites from compound hints without provenance.
- Treating `notes` as dump for hard requirements (keep rule: actionable → `scientific_constraints`).

**Keep hard:**
- Executable option patches (numeric `1` cannot invent `run_horizon`).
- Critic cannot write / confirm.
- Confirm fingerprint + awaiting_confirm.
- `scientific_constraints` never low-risk skip.
- No search before confirm.

---

## 6. Free-form intent beyond enum card (NB-C)

### Problem
First-class enums cannot hold every scientific ask. Today:
- `notes` = context only (correct for hard science).
- `scientific_constraints` = structured hard/soft (correct) but UX for partial lists is harsh if atomic fail-closed.
- `objective` / `special_themes` = soft topic homes (soft-reject keep).

### Design

```text
User free intent
  ├─ maps to first-class field → patch field
  ├─ maps to structured constraint → scientific_constraints[] (WP-B normalize Result)
  └─ maps to neither → open_constraint / review_note with:
        id, text, strength_hint, evidence_required?,
        provenance=user, status=open|accepted|dropped
```

Discovery consumption:
- Hard structured constraints → CEM rows (SP-A) after WP-B.
- Open constraints → search observations / expert review prompts; **never** silently drop; **never** fake as satisfied hard_pass.
- Card UI: show open constraints as chips under strategy, not buried only in notes.

Atomicity (align SP-B):
- Invalid **known** constraint field → fail that write with `rejected[]` indices for repair turn.
- Pure open notes never block low-risk topic writes.

---

## 7. Write-card + verifier policy (NB-B)

### Keep
- Manager-only writer (ADR 0002).
- Low-risk single/compound skip whitelist for latency.
- Soft-reject keep for `objective` / `special_themes` / `notes` / explicit `task_type`.
- Option selection applies stored `strategy_patch` only.

### Tighten (from SP-E R2)
1. **P_low_risk_skip:** multi-field skip only if patch keys ⊆ commitment_audit ∪ hint_provenance; else critic.
2. **P_hint_field_allowed:** structural lexical OK; policy satellites default **off-card** unless explicit user language or accepted recommendation; provenance never `may_be_hard`.
3. **P_ready_for_confirm:** server recompute from projected critical agenda only; ignore model `true` when critical remains; never from `weak_ready`.
4. Verifier reject of hard fields: soft keep only soft keys; surface Chinese partial-success copy (already partially landed).

### Kill
- Whole-card wipe on soft verifier reject when soft fields grounded.
- Expanding low-risk skip to `scientific_constraints` / exclude_rules / PTM hard.

---

## 8. Fail / success recovery UX (NB-D)

### Already good (do not regress)
- `CarbonAgentChat.tsx`: done/failed → re-enter `grilling` with same session/history/card.
- Restart only full reset (session id changes — covered by tests).

### Missing product affordances
After **failed**:
1. Explain failure in notebook language (from job error, not raw stack).
2. Offer: revise strategy → confirm again → re-search.
3. Offer: view partial L1 / downloads if any (honest: only published receipts later WP-D).
4. Do **not** auto-clear card.

After **done**:
1. Summarize horizon-honest outcome (candidates vs reviewed vs not training-ready).
2. Continue chat for refine / second search / open constraint follow-ups.
3. Tab navigation (`detectNextStepCommand`) remains optional shortcut, not the only path.

### Phase copy
Replace “任务结束” dead-end tone with “检查点：可继续改策略或再次确认搜索”.

---

## 9. Adversarial risks (NB-E) — must not regress

| Attack | Defense |
|--------|---------|
| Option `1` widens patch | Stored option `strategy_patch` only (ADR 0002) |
| “好的” during grill confirms | phase≠awaiting_confirm → reject confirm |
| Confirm starts search | confirm ≠ grill_confirmed |
| Fake green / weak_ready ready | P_ready_for_confirm ignores weak_ready; Release predicates separate |
| Context overflow | Session memory filter by snapshot; don’t dump full raw tool traces into user chat |
| Losing audit | Keep semantic_verification, option contract, fingerprint in audit |
| Soft-skip smuggles hard science | constraints always critic; provenance on hints |
| Post-fail Restart pressure | Continue path default; Restart explicit |

---

## 10. Work packages (implement later)

| ID | Slice | Primary files | Depends |
|----|-------|---------------|---------|
| NB1 | Non-mutating short-circuit + friendly chat path | `app.py` grill pre/post, FE message copy | none |
| NB2 | Free-text-first gap presentation; menu optional | `app.py` next_decision synth, `CarbonAgentChat.tsx` | NB1 |
| NB3 | Hint provenance + policy satellites off-card | `app.py` compound hints/merge | WP-B preferred |
| NB4 | Open-constraint IR + UI chips | strategy schema, FE strategy rail | WP-B normalize |
| NB5 | Post done/failed recovery actions | `CarbonAgentChat.tsx`, job summary | WP-D optional for receipts |
| NB6 | Prompt slim + regression armor as tests | system prompt, `test_discovery_agent_turn.py` | NB1–3 |
| NB7 | Confirm ladder contract tests FE+BE | agent-turn tests, grill tests | keep always |

Ordering vs science master plan: **WP-B before heavy NB3/NB4 production**; NB1/NB2/NB5/NB7 can land earlier without weakening gates.

---

## 11. Acceptance matrix (design-level)

| # | Scenario | Expect |
|---|----------|--------|
| A1 | Greeting / “什么是 DDA” | `chat`/`advise`, no patch, no scary contract |
| A2 | Packed compound NL | one multi-field `update_strategy`; no re-ask stated fields |
| A3 | Topic-only 免疫肽 | no invented `task_type=rt_prediction` |
| A4 | Correction overwrites | latest field wins; fingerprint invalidates old confirm |
| A5 | Critical species missing after compound | one gap ask (free-text first); not silent ready |
| A6 | Option `1` | exact stored patch only |
| A7 | NL yes while grilling | not confirm |
| A8 | awaiting_confirm + explicit approve | `confirm_strategy`; still no PRIDE until start |
| A9 | Start discovery | requires grill_confirmed + fingerprint |
| A10 | Job failed | same session continue; revise/re-search; no forced Restart |
| A11 | Hard constraint invalid | visible reject; no silent drop |
| A12 | No fake build-ready language on confirm | horizon-honest copy |

---

## 12. Explicit non-goals

- Replace OpenAI Agents SDK with pi coding-agent as product runtime.
- Remove confirm-before-search.
- Soften CEM / Release / DownloadReceipt for dialogue UX.
- Case-specific immunopeptide branches as primary intent path.
- Deploy auth / docker socket hardening (boss deferred).

---

## 13. What to delete / weaken / keep (summary)

### Weaken
- Menu-as-default recovery
- Full clause_audit burden on pure chat turns
- Silent policy defaults from hints
- Dead-end tone after done/failed

### Keep
- Agents SDK Manager + tools
- Server publication plane
- Executable options
- Critical agenda as readiness (not as quiz script)
- Confirm / grill_confirmed split
- Soft-reject for soft fields
- Compound multi-commitment default

### Add
- Notebook state machine product language
- Open-constraint IR for beyond-card intent
- Provenance on deterministic hints
- Recovery action affordances after fail/done
- Short-circuit non-mutating path

---

## 14. R2 questions for peers / chair

1. Free-text-first gaps: keep synthesizing full `options[]` for accessibility, or make options truly optional in schema when `allow_free_text=true`?
2. Open constraints: new first-class array vs reuse `scientific_constraints` with `operator=note`?
3. Should post-fail re-search require a fresh confirm always (recommended: **yes**, fingerprint discipline)?
4. Prompt slim strategy: two system prompts vs one prompt with “mode=chat|mutate” server injection?

---

## 15. Recommended R3 deliverable

Chair synthesizes this R1 + peer R1/R2 into:

`docs/plans/NOTEBOOK_DIALOGUE_MASTER_PLAN.md`

Must include: state machine, prompt principles, tool contract, deletions, free notes IR, fail recovery UX, migration/tests, non-goals — per board `SWARM_NOTEBOOK_DIALOGUE.md`.

---

END R1 DESIGN. Analysis only for this round; no large implementation.