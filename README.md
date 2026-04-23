# PRIDE AI-ready Agent

Python implementation of a PRIDE-first AI-ready data agent aligned with the
`guomics-lab/MSDT-Converter` input and output contracts.

## Available Commands

- `check-runtime`
- `bootstrap-msdt-converter`
- `resolve-project`
- `infer-attributes`
- `plan-dda-run`
- `prepare-dda-bundle`
- `run-dda-msdt`
- `export-ai-ready`

## Current Runtime Model

- PRIDE resolution works directly against the PRIDE Archive v2 API.
- `MSDT-Converter` can be bootstrapped from GitHub as a ZIP archive.
- Strict `DDA -> MSDT` is the supported v1 execution path.
- `DIA` is recognized and reserved for a future adapter layer, but it is not
  materialized into strict MSDT output yet.

## Example Flow

```powershell
python -m agent.cli check-runtime
python -m agent.cli bootstrap-msdt-converter --destination external
python -m agent.cli resolve-project "WT_5_Lys-c.raw"
python -m agent.cli prepare-dda-bundle "WT_5_Lys-c.raw" C:\data\WT_5_Lys-c.mzML .\task_out
```

`prepare-dda-bundle` writes the strict task directory structure needed before a
real `FragPipe + MSDT-Converter` run:

- `project_resolution.json`
- `metadata.json`
- `attributes.json`
- `decision_trace.json`
- `converter_config.json`
- `fragpipe/fragpipe-files.fp-manifest`
- `fragpipe/<workflow>.workflow`
