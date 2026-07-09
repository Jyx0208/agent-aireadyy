# Public Raw Mass Spectrometry Standardization Agent MVP

## Goal

This MVP is not just a fixed workflow runner. It is designed to behave like a controlled expert assistant for public raw mass spectrometry data standardization:

- Understand which repository, project, file, and experimental context the input belongs to.
- Select the highest-confidence repository, database, workflow, and search parameters.
- Stop with auditable reasons when evidence is insufficient or biological risk is high.
- Provide recovery strategies for low-risk engineering failures and write them to `recovery_audit.json`.
- Produce consistent audit artifacts across `parameters`, `prepare`, `full`, and `batch` modes.

## Autonomy Levels

| Level | Meaning | Current behavior |
| --- | --- | --- |
| L0 | Logging only | Records input, repository resolution, attribute inference, plan, and run status. |
| L1 | Evidence-gated planning | Selects project, file, FASTA, workflow, and parameters; low-confidence or conflicting evidence enters review. |
| L2 | Controlled engineering recovery | Generates recovery suggestions and audit records for memory, download, conversion, and missing-output failures. |
| L3 | Biological autonomous rewriting | High-risk biological facts are not rewritten automatically and require human confirmation. |

The current MVP target is stable L1 plus limited L2. L3 is intentionally disabled because incorrect species, database, acquisition mode, or digestion strategy can directly undermine result credibility.

## Decision Chain

Core audit files:

- `project_resolution.json`: repository/project candidates, match scores, and review requirement.
- `asset_resolution.json`: matched repository file, file type, download method, conversion requirement, and asset confidence.
- `attributes.json`: acquisition mode, species, instrument, enzyme, modifications, search parameters, and evidence sources.
- `decision_trace.json`: DDA execution plan and blockers.
- `agent_observation.json`: project/file/metadata observed by the agent.
- `agent_plan.json`: plan summary for database, workflow, search parameters, and resource policy.
- `agent_decision_trace.json`: auditable decision records, including `project_selection`, `file_matching`, `database_selection`, `workflow_selection`, `resource_policy_selection`, and attribute inference.

Any `review_required` decision changes `agent_plan.execution_gate` to `review_required` and writes the reason into `blocking_issues`.

## Evidence Gate

Automatic execution is allowed only when:

- Project resolution has no cross-repository tie ambiguity and `resolution_confidence >= 0.85`.
- File asset type is not `unknown`, `asset_confidence >= 0.75`, and a repository match, logical path, or local path exists.
- DDA acquisition mode is confirmable; DIA, top-down, and metabolomics/small-molecule data are blocked.
- Species, instrument family, and digestion enzyme are interpretable non-empty values; conflicts must come from trusted sources with high confidence.
- FASTA must be a real downloadable or user-confirmed database; placeholder FASTA files are not allowed in real searches.
- Workflow selection cannot rely only on weak filename guessing; it must be supported by LLM evidence, SDRF, human confirmation, or at least supporting database/tolerance parameter evidence.

These rules are intentionally conservative: the system prefers human review over producing biologically unsupported results.

## Recovery Strategy

On failure, the system writes structured errors to `recovery_audit.json`, including:

- `task`: task, input, repository, project, output directory, and run mode.
- `failure`: stage, error category, evidence, public message, and operator hint.
- `recovery`: whether automated recovery is allowed, allowed actions, parameters, safety checks, and next human action.
- `artifacts`: related `task_state.json`, `review_queue.json`, `run_manifest.json`, `error.json`, run logs, and other artifacts.
- `integrity`: idempotency key and redaction status.

Low-risk categories that may allow automatic or semi-automatic handling include:

- `insufficient_memory` / `fragpipe_oom`: suggest retrying with fewer threads.
- `download_failure` / `network` / `timeout`: suggest retrying within the original download boundary.
- `conversion_failure` / `docker_unavailable`: suggest switching to a known converter or checking the local toolchain.

Categories that require human review include:

- Missing `PIN`, missing `MSDT parquet`, empty mzML, or corrupted mzML.
- Any change to species, database, acquisition mode, labeling strategy, digestion strategy, or PTM interpretation.
- Any free-form command or arbitrary shell action outside the allowlist.

## Run-mode Artifacts

| Mode | Artifact policy |
| --- | --- |
| parameters | Produces parameters, workflow preview, and audit only; does not download RAW/mzML/FASTA large files. |
| prepare | Downloads/converts data and builds the MSDT-Converter input package; does not run the full workflow. |
| full | Runs the full Docker workflow; writes `recovery_audit.json` on failure and packages results on success. |
| batch | Each item produces independent audit, status, and recovery records; the batch manifest never stores API keys. |

`msdt_input_manifest.json` lists only existing files in `audit_files`, preventing the frontend or ZIP download from referencing missing artifacts.

## Engineering Boundaries

- Do not automatically install Java, Git, msconvert, or Docker.
- Do not execute arbitrary commands generated by an LLM.
- Do not retry indefinitely; recovery must pass the allowlist.
- Do not treat weak keyword rules as final biological facts.
- Do not force full workflow execution on display servers where full workflow is disabled.
- Do not leak MassIVE, iProX, or PRIDE repository-specific details into downstream planning layers; downstream code uses normalized models only.

## Verifiability

Recommended regression suite:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_core.py tests\test_agent_recovery.py tests\test_execution_outputs.py tests\test_docker_pipeline.py tests\test_assets_integration.py tests\test_decision.py tests\test_repositories.py tests\test_web.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

The current agent loop should be called an engineering MVP only when these tests pass and no known blockers remain.
