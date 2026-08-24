# Repository Pagination Contract

## Scenario: PRIDE project and file pagination

### 1. Scope / Trigger

Use this contract whenever backend code searches PRIDE projects or lists files in a PRIDE project. It prevents a first page, capped result set, failed request, or uninspected inventory from being presented as a complete repository result.

### 2. Signatures

```python
search_projects_paginated_with_state(
    client,
    keyword,
    *,
    mode: Literal["exhaustive", "budgeted"],
    page_size: int,
    max_pages: int | None = None,
    max_results: int | None = None,
    start_page: int = 0,
    start_page_offset: int = 0,
    on_page: Callable[[int, int, int], None] | None = None,
) -> PridePaginatedResult

list_project_files_paginated_with_state(
    client,
    accession,
    *,
    mode: Literal["exhaustive", "budgeted"],
    keyword: str | None = None,
    page_size: int = 1000,
    max_files: int | None = None,
    max_pages: int | None = None,
    start_page: int = 0,
    start_page_offset: int = 0,
    on_page: Callable[[int, int, int], None] | None = None,
) -> PridePaginatedResult
```

The historical list-returning `PrideClient.search_projects()` and `PrideClient.list_project_files()` methods remain compatibility wrappers. New cross-layer callers that display or persist completeness must use the state-returning functions.

### 3. Contracts

`PridePaginatedResult.records` contains the delivered records. `PridePaginatedResult.state` is the authoritative stop proof and always serializes the same fields:

```text
repository, operation, mode, query_id, query_text,
project_accession, keyword, page_size,
next_page, next_page_offset, pages_completed,
records_seen, unique_records_seen, last_page_count,
exhausted, truncated, stop_reason, page_progress_exact
```

- `exhaustive` accepts no page, result, or file limit and continues to a short or empty page.
- `budgeted` requires at least one applicable limit.
- PRIDE page size is clamped to 100.
- `exhausted` and `truncated` are mutually exclusive.
- A stop inside a fetched page stores that page in `next_page` and the consumed row count in `next_page_offset`.
- Search deduplication is a caller concern. Duplicate full pages do not prove exhaustion.
- `page_progress_exact=true` means the adapter observed real page callbacks. When a compatible bulk client only returns a combined list, page counts are derived from the configured page size and the field must be `false`.
- Errors and uninspected inventories use `PridePaginationState.unavailable(...)`, preserving the same serialized field set as successful states.
- Flat legacy summaries use `state.to_prefixed_dict("file_inventory")`; callers must not hand-build partial projections.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| `exhaustive` plus any limit | Raise `ValueError` |
| `budgeted` without a limit | Raise `ValueError` |
| Limit less than 1 | Raise `ValueError` |
| HTTP or payload validation failure | Propagate the error; never convert it to an empty terminal page |
| Resume offset exceeds returned page | Raise `RuntimeError` |
| Legacy client returns a full page and cannot honor a page cursor | Fail closed with `RuntimeError` in exhaustive mode |
| A declared cursor returns the same full page three times in a row | Fail closed with `RuntimeError`; do not claim exhaustion |
| Bulk client has no per-page callback | Derive page counts from record count and set `page_progress_exact=false` |
| Limit may hide later records | `truncated=true`, `exhausted=false`, with a resume cursor |
| Empty or short terminal page | `exhausted=true`, `truncated=false`, no resume cursor |
| Query not started or failed | Full state schema with `stop_reason=not_inspected` or `error` |

When adapting legacy clients, match the exact quoted unexpected argument name. Do not search for a bare substring such as `keyword` inside the phrase `unexpected keyword argument`, because that can silently remove the wrong argument.

### 5. Good / Base / Bad Cases

- Good: exhaustive project search reads pages 0 through 19 and finds a target on page 20.
- Good: a 120-record cap over 100-record pages returns `next_page=1` and `next_page_offset=20`.
- Base: a short first page returns complete records with `stop_reason=short_page`.
- Bad: returning 100 records from page 0 and labelling them complete without requesting page 1.
- Bad: treating a duplicate full page as the repository end.
- Bad: an SDRF error payload containing only three fields while a success payload contains the full state schema.

### 6. Tests Required

- Unit: targets on pages 2, 5, and 20 are found.
- Unit: a duplicate full page is followed by another request.
- Unit: three identical full pages fail closed, while a single duplicate page is tolerated.
- Unit: a bulk client without callbacks reports two estimated pages for 150 records at page size 100.
- Compatibility: an injected client that accepts `page` but not `page_size` still advances correctly; substring collisions are not accepted as argument matches.
- Unit: empty-page, short-page, `max_pages`, `max_results`, and `max_files` stop states are asserted.
- Unit: page-internal resume returns the unconsumed tail with no duplicates or gaps.
- Unit: a 201-file project requires three requests with page size at most 100.
- Compatibility: page-capable legacy clients exhaust; page-less full-page clients fail closed.
- Contract: success, error, and not-inspected states serialize the same keys.
- Integration: resolver, Discovery, Agent search events, repository adapter, metadata context, and Web summaries expose the shared semantics.

### 7. Wrong vs Correct

#### Wrong

```python
files = client.list_project_files(accession, max_files=100)
summary["file_count"] = len(files)  # Looks complete but may be capped.
```

#### Correct

```python
result = list_project_files_paginated_with_state(
    client,
    accession,
    mode="budgeted",
    max_files=100,
)
summary["file_count"] = len(result.records)
summary["file_inventory"] = result.state.to_dict()
```
