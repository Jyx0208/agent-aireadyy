# OpenAI Agents SDK Control Plane

## Status

This is the quality-first v2 control plane for bounded ReAct-style proteomics
dataset discovery. `multi_agent` is now the deployment default. The Workflow
path remains temporarily available as a benchmark baseline; it must not be
removed until the replacement gate has passed on real paired PRIDE runs.

The runtime is pinned and tested with `openai-agents==0.18.1`; this avoids
silent interface or serialized-state drift while the SDK is still on 0.x.

## Architecture

```text
CLI / Web
  -> OpenAI Agents SDK Discovery Manager
  -> OpenAI Agents SDK Budget Agent (nested review)
  -> typed candidate-search and candidate-inspection tools
  -> query-bound grants and staged quality budget
  -> deterministic governor and hard ceilings
  -> deterministic PRIDE adapters, validity rules, and manifest builders
  -> dataset manifests and audit artifacts
```

The Discovery Manager proposes materially different searches, chooses depth
per query, browses a persistent candidate pool, chooses accessions for detailed
inspection, evaluates observations, and selects the final manifest. The
Budget Agent independently reviews marginal value and returns `grant`,
`shrink`, `replan`, or `stop`; it cannot invent or execute queries. The
deterministic governor validates every decision, enforces hard ceilings, and
issues one-use grants bound to the approved query hash.

The Agents SDK owns model turns and function-tool dispatch. The project owns
scientific state, artifact paths, policy, metering, idempotency, and grants in a
separate SQLite `AgentRunStore`. Repository requests are metered immediately
before HTTP dispatch, so retries and pagination consume the hard request limit.

## Discovery Tools

```text
single_agent:
  search_repository_candidates
  inspect_repository_candidates
  get_discovery_state
  select_discovery_manifest

multi_agent:
  request_search_budget
  search_repository_candidates_with_grant
  inspect_repository_candidates
  get_discovery_state
  select_discovery_manifest

Budget Agent:
  submit_budget_decision
```

The dynamic protocol is:

```text
SearchProposal
  -> Budget Agent review
  -> deterministic validation and hard-limit check
  -> query-bound, one-use SearchGrant
  -> candidate search and metered observation
  -> targeted candidate inspection
  -> next proposal or final manifest selection
```

No download, conversion, shell, full workflow, model training, or biological
override tool is exposed to the discovery agents.

## State And Audit

Each run writes:

```text
agent_control.sqlite
agents_discovery_summary.json
agents_discovery_events.json
agents_discovery_report.md
agents_discovery_budget.json
round_01/dataset_manifest.json
round_02/dataset_manifest.json (when justified)
dataset_manifest.json (selected compatibility manifest)
```

Repository search calls use an idempotency key derived from the run ID, tool
name, and canonical arguments. Repeating an identical repository search
reuses the stored result instead of executing the same operation again. State
inspection calls are budgeted and recorded as events, but are not cached.

The public event stream records actor, activity summary, evidence references,
tool lifecycle, budget decisions, grants, counters, warnings, and stop reasons.
Raw reasoning deltas are discarded. Visible reasoning is a concise public
evidence summary, not hidden model chain-of-thought.

## Provider Configuration

The current project defaults to an OpenAI-compatible Chat Completions model so
the same path can be tested with the existing DeepSeek configuration:

```text
AGENT_LLM_API_KEY
AGENT_LLM_BASE_URL
AGENT_LLM_MODEL
```

`OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `OPENAI_DEFAULT_MODEL` are accepted as
fallbacks. Provider support for strict structured outputs and tool-call
streaming varies, so the runtime uses strict function-tool schemas but derives the final
run status deterministically from persisted artifacts.

Tracing is off by default:

```text
AGENT_OPENAI_AGENTS_TRACING=0
```

Enable it only after reviewing whether model and tool payloads may be exported
to the configured trace processor.

Both agents share the configured OpenAI-compatible model by default, including
DeepSeek-compatible endpoints. `budget_model` can be supplied programmatically
for evaluation, but no second provider key is required for normal operation.

## Rollout And Hard Limits

Server deployment values are configured in `.env` and passed through Compose:

```text
AGENT_DISCOVERY_MODE=multi_agent
AGENT_MAX_MODEL_TURNS=50
AGENT_MAX_TOOL_CALLS=100
AGENT_INITIAL_QUERY_UNITS=12
AGENT_EXPANDED_QUERY_UNITS=30
AGENT_MAX_QUERY_UNITS=60
AGENT_INITIAL_REPOSITORY_REQUESTS=80
AGENT_EXPANDED_REPOSITORY_REQUESTS=160
AGENT_MAX_REPOSITORY_REQUESTS=300
AGENT_MAX_ELAPSED_SECONDS=1800
AGENT_BUDGET_AGENT_MAX_TURNS=3
```

Model turns and tool calls are safety ceilings. In `multi_agent` mode, the
Discovery Manager and Budget Agent decide query count, query depth, candidate
inspection, and whether measured quality gaps justify moving from initial to
expanded or maximum-quality capacity. Query units, actual repository requests,
elapsed time, and Budget Agent turns remain non-negotiable hard limits.

## Safety Policy

```text
read_only       -> automatic within budget
bounded_write   -> automatic only for registered artifact operations
expensive       -> human approval required and budgeted
biological      -> human review required
forbidden       -> denied
```

Unknown tools and arbitrary command execution are denied. The existing
external model adapter is not exposed to the discovery agent.

## Command

```powershell
python -m agent.cli agents-discover-dataset `
  --prompt "Find human phosphoproteomics DDA data for RT prediction" `
  --repository pride `
  --task-type rt_prediction `
  --discovery-mode multi_agent `
  --max-query-units 60 `
  --max-repository-requests 300 `
  --max-elapsed-seconds 1800 `
  --budget-agent-max-turns 3 `
  --output-dir runs/discovery/agents_sdk_smoke
```

Legacy scripts may continue using `--max-rounds`, `--max-turns`, and
`--max-tool-calls`. `--max-rounds` controls `single_agent` search rounds and is
ignored by dynamic search allocation in `multi_agent`; model-turn and tool-call
values remain safety ceilings in both modes.

## Replacement Evaluation Gate

The v1 equal-two-round comparison produced 0 wins, 3 ties, and 0 losses. It
proved lower resource use but no quality improvement. That result motivated v2:
the Agent now controls candidate search depth and inspection instead of only
supplying query text to the same fixed selection pipeline.

The v2 benchmark is implemented by
`scripts/run_discovery_replacement_benchmark.py` and
`benchmarks/discovery_replacement_scenarios.v2.json`. It compares Workflow with
Agent 1x, 2x, and max-quality tiers across structured, clear, vague, and
ambiguous variants. The primary gate uses graded relevance, nDCG@5,
high-relevance recall, task readiness, evidence completeness, hard-constraint
violations, and false early stops. Cost is a hard ceiling and secondary metric,
not a reason to accept lower quality. See
`docs/discovery-agent-v2-quality-first.md`.

## Legacy Replay Budget Gate

The release gate is implemented by
`scripts/evaluate_dynamic_discovery_budget.py`. It pairs single-Agent and
multi-Agent artifacts by the eight replay IDs in
`tests/fixtures/dynamic_budget_replays.json`. Each replay directory must contain
both `agents_discovery_summary.json` and `dataset_manifest.json`; missing or
malformed evidence is an evaluation error rather than a zero-quality run.

```powershell
python scripts/evaluate_dynamic_discovery_budget.py `
  --baseline-dir runs/discovery/eval_single_agent `
  --dynamic-dir runs/discovery/eval_multi_agent `
  --output runs/discovery/dynamic_budget_evaluation.json
```

The gate requires at least 95% usable recall, at least 20% repository-request
reduction, a false early-stop rate below 5%, no per-replay valid-share
regression, and zero hard-constraint violations. Exit code `0` means the gate
passed, `1` means complete evidence failed a target, and `2` means evaluation
inputs were missing or malformed.

Local core verification on 2026-07-11 completed with `749 passed in 138.28s`.
The release-gate command currently exits `2` because the repository does not
yet contain the required eight paired baseline and dynamic replay directories.
This legacy gate measures budget efficiency only and does not establish
Workflow replacement quality.

Install the optional runtime outside Docker with:

```powershell
pip install -e ".[agents-sdk,dev,web]"
```

## Web UI

Start the Web service and open `http://127.0.0.1:8000`, then select:

```text
Dataset discovery -> Execution -> OpenAI Agent
```

The page does not ask the user to design a search budget. It shows the
server-configured autonomous budget status and live counters, reuses the
existing asynchronous Discovery manifest tables, and adds activity, tools, and
raw-event log tabs plus the Agent conclusion, warnings, blockers, stop reason,
and audit downloads.

The Agent runtime uses the API configuration entered in the browser for that
run when one is provided. The key is kept only in the active request and is not
written to discovery results, logs, downloads, or persisted job status. When
the browser does not provide a key, configure `AGENT_LLM_API_KEY`,
`AGENT_LLM_BASE_URL`, and `AGENT_LLM_MODEL` in the server environment before
starting the Web process.

## Next Stages

1. Complete real paired PRIDE runs and pooled relevance review for v2.
2. Tune search strategy from failure clusters without weakening hard limits.
3. Remove the Workflow product path only after the replacement gate passes.
4. Persist and resume Agents SDK `RunState` for approval interruptions.
5. Add model-gap analysis while keeping split, leakage, and exporters
   deterministic.

Official SDK references:

- https://openai.github.io/openai-agents-python/agents/
- https://openai.github.io/openai-agents-python/tools/
- https://openai.github.io/openai-agents-python/human_in_the_loop/
- https://openai.github.io/openai-agents-python/models/
