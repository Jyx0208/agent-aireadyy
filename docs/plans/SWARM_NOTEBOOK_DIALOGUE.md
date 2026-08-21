# Swarm: Notebook-style dialogue agent (DESIGN ONLY)

## User ideal (non-negotiable)
1. User chats freely (including chitchat) — never scary contract errors on non-mutating turns
2. Agent extracts informative commitments from full dialogue context and writes the strategy card via tools
3. Later corrections overwrite earlier card fields (latest user intent wins)
4. Agent asks only about real gaps the user did not cover — not a fixed questionnaire
5. Once the task is clear enough, agent calls update_strategy itself (prompt + tools), not form wizard
6. After discovery fail/success, same conversation can continue: view results / revise card / search again — not dead-end
7. Keep thin gates only: confirm before PRIDE search; no fake build-ready; do not silently drop user hard asks
8. Prefer fewer hard rules; agent-led; OpenAI Agents SDK stays

## Out of scope
- Deploy auth / docker socket
- Full discovery retrieval redesign (portfolio/CEM already separate)
- Rewriting batch status bar

## Room
`notebook-dialogue-plan`

## Rounds
- R1: diagnose current grill-turn vs ideal; file map; failure modes
- R2: critique peers; resolve conflicts (verifier vs trust, free notes vs schema, fail recovery UX)
- R3: single implementable master plan → `docs/plans/NOTEBOOK_DIALOGUE_MASTER_PLAN.md`

## Deliverable must include
- Target conversation state machine (chat vs write-card vs running vs failed-recoverable)
- Prompt principles (notebook agent)
- Tool contract (update_strategy / confirm) — thin
- What to delete/weaken (menu-first, SV on bare option index, chitchat failure copy)
- Free-form intent storage (notes / constraints) if card fields insufficient
- Post-discovery recovery UX (buttons + phase behavior)
- Migration/tests/non-regression (option `1`, compound write, L1 batch seed)
- Explicit non-goals

## Boss review bar
Any hand-wavy “just prompt better” without file paths, or reintroducing thick questionnaire, or killing confirm-before-search, or allowing fake green → REJECT and re-discuss.
