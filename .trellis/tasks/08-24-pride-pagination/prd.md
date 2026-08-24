# Reliable PRIDE pagination

## Goal

Make every PRIDE project-search and project-file inventory path either prove that it reached the repository end or explicitly report that it stopped early. No path may silently present a first page or a capped file list as complete.

## Requirements

- Project and file pagination have two explicit modes: `exhaustive` and `budgeted`.
- Exhaustive mode starts at the requested page and continues through a short or empty terminal page.
- Budgeted mode requires a page/result/file limit and returns `truncated=true` when that limit may have hidden more records.
- Pagination state records query, page size, next page, next-page row offset, completed pages, records seen, unique records seen, last page size, exhaustion, truncation, and stop reason.
- Resume uses the saved next page. A client that cannot resume must fail closed.
- Duplicate accessions on a full page do not count as repository exhaustion.
- Existing list-returning public methods remain source compatible. Their no-limit behavior is exhaustive.
- Discovery, resolver, neutral-pool, preflight, repository adapter, and Web callers do not rely on an implicit one-page default.
- File-inventory caps and SDRF caps are visible as incomplete inventory rather than complete absence.
- Progress callbacks stay lightweight and report per-page progress.

## Constraints

- PRIDE Archive v2 pages are capped at 100 records.
- Preserve injected fake/legacy clients used by tests where they can honor a cursor; fail closed when they cannot.
- Do not load unbounded file details into Web responses. Completeness state is stored separately from visible-page data.
- Do not change unrelated immunopeptidomics work already present in the working tree.

## Acceptance Criteria

- [ ] A project appearing only on pages 2, 5, or 20 is found in exhaustive mode.
- [ ] A full page containing only duplicates does not stop pagination.
- [ ] Empty-page and short-page endings produce `exhausted=true`, `truncated=false`.
- [ ] Page/result/file limits produce `truncated=true`, `exhausted=false` when more data may exist.
- [ ] Resume from page 7 requests page 7 first and does not replay pages 0-6.
- [ ] A 120-record cap over 100-record pages resumes at page 1 row 20, so no fetched-but-unreturned rows are lost.
- [ ] A 201-file project is read in three requests of at most 100 records.
- [ ] Resolver, neutral-pool, preflight, legacy Discovery, repository adapter, Agent, and Web entry points have an explicit pagination contract.
- [ ] Pagination unit tests, affected integration tests, type checks, and the final full suite pass.

## Out of Scope

- File-level LLM judgment and reason generation.
- Cursor pagination of the local Operations database.
- Frontend virtual-list rendering.
