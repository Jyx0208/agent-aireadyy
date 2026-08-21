# Swarm: Science-Semantics Fix Plan (no deployment-boundary work)

## Room
`science-fix-plan`

## Out of scope (boss order)
- Deployment boundary / auth / Docker socket / bind address hardening

## In scope (must plan complete fixes)
1. Discovery: multi-seed queries, candidate-level hard-constraint coverage, stop rules, no high-relevance fallback abuse
2. Hard constraints: no silent drop on normalize; fail-closed / clarify
3. Release predicates: ready/completed/build-ready require leakage+artifacts; zero-row not completed
4. RT/PSM scientific contracts (units, confidence metrics, target/decoy, FDR)
5. Status semantics: weak_ready not task-ready; unknown defaults; no silent model defaults into user intent
6. Download atomic+checksum; durable job state (not memory-only authority)
7. Dialogue: keep compound write + scientific grill; do not weaken scientific gates for UX

## Baseline
- Review: agent_aireadyy_extreme_review_2026-07-24.md (main @ 546b8cc)
- Repo: E:/ai-agent-already/github-publish/agent-aireadyy/.claude/worktrees/benchmark-review-planning
- Prefer OpenAI Agents SDK; deterministic control plane owns publication truth

## Protocol
- Post to chatroom with prefix: R1 / R2 / R3 / SYNTHESIS / BOSS
- CLAIM area before deep file claims
- Round 1: diagnose + file map + proposed predicates
- Round 2: critique others; find holes/conflicts
- Round 3: converge on single implementable plan (file paths, APIs, tests, non-goals)
- Boss reviews; if REJECT, more rounds

## Deliverable
Single plan doc: `docs/plans/SCIENCE_SEMANTICS_MASTER_PLAN.md` with:
- Problem → root cause → design → files → tests → acceptance → risks
- Ordered work packages WP-A.. for implementation later
