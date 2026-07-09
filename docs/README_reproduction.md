# AI-ready Data Agent Reproduction Guide

This guide freezes the current small/medium smoke path for the AI-ready Data Agent closure milestone. It is intentionally conservative: it uses protected local evidence, avoids large downloads by default, and treats blocked/partial runs as first-class evidence rather than forcing every candidate into a completed training table.

## Scope

This reproduction guide covers:

- Web UI smoke for the simplified AI-ready Build flow.
- CLI smoke for the protected 4-5 sample benchmark pool.
- Recipe, split, leakage, hard benchmark, curation queue, and model-loop smoke.
- Known current limits for PRIDE, MassIVE, iProX, RAW/WIFF-like files, and full workflow execution.

It does not claim that large-scale public one-click training data generation is complete.

## Environment

Recommended runtime:

```text
Docker all-in-one image: pride-agent-all-in-one-local:latest
Working directory inside container: /app
Python: 3.11+ recommended for the full test suite
```

Local Windows Python 3.9 can run some frontend/template tests, but the full web test suite expects newer Python APIs such as `datetime.UTC`.

Start Web:

```powershell
docker compose up -d web
```

Open:

```text
http://127.0.0.1:8000
```

Full workflow remains explicit. Enable only when Docker Desktop or Docker Engine is ready and disk usage is acceptable:

```powershell
$env:AGENT_WEB_FULL_WORKFLOW_ENABLED = "1"
```

## Protected Benchmark Pool

The current protected benchmark pool is:

```text
runs/_protected_benchmark_20260624_4_5_sample_pool
```

It contains `.agent_keep`, so the Web cleanup worker should not remove it during the normal 1800-second result cleanup.

Current expected protected summary:

```text
files: 1074
size: about 1506 MB
keep marker: .agent_keep
```

Protected evidence folders:

```text
samples/
batches/
agent_runs/
ai_ready_builds/
model_loop/
reports/
```

Benchmark report:

```text
runs/_protected_benchmark_20260624_4_5_sample_pool/reports/benchmark_sample_pool_4_5.md
```

Readiness/value/recovery calibration notes:

```text
runs/_protected_benchmark_20260624_4_5_sample_pool/reports/readiness_value_recovery_calibration_notes.md
```

## Current Benchmark Candidates

The protected v3 smoke uses five real candidates:

```text
PXD079076: clean completed / RT 14 / de novo 10
PXD027067: usable partial output / RT 622 / de novo 622
PXD079072: blocked / spectrum mismatch or export empty
PXD074954: parameters evidence / needs_search_results
PXD077080: HLA parameters evidence / needs_search_results
```

Interpretation:

- `PXD079076` proves a clean completed route with MSDT and AI-ready parquet.
- `PXD027067` proves usable partial-output recovery.
- `PXD079072` is retained as blocked/review evidence.
- `PXD074954` proves general drug-treatment discovery to Batch parameters.
- `PXD077080` proves HLA/immunopeptidomics discovery to Batch parameters.

Parameters-only candidates must not be treated as training data. They should stay blocked for AI-ready Build until search results and peaklists exist.

## CLI Smoke

Run from the repository root:

```powershell
pwd
```

The already generated v3 outputs are:

```text
runs/ai_ready_builds/mini_e2e_batch_20260624_protected_pool_v3
runs/ai_ready_builds/dataset_recipe_20260624_protected_pool_v3
runs/model_loop/model_loop_20260624_protected_pool_v3
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
threshold: 0.75
```

To regenerate the batch validation from protected evidence, use the protected run and batch item directories. Keep output IDs unique if you do not want to overwrite the current v3 outputs:

```powershell
python -m agent.cli validate-agent-runs-ai-ready-batch `
  --agent-run-dir runs/_protected_benchmark_20260624_4_5_sample_pool/agent_runs/PXD079076_20190404_TMT10_rebuild_20260624 `
  --agent-run-dir runs/_protected_benchmark_20260624_4_5_sample_pool/agent_runs/sk_BNWTTS2_C6_160307__20260611-174303__9395af9f `
  --agent-run-dir runs/_protected_benchmark_20260624_4_5_sample_pool/agent_runs/Xinyi3_-80__20260611-172915__55420074 `
  --agent-run-dir runs/_protected_benchmark_20260624_4_5_sample_pool/batches/3521c0b58415/items/001_CM_phospho_ReN_18plx_Aug2024_Fr21_C_C_000632 `
  --agent-run-dir runs/_protected_benchmark_20260624_4_5_sample_pool/batches/d5bb54a5e26a/items/001_P1039 `
  --task-type rt_prediction `
  --task-type denovo `
  --output-dir runs/ai_ready_builds/mini_e2e_batch_<new_id>
```

Then regenerate recipe/split/leakage/curation:

```powershell
python -m agent.cli make-dataset-recipe `
  --batch-dir runs/ai_ready_builds/mini_e2e_batch_<new_id> `
  --output-dir runs/ai_ready_builds/dataset_recipe_<new_id> `
  --split-strategy auto
```

Then run model-loop smoke:

```powershell
python -m agent.cli run-dataset-model-loop `
  --recipe-dir runs/ai_ready_builds/dataset_recipe_<new_id> `
  --task-type rt_prediction `
  --mode smoke `
  --output-dir runs/model_loop/model_loop_<new_id>
```

## Web Smoke

Use the simplified AI-ready Build UI as three stages:

```text
1. Input source
2. Task and build
3. Results and next step
```

Recommended smoke route:

1. Open the AI-ready Build tab.
2. Choose `From Batch run` or `From existing AI-ready output`.
3. Use a protected run or existing v3 output path.
4. Pick `rt_prediction` or `denovo`.
5. Run the smallest useful build validation.
6. Generate recipe/split.
7. Run model-loop smoke.

Low-frequency and debugging controls should stay under Advanced:

- Manual TSV/MGF paths.
- Locate inputs / locate agent run.
- Real smoke tools.
- Repository audit inputs.
- Metric adapter and external metrics file.

## Discovery Smoke

General Discovery is the default route. Use it when the request is not a pre-defined PTM task:

```text
Discovery target: General data search
Task readiness: choose the downstream task to evaluate
Species: leave empty unless there is a real user constraint
```

Examples already validated:

- HLA / immunopeptidomics general discovery -> Batch parameters.
- Drug treatment / kinase inhibitor general discovery -> Batch parameters.

Use `PTM-enriched data` only when the discovery target itself is PTM-enriched data. In that case, `PTM type` supports multiple selections.

## Repository Notes

Current repository maturity:

```text
PRIDE: mature online-first path
MassIVE: adapter / discovery v1 / smoke path
iProX: index-first path using refresh-iprox-index and local JSONL cache
```

iProX cannot be treated like real-time PRIDE search in the current implementation. Refresh an index first:

```powershell
python -m agent.cli refresh-iprox-index --help
```

If no iProX index is available, the expected blocker is:

```text
iprox_index_missing
```

## Verification

Targeted frontend verification:

```powershell
python -m pytest tests/test_frontend_template.py
```

Expected on the current local Windows Python 3.9 environment:

```text
30 passed
```

Cleanup protection tests should be run in the Docker/Python 3.11 environment:

```powershell
python -m pytest `
  tests/test_web.py::test_cleanup_results_preserves_protected_validation_directories `
  tests/test_web.py::test_cleanup_results_removes_expired_process_directories `
  tests/test_web.py::test_cleanup_results_keeps_only_four_latest_downloadable_runs
```

The cleanup worker is expected to skip:

- `_batches`
- configured protected result directories
- any top-level run directory containing `.agent_keep`

## Known Limits

1. The protected v3 benchmark is a small/medium smoke, not a large-scale training corpus.
2. Model-loop is currently dry-run / smoke-oriented and does not represent full closed-loop model training.
3. MassIVE and iProX are not yet validated to the same level as PRIDE.
4. RAW conversion and WIFF-like mzML nativeID / scan mismatch remain separate compatibility work.
5. Full workflow inside Windows Docker Desktop depends on correct nested Docker path mapping and local disk capacity.
6. Parameters-only candidates are discovery evidence only; they need search outputs before AI-ready Build can export training rows.

## Current Closure Criteria

The current milestone is considered ready for demo/review when all of the following remain true:

- AI-ready Build Web UI stays three-stage, with debug/manual controls under Advanced.
- Protected benchmark pool contains `.agent_keep` and the five v3 candidates.
- v3 batch validation has two completed candidates and three blocked/review candidates.
- Recipe status is `ready`.
- Leakage status is `passed`.
- Hard benchmark and curation queue are generated from real evidence.
- Model-loop smoke completes and reports failure/gap information.
- Remaining limitations are documented rather than hidden.
