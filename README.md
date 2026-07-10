# Task-aware AI-ready Data Agent

Task-aware AI-ready Data Agent is a proteomics data-discovery and AI-ready dataset-building agent. It can search for task-fit public or local proteomics data, score readiness and data value, run or reuse search workflows, build AI-ready task tables, and generate reproducible dataset recipes with split, leakage, hard-benchmark, curation, and model-loop smoke reports.

The current handoff version is a **small/medium real-data v1 smoke release**, not a claim of arbitrary large-scale one-click reproduction or full model-training closure.

## Current Status

The final protected benchmark and handoff evidence are preserved under:

```text
runs/_protected_benchmark_20260624_4_5_sample_pool
```

Protected pool summary:

```text
files: 1074
size: about 1506 MB
keep marker: .agent_keep
```

The protected pool contains:

```text
samples/
batches/
agent_runs/
ai_ready_builds/
model_loop/
reports/
```

Key candidates in the v3 smoke benchmark:

```text
PXD079076: clean completed / TMT10 / MSDT + AI-ready parquet
PXD027067: usable partial output / partial-output recovery
PXD079072: blocked / spectrum or export mismatch
PXD074954: drug treatment / phospho discovery parameters evidence
PXD077080: HLA / immunopeptidomics discovery parameters evidence
```

## Main Capabilities

- General dataset discovery from natural language or structured form input.
- Task-aware readiness and data-value scoring.
- PRIDE-first online route, plus MassIVE and iProX repository-aware smoke paths.
- Batch parameters/full/partial workflow handoff.
- AI-ready task table export for:
  - `rt_prediction`
  - `fragment_intensity_prediction`
  - `psm_scoring`
  - `denovo`
  - `ptm_denovo`
  - `chimeric_interpretation`
- Leakage-aware dataset recipe generation.
- Hard benchmark, evidence graph, and active curation queue.
- Dry-run model-loop smoke and model-informed gap plan.
- Web UI with simplified AI-ready Build flow:

```text
Input source -> Task and build -> Results and next step
```

Low-frequency debug tools are under Advanced sections.

## Quick Start

Recommended Docker path:

```powershell
# From the repository root:
docker compose up -d web
```

Open:

```text
http://127.0.0.1:8000
```

Health check:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/api/health
```

CLI inside Docker:

```powershell
docker compose exec web python -m agent.cli check-runtime
```

## OpenAI Agents SDK Discovery (Experimental)

The project includes an opt-in OpenAI Agents SDK control plane for bounded
ReAct-style repository discovery. The existing `discover-dataset` path remains
unchanged. `single_agent` preserves the original fixed-round behavior;
`multi_agent` adds a Discovery Manager and a separate Budget Agent so search
depth is decided from observed marginal value inside server-enforced ceilings.

```powershell
docker compose exec web python -m agent.cli agents-discover-dataset `
  --prompt "Find human phosphoproteomics DDA data for RT prediction" `
  --repository pride `
  --task-type rt_prediction `
  --discovery-mode multi_agent `
  --max-query-units 30 `
  --max-repository-requests 200 `
  --max-elapsed-seconds 1200 `
  --output-dir runs/discovery/agents_sdk_smoke
```

In multi-Agent mode, each proposed search batch must be reviewed by the Budget
Agent. Its `grant`, `shrink`, `replan`, or `stop` decision is validated by the
deterministic governor. An issued grant is query-bound and single-use. The
runtime still cannot download files, run shell commands, start a full search
workflow, train a model, or change biological constraints.

Each run writes a SQLite run ledger, public structured events, per-round
manifests, the selected compatibility manifest, and
`agents_discovery_budget.json`. The visible activity log contains concise
evidence summaries and tool outcomes, not raw hidden model chain-of-thought.
See `docs/openai-agents-control-plane.md`.

The same runtime is available in the Web UI under `Dataset discovery` by
switching `Execution` from `Workflow` to `OpenAI Agent`. The page can use the
API key entered for that run, or fall back to server environment variables.
Browser-supplied keys are kept only for the active request and are not written
to discovery results, logs, or downloads. Search allocation is autonomous in
the Web UI; operators configure only hard ceilings through server environment
variables. The activity, tools, and raw-event tabs expose the public audit
stream without displaying hidden chain-of-thought.

## Reproduce the Protected Benchmark

See the full reproduction guide:

```text
docs/README_reproduction.md
```

Current v3 expected outputs:

```text
runs/ai_ready_builds/mini_e2e_batch_20260624_protected_pool_v3
runs/ai_ready_builds/dataset_recipe_20260624_protected_pool_v3
runs/model_loop/model_loop_20260624_protected_pool_v3
```

Protected copies are also under:

```text
runs/_protected_benchmark_20260624_4_5_sample_pool/ai_ready_builds/
runs/_protected_benchmark_20260624_4_5_sample_pool/model_loop/
```

Expected v3 summary:

```text
runs: 5
completed: 2
blocked: 3
selected task outputs: 4
excluded task outputs: 6
split strategy: file_disjoint
split counts: train 2 / val 2
leakage status: passed
hard benchmark rows: 10
curation queue rows: 10
model-loop status: completed
RT rows: 636
smoke score: 0.7165
```

## Handoff Documents

- `docs/final_agent_capability_report.md` - concise capability report for PPT and final handoff.
- `docs/known_limitations_and_next_stage.md` - current boundaries and next-stage priorities.
- `docs/README_reproduction.md` - protected v3 benchmark and Web/CLI reproduction guide.
- `docs/README.md` - documentation index for handoff.
- `docs/docker_reproducibility.md` - Docker reproducibility notes.
- `docs/model-adapter-metrics.md` - model adapter metric schema.
- `runs/_protected_benchmark_20260624_4_5_sample_pool/reports/final_delivery_checklist.md` - goal-by-goal closure audit.
- `runs/_protected_benchmark_20260624_4_5_sample_pool/reports/web_smoke/web_ui_smoke_report.md` - browser-level Web UI smoke evidence.

## Verification

Recent Docker targeted regression:

```text
tests/test_dataset_recipe.py
tests/test_model_loop.py
tests/test_web_ai_ready.py
tests/test_frontend_template.py
selected public-results / cleanup tests

68 passed
```

Recovery / harness / data-scientist loop regression:

```text
tests/test_agentic_recovery.py
tests/test_mini_e2e.py
tests/test_mini_e2e_batch.py
tests/test_agent_recovery.py
tests/test_agent_harness.py
tests/test_data_scientist_loop.py
tests/test_guidance_alignment.py

59 passed
```

Focused local command:

```powershell
python -m pytest tests/test_frontend_template.py
```

Docker targeted command:

```powershell
docker compose exec web python -m pytest tests/test_dataset_recipe.py tests/test_model_loop.py tests/test_web_ai_ready.py tests/test_frontend_template.py
```

## Repository Notes

Current repository maturity:

```text
PRIDE: mature online-first path
MassIVE: adapter / discovery v1 / smoke path
iProX: index-first path using refresh-iprox-index and local JSONL cache
```

iProX is not treated as real-time online search. Refresh an index first:

```powershell
python -m agent.cli refresh-iprox-index --help
```

## Known Limits

- The protected v3 benchmark is a small/medium smoke, not a large-scale training corpus.
- Model-loop is currently dry-run / metric-schema smoke, not real model training.
- MassIVE and iProX need more real-project validation before matching PRIDE maturity.
- RAW/WIFF-like conversion and nativeID mismatch remain separate compatibility work.
- Parameters-only candidates are discovery evidence only; they need search outputs before AI-ready Build can export training rows.
