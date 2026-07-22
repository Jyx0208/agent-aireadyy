# ADR 0002: One Dialogue Manager, Read-Only Specialists, and Executable Options

- Status: Accepted
- Date: 2026-07-22
- Supersedes the multi-writer parts of ADR 0001

## Context

The first LLM-owned dialogue implementation still allowed two semantic actors
to influence a strategy write. The user-facing Manager proposed a patch, then
a separately run “semantic verifier” could repair, replace, or even recover a
missing patch. Short replies such as `1` were especially fragile because the
second Agent did not inherit the Manager conversation automatically.

A production session exposed the deeper issue. The Manager offered
“构建训练集”, but the dynamic decision declared `run_horizon` as a target even
though that option did not mean “只做计划”. On the following numeric selection,
the model wrote `run_horizon=plan_only`. The UI later treated the resulting card
as an explicitly confirmed plan and rotated the conversation, losing the
already selected de novo task and other constraints.

The OpenAI Agents SDK distinguishes two relevant orchestration patterns:

- **Agents as tools**: a Manager retains the conversation and calls bounded
  specialists;
- **handoffs**: another Agent becomes active and owns the rest of the turn.

The SDK also documents that an Agent used as a tool does not automatically
inherit the parent conversation state; required state must be passed
explicitly. Sessions preserve dialogue state, while tracing records workflow
events; neither is a business authorization boundary.

Official references:

- <https://openai.github.io/openai-agents-python/multi_agent/>
- <https://openai.github.io/openai-agents-python/tools/#agents-as-tools>
- <https://openai.github.io/openai-agents-python/sessions/>
- <https://openai.github.io/openai-agents-python/tracing/>

## Decision

### 1. One user-facing Manager is the only proposal writer

The Dialogue Manager owns the conversation and is the only Agent allowed to
propose `update_strategy` or `confirm_strategy`. A deterministic server boundary
then performs:

```text
Manager proposal
  -> schema validation
  -> option-scope / evidence / version validation
  -> atomic strategy event
```

A critic may accept, reject, or narrow a Manager proposal. It may not add a
field, change a value, rename its tool to `update_strategy`, confirm a strategy,
or start Discovery. If the Manager needs semantic repair, the Manager must
produce a new proposal; the critic never becomes a second writer.

### 2. Scientific specialists are Agents-as-tools, not handoffs

The Dialogue Manager may call a read-only Proteomics Scientific Planning
Advisor through `Agent.as_tool()`. The specialist receives an explicit bounded
context containing the current strategy, unresolved critical agenda, user
message, and decision memory. It returns structured analysis, task-specific
critical decisions, repository evidence to retrieve, and scientific risks.

The specialist does not speak directly to the user and cannot mutate the card.
The Manager combines its output with the conversation and then finishes with
one public action tool. Handoffs are reserved for a user-visible transition to
an independent workbench after a server-approved phase change.

### 3. Every rendered option is an executable contract

Each new `next_decision.options[]` entry carries a validated
`strategy_patch`. `target_fields` is derived from those patches and is only a
display/memory projection.

When a user selects an option by number, exact id, or exact label, the server
applies exactly the stored option patch. A later model call may explain the
choice and plan the next question, but it cannot add defaults or reinterpret
the selected option. Mixed patched/unpatched menus are invalid. All-unpatched
menus remain readable only as a rollout compatibility shape.

### 4. Critical agenda is dynamic but readiness is deterministic

The Agent is not forced through a Q1-Q10 order. A server-generated agenda
prioritizes unresolved choices that materially affect feasibility, scientific
validity, runtime, or review cost. The Manager can still chat, answer a
question, accept a compound instruction, or ask a more relevant personalized
question.

For every executable search, project scale must be explicit or explicitly
open-ended. For training tasks, acquisition compatibility and biological
generalization are also critical. Optional labeling or instrument preferences
cannot displace unresolved critical items. Repository facts are retrieved by
tools rather than asked of the user.

### 5. Confirmation and session lifecycle are separate

`ready_to_confirm` and `update_strategy` only offer confirmation. Search can
start only after a `confirm_strategy` event in `awaiting_confirm`, bound to the
current nonempty strategy fingerprint. A trusted confirm button creates the
same typed event and passes the same reducer and fingerprint checks.

Completion or failure is a checkpoint in the same scientific conversation.
Continuing to chat or revising the strategy preserves the session, history,
decision memory, and card. A strategy change invalidates the old confirmation.
Only explicit Restart creates an empty strategy and a new session.

### 6. OpenAI-compatible providers use a fail-closed action compatibility path

Some OpenAI-compatible providers may ignore `tool_choice="required"` and
return ordinary assistant prose. Plain prose never becomes mutation authority.
The runtime first treats it as non-mutating, then requests one bounded JSON
action contract from the same Dialogue Manager. The server synthesizes an
equivalent typed event only after validating the action and patch schema; the
normal commitment, semantic, option-scope, version, and confirmation gates
still run.

The read-only critic has a separate JSON compatibility contract when its SDK
verification tool is unavailable. In omission mode it returns
`candidate_findings` rather than a strategy patch. Those findings can trigger
one bounded Manager retry but can never write the card themselves. This keeps
provider compatibility separate from business authority and also allows short
utterances such as one topic, organism, method, or quantity to be audited
without vocabulary-specific parsing.

## Consequences

- Numeric and short option replies are deterministic and auditable.
- A model-authored `target_fields` list can no longer authorize unrelated
  fields such as `run_horizon=plan_only`.
- The scientific Advisor improves task-specific questioning without taking
  over the conversation or becoming another mutation authority.
- Multi-clause free-text updates still depend on the Manager's semantic
  proposal, but the read-only critic can fail them closed.
- Some turns can use an extra nested model call when scientific consultation
  is materially useful; greetings, direct edits, exact option selections, and
  confirmations stay on the one-call path.
- Application audit records expose the selected option contract, discarded
  model fields, specialist output, semantic-critic result, strategy version,
  and confirmation fingerprint without exposing hidden chain-of-thought.
- Provider compatibility recovery is explicit in the audit record; it cannot
  silently convert prose or critic findings into a strategy mutation.
