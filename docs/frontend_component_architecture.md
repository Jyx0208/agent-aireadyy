# Frontend Component Architecture

## Component Architecture

The current production-minimal frontend is a single HTML template with inline CSS and JavaScript so the release remains easy to unzip and run. The page is organized as a small component system:

- App shell: light operational dashboard header, language switch, server status, and responsive layout.
- Status cards: high-level queue/history/storage metrics for fast situational awareness.
- Workflow tabs: explicit switch between single-file runs and batch Excel planning.
- Single task panel: one PRIDE file, run mode, FASTA preference, and API settings.
- Batch panel: multiline input, bounded parallelism, batch status, per-item status, and Excel download.
- Run monitor: stepper, live logs, review panel, blocked issues, result download.
- History panel: active single-file tasks, retained single-file results, active batch Excel jobs, and completed batch reports with consistent primary actions.
- Inline alert region: validation and setup problems are announced without browser popups.

## Props Design

The inline `UI` helpers act like small view components. Each helper accepts plain data and returns escaped HTML:

- `UI.emptyState(message)`
  - `message`: human-readable empty/error state text.
- `UI.statusPill(status, label)`
  - `status`: one of `queued`, `running`, `completed`, `failed`, `blocked`, `needs_review`.
  - `label`: visible label. Defaults to `status`.
- `UI.metricCard(label, value, meta)`
  - `label`: short metric name.
  - `value`: primary value.
  - `meta`: secondary explanation.
- `UI.helperText(title, body)`
  - `title`: short leading phrase.
  - `body`: supporting text.
- `UI.actionButton(label, handler, ariaLabel)`
  - `label`: visible button label.
  - `handler`: existing inline click handler string.
  - `ariaLabel`: accessible label for context-specific actions.
- `UI.taskRow(item, options)`
  - `item`: task/history object from the API.
  - `options.meta`: already formatted secondary text.
  - `options.actions`: button HTML.
- `UI.batchItem(item)`
  - `item.input`: PRIDE file name.
  - `item.status`: batch item status.
- `UI.reviewItem(item)`
  - `item.label`, `item.value`, `item.source`, `item.confidence`, `item.conflict`.

All helpers escape user-visible values before rendering.

## Implementation Notes

- The workflow tabs use `role="tablist"`, `role="tab"`, `role="tabpanel"`, `aria-selected`, and `aria-controls`.
- Form controls have labels, invalid states, and focus-visible outlines.
- Live status regions use `role="status"` and `aria-live="polite"`.
- Validation failures use the `formAlert` region with `role="alert"`, `aria-live="assertive"`, and `aria-atomic="true"`.
- Busy actions use `setButtonBusy`, which sets both `disabled` and `aria-busy`.
- The layout collapses from dashboard columns to single-column mobile panels under `1100px`, then tightens spacing under `720px`.
- Batch mode is parameter-only by design, so it avoids RAW downloads, Docker, mzML conversion, and large intermediate outputs.
- Batch Excel jobs are shown in Project History. Active batches reopen the batch panel for tracking; completed batches expose the Excel download action directly.
- Project History is rebuilt from `project_history.json`, `task_history.json` files, and batch manifests. The index is written atomically with a `.bak` fallback so refreshes, restarts, and interrupted updates do not leave the panel empty.

## Usage Examples

Single file:

1. Choose `Single file`.
2. Enter one PRIDE file name.
3. Select `Parameters only` for quick validation or `Full workflow` for execution.
4. Start the task and monitor logs/review issues.

Batch Excel:

1. Choose `Batch Excel`.
2. Paste one file name per line.
3. Pick a small parallel job count, normally `2-4`.
4. Click `Run batch Excel`.
5. Download `benchmark_results.xlsx` when the status turns ready.

History:

1. Use the right-side history list to return to running, failed, blocked, or completed work.
2. Use `Inspect` for failures and `Download` when retained result files are available.
3. Use `Track batch` to reopen an active batch Excel job, or `Download Excel` for a completed report.
