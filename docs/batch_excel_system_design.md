# Batch Excel Report System Design

## Architecture

The batch feature is a parameter-planning pipeline, separate from the full RAW/MSDT execution path. It is designed as a lightweight job layer above the existing PRIDE planning service:

- Web API accepts a list of PRIDE file names and creates a batch entity.
- A background worker runs parameter-only planning in a bounded thread pool.
- Each input gets its own audit directory with `project_resolution.json`, `metadata.json`, `attributes.json`, `decision_trace.json`, `task_state.json`, and `error.json` when needed.
- After all items finish, the existing benchmark exporter summarizes item directories into one `benchmark_results.xlsx`.

The minimal production version uses JSON files as the persistent store. This keeps the local desktop/server deployment simple and preserves the same directory artifacts that are already used for debugging.

## Component Structure

- `src/agent/web/app.py`
  - Batch API endpoints.
  - In-memory batch registry.
  - JSON manifest persistence.
  - Background batch worker.
- `scripts/export_benchmark_excel.py`
  - Existing Excel summarizer reused by the batch worker.
- `src/agent/web/templates/index.html`
  - Batch input panel.
  - One-click run button.
  - Polling status and Excel download button.

## Data Flow

1. Browser posts `inputs`, `submitter`, `jobs`, `fasta_preference`, `ui_language`, and `llm_config` to `/api/batches/parameters`.
2. Server checks the LLM configuration once, creates `runs/_batches/<batch_id>/batch_manifest.json`, and starts a background worker.
3. Worker processes each item with `AgentService.plan_dda_run_from_pride`.
4. Worker writes per-item audit files under `runs/_batches/<batch_id>/items/NNN_<stem>/`.
5. Worker calls `summarize_source` and `write_xlsx` to generate `runs/_batches/<batch_id>/benchmark_results.xlsx`.
6. Browser polls `/api/batches/<batch_id>` and enables the download button when `can_download=true`.

## API Design

### POST `/api/batches/parameters`

Creates a parameter-only batch.

Request:

```json
{
  "inputs": ["sample_a.raw", "sample_b.raw"],
  "submitter": "Alice",
  "jobs": 4,
  "fasta_preference": "llm",
  "ui_language": "en",
  "llm_config": {
    "api_key": "sk-...",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-v4-flash",
    "timeout": "1200"
  }
}
```

Response:

```json
{
  "batch_id": "abc123",
  "status": "queued",
  "item_count": 2,
  "completed_items": 0,
  "failed_items": 0,
  "needs_review_items": 0,
  "jobs": 2,
  "can_download": false,
  "items": []
}
```

### GET `/api/batches/{batch_id}`

Returns public batch state. API keys and model secrets are never returned.

### GET `/api/batches/{batch_id}/download`

Downloads `benchmark_results.xlsx` when ready.

## Database Schema

Current minimal production storage is file-backed JSON:

- `runs/_batches/<batch_id>/batch_manifest.json`
- `runs/_batches/<batch_id>/items/<item>/...`
- `runs/_batches/<batch_id>/benchmark_results.xlsx`

Future SQLite schema can use the same public contract:

```sql
CREATE TABLE batches (
  batch_id TEXT PRIMARY KEY,
  submitter TEXT NOT NULL,
  status TEXT NOT NULL,
  jobs INTEGER NOT NULL,
  ui_language TEXT NOT NULL,
  prefer_project_fasta INTEGER NOT NULL DEFAULT 0,
  output_dir TEXT NOT NULL,
  excel_path TEXT NOT NULL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE batch_items (
  batch_id TEXT NOT NULL,
  item_index INTEGER NOT NULL,
  input_value TEXT NOT NULL,
  status TEXT NOT NULL,
  output_dir TEXT NOT NULL,
  error TEXT NOT NULL DEFAULT '',
  started_at TEXT,
  finished_at TEXT,
  PRIMARY KEY (batch_id, item_index),
  FOREIGN KEY (batch_id) REFERENCES batches(batch_id)
);

CREATE TABLE batch_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id TEXT NOT NULL,
  item_index INTEGER,
  level TEXT NOT NULL,
  message TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (batch_id) REFERENCES batches(batch_id)
);
```

## Cache Strategy

- Parameter-only batches do not download RAW files and do not run Docker.
- PRIDE metadata and LLM planning artifacts are persisted per item for reproducibility.
- Existing PRIDE file cache cleanup remains separate from batch output cleanup.
- Batch concurrency is bounded by `AGENT_MAX_BATCH_JOBS`, default `4`.
- Batch size is bounded by `AGENT_MAX_BATCH_ITEMS`, default `100`.
- API keys remain in memory only and are excluded from manifests and public API responses.

## Production Boundaries

This version is production-minimal for a single desktop/server process. The API and manifest shape are stable enough to migrate later to SQLite plus a durable queue without changing the browser workflow.
