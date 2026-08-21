# D1 Implementation Plan

1. Add failing API tests for the D1 turn envelope, no tool call on chat, valid
   immunopeptide guidance, and backend confirmation rejection.
2. Add frontend contract tests for decoding, explicit tool-driven strategy
   patches, chat no-op behavior, and personalized next decisions.
3. Implement the backend D1 response normalization and prompt contract while
   preserving legacy response fields.
4. Add a semantic gap report and typed frontend agent-turn reducer.
5. Route the chat UI through the agent-turn reducer. Demote Q1-Q10 pending
   questions to offline fallback and option catalog.
6. Add repository runtime guidance for proteomics recommendations and inject it
   into grill-turn context.
7. Make defaults show confirmation only; enforce confirmation at the server.
8. Run targeted tests after each slice, then the full frontend suite/build and
   targeted/full backend suite as practical.
9. Run two-axis code review, address findings, and prepare a scoped commit plan.
10. Run a real-model semantic matrix across conversation, hypothetical advice,
    replacement, correction, clear/open, compound updates, historical
    references, unmapped scientific constraints, and contextual confirmation.
    Audit production files to ensure the probe vocabulary did not become
    normal-path branches.
11. Add immutable option patches and a regression for build-training choices
    that must not imply plan-only.
12. Revoke semantic-critic write/recovery authority and deterministically
    reject critic overreach.
13. Add the read-only scientific Advisor as an Agents SDK agent-tool, plus a
    task-aware critical decision agenda with search scale as an executable-run
    blocker.
14. Preserve dialogue/session/card state after completed and failed work;
    strengthen confirmation fingerprint tests across button and language paths.

## Rollback points

- D1 fields are additive; frontend can temporarily fall back to legacy intent
  and extra_fields if decoding fails.
- Existing `IntentSpec`, strategy card rendering, and discovery execution stay
  intact, limiting rollback to turn routing and response normalization.
