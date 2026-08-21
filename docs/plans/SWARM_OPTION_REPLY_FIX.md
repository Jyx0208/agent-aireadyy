# Swarm: Option reply + soft preference write-card fix

## Room
`option-reply-fix`

## Bug (user repro)
1. Agent shows options (1 优先 label-free soft, 2 只要 hard, 3 不限)
2. User says `label-free, 仪器不限` → no verifiable strategy update
3. User says `1` → semantic-verification **rejected**:
   - "latest user message is '1' ... no strategy commitments"
   - model proposed labeling_strategy/labeling_hard/instrument_preference but critic rejects bare `1`

## Root cause (boss)
- Server already has `_resolve_discovery_pending_selection` + `_discovery_selected_option_strategy_patch`
- Should force option's predeclared `strategy_patch` and skip verifier via `selected_option_patch_is_scoped`
- Failures when: (a) frontend `pending_decision` missing/malformed strategy_patch on options, (b) scoped gate too strict so verifier still runs on bare "1", (c) NL soft preference not grounded

## Goals
1. Bare `1`/`2`/`3` (or exact option label) **must** apply option.strategy_patch and write card; **must not** re-verify against bare digit text
2. If option lacks strategy_patch, synthesize safe patch from option id/label or fail with clear Chinese message — never empty "no verifiable change"
3. NL `label-free` / `仪器不限` should update labeling soft + instrument_preference=none when possible (compound soft path)
4. Keep fail-closed for inventing hard fields not on the selected option
5. Tests for option index path + verifier skip

## Files
- `src/agent/web/app.py` — grill turn option resolution / verifier skip
- `frontend/benchmark-review/src/CarbonAgentChat.tsx` — pending_decision wiring
- `frontend/benchmark-review/src/agent-turn.ts` — option strategy_patch decode
- `tests/test_discovery_agent_turn.py`

## Non-goals
- Deploy auth, discovery search redesign

## Protocol
CLAIM → implement → DONE; S verifies with pytest
