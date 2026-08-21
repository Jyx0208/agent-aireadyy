# Technical Design: Carbon Industrial Operations Workbench

## 1. Design intent

The product remains a single-server Windows application. The redesign replaces large JSON files and high-frequency full-response polling with:

1. a transactional SQLite operational database;
2. an indexed append-only event stream;
3. a durable Windows-compatible task queue;
4. SSE updates backed by database sequence numbers;
5. server-side pagination and filtering;
6. a Carbon-first operations UI.

Scientific discovery, review, file-selection, and 500-file publication rules remain unchanged.

## 2. Adopt / extend / build matrix

| Capability | Decision | Mature solution / existing owner | Project-owned code |
|---|---|---|---|
| UI design language | Adopt | Carbon React 1.112, Carbon Grid/tokens/patterns | Domain-specific composition only |
| REST server state | Adopt | TanStack Query | Query keys and typed API adapters |
| Very long lists | Adopt selectively | TanStack Virtual | Carbon-compatible row renderer |
| Relational access | Adopt | SQLAlchemy 2.x | Repository interfaces and domain queries |
| Schema migrations | Adopt | Alembic, SQLite batch migrations | Reviewed migration scripts |
| Transactional store | Extend | SQLite WAL already used by control plane | Unified operations schema |
| Background queue | Adopt | Huey `SqliteHuey`, thread workers on Windows | Idempotent discovery/review task functions |
| Realtime transport | Adopt | `sse-starlette` | Typed event serialization and authorization boundary |
| Validation | Extend | Existing Pydantic models | Snapshot/event API models |
| Run/event projection | Extend | Existing `_project_discovery_execution_state` reducer and control-plane events | One authoritative transition table |
| Repository project/file search index | Extend | Existing `RepositoryIndex` SQLite store | Migration into or attachment to operations queries |
| Raw assets and exports | Extend | Managed filesystem and existing manifests | Indexed asset ownership and lifecycle receipts |
| Scientific decisions | Build | Existing discovery/scoring/publication domain | Required product-specific logic |

### Why these choices

- SQLAlchemy provides established transaction, connection, statement, and schema abstractions; Alembic owns versioned migrations, including SQLite batch-mode table recreation.
- Huey provides SQLite-backed task persistence, retry, scheduling, priority, expiration, locks, and thread workers. Its documentation explicitly notes that process workers are unavailable on Windows, so the deployment uses thread workers. PRIDE, SDRF, and model calls are primarily I/O-bound.
- `sse-starlette` owns protocol formatting, keepalive, disconnect detection, cancellation, and graceful shutdown.
- TanStack Query owns caching, cancellation, retry/backoff, invalidation, and request deduplication. TanStack Virtual is used only for event feeds or unusually long variable-height lists; normal business tables use Carbon DataTable with server-side pagination.

## 3. Runtime architecture

```text
Browser
  Carbon UI Shell
  TanStack Query + EventSource
        │ REST snapshots / paged queries
        │ SSE events by sequence
        ▼
FastAPI web service
  typed API models
  operations query service
  sse-starlette endpoint
        │
        ├──────────────► operations.sqlite (SQLAlchemy + Alembic)
        │                   jobs / terms / reviews / files
        │                   events / batches / assets / history
        │
        └──────────────► managed artifact filesystem

Huey consumer Windows service
  4 thread workers for discovery/review I/O
  periodic reconciler and retention tasks
        │
        ├──────────────► queue.sqlite (Huey-owned)
        └──────────────► operations.sqlite + artifact filesystem
```

The queue database is separate from `operations.sqlite` to reduce write-lock coupling and keep queue internals owned by Huey.

## 4. Operational schema

### 4.1 `jobs`

One authoritative row per executable job.

Key fields:

- `job_id` primary key
- `kind`
- `status`
- `phase`
- `version`
- `last_event_sequence`
- `idempotency_key`
- `strategy_fingerprint`
- `active_term_index`
- `term_count`
- `candidate_count`
- `reviewed_project_count`
- `pending_review_count`
- `active_review_batch_size`
- `review_workers`
- `worker_heartbeat_at`
- `cancel_requested_at`
- `resumable`
- `terminal_reason`
- `created_at`, `started_at`, `updated_at`, `finished_at`

Indexes:

- `(status, updated_at DESC)`
- `(kind, status, updated_at DESC)`
- unique partial/normal index on `idempotency_key`
- `(strategy_fingerprint, created_at DESC)` for user-initiated history lookup only

### 4.2 `job_terms`

One row per confirmed search term:

- `(job_id, term_index)` primary key
- normalized and submitted term
- Carbon-facing role label
- status and failure reason
- page/chunk count
- raw result count
- new candidate count
- reviewed project count
- exhausted flag
- timing fields

Indexes:

- `(job_id, term_index)`
- `(job_id, status, term_index)`

### 4.3 `project_reviews`

One row per job/project pair:

- `(job_id, project_accession)` primary key
- first-seen term and all matched terms
- queue status and Worker slot
- metadata/SDRF/file-inspection status
- scientific decision, score, confidence, reason summary
- candidate and review timestamps
- evidence bundle reference

Indexes:

- `(job_id, review_status, queued_at)`
- `(job_id, decision, project_accession)`
- `(project_accession)`

### 4.4 `file_records`

One row per job/project/file identity:

- stable file identifier
- project accession
- native path/name and repository URL
- acquisition mode and evidence source
- validation state, reason, score/confidence
- deliverable state and published batch index
- size and asset reference

Indexes:

- `(job_id, deliverable_status, project_accession)`
- `(job_id, project_accession, file_identifier)`
- `(job_id, published_batch_index)`

### 4.5 `job_events`

Append-only typed events:

- `(job_id, sequence)` primary key
- source sequence
- timestamp, level, actor, event type
- compact message and metrics JSON
- bounded payload JSON
- optional artifact/evidence reference

Indexes:

- `(job_id, sequence)`
- `(job_id, event_type, sequence)`
- `(job_id, level, sequence)`

Large SDK state, complete prompts, raw API responses, and full evidence are never stored inline.

### 4.6 `batches`, `assets`, `deletion_requests`, `history_entries`

These tables implement the already-approved 500-file batches and storage lifecycle:

- immutable batch membership;
- managed asset ownership, path, size, checksum, and lifecycle state;
- deletion preview/request/result;
- compact indexed history projection and garbage classification.

## 5. Transaction and event contract

Every business transition is one SQLite transaction:

1. validate the current `jobs.status`, `phase`, and `version`;
2. update the relevant term/project/file row;
3. update the `jobs` snapshot and increment `version`;
4. append exactly one or more typed `job_events`;
5. set `last_event_sequence`;
6. commit.

HTTP, PRIDE, SDRF, model calls, and filesystem downloads never run inside the transaction.

The existing event reducer remains the starting point, but the transition table becomes a domain module outside `app.py`. React consumes the projected snapshot and never infers business state from log strings.

## 6. Queue and Worker design

### 6.1 Huey responsibilities

- durable enqueue/dequeue;
- retries with bounded backoff;
- scheduled retry and periodic reconciliation;
- task priority;
- task timeout/expiration metadata;
- Worker health supervision.

### 6.2 Application responsibilities

- idempotent task body;
- checkpoint boundaries;
- cancellation checks;
- job lease/heartbeat in `operations.sqlite`;
- scientifically correct state transitions;
- artifact receipts.

The initial consumer uses four Huey thread workers. Search-term orchestration remains sequential where required; project review tasks can occupy up to four slots. The UI displays these domain Worker slots from `project_reviews`, not Huey's internal implementation details.

## 7. Realtime contract

### 7.1 Snapshot

`GET /api/operations/jobs/{job_id}`

Returns a typed snapshot smaller than 100 KB. It contains no event list, full project list, file list, or complete result record.

### 7.2 Event stream

`GET /api/operations/jobs/{job_id}/events/stream`

- implemented with `sse-starlette`;
- event `id` equals the database sequence;
- honors `Last-Event-ID` and optional `after`;
- sends keepalive comments;
- queries `job_events(job_id, sequence)` using the covering primary-key index;
- emits terminal status and closes only when the client chooses or policy requires.

Because the Worker is a separate process, the database sequence is authoritative. The SSE endpoint may use a short indexed wait/poll interval for new committed rows; reconnect never depends on an in-memory publisher.

### 7.3 Paged events

`GET /api/operations/jobs/{job_id}/events?after=&limit=&level=&type=`

Uses keyset pagination. The frontend requests older technical events only when the user expands the event timeline.

## 8. History reconciliation and garbage classification

The periodic reconciler compares indexed metadata and managed roots without blocking history requests.

Classifications:

- `valid`
- `empty_failed_stub`
- `orphaned_metadata`
- `orphaned_storage`
- `corrupt_manifest`
- `development_record`
- `cleanup_candidate`
- `deletion_requested`
- `purge_failed`
- `purged`

Default history queries exclude cleanup candidates and purged tombstones. No existing directory is deleted merely because it is classified. Physical deletion continues to require a preview and explicit confirmation.

## 9. Carbon information architecture

### 9.1 Product shell

- `Header`, `HeaderName`, `HeaderMenuButton`
- `SideNav`, `SideNavItems`, `SideNavLink`
- `Content` and 16-column Carbon Grid

Primary navigation:

1. Current task
2. History
3. Batch processing
4. Single-file processing
5. Settings

Top-level product navigation does not use a horizontally overflowing TabList.

### 9.2 Current task before execution

- Agent conversation as the main workspace;
- strategy summary using structured Carbon layers/tiles;
- search-term selection using Carbon form and filtering patterns;
- explicit confirmation and start action.

### 9.3 Current task during execution

- sticky task header with `Tag`, heartbeat, elapsed time, Stop/Resume;
- `ProgressIndicator` for business phases;
- determinate `ProgressBar` for search terms and project review;
- Carbon Tabs only for local task views:
  - Overview
  - Search terms
  - Review queue
  - Deliverable files
  - Events
  - Task conversation
- Carbon DataTable + Pagination for terms, projects, files, and batches;
- right panel only for a selected project's evidence detail;
- raw technical payload behind progressive disclosure.

### 9.4 History

- Carbon DataTable, TableToolbar, batch filters, search, Pagination;
- saved URL query state;
- status Tags and storage columns;
- separate valid, cleanup suggestions, archive, and trash views;
- deletion preview Modal with exact targets and bytes.

### 9.5 Loading and notifications

- Skeleton for first structural load;
- InlineLoading for bounded local actions;
- InlineNotification for contextual errors;
- Toast/notification center for background task transitions;
- no simultaneous global spinners.

## 10. Scrolling and rendering rules

- The document/content area owns the main vertical scroll.
- Header and current-task summary may be sticky.
- DataTable uses pagination rather than an unbounded nested scroll.
- Event timeline uses window-based TanStack Virtual only after the rendered row threshold is exceeded.
- Project detail right panel owns its own intentional scroll and does not capture wheel events while closed.
- No `height: calc(100dvh - ...)` workbench cage and no page-level `overflow: hidden`.

## 11. Internal implementation waves and single cutover

The following stages are internal dependency and verification boundaries. They
are not partial user deliveries. The existing deployed product remains the
user-facing system until every stage is complete and the release candidate
passes the full gate.

### Stage 0 — Protect current work

- do not migrate or rewrite a running JSON-backed job;
- retain current JSON and artifact directories;
- backup SQLite and job metadata before each migration stage.

### Stage 1 — Database foundation and indexed history

- add SQLAlchemy and Alembic;
- create operations schema;
- import compact history shells and classify stale entries;
- verify database-backed history pagination behind the unreleased build;
- retain old history data as migration input and rollback material.

### Stage 2 — New-job authority

- new jobs write authoritative SQLite snapshot/events;
- old jobs remain JSON-backed read-only;
- add an internal migration adapter that presents both through one API model;
- export a compact terminal JSON bundle for portability, not runtime authority.

### Stage 3 — Huey execution and SSE

- run Huey as a separate Windows service with thread workers;
- enqueue new discovery jobs;
- enable SSE snapshot/event flow;
- keep REST snapshot fallback.

### Stage 4 — Carbon workbench

- replace top-level tabs with UI Shell;
- add current-task console;
- migrate history, projects, files, and events to paged queries;
- remove nested-scroll layout.

### Stage 5 — Cleanup and performance gate

- enable reconciler and cleanup suggestions;
- benchmark 1,000 histories, 765 projects, 20,000 files, and 10,000 events;
- remove legacy high-frequency polling only after SSE recovery tests pass;
- build one release candidate containing all backend and frontend replacements;
- run migration rehearsal, rollback rehearsal, and final user-flow acceptance.

### Single production cutover

1. stop creation of new jobs and wait for or explicitly checkpoint active work;
2. back up JSON metadata, manifests, SQLite files, and managed indexes;
3. install the complete release candidate and dependencies;
4. run Alembic migrations and the idempotent history/job importer;
5. start the FastAPI service and Huey consumer as managed Windows services;
6. run health, SSE, history, discovery, cancellation, resume, and batch smoke tests;
7. expose the new Carbon workbench only after all checks pass.

No partial database-only or UI-only release is delivered.

## 12. Rollback

- Alembic migrations have reviewed downgrade or restore procedures.
- Each internal stage has a local verification boundary, but production receives
  a single complete release.
- Old JSON jobs remain untouched until the new store passes recovery tests.
- A cutover failure stops the new services, restores the pre-cutover database
  and metadata snapshot, and restarts the previously deployed build.
- REST snapshot remains a transport fallback for a temporary SSE disconnect,
  not a permanently supported legacy product mode.

## 13. Explicitly rejected approaches

- Continuing full JSON rewrite and directory-scan history.
- A custom task queue built from threads and dictionaries.
- Redis, Elasticsearch, or PostgreSQL for the current single-server requirement.
- Celery on native Windows.
- Using WebSocket for a primarily server-to-client event feed.
- Replacing Carbon with another component library.
- Treating Carbon as visual CSS while retaining the current custom information architecture.

## 14. Primary implementation references

- Carbon monorepo and packages: https://github.com/carbon-design-system/carbon
- Carbon patterns: https://carbondesignsystem.com/patterns/overview/
- SQLAlchemy 2.x: https://docs.sqlalchemy.org/en/20/
- Alembic: https://alembic.sqlalchemy.org/en/latest/
- Alembic SQLite batch migrations: https://alembic.sqlalchemy.org/en/latest/batch.html
- Huey task queue: https://huey.readthedocs.io/en/stable/
- Huey SQLite storage: https://huey.readthedocs.io/en/stable/guide.html
- Huey Windows/thread consumer constraints: https://huey.readthedocs.io/en/stable/consumer.html
- sse-starlette: https://github.com/sysid/sse-starlette
- TanStack Query: https://tanstack.com/query/latest/docs/framework/react/
- TanStack Virtual: https://tanstack.com/virtual/latest/docs/framework/react/react-virtual
