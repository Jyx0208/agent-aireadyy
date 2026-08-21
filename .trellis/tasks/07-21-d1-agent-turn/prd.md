# LLM-owned discovery dialogue

## Goal

Make the PRIDE discovery assistant behave like a Codex-style scientific agent:
the model chooses whether to chat, advise, ask one high-value question, update
the live strategy, or offer confirmation. The fixed Q1-Q10 questionnaire must
not own turn order.

## Requirements

- The LLM owns the dialogue action for every turn.
- Scientific guidance and current strategy state inform the LLM, but do not
  prescribe a fixed question sequence.
- A strategy card changes only through an explicit `update_strategy` tool
  event. Chat and explanation turns do not mutate it.
- The agent may ask one personalized decision question with a recommendation,
  reasons, and optional choices. Free text is always accepted.
- The former decision tree is retained only as a semantic gap validator,
  option catalog, and offline fallback.
- A user can revise any strategy field at any point before confirmation.
- Natural-language confirmation is also an agent-owned decision. It may produce
  `confirm_strategy` only while the current strategy is awaiting confirmation;
  a generic acknowledgement during chat or clarification must not start work.
  A dedicated confirmation button remains a trusted, explicit UI event.
- Natural-language examples such as fish, DIA, or 15 projects are acceptance
  probes, not phrase-specific branches. The normal online path must generalize
  through model reasoning and a field-generic strategy patch contract.
- Ontologies, keyword rules, and regular expressions may validate or recover a
  failed/offline turn, but must not be the primary source of supported intents.
- Clearing, opening, replacing, correcting, and referring back to a prior
  proposal use the same field-generic patch path. A scientifically meaningful
  constraint that has no first-class field must be preserved as an open
  constraint/note rather than discarded or turned into a phrase-specific
  branch.
- Immunopeptide exploration must not default to PTM de novo. A safe initial
  recommendation is human-prioritized, browse-only, curated around 20 projects,
  while de novo, PSM scoring, and RT prediction remain possible downstreams.
- PRIDE discovery may start only after explicit user confirmation. This gate
  must be enforced by the backend as well as the UI.
- Existing server runtime, disk, round, quota, and concurrency ceilings remain
  authoritative.
- New turn fields should be backward compatible during migration so the
  existing UI can roll over without breaking older responses.

## Acceptance Criteria

- [ ] `POST /api/discovery/grill-turn` returns an agent-owned action, explicit
      tool calls, an optional next decision, and a gap report.
- [ ] A pure chat/advice turn with no constraint change produces no
      `update_strategy` tool call and leaves the card unchanged.
- [ ] A natural-language revision can patch any supported strategy field in
      the same turn, including species, count, acquisition, task, theme,
      labeling, coverage, horizon, and objective.
- [ ] A multi-field revision using terms not present in the motivating examples
      is applied through the same generic reducer without adding a new branch.
- [ ] The frontend no longer uses `pendingQuestionRef` / `nextQuestion` as the
      normal driver of each turn; these remain fallback-only.
- [ ] The agent can choose a personalized next decision and show one
      recommendation with a short reason.
- [ ] Mentioning immunopeptide and asking for a recommendation yields a
      scientifically valid exploratory path, not default PTM de novo.
- [ ] Defaults fill the strategy and show confirmation; they do not start a
      discovery run automatically.
- [ ] A discovery request without explicit confirmation is rejected by the
      backend.
- [ ] Free-text confirmation is accepted only through an explicit agent
      `confirm_strategy` result in `awaiting_confirm`; acknowledgements in other
      phases are harmless.
- [ ] A semantic black-box matrix covers consultation, hypothetical questions,
      replacement, correction, clearing, unrestricted values, compound
      updates, historical references, unmapped scientific constraints, and
      confirmation without adding those phrases to production branches.
- [ ] Frontend tests, typecheck/build, and targeted backend tests pass.

## 2026-07-22 production regression requirements

- [ ] A dynamic option is executable only through a schema-validated patch
      declared when the option is created. A later numeric/id/label selection
      cannot widen that patch through model-authored `target_fields`.
- [ ] Selecting “构建训练集” cannot silently set `run_horizon=plan_only` unless
      that exact option contract explicitly declares it.
- [ ] The semantic critic is read-only. It may veto or narrow a Manager patch,
      but cannot add/change fields, confirm, or become `update_strategy`.
- [ ] A read-only Proteomics Scientific Planning Advisor is available as an
      Agents SDK `Agent.as_tool()` specialist with explicitly supplied bounded
      context; the Dialogue Manager retains the user conversation.
- [ ] Executable discovery cannot become confirmable until search scale is
      explicit or explicitly open-ended. Training tasks also require explicit
      acquisition compatibility and biological generalization decisions.
- [ ] Continuing after plan-only, completed, failed, or blocked work preserves
      task, strategy, history, pending decision, and decision memory. Only an
      explicit Restart creates an empty session.
- [ ] `ready_to_confirm` and `update_strategy` never trigger Discovery. Only a
      current-fingerprint `confirm_strategy` in `awaiting_confirm`, or the
      trusted button projected through the same reducer, may start it.

## Notes

- Public seams agreed with the user: agent-turn API, frontend turn reducer,
  and server confirmation boundary.
