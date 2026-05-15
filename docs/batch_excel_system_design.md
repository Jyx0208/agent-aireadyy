# Batch Excel System Design

## Purpose

Batch mode is a lightweight parameter-audit pipeline. It is not the full execution path. Its job is to plan many inputs in parallel, write audit artifacts, and export one Excel report for benchmark review.

## Design Goals

- keep memory and disk usage bounded
- avoid RAW and mzML payload downloads by default
- preserve per-item auditability
- make the result easy to review in Excel
- keep the UI responsive while batch jobs are running

## Architecture

```text
batch input
  -> preflight
  -> batch manifest creation
  -> bounded worker pool
  -> per-item planning / prepare / full execution
  -> per-item audit files
  -> Excel aggregation
  -> batch ZIP packaging
```

## Modes

### Parameters only

Plan metadata and search parameters only.

### Prepare

Prepare the MSDT-Converter input package for each item.

### Full

Run the full workflow for each item.

## Server-Side Components

- `src/agent/web/app.py`
  - batch API
  - batch state
  - worker dispatch
  - audit export
- `scripts/export_benchmark_excel.py`
  - Excel summarizer
- `src/agent/web/templates/index.html`
  - batch form
  - mode selection
  - preflight feedback
  - result download

## Data Flow

1. The user submits `inputs`, `submitter`, `repository`, `run_mode`, `resource_policy`, `jobs`, and `llm_config`.
2. The server runs preflight before creating the batch.
3. A batch manifest is written under `runs/_batches/<batch_id>/`.
4. Each worker item gets an isolated output directory.
5. The worker writes audit files after each item finishes.
6. The exporter converts all item directories into `benchmark_results.xlsx`.

## Public API Contract

### `POST /api/batches/parameters`

Creates a batch job.

### `GET /api/batches/{batch_id}`

Returns public status and item progress.

### `GET /api/batches/{batch_id}/download`

Downloads the Excel report when ready.

### `GET /api/batches/{batch_id}/audit`

Downloads the audit ZIP.

## Persistent Artifacts

- `batch_manifest.json`
- `benchmark_results.xlsx`
- `batch_parameter_audit.zip`
- per-item `parameter_audit.json`
- per-item `decision_trace.json`
- per-item `task_state.json`

## Why This Is Safe for Large Batch Runs

- No raw payload download in parameter mode
- Docker is optional by mode
- Worker concurrency is capped
- Disk-space checks happen before the batch starts
- API keys are not written to public history
- Existing single-file execution logic is reused instead of duplicated

## Reporting Fields

The Excel report is designed for benchmark comparison. Its primary columns are:

- Input file
- Project
- MS_methods
- Species
- Organism part
- Modification
- Digestion
- Instrument

Supporting columns include workflow, FASTA, parameter overrides, and blocking issues.

## Operational Recommendation

For a benchmark study, use:

- `parameters` for correctness verification
- `prepare` for input-package verification
- `full` only when you need execution outputs

