# Frontend Component Architecture

## Overview

The UI is a single-page operational console built from one HTML template, inline CSS, and inline JavaScript. This keeps deployment simple while still providing a production-grade workflow surface.

## Component Model

### App Shell

- page header
- language toggle
- server health summary
- responsive content layout

### Workbench Area

- single-file task panel
- batch Excel panel
- preflight summary
- run control state

### Runtime Area

- stepper
- live logs
- review panel
- blocked issue list
- result download controls

### History Area

- active tasks
- retained results
- batch jobs
- download actions

## Reusable UI Helpers

The template uses small render helpers instead of a framework. Each helper takes plain data and returns escaped HTML.

- `UI.statusPill(status, label)`
- `UI.metricCard(label, value, meta)`
- `UI.helperText(title, body)`
- `UI.emptyState(message)`
- `UI.taskRow(item, options)`
- `UI.batchItem(item)`
- `UI.reviewItem(item)`

## Accessibility

- labeled form controls
- `role="tablist"` / `role="tab"` / `role="tabpanel"`
- `role="status"` for passive updates
- `role="alert"` for validation failures
- `aria-busy` for long-running actions
- focus-visible states on interactive controls

## Responsive Behavior

- desktop: two-column operational layout
- tablet: stacked workbench and history sections
- mobile: single-column layout with compact controls

## Interaction Model

### Single-file flow

1. Select repository and run mode.
2. Run preflight.
3. Submit task.
4. Monitor logs and review state.
5. Download the ZIP if the run is complete.

### Batch flow

1. Paste one file per line.
2. Select repository, mode, and resource policy.
3. Run preflight.
4. Launch the batch.
5. Watch per-item progress and download the Excel report.

## Production Notes

- Batch mode is intentionally lightweight by default.
- Parameter-only mode avoids large downloads.
- Preflight runs before the expensive operations start.
- The history panel is rebuilt from disk-backed manifests so refreshes do not erase status.

