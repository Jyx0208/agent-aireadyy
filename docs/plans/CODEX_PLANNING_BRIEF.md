# Codex briefing: joint plan review (analysis only first)

You are **Codex implementer/planner** on model **gpt-5.6-sol** with **high** reasoning.

## Mission

Collaborate on an architecture + implementation plan to turn the proteomics
Discovery product into a **Codex-class autonomous agent**: reliable, flexible,
self-healing, built on **OpenAI Agents SDK** mature patterns — **not** a
one-off fix for a single immunopeptidomics run.

## Hard rules

- Worktree ONLY:
  `E:\ai-agent-already\github-publish\agent-aireadyy\.claude\worktrees\benchmark-review-planning`
- Do **not** reset/clean; do not stage `.env`, `.agent_secrets/`, run bundles, dialogue DBs.
- Do **not** print API keys.
- **No Claude Code. No Gemini.**
- First response is **analysis + plan critique only**.  
  `This is analysis only. Do NOT edit, create, or delete any files. Do NOT write code.`
  until the human/orchestrator says `IMPLEMENT WAVE N`.
- Grok will supervise later implementations; you must not self-declare merge-ready.

## Required reading (read fully before answering)

1. `docs/plans/2026-07-22-autonomous-discovery-agent.md` — primary plan
2. `E:\TEMP\proteomics-discovery-agent-handoff-20260722.md` — failure + root causes
3. `docs/adr/0001-llm-owns-discovery-dialogue.md`
4. `docs/adr/0002-discovery-dialogue-manager-and-option-contracts.md`
5. `docs/discovery-agent-guidance.md`
6. OpenAI Agents SDK docs (fetch current):
   - https://openai.github.io/openai-agents-python/
   - https://openai.github.io/openai-agents-python/multi_agent/
   - https://openai.github.io/openai-agents-python/tools/
   - https://openai.github.io/openai-agents-python/sessions/
   - https://openai.github.io/openai-agents-python/tracing/
   - https://openai.github.io/openai-agents-python/guardrails/
   - https://openai.github.io/openai-agents-python/results/
   - https://openai.github.io/openai-agents-python/handoffs/
7. Code inventory (sample, then expand):
   - `src/agent/control_plane/discovery.py`
   - `src/agent/control_plane/models.py` (audit/repair types)
   - `src/agent/discovery/project_judgment.py`
   - dialogue/discovery agent wiring under `src/agent/web/app.py` (search symbols: Agent, Runner, as_tool, Session)
   - `frontend/benchmark-review/src/grill-tree.ts` (hard_constraint_fields / payload)

## User intent (non-negotiable)

- Fix **generic failure classes** (horizon mismatch, soft→hard, evidence scope,
  dual quality defs, non-convergent repair, dishonest UI), not only immuno 32/0.
- Prefer **mature Agents SDK** orchestration over home-grown agent loops where
  the SDK already solves it.
- Keep fail-closed for true hard conflicts.
- Autonomous error recovery must be a **controller**, not a hopeful re-prompt.
- Result should feel as powerful/flexible as **Codex**: tools + budgets +
  honest progress + stop conditions.

## Deliverable for this planning turn

Write a structured response:

### A. SDK fit assessment
- What we already use correctly vs misuse
- What should move to: agents-as-tools, handoffs, sessions, guardrails, tracing
- What must stay deterministic outside the model

### B. Plan critique of `docs/plans/2026-07-22-autonomous-discovery-agent.md`
- Agree / disagree per section
- Missing pieces for “Codex-like” autonomy
- Over-scoped parts to cut

### C. Recommended architecture decision
Answer plan §8 questions 1–5 with a concrete recommendation and why.

### D. Revised wave plan
- Ordered waves with entry/exit criteria
- Test strategy (fixtures, sacred greens)
- File-level touch list (paths only)

### E. Risks & anti-patterns
Especially: case-specific patches, second strategy writers, fake repair success.

End with:
`PLAN_STATUS: DRAFT_FOR_ORCHESTRATOR` or `PLAN_STATUS: READY_FOR_GROK_AUDIT`

Do not implement until ordered.
