# Project Report Summary

## Positioning

Task-aware AI-ready Data Agent is an engineering-grade proteomics data-discovery and AI-ready dataset-building system. It converts a natural-language data need, file name, accession, or existing run into reproducible discovery evidence, parameter plans, prepared input packages, full/partial execution evidence, AI-ready task tables, and dataset recipes.

## Problem Statement

Manual proteomics pipeline setup is slow, error-prone, and difficult to audit. The main failures come from:

- wrong project resolution
- wrong species or instrument inference
- incorrect workflow selection
- missing FASTA or wrong database choice
- large-file handling and disk pressure
- fragile batch processing
- hard-to-audit training dataset construction

## Solution

The system solves these problems by combining:

- repository adapters for PRIDE, MassIVE, and iProX
- canonical metadata normalization
- LLM-assisted parameter inference with explicit evidence
- preflight checks before expensive execution
- parameter / prepare / full run modes
- batch Excel reporting for benchmark validation
- AI-ready Build, recipe/split/leakage, hard benchmark, curation queue, and model-loop smoke

## Architecture Summary

```text
goal/input -> discovery or repository adapter -> canonical metadata -> parameter inference
      -> workflow / FASTA planning -> optional prepare/full/partial evidence
      -> AI-ready Build -> recipe/split/leakage -> model-loop smoke
      -> audit artifacts -> ZIP / Excel / history / reports
```

## Engineering Highlights

- One-click flow with explicit run modes
- Preflight gating before expensive work
- Repository abstraction instead of PRIDE-only logic
- Stable JSON-backed audit and history model
- Accessible operational UI with English/Chinese switch
- Batch mode designed for memory and disk control
- Evidence-first training table export: blockers are explicit and labels are not fabricated

## What the User Can Demonstrate

1. Start the web app.
2. Run General Discovery or enter a known file/run.
3. Select `parameters`, `prepare`, `full`, or AI-ready Build depending on the goal.
4. Observe preflight, blocker, or recovery evidence.
5. View generated workflow, FASTA, audit files, AI-ready outputs, recipe, split, leakage, curation, and model-loop smoke reports.
6. Open Project History and batch Excel output.
7. Show that completed, usable-partial, and blocked/review cases are all preserved as evidence.

## Deliverables

- README with quick start and command examples
- architecture docs for modules, batch, frontend, and adapters
- CLI one-click command
- Web UI with preflight and history
- benchmark Excel export
- reproducible output directories under `runs/`
- protected v3 benchmark pool and handoff reports
- AI-ready capability, limitations, and reproduction documents

## Current Scope Boundaries

- single-process deployment
- JSON-backed persistence
- local disk artifact model
- no external queue service yet
- repository auto-search is supported through adapters, with PRIDE the mature path, MassIVE smoke/v1, and iProX index-first
- model-loop is dry-run smoke, not full model training

## Presentation Angle

The project is not just a data downloader. It is a controlled, auditable data-scientist agent that turns fragile proteomics setup and training-dataset construction into a repeatable, evidence-backed workflow.
