# Implementation plan: Reliable PRIDE pagination

## Slice 1: shared public pagination contract

- [x] Add failing tests for exhaustive default search and state-bearing budget stops.
- [x] Implement shared pagination state/result types and project pagination engine.
- [x] Preserve list-returning compatibility and legacy-client fail-closed behavior.
- [x] Run `tests/test_pride_client.py` (33 passed under the Python 3.12 compatibility environment).
- [x] Run type checking for touched modules.

## Slice 2: complete project-file inventory contract

- [x] Add failing tests for 201 files, explicit file caps, completeness state, and page-internal resume.
- [x] Move file-list looping through the shared state rules.
- [x] Keep PRIDE's maximum page size at 100.
- [x] Run the PRIDE client tests again (19 passed).

## Slice 3: migrate direct callers

- [x] Resolver uses exhaustive project search and does not depend on implicit first-page behavior.
- [x] Neutral pool and preflight use explicit budgeted search and record truncation in their outputs.
- [x] Legacy Discovery and Web file inventory propagate capped/incomplete state.
- [x] Audit every `search_projects` and `list_project_files` call with `rg`.
- [x] Run affected single test files after each caller group.

## Slice 4: resume and cross-entry regression coverage

- [x] Add public-seam tests for second-page targets, duplicate full pages, page-7 resume, and capped inventories.
- [x] Fail closed when a nominal page cursor repeats the same full page three times; do not trust `**kwargs` alone as cursor proof.
- [x] Mark page progress as estimated when a compatible bulk client has no page callback.
- [x] Match rejected parameter names exactly so `page_size` cannot be mistaken for `page`.
- [x] Run affected Discovery and Web integration tests.
- [x] Run lint/type check commands discovered from repository configuration.

## Quality gate

- [x] Review all changed code against PRD/design and backend/cross-layer specs.
- [x] Run the full test suite (1754 passed; 2 unrelated existing/user-worktree failures recorded in `progress.md`).
- [x] Record any environment-only failures separately from product failures.
- [x] Update project specs if a durable pagination convention was learned.
- [x] Prepare a scoped commit plan without including unrelated dirty files.
