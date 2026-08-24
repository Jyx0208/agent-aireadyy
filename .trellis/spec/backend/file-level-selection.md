# File-level Discovery Selection

## Authority

`file_id` is the final selection key. Project judgments provide shared context only and must never authorize every file in a project.

## Review contract

Every reviewed file has exactly one decision: `include`, `investigate`, or `exclude`.

- `include` requires grade 2-3, a passing hard gate, valid file evidence references, and a coherent file-level reason.
- `investigate` requires named missing information and a concise file-level reason.
- `exclude` requires a concise file-level reason; a failed hard gate always excludes.
- Selected-file reasons are generated in a second pass so structural batch review stays compact.

## Companion contract

SDRF is included only when repository metadata and parsed SDRF rows establish a file relation. A selected primary file closes over its required companions. Shared companions appear once. Deterministic relations cannot be removed by model output.

## Live operations contract

File review events project into `file_records`. List queries use indexed server filters and keyset cursor paging. List responses contain only a reason preview; evidence and the full reason are fetched from the file-detail endpoint.

## Export contract

- `selected_files.xlsx`: small human-readable workbook containing selected primary files and companions.
- `file_judgments.jsonl` and `file_judgments.parquet`: all reviewed decisions, including exclusion and investigate reasons.
- `files.parquet` and `file_evidence.parquet`: scalable machine-readable file/evidence data.

Excel/Parquet exports are produced only for final manifests, not every discovery round or review batch.
