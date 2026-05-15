# System Architecture

This document describes the production shape of PRIDE AI-ready Agent as it stands today.

## Goal

The system turns a proteomics file name or accession into one of three outcomes:

1. parameter-only planning
2. prepared MSDT-Converter input package
3. full end-to-end execution

The implementation is designed to be auditable, repository-agnostic, and safe to run on a single workstation or a small server.

## High-Level Architecture

```text
input file / accession
  -> input normalization
  -> repository adapter resolution
  -> project/file matching
  -> metadata normalization
  -> attribute inference and LLM confirmation
  -> workflow / FASTA / search-parameter planning
  -> optional file download and conversion
  -> optional MSDT-Converter execution
  -> output packaging and history persistence
```

## Core Layers

### 1. Web and CLI entry layer

- `src/agent/web/app.py`
- `src/agent/web/templates/index.html`
- `src/agent/cli.py`

Responsibilities:

- accept user input
- validate API settings
- dispatch single-file or batch workflows
- expose project history and downloads
- surface preflight failures before expensive work starts

### 2. Repository layer

- `src/agent/repositories/`
- `src/agent/metadata/canonical.py`

Responsibilities:

- resolve PRIDE, MassIVE, and iProX through one adapter interface
- map external metadata into canonical project/file objects
- hide API-specific differences from downstream planning

### 3. Planning and decision layer

- `src/agent/orchestrator/pipeline.py`
- `src/agent/decision/`
- `src/agent/llm/`

Responsibilities:

- infer acquisition mode, digestion, modifications, species, and instrument
- choose workflow and FASTA
- identify review cases and blocking issues
- support manual overrides when the operator confirms them

### 4. Execution layer

- `src/agent/assets/`
- `src/agent/execution/`
- `src/agent/msdt_converter/`

Responsibilities:

- download data when needed
- convert RAW/vendor files to mzML when needed
- materialize MSDT-Converter inputs
- run Docker only in `full` mode

### 5. Audit and history layer

- `src/agent/audit/`
- `src/agent/web/history.py`
- `runs/`

Responsibilities:

- persist task history
- persist batch manifests
- persist error summaries and review queues
- rebuild the Project History panel after refresh or restart

## Run Modes

### Parameters only

- No RAW download
- No Docker
- No large conversion outputs
- Produces planning artifacts and parameter audit files

### Prepare input package

- Downloads or prepares the source data
- Builds workflow, FASTA, manifest, and converter config
- Produces a downloadable input-package ZIP
- Does not run MSDT-Converter Docker

### Full workflow

- Runs the full planning chain
- Converts data if needed
- Runs MSDT-Converter Docker
- Packages execution results

## Design Principles

- Canonical data first, repository-specific logic only inside adapters
- Separate preflight from execution
- Prefer explicit audit files over hidden state
- Keep large files out of parameter-only mode
- Make batch mode lightweight and predictable
- Preserve backward compatibility for PRIDE commands

## Operational Boundaries

- The system is not a distributed queue service.
- The current production target is a single process with JSON-backed state.
- Batch and single-file history are rebuilt from local disk artifacts.
- Large intermediates live under `runs/` and `.agent_cache/`, not in Git.

## Key Deliverables

- `converter_config.json`
- `msdt_input_manifest.json`
- `parameter_audit.json`
- `decision_trace.json`
- `attributes.json`
- `project_resolution.json`
- `benchmark_results.xlsx`
- `batch_parameter_audit.zip`

## What Makes It "Engineering Grade"

- explicit preflight
- repository abstraction
- stable canonical metadata model
- auditable outputs
- batch and single-file parity
- clear run modes
- downloadable artifacts for verification

