# Implementation Plan: Carbon Industrial Operations Workbench

## Delivery rule

All work is developed and validated as one release candidate. Internal work
packages may be completed in dependency order, but no partial backend or
frontend is delivered to the user. The existing deployed product remains in
service until the full release gate passes.

## 0. Protect the baseline

- [ ] Record the current commit, branch, running job identifiers, service
      process, database files, task JSON files, and managed artifact roots.
- [ ] Review the six currently modified source/test files line-by-line and
      classify each hunk as required behavior, obsolete experiment, or conflict
      with this redesign.
- [ ] Preserve user and prior-agent changes; do not reset, delete, or overwrite
      them. Integrate required behavior into the replacement architecture.
- [ ] Run the current focused backend/frontend tests to establish a truthful
      baseline and record existing failures separately.
- [ ] Capture representative fixtures from the existing 765-candidate run
      without modifying the live task.

Rollback point: no product change; planning and baseline evidence only.

## 1. Add and verify mature dependencies

- [ ] Add compatible bounded dependencies:
  - SQLAlchemy 2.x
  - Alembic
  - Huey with SQLite storage
  - sse-starlette
  - TanStack Query
  - TanStack Virtual
- [ ] Verify licenses, Python 3.13/React 19 compatibility, Windows import and
      startup behavior.
- [ ] Pin versions through the project's existing dependency workflow and
      update the lock file.
- [ ] Add a dependency smoke test that opens SQLite, runs Alembic metadata,
      enqueues a Huey immediate-mode test task, creates an SSE response, and
      mounts the React providers.

Validation:

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest tests\test_operations_dependencies.py -q
npm --prefix frontend\benchmark-review run build
```

Rollback point: dependency-only commit can be removed without data migration.

## 2. Build the operations database foundation

- [ ] Create a deep `agent.operations` module rather than adding more storage
      logic to `src/agent/web/app.py`.
- [ ] Define SQLAlchemy models and Pydantic API projections for:
  - jobs
  - job_terms
  - project_reviews
  - file_records
  - job_events
  - batches and batch_files
  - assets
  - history_entries
  - deletion_requests and deletion_results
- [ ] Configure one SQLAlchemy Engine for `operations.sqlite` with WAL,
      foreign keys, busy timeout, and connection health settings.
- [ ] Add Alembic with SQLite batch migration configuration and named
      constraints.
- [ ] Add the indexes declared in `design.md`.
- [ ] Implement repository/query modules with keyset pagination; API and UI
      code must not construct SQL.
- [ ] Add transaction, concurrent reader/writer, uniqueness, foreign-key,
      pagination, query-plan, backup, and recovery tests.

Validation:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m pytest tests\operations -q
```

Rollback point: restore the pre-migration SQLite backup; JSON jobs remain
untouched.

## 3. Centralize the authoritative state machine

- [ ] Move the existing discovery execution reducer out of `app.py` into a
      typed domain state module.
- [ ] Define the legal transition table for queued, searching, reviewing,
      finalizing, completed, blocked, failed, cancelled, and interrupted.
- [ ] Make snapshot update and event append one database transaction.
- [ ] Add compare-and-swap/version checks so stale writers cannot overwrite
      newer state.
- [ ] Persist cancellation intent, Worker heartbeat, term position, review
      batch state, resumability, and terminal reason.
- [ ] Reject and audit illegal transitions.
- [ ] Ensure top-level status and phase are one projection and cannot produce
      an unexplained `running + failed`.

Validation:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\operations\test_state_machine.py tests\operations\test_event_transactions.py -q
```

Rollback point: new state module remains unused by production entry points.

## 4. Add durable Huey execution

- [ ] Configure a separate `queue.sqlite` through `SqliteHuey`.
- [ ] Create a standalone Huey consumer entry point with four thread workers.
- [ ] Wrap discovery execution, per-term orchestration, review batches,
      reconciliation, cleanup, and retry as idempotent tasks.
- [ ] Keep required search-term order while allowing four project-review slots.
- [ ] Persist checkpoint and heartbeat before/after every external operation.
- [ ] Check cancellation between bounded external operations.
- [ ] Configure bounded retry/backoff by failure class; scientific rejection is
      never treated as an infrastructure retry.
- [ ] Add crash/restart, duplicate delivery, timeout, cancellation, and
      no-progress tests.
- [ ] Add Windows service definitions for Web and Huey consumer using a mature
      service wrapper selected during implementation verification.

Validation:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\operations\test_worker.py tests\operations\test_recovery.py -q
```

Rollback point: consumer remains disabled; existing deployed execution path is
unchanged.

## 5. Replace discovery APIs and add SSE

- [ ] Split operations routes/services out of the monolithic `app.py`.
- [ ] Add typed lightweight endpoints for:
  - job snapshot
  - terms
  - project reviews
  - project evidence
  - files
  - batches
  - paged events
  - cancellation and resume
- [ ] Implement the event stream with `sse-starlette`, sequence IDs,
      keepalive, disconnect handling, and `Last-Event-ID`.
- [ ] Keep the database event sequence authoritative across Web/Worker process
      boundaries.
- [ ] Enforce response-size limits and bounded page sizes.
- [ ] Add API contract, reconnect, duplicate suppression, gap recovery,
      terminal event, slow-client, and cancellation tests.
- [ ] Remove full job log/result payloads from normal polling APIs.

Validation:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_operations_api.py tests\test_operations_sse.py -q
```

Rollback point: new API is not exposed by the deployed frontend.

## 6. Build indexed history and lifecycle reconciliation

- [ ] Import existing history records into `history_entries` idempotently.
- [ ] Classify empty failed stubs, missing directories, orphan storage,
      corrupt manifests, development records, and valid results.
- [ ] Preserve all existing physical files during import.
- [ ] Make history list/filter/sort/pagination database-only.
- [ ] Reuse the approved 500-file batch, deletion preview, managed-root safety,
      and source cleanup contracts from the lifecycle task.
- [ ] Add periodic reconciliation and cleanup-suggestion tasks through Huey.
- [ ] Record deletion requests, per-target outcomes, actual released bytes,
      and audit tombstones.
- [ ] Verify that physical deletion always requires explicit confirmation.

Validation:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_history_index.py tests\test_storage_lifecycle.py -q
```

Rollback point: restore the history database backup; original index and task
directories remain available.

## 7. Replace the frontend shell and server-state layer

- [ ] Install and configure TanStack Query at the application root.
- [ ] Replace top-level workflow Tabs with Carbon Header, SideNav, Content,
      Grid, and stable routes/views.
- [ ] Add typed query keys and API hooks for snapshots, terms, projects, files,
      batches, history, and events.
- [ ] Integrate EventSource updates into the query cache using event sequence
      checks.
- [ ] Add Skeleton, InlineLoading, InlineNotification, and background
      notifications according to Carbon patterns.
- [ ] Remove page-level `overflow: hidden`, viewport-height cages, and nested
      scroll ownership.
- [ ] Preserve current single-file, batch, AI-ready, settings, and navigation
      behaviors inside the new shell.

Validation:

```powershell
npm --prefix frontend\benchmark-review test
npm --prefix frontend\benchmark-review run build
```

Rollback point: old frontend build remains the deployed artifact.

## 8. Build the Carbon current-task console

- [ ] Preserve Agent conversation as the pre-run primary view.
- [ ] Switch to the current-task console when a job starts or is restored.
- [ ] Add sticky job header, status Tag, heartbeat, elapsed time, Stop/Resume.
- [ ] Use ProgressIndicator for business phases and ProgressBar for known
      quantities.
- [ ] Add local task views:
  - overview
  - search terms
  - review queue
  - deliverable files
  - batches
  - structured events
  - task conversation
- [ ] Render terms/projects/files with Carbon DataTable and Pagination.
- [ ] Render long event feeds with TanStack Virtual only above the threshold.
- [ ] Use the right panel only for selected project evidence and system-level
      auxiliary content.
- [ ] Show four review Worker slots and their actual project assignments.
- [ ] Display metadata, SDRF, file evidence, scores, reasons, retries, timing,
      and failure scope through progressive disclosure.

Validation:

```powershell
npm --prefix frontend\benchmark-review test
npm --prefix frontend\benchmark-review run build
```

Browser checks:

- 1280×720, 1440×900, 1920×1080 and narrow layouts;
- mouse wheel, touchpad, PageUp/PageDown, Home/End and keyboard focus;
- refresh and active-task restore;
- SSE disconnect/reconnect;
- Stop/Resume;
- project evidence details;
- 500-file batch handoff.

## 9. Remove replaced runtime paths

- [ ] Remove high-frequency discovery polling as the primary update path.
- [ ] Remove whole-job JSON rewrite from normal event handling.
- [ ] Keep terminal portable exports and legacy read-only importer only.
- [ ] Remove old discovery context rail/main progress layout and obsolete CSS.
- [ ] Remove unpaged history rendering and directory scans from request paths.
- [ ] Break remaining `app.py` responsibilities into bounded modules.
- [ ] Search for duplicate state parsing, raw payload casts, obsolete constants,
      and old API consumers.
- [ ] Preserve approved behavior from the six pre-existing modified files before
      deleting replaced code.

Validation:

```powershell
rg -n "setTimeout\\(poll|delay\\(1000|_persist_discovery_job|history-list.*map" frontend src
```

Expected matches must be explicitly reviewed rather than assumed to be zero.

## 10. Migration, scale, and recovery gate

- [ ] Create deterministic fixtures for:
  - 1,000 history tasks
  - 765 candidate projects
  - 20,000 file records
  - 10,000 job events
- [ ] Verify history API p95 ≤ 1 second and interactive shell ≤ 2 seconds in
      the target environment.
- [ ] Verify snapshot ≤ 100 KB and normal event ≤ 16 KB.
- [ ] Verify live event visibility within 1 second on the LAN.
- [ ] Verify no long list blocks the browser main thread for more than 100 ms.
- [ ] Kill Web and Worker independently during search, review, finalization,
      deletion, and migration; verify honest recovery.
- [ ] Rehearse migration twice against a copied real data directory and compare
      counts, checksums, batches, and openability.
- [ ] Rehearse complete rollback to the previous deployment.

## 11. Full quality gate

- [ ] Run backend focused tests.
- [ ] Run complete backend test suite.
- [ ] Run frontend unit tests and production build.
- [ ] Run browser acceptance flows.
- [ ] Run migration/recovery/performance suites.
- [ ] Run Carbon accessibility, keyboard, zoom, and reduced-motion checks.
- [ ] Review all PRD acceptance criteria and attach evidence.
- [ ] Run `trellis-check` before release preparation.

Commands:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
npm --prefix frontend\benchmark-review test
npm --prefix frontend\benchmark-review run build
```

## 12. Single production cutover

- [ ] Announce and enter the maintenance window.
- [ ] Prevent new task creation and checkpoint active tasks.
- [ ] Back up task JSON, manifests, operations/control-plane SQLite files,
      history indexes, configuration, and the previous frontend build.
- [ ] Deploy the one complete release candidate.
- [ ] Run Alembic migration and idempotent importer.
- [ ] Start Web and Huey Windows services.
- [ ] Run health, history, SSE, discovery, Stop/Resume, review, and batch smoke
      tests.
- [ ] Expose the new Carbon workbench only when every smoke check passes.
- [ ] On any blocking failure, stop new services, restore backups, and restart
      the previous build.

## Final handoff

Deliver only after:

- all PRD acceptance criteria pass;
- no required work remains behind a feature flag;
- old and new UI are not exposed as a permanent dual product;
- migration and rollback evidence is recorded;
- source changes are committed intentionally and the release is reproducible.

