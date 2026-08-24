# Design: Reliable PRIDE pagination

## Data flow

`PRIDE HTTP page -> validated record page -> pagination engine -> PaginationResult(records, state) -> Discovery caller -> persisted/run progress -> Web summary`

The pagination engine owns page numbers, terminal-page detection, limits, and completeness state. Callers own scientific deduplication and candidate ranking.

## Public contracts

Add a shared immutable pagination state and result in `agent.pride.client`:

- `PaginationMode = Literal["exhaustive", "budgeted"]`
- `PridePaginationState`: repository, operation, query id/text, page size, next page, next-page offset, pages completed, records seen, unique records seen, last page count, exhausted, truncated, stop reason.
- `PridePaginatedResult`: records plus state.

Add state-returning project and file methods. Keep existing list-returning methods as compatibility wrappers:

- no explicit limit => exhaustive;
- an explicit page/result/file limit => budgeted;
- callers that must display completeness use the state-returning method.

## Terminal and limit rules

- Empty page: exhausted, next page absent, `stop_reason=empty_page`.
- Short page: exhausted, next page absent, `stop_reason=short_page`.
- Full page followed by page budget: truncated, next page is the following page, `stop_reason=max_pages`.
- Result/file limit reached inside a page: truncated unless that same page is short; `stop_reason=max_results` or `max_files`.
- When a limit cuts through a fetched page, resume points to that same page plus the consumed row offset. Advancing directly to the next page would silently lose the unreturned tail.
- HTTP/validation errors propagate. They never become an empty terminal page.
- Duplicate records affect `unique_records_seen`, but never terminal detection.

## Compatibility

`search_projects_paginated` remains the adapter for injected clients. It prefers the new state-returning capability when present, otherwise uses the existing modern list method, then explicit `page=`, and fails closed for resume on a page-less legacy client.

Fake clients that only expose the historical two-argument API remain usable for bounded single-page tests. They cannot claim exhaustive or resumable completion.

## Caller migration

- Resolver: exhaustive project search; file search exhaustive unless the caller intentionally supplies a cap.
- Neutral pool and download preflight: budgeted candidate collection with trace fields describing truncation.
- Legacy Discovery: budgeted per-query search and explicit trace state.
- Agent search environment/control plane: preserve current checkpoints and expose shared stop semantics.
- Repository adapter and metadata context: exhaustive file inventory.
- Web: use state-bearing file inventory and show incomplete status when capped.

## Rollback

List-returning wrappers preserve the old return shape. If an upper-layer migration causes a regression, it can temporarily keep the wrapper while completeness state is rolled out without reverting the shared engine.
