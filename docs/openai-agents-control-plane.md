# OpenAI Agents SDK Control Plane

## Status

This is an experimental P0 control plane for bounded ReAct-style proteomics
dataset discovery. It is additive: the legacy deterministic and agentic
discovery commands remain available and unchanged.

The runtime is pinned and tested with `openai-agents==0.18.1`; this avoids
silent interface or serialized-state drift while the SDK is still on 0.x.

## Architecture

```text
CLI / Web
  -> OpenAI Agents SDK Agent + Runner
  -> typed function tools
  -> control-plane policy and budgets
  -> existing deterministic discovery modules
  -> dataset manifests and audit artifacts
```

The Agents SDK owns model turns and function-tool dispatch. The project owns
scientific state, artifact paths, budgets, safety policy, and idempotency in a
separate SQLite `AgentRunStore`.

## P0 Tools

```text
search_repository_datasets
get_discovery_state
```

No download, conversion, shell, full workflow, model training, or biological
override tool is exposed in P0.

## State And Audit

Each run writes:

```text
agent_control.sqlite
agents_discovery_summary.json
agents_discovery_events.json
agents_discovery_report.md
round_01/dataset_manifest.json
round_02/dataset_manifest.json (when justified)
dataset_manifest.json (selected compatibility manifest)
```

Repository search calls use an idempotency key derived from the run ID, tool
name, and canonical arguments. Repeating an identical repository search
reuses the stored result instead of executing the same operation again. State
inspection calls are budgeted and recorded as events, but are not cached.

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
streaming varies, so P0 uses strict function-tool schemas but derives the final
run status deterministically from persisted artifacts.

Tracing is off by default:

```text
AGENT_OPENAI_AGENTS_TRACING=0
```

Enable it only after reviewing whether model and tool payloads may be exported
to the configured trace processor.

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
  --max-rounds 3 `
  --max-turns 8 `
  --output-dir runs/discovery/agents_sdk_smoke
```

Install the optional runtime outside Docker with:

```powershell
pip install -e ".[agents-sdk,dev,web]"
```

## Web UI

Start the Web service and open `http://127.0.0.1:8000`, then select:

```text
Dataset discovery -> Execution -> OpenAI Agent
```

The page exposes search-round, model-turn, and tool-call budgets. It reuses
the existing asynchronous Discovery job log and manifest tables, and adds the
Agent conclusion, warnings, blockers, stop reason, and audit downloads.

The Agent runtime uses the API configuration entered in the browser for that
run when one is provided. The key is kept only in the active request and is not
written to discovery results, logs, downloads, or persisted job status. When
the browser does not provide a key, configure `AGENT_LLM_API_KEY`,
`AGENT_LLM_BASE_URL`, and `AGENT_LLM_MODEL` in the server environment before
starting the Web process.

## Next Stages

1. Run shadow comparisons against legacy one- and two-round discovery.
2. Add recovery tools with explicit approval for expensive actions.
3. Persist and resume Agents SDK `RunState` for approval interruptions.
4. Add model-gap analysis while keeping split, leakage, and exporters
   deterministic.

Official SDK references:

- https://openai.github.io/openai-agents-python/agents/
- https://openai.github.io/openai-agents-python/tools/
- https://openai.github.io/openai-agents-python/human_in_the_loop/
- https://openai.github.io/openai-agents-python/models/
