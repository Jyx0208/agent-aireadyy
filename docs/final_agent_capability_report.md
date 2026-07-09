# AI-ready Data Agent Final Capability Report

Generated on: 2026-06-26

## One-sentence Positioning

AI-ready Data Agent has evolved from a PRIDE-first parameter inference/export prototype into a task-aware proteomics data scientist agent v1. Starting from a natural-language data need, it can discover candidate datasets, assess task readiness and data value, connect full or partial workflow outputs, generate AI-ready training tables, and produce auditable recipe, split, leakage, hard benchmark, curation queue, and model-loop smoke reports.

## Demonstrable End-to-end Loop

```text
General / task-aware Discovery
-> candidate validity / readiness / value
-> Batch parameters / full / partial evidence
-> AI-ready Build
-> dataset recipe / split / leakage
-> hard benchmark / curation queue
-> dry-run model-loop / gap plan
-> protected reproducible benchmark package
```

## Core Capabilities

### 1. General Data Discovery

- The default entry point is now `General data search`; it is no longer limited to preset PTM/HLA scenarios.
- Natural-language queries cover HLA immunopeptidomics, drug treatment DDA, disease cohorts, cell lines, PTM-enriched data, and related data needs.
- Outputs candidate project/file manifests grouped by `valid / weak_keep / needs_review / exclude`.
- Discovery -> Batch handoff is wired and can proceed to `parameters / prepare / full`.

### 2. Task Readiness and Data-value Assessment

- Supports task readiness, data value, trust score, diversity tags, and quality reports.
- For DDA targets, pure DIA/SWATH/PRM/SRM/MRM evidence conflicts with the target and is excluded; mixed-acquisition projects enter file-level review.
- Species is open by default; when the user specifies a species such as human, non-human species diversity no longer adds extra value.
- TMT/iTRAQ is treated as a weak-but-allowed labeling strategy rather than being automatically excluded.
- PTM semantics cover phospho, acetyl, ubiquitin/GlyGly, glyco, and methyl. When the PTM discovery target is selected, the Web UI supports multi-select PTM types.

### 3. AI-ready Build

Current task-table exporters:

```text
rt_prediction
fragment_intensity_prediction
psm_scoring
denovo
ptm_denovo
chimeric_interpretation
```

Implemented capabilities:

- Local search-result locator.
- Original agent run bridge.
- Peaklist / MGF completion.
- Mini E2E validation.
- Multi-run batch validation.
- Usable partial output reuse.
- Blocker-first export policy: missing inputs do not produce fabricated labels.

### 4. Web Main Flow

- Web title and positioning are now `Task-aware AI-ready Data Agent`.
- AI-ready Build UI is simplified into three stages: `Input source -> Task and build -> Results and next step`.
- Debug and low-frequency tools are folded into Advanced sections.
- Discovery, Batch, AI-ready Build, and Recipe steps provide next-step hints and prefilled paths.
- Protected benchmark directories are skipped by public-results scanning, preventing the page from stalling on large protected pools.

### 5. Minimal Recovery v2

Only low-risk actions are automated:

```text
missing peaklist -> generate MGF -> retry Build
usable partial outputs -> partial AI-ready Build
```

High-risk situations only produce structured plans:

```text
OOM
large download
conversion failure
workflow change
RAW/WIFF-like compatibility
```

### 6. Recipe / Benchmark / Model-loop

- `make-dataset-recipe` supports leakage-aware split, hard benchmark, evidence graph, and curation queue.
- `run-dataset-model-loop` supports dry-run adapter, metric schema, failure modes, and gap expansion plan.
- Recipe output includes `score_source`, which distinguishes discovery-scored rows from existing-run evidence.

## Protected v3 Benchmark

Protected root:

```text
runs/_protected_benchmark_20260624_4_5_sample_pool
```

Current protected pool:

```text
files: 1074
size: about 1506 MB
keep marker: .agent_keep
```

Five real candidates:

```text
PXD079076: clean completed / TMT10 / MSDT + AI-ready parquet
PXD027067: usable partial output / partial-output recovery
PXD079072: blocked / spectrum or export mismatch
PXD074954: drug treatment / phospho discovery parameters evidence
PXD077080: HLA / immunopeptidomics discovery parameters evidence
```

v3 results:

```text
completed candidates: 2
blocked/review candidates: 3
selected task outputs: 4
excluded task outputs: 6
recipe status: ready
split strategy: file_disjoint
split counts: train 2 / val 2
leakage status: passed
hard benchmark rows: 10
curation queue rows: 10
model-loop status: completed
RT rows scanned: 636
smoke score: 0.7165
```

## Validation Results

Docker targeted regression passed:

```text
tests/test_dataset_recipe.py
tests/test_model_loop.py
tests/test_web_ai_ready.py
tests/test_frontend_template.py
selected public-results / cleanup tests

68 passed
```

Recovery / harness / data scientist loop passed:

```text
tests/test_agentic_recovery.py
tests/test_mini_e2e.py
tests/test_mini_e2e_batch.py
tests/test_agent_recovery.py
tests/test_agent_harness.py
tests/test_data_scientist_loop.py
tests/test_guidance_alignment.py

59 passed
```

Web HTTP smoke:

```text
/api/health: 200
/: 200, title = Task-aware AI-ready Data Agent
/api/results: 200, about 0.1 s after protected-scan optimization
```

Web browser smoke:

```text
AI-ready Build tab click: passed
Input source / Task and build / Results and next step: visible
Advanced build tools: clickable and expandable
screenshots: runs/_protected_benchmark_20260624_4_5_sample_pool/reports/web_smoke/
```

## PPT-ready Conclusions

1. The agent can now complete a small-to-medium real-data loop from data discovery to AI-ready training tables and dataset recipe.
2. The system is no longer limited to PRIDE-only or PTM-specific scenarios; it supports general data discovery while retaining HLA, drug treatment, and PTM as test scenarios.
3. The core design is conservative and evidence-driven: missing search results do not create labels, missing hard-case evidence is marked as missing, and high-risk recovery does not auto-rerun.
4. The v3 benchmark covers successful completion, partial recovery, blocked/review cases, and parameters-only discovery evidence.
5. The current stage is best described as data scientist agent v1 / smoke-ready, not as large-scale production readiness or a completed real model training loop.
