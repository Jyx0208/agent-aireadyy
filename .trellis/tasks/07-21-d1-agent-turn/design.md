# D1 Design: LLM-Owned Discovery Dialogue

## Ownership inversion

Current control is `nextQuestion -> pending Q -> LLM rephrase -> applyAnswer`.
The new control is `LLM action -> tool events -> reducer -> validator -> UI`.

```text
User message
  -> grill-turn agent (history + strategy snapshot + semantic gap report + guidance)
  -> action + assistant_message + tool_calls + optional next_decision
  -> frontend applies validated update_strategy calls
  -> strategy card renders the resulting IntentSpec
  -> validator reports remaining semantic gaps for the next agent turn
```

## API contract

The primary response contract is:

```json
{
  "action": "chat|advise|clarify|update_strategy|ready_to_confirm|confirm_strategy|refuse_search",
  "assistant_message": "...",
  "tool_calls": [
    {"name": "update_strategy", "arguments": {"species": ["human"]}}
  ],
  "next_decision": {
    "focus": "species",
    "question": "...",
    "recommendation": {"id": "human_prefer", "label": "...", "reason": "..."},
    "options": [{"id": "...", "label": "...", "reason": "..."}],
    "allow_free_text": true
  },
  "gap_report": {
    "required_missing": [],
    "optional_missing": [],
    "ready_for_confirm": false
  }
}
```

Legacy `intent`, `extra_fields`, `advance`, and `answer_text` remain temporarily
as compatibility projections. They are not the source of turn ownership.

## Frontend boundary

A single typed decoder/reducer owns unknown API data. Rendering code receives
typed `AgentTurn` and must not parse raw fields independently.

`update_strategy` is the only normal mutation path. The reducer validates the
tool name and patch shape, applies `mergeLlmFields(..., "patch")`, reinforces
explicit user numbers, derives the objective, and clears confirmation.

The patch mechanism is field-generic. Example utterances are used in tests but
must not become production string branches. New scientific language should be
handled by model reasoning as long as it maps to the existing strategy schema;
new schema fields require a contract change, not another phrase detector.

Local free-text parsing remains an offline safety fallback only when the agent
request fails. It must not silently compete with a successful agent tool call.

The contract generalizes by **operation and field**, not by vocabulary. The
model decides whether an utterance is consultation, commitment, replacement,
clear/unset, correction, referential acceptance, or confirmation. A typed
schema validator then accepts only supported values and a generic reducer
applies the resulting delta. Examples live in tests only. Constraints that do
not yet have a first-class field are retained in notes/open risks so new
scientific language does not disappear; adding a new rendered field still
requires a schema evolution, not a new sentence detector.

## Gap validator

`assessStrategyGaps(IntentSpec)` uses semantic slot names, not Q progress:
task, horizon, species, acquisition, coverage, theme, labeling, and objective.
It reports gaps to the agent and checks readiness, but never chooses the next
question. Existing Q option banks can provide fallback choices by focus.

## Confirmation boundary

For typed natural language, the UI sets `confirmed=true` only after the agent
returns `confirm_strategy` while the phase is `awaiting_confirm`. A dedicated
confirmation button is already an unambiguous UI event and may confirm without
language classification. The backend rejects every discovery-start route
without `grill_confirmed: true`. Defaults only update the card and transition
to `awaiting_confirm`.

The primary turn uses one OpenAI Agents SDK action tool. `respond` handles
non-mutating conversation, `update_strategy` carries a committed delta, and
`confirm_strategy` carries eligible approval. Multi-clause or multi-field
updates receive one bounded, independent SDK semantic-verifier pass. That
verifier can canonicalize existing values and recover omitted open-ended
scientific themes/constraints, but every field needs an exact latest-message
evidence span and the verification record is returned in the public API. It
cannot silently add unrelated first-class defaults. If the verifier is
unavailable, the validated primary tool delta remains usable and the verifier
failure is exposed instead of falling back to phrase rules.

SDK dialogue memory and the live card share one lifecycle: memory persists
across turns in a mounted conversation, while full page reload and Restart both
start a fresh session. This prevents an old SDK session from claiming a card
update that the newly initialized frontend does not contain.

## Compatibility and rollout

- Keep the existing `IntentSpec` and job payload to avoid rewriting discovery.
- Add the D1 contract alongside legacy fields.
- Prefer D1 handling when `action` or `tool_calls` is present.
- Fall back to the existing local parser if the LLM is unavailable.
- Keep legacy question helpers until the new path is verified, then remove
  mandatory pending-question state in a later cleanup.

## 2026-07-22 authority and orchestration amendment

The production failure proved that `target_fields` cannot authorize a numeric
option. New options therefore carry a prevalidated `strategy_patch`; selection
applies exactly that stored patch. `target_fields` is derived from option
patches and used only for display and decision-memory identity.

The user-facing Dialogue Manager remains the sole proposal writer. The
semantic verifier is now a read-only critic: deterministic code discards any
critic field absent from the Manager proposal or any value that differs from
the Manager proposal. It can never be renamed to `update_strategy`.

Following the OpenAI Agents SDK manager pattern, a Proteomics Scientific
Planning Advisor is exposed with `Agent.as_tool(parameters=...)`. Its bounded
context is passed explicitly because nested tool Agents do not inherit parent
conversation state. The Manager resumes after the specialist and must finish
with `respond`, `update_strategy`, or `confirm_strategy`; no handoff occurs.

The server also projects a dynamic critical agenda. It does not select a fixed
question order, but confirmation is impossible while common high-impact items
remain unresolved. Search scale is a blocker for executable runs unless quota
is explicitly open-ended. Training tasks additionally block on acquisition
compatibility and biological generalization.

Completion is not a session reset. A later turn retains the same card,
history, pending decision, and decision memory; mutation invalidates the old
confirmation. Restart is the sole full-reset action.
