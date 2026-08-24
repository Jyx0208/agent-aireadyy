# Discovery file-level selection

## Goal

Turn Discovery into a file-level, evidence-backed selection system with independent reasons per selected file, correct SDRF/companion handling, complete PRIDE pagination, responsive live progress, and scalable exports.

## Child delivery map

1. `08-24-pride-pagination`: prove or disclose repository pagination completeness.
2. File judgment and evidence model.
3. SDRF/file-family relation and selection closure.
4. File-level LLM review and selection authority.
5. Live Web progress, server-side filters, and scalable export.
6. Scientific validation, rollout, and legacy-path retirement.

## Cross-child acceptance criteria

- [x] Every selected file has an independent, coherent model reason and valid evidence references.
- [x] Every reviewed excluded or investigate file has a concise file-level model reason.
- [x] Required SDRF/companions are included once and linked to their primary files.
- [x] Project-level judgment cannot authorize unrelated files.
- [x] PRIDE searches and file inventories are exhaustive or explicitly marked truncated.
- [x] Web status separates review progress from selection decisions with cursor paging and lazy detail.
- [x] Excel is a readable selected-file summary; Parquet/JSONL retain complete authority data.

## Source plan

The user-reviewed detailed plan remains in repository-root `task_plan.md`.
