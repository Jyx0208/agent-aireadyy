# Database Guidelines

> Executable persistence contracts for the single-server Windows operations runtime.

## Scenario: Durable operations state and storage-scoped legacy discovery

### 1. Scope / Trigger

Use this contract whenever code changes discovery execution, job state, history,
events, project reviews, file delivery, task recovery, or the configured run
storage root.

The live authority is `agent.operations`, not the legacy in-memory dictionaries
or whole-job JSON. Legacy JSON is a compatibility/checkpoint artifact and may
only be rewritten at required lifecycle boundaries.

### 2. Signatures

- Repository factory: `agent.operations.runtime.get_operations_repository()`
- Web router: `/api/ops`
- Snapshot: `GET /api/ops/jobs/{job_id}`
- SSE: `GET /api/ops/jobs/{job_id}/events?after={sequence}` with
  `Last-Event-ID` support
- History: `GET /api/ops/history?page=&page_size=&status=&kind=&search=`
- Queue: `agent.operations.queue.enqueue_discovery_job(job_id)`
- Databases:
  - `operations.sqlite` is SQLAlchemy/Alembic-owned business state.
  - `queue.sqlite` is Huey-owned queue state.
- Legacy discovery jobs persist a `storage_scope`, computed as a truncated
  SHA-256 of the normalized `AGENT_RUNS_DIR`; raw local paths are not exposed.

### 3. Contracts

Environment keys:

| Key | Contract |
|---|---|
| `AGENT_RUNS_DIR` | Managed legacy runs and compatibility artifacts |
| `AGENT_OPERATIONS_DIR` | Root for operational database and artifacts |
| `AGENT_OPERATIONS_DB` | Optional explicit `operations.sqlite` path |
| `AGENT_QUEUE_DB` | Optional explicit Huey SQLite path |
| `AGENT_OPERATIONS_ARTIFACTS` | Managed large-evidence/export root |
| `AGENT_DISCOVERY_WORKERS` | Integer worker-slot count; Windows uses threads |

Transaction boundary:

1. validate the current state/version;
2. update the domain row(s);
3. increment the job snapshot version;
4. append typed event(s) with monotonically increasing sequence;
5. commit once.

Never perform PRIDE, SDRF, model, or filesystem network I/O inside the database
transaction. History/project/file/event endpoints must issue indexed, bounded
database queries and must not scan run directories.

Legacy compatibility:

```python
job.setdefault("storage_scope", _discovery_storage_scope())
```

Only jobs with the current storage scope may participate in in-memory history
or idempotency lookup. Jobs without the field remain visible for read-only
backward compatibility.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Illegal state transition | Reject it and append a structured diagnostic event |
| Stale version writes newer snapshot | Reject/ignore; never regress counters or status |
| Queue task cancelled before claim | Revoke Huey task and persist `cancelled` |
| Web restarts while Worker runs | Recover snapshot/events from SQLite |
| Active job loses heartbeat | Report the condition separately from transport loss |
| Legacy job scope differs from current root | Exclude it from history and idempotency lookup |
| Legacy job has no scope | Include for backward compatibility |
| Physical deletion lacks explicit confirmation | Refuse deletion and retain audit state |
| SQLite backup requested | Use SQLite online backup; never copy a live WAL database blindly |

### 5. Good / Base / Bad Cases

- Good: 20,000 file rows remain in indexed tables while the snapshot contains
  only counters and the current position.
- Base: a terminal legacy job is imported into history once and remains
  openable through a stable operations job ID.
- Bad: append an event, then rewrite a 38 MB job JSON and return it to the
  browser.
- Bad: merge every process-global `_discovery_jobs` entry after
  `AGENT_RUNS_DIR` changes; this leaks jobs between storage scopes and breaks
  idempotency.

### 6. Tests Required

- State-machine tests assert all legal and illegal transitions.
- Repository tests assert WAL, foreign keys, uniqueness, pagination, query
  plans, compare-and-swap, backup, and concurrent access.
- API tests assert snapshot/event size bounds, SSE sequence recovery,
  cancellation, and bounded page sizes.
- History tests seed at least 1,000 rows and assert database-only paging.
- Batch tests assert `500 + remainder` exact membership with no file repeated
  from an earlier batch.
- Storage-scope regression tests must run a job under one `_runs_dir`, switch
  roots, and assert it is absent from both fast history and idempotency lookup.

### 7. Wrong vs Correct

#### Wrong

```python
for job in _discovery_jobs.values():
    if job.get("idempotency_key") == key:
        return job
```

This treats a process-global dictionary as a multi-storage authority.

#### Correct

```python
for job in _discovery_jobs.values():
    if (
        _discovery_job_is_in_current_storage_scope(job)
        and job.get("idempotency_key") == key
    ):
        return job
```

The compatibility cache is scoped, while SQLite remains the live authority.

## Naming and module ownership

- SQLAlchemy models and schema: `src/agent/operations/models.py`
- Migrations: `src/agent/operations/migrations/`
- State machine: `src/agent/operations/state.py`
- Repository/query code: `src/agent/operations/repository.py`
- Queue adapter: `src/agent/operations/queue.py`
- API and SSE adapter: `src/agent/operations/api.py`
- Legacy import only: `src/agent/operations/legacy.py`

Do not add operations SQL or migration branches to `src/agent/web/app.py`.
