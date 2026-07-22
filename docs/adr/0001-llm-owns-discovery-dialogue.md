# ADR 0001: The LLM Owns Discovery Dialogue Turns

- Status: Accepted
- Date: 2026-07-21

## Context

The discovery assistant was implemented as a fixed Q1-Q10 state machine. The
LLM classified answers and rewrote question wording, while `nextQuestion`,
`answered.Q*`, `applyAnswer`, and automatic defaults decided what happened
next. This produced a questionnaire experience even when prompts asked the
model to behave like an agent.

The product goal is a Codex-style scientific collaborator with a grill-me
skill: it can chat, explain, recommend, update a live strategy, or ask one
dependency-resolving question based on the current scientific problem.

## Decision

The LLM owns each discovery dialogue turn. It returns an explicit action,
optional `update_strategy` tool calls, and an optional next decision with a
recommendation and reasons.

Natural-language approval is represented by a separate `confirm_strategy`
action and is valid only for a strategy already in `awaiting_confirm`. It is
not inferred by a global list of acknowledgement words. A dedicated confirm
button remains a direct, trusted UI event.

The structured strategy remains the source of truth for the strategy card and
discovery payload. A semantic gap validator may report missing information and
validate readiness, but it must not prescribe a fixed question order.

The Q1-Q10 option banks and local parsers remain temporarily as catalogs and
offline fallbacks. They are not the normal online control path.

Starting PRIDE discovery remains a separate, explicitly confirmed action. Both
the frontend and backend enforce confirmation, and server resource ceilings
remain authoritative.

## Consequences

- A turn normally requires one model call and exactly one SDK action tool:
  `respond`, `update_strategy`, or `confirm_strategy`. The tool carries the
  complete response JSON, so no follow-up phrasing call is required.
- Deterministic schema and state-machine validation may reject malformed or
  out-of-context output; no hidden repair call may invent a commitment.
- The strategy card changes only when an `update_strategy` tool event is
  applied; pure conversation leaves it unchanged.
- Scientific guidance can evolve independently of dialogue control flow.
- Backward-compatible response fields are kept during rollout, increasing the
  short-term contract surface.
- Offline fallback may still feel more structured than the online agent, which
  is acceptable as a degraded mode and should be clearly identified in traces.

## Alternatives considered

### Continue refining the fixed decision tree

Rejected because personalized wording does not change who owns turn order.
Additional branches and regular expressions increase maintenance without
providing agent autonomy.

### Remove all structured strategy state

Rejected because the strategy card, reproducible discovery payload, user
confirmation, and server safety checks require a validated structured plan.
