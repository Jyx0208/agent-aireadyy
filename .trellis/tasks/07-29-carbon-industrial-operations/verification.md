# Verification record: Carbon industrial operations workbench

Date: 2026-07-30  
Branch: `worktree-benchmark-review-planning`  
Baseline: `6c38db5`

## Release-candidate verdict

The replacement implementation is internally complete and has passed the
local release-candidate gate. It replaces log-driven browser state with a
durable, indexed operations control plane and presents it through a Carbon
workbench.

Production cutover has deliberately not been performed. Port 8000, the
existing deployment, and any user discovery task were not stopped, migrated,
or replaced during this gate. The isolated candidate used port 8010 and
`.test_tmp/industrial-browser-2`.

## Architecture verified

| Concern | Implemented contract |
| --- | --- |
| Operational state | SQLAlchemy 2.0.51, Alembic 1.18.5, SQLite WAL and indexed tables |
| Durable execution | Huey 3.3.0 with a separate SQLite queue, bounded retry, cancellation, resume, and 15-second heartbeat |
| Live updates | Database-sequenced SSE through sse-starlette 3.4.6 with `Last-Event-ID`; bounded JSON snapshots remain the recovery path |
| History | Database-only paging/filtering, archive and deletion audit; ordinary history requests do not scan full logs or task directories |
| Review | Four visible bounded review slots, project/file evidence, step and reason events |
| Delivery | File-level delivery with deterministic batches of at most 500 previously undelivered files |
| Frontend state | Carbon 11 shell, TanStack Query server cache, TanStack Virtual long-event rendering, stable hash routes |
| Windows operations | Separate Web/Worker services, pinned WinSW v2.12.0 with SHA-256 verification, health script, online SQLite backup |

## Automated verification

### Backend

Final isolated full suite:

```text
1674 passed, 14 deselected, 1 warning in 332.47s
```

The 14 deselections are the repository's existing opt-in selections, not
newly skipped failures. The single warning is Starlette's third-party
`TestClient`/`httpx` deprecation notice.

The suite includes operations repository/API/state transitions, SSE resume,
cancel/resume, four-worker review metering, exhaustive term paging, duplicate
suppression, 500-file batches, history paging, deletion safeguards, migration,
and large-fixture coverage.

### Frontend

Final production-source run:

```text
12 test files passed
241 tests passed
TypeScript production build passed
```

The normal shell bundle is 299.31 kB (93.39 kB gzip). The Carbon AI chat
bundle remains 2,556.98 kB (449.71 kB gzip), but is route- and activity-lazy:
opening History directly did not request `CarbonAgentChat`.

### Browser

Chrome/Playwright checks against the isolated production build verified:

- 672 px and 1440 px layouts without horizontal document overflow;
- Carbon's vertical phase indicator on the narrow breakpoint;
- hidden route panels are actually hidden;
- direct History navigation loads the indexed view without the chat bundle;
- current-to-history navigation deactivates current-task effects and restores
  state on return;
- project evidence appears only in the review context;
- no console or page errors in the checked flows;
- first keyboard focus is `Skip to main content`, and Enter moves focus to
  `<main id="main-content">`;
- reduced-motion mode uses non-smooth scrolling.

## Real-data isolation proof

The final full backend run recorded SHA-256 and modification time before and
after the suite for the checkout's real files:

```text
runs/project_history.json
  SHA-256 before = C67B02724274C50F4CF6A7700C0D741079EB4C70094C34A421B427CA256BB045
  SHA-256 after  = C67B02724274C50F4CF6A7700C0D741079EB4C70094C34A421B427CA256BB045

runs/_operations/queue.sqlite
  SHA-256 before = 136E5D75B46B22D14867253AC181E0A9941343343E231998B6ADCFA5E2D81B9A
  SHA-256 after  = 136E5D75B46B22D14867253AC181E0A9941343343E231998B6ADCFA5E2D81B9A
```

Both modification times were also unchanged. Pytest establishes its storage
root before application/queue imports, then removes the session root at
session finish. No session isolation directory remained after the run.

Before that protection was added, automated tests had written 21 proven test
records and 10 matching directories into the checkout's legacy history.
They were not deleted. They were moved into the recoverable quarantine:

```text
.test_tmp/history-contamination-20260730-135205
```

The quarantine includes the original history JSON, original backup, all
remaining test directories, exact task IDs, hashes, and recovery instructions
in `receipt.json`. The cleaned legacy history round-trips as valid JSON and
contains 201 records.

## Migration and recovery

- Online SQLite backup produced a manifest with byte counts and SHA-256 for
  both `operations.sqlite` and `queue.sqlite`.
- Two independent restored copies were migrated and opened.
- Both reported `PRAGMA integrity_check = ok` and Alembic revision
  `0001_operations_plane`.
- Repeated migration preserved 13 jobs, 24 history entries, 37 events, and
  36,021,583,907 indexed storage bytes.
- Running the migration twice was idempotent.
- `pip check`, Python bytecode compilation, PowerShell parsing, and
  `git diff --check` passed. `git diff --check` emitted only Windows
  LF-to-CRLF notices, not patch errors.

## Scale and response-shape gate

Repository tests exercised 765 candidate projects, 20,000 file records, and
10,000 job events. Snapshot and list endpoints remain paged/bounded; ordinary
history pages default to 25 rows, API list pages are capped at 100, event pages
are capped at 200, and SSE reads events in batches of 100.

This validates the local implementation and response bounds. Production LAN
p95 and long-running real PRIDE throughput must be measured during the
maintenance-window smoke test rather than inferred from unit tests.

## Known non-blocking items

- The Carbon AI chat chunk is large. It no longer penalizes direct History or
  inactive task views, but future upstream Carbon AI releases may allow
  further subdivision.
- Starlette emits one dependency deprecation warning for its test client.
- The isolated port-8010 database is a disposable pre-cleanup fixture. A
  production cutover must initialize/import from the cleaned source under the
  documented maintenance procedure, not copy that fixture.

## Cutover boundary

The release candidate is ready for a controlled cutover, but cutover is a
separate state-changing operation. It requires:

1. a maintenance window and checkpoint of active work;
2. online database backup plus storage snapshot;
3. Web/Worker service installation and migration;
4. health, SSE, Stop/Resume, history, review, and 500-file batch smoke tests;
5. rollback to the previous build if any blocking smoke test fails.
