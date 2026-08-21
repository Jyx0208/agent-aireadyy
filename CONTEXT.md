# Domain Glossary

## Canonical workspace

All current development happens in `E:\ai-agent-already\github-publish\agent-aireadyy` on `main`. Historical references to `worktree-benchmark-review-planning` or `.claude/worktrees/benchmark-review-planning` are retired and must not be used as working paths.

## Discovery dialogue

**Dialogue turn** — One user utterance and the Agent's response while shaping a
data-discovery strategy.

**Dialogue action** — The Agent's interpretation of what the current turn is
doing: converse, advise, clarify one decision, revise the strategy, offer the
strategy for confirmation, confirm it, or refuse a premature search.

**Consultation** — A question, comparison, hypothetical, explanation request,
or tentative thought that does not commit the user to a strategy value.

**Commitment** — A user choice that establishes, accepts, replaces, excludes,
opens, clears, or corrects one or more strategy values. A commitment can be
expressed indirectly by referring to a prior proposal.

**Next decision** — The single unresolved scientific trade-off with the highest
expected effect on search scope, ranking, feasibility, or the requested output.
It includes one recommendation and its task-specific reason; it is not the next
item in a fixed questionnaire.

## Strategy

**Strategy** — The current structured, reproducible proposal for discovering
and selecting public proteomics data. It distinguishes hard constraints, soft
preferences, open choices, and evidence that must be retrieved.

**Strategy patch** — The validated delta produced from a commitment. Fields not
present in the delta retain their current values. Consultation never produces
a strategy patch.

**Open constraint** — A meaningful scientific requirement that is not yet a
first-class strategy dimension. It is retained for later search or review
rather than discarded or silently translated into a different constraint.

**Semantic gap** — A missing or contradictory decision that may affect whether
the current strategy is executable. A semantic gap is advisory state, not a
question number and not an instruction about what the Agent must ask next.

**Strategy confirmation** — The user's unambiguous authorization to execute the
complete current strategy. It applies to that strategy version only; any later
strategy patch invalidates it.

## Discovery execution

**Discovery start** — Crossing from strategy dialogue into repository work.
It requires strategy confirmation and remains subject to server safety limits.

**Evidence check** — A repository, metadata, method, or file fact that the
Agent should retrieve rather than ask the user to guess.

**Degraded dialogue** — A turn where the language model is unavailable or its
result is invalid. It may explain the failure and preserve state, but it must
not infer a strategy patch from a phrase list.
