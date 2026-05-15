# PRIDE AI-ready Agent

PRIDE AI-ready Agent is a production-oriented proteomics workflow system that turns a raw file name or accession into a reproducible planning package, a prepared MSDT-Converter input package, or a full end-to-end execution run.

## What It Does

- Resolves PRIDE, MassIVE, and iProX projects through a repository adapter layer.
- Infers project metadata, species, instrument, digestion, modifications, and search parameters.
- Builds actual FragPipe and MSDT-Converter inputs, including workflow and FASTA selection.
- Supports three run modes for single-file and batch use:
  - `parameters` - infer parameters only, no large downloads or Docker.
  - `prepare` - prepare the input package, but do not run Docker.
  - `full` - prepare, run Docker, validate outputs, and package results.
- Runs a preflight check before execution to catch Docker, disk, and repository-specific blockers early.
- Produces benchmark Excel reports for batch parameter audits.

## Quick Start

Start the Web UI:

```powershell
.\start-web.ps1
```

Open:

```text
http://127.0.0.1:8000
```

Create a single task from CLI:

```powershell
python -m agent.cli one-click-run "HeLa_ArgC-Try_CID_1.raw" --mode parameters
python -m agent.cli one-click-run "HeLa_ArgC-Try_CID_1.raw" --mode prepare --repository pride
python -m agent.cli one-click-run "HeLa_ArgC-Try_CID_1.raw" --mode full --resource-policy conservative
```

## Web Workflow

The UI provides:

- Single-file planning and execution.
- Batch benchmark planning with Excel export.
- English and Chinese UI switching.
- Project History with submitter, timestamps, mode, status, download links, and failure reasons.
- Preflight feedback before batch creation and single-task creation.

Batch mode also supports `parameters`, `prepare`, and `full`.

## Output Contract

Single-file runs are written under:

```text
runs\<task_name>\
```

Key artifacts:

```text
converter_config.json
msdt_input_manifest.json
project_resolution.json
metadata.json
asset_resolution.json
attributes.json
decision_trace.json
parameter_audit.json
task_state.json
workflows\*.workflow
fragpipe\fragger.params
fragpipe\msbooster_params.txt
logs\runtime.log
rawspectrum\
msdt\
ai_ready\
```

Batch runs are written under:

```text
runs\_batches\<batch_id>\
```

and produce:

- `benchmark_results.xlsx`
- `batch_manifest.json`
- `batch_parameter_audit.zip`

## Documentation

- [System architecture](docs/module-architecture.md)
- [Project report summary](docs/project-report.md)
- [Batch Excel design](docs/batch_excel_system_design.md)
- [Frontend component architecture](docs/frontend_component_architecture.md)
- [Repository adapter architecture](docs/repository-adapters.md)
- [Deployment guide](docs/one-click-deploy.md)

## Engineering Notes

- PRIDE is the default first-class source, but the downstream pipeline is repository-agnostic.
- Preflight is separated from execution so the UI can stop bad runs before they consume disk or time.
- Parameter-only mode is intentionally lightweight and does not download RAW or run Docker.
- Prepare mode writes the exact input package for MSDT-Converter without executing the full workflow.
- Full mode is the only path that executes Docker and produces the execution result ZIP.

## Requirements

- Python 3.13+
- Network access to PRIDE, UniProt, and the selected LLM API
- Docker Desktop or Docker Engine for `prepare` and `full`
- ProteoWizard `msconvert` if available locally; otherwise Docker fallback is used

## Testing

Run the focused regression suite:

```powershell
python -m pytest tests\test_oneclick.py tests\test_web.py tests\test_frontend_template.py tests\test_cli_entrypoint.py -q
```

Run the full suite:

```powershell
python -m pytest -q
```

## Deployment

One-command server deployment:

```powershell
.\scripts\deploy.ps1 -CommitMessage "docs: update report-ready documentation"
```

The deployment script pushes to GitHub and refreshes the server-side Docker Compose deployment.
