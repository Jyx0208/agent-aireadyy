# Agentic Public Raw MS Data Standardization Agent Design

## Purpose

This design upgrades the current public proteomics data standardization system from a mostly fixed workflow into an evidence-gated agent. The agent should be able to understand heterogeneous public raw MS data, choose biologically appropriate databases and workflows, execute at scale, recover from common failures, and preserve a complete decision trace.

The core claim is not a single metric. The system must prove three things:

1. It understands the data: files, projects, experiment design, and metadata.
2. It chooses the right flow: database, FASTA, workflow, and search parameters.
3. It runs stably at scale and produces useful AI-ready data with reduced expert labor.

## Design Position

The selected architecture is:

```text
Evidence-gated Planner-Executor Agent
```

The system keeps the existing repository adapters, inference pipeline, execution bundle, and MSDT-Converter integration. A new agent control layer wraps and coordinates them:

```text
Observation
-> Evidence Reasoning
-> Planning
-> Execution
-> Recovery
-> Audit
```

This is not a free-form autonomous system. It is autonomous only inside explicit safety boundaries.

## Operating Principle

The agent may optimize computational strategy, but it must not casually change biological facts.

Biological facts include:

- species and database organism
- acquisition mode, such as DDA or DIA
- experiment design, such as LFQ, TMT, SILAC, phospho, ubiquitin, glyco
- enzyme and digestion strategy
- labeling strategy
- project-provided or strongly evidenced critical modifications

Computational strategy includes:

- thread count
- memory policy
- FASTA scope when biologically equivalent
- reviewed/canonical selection when justified
- low-evidence variable modification pruning
- workflow materialization details
- retry and conversion strategy

## Decision Levels

### L0: Observation Only

Always allowed:

- read repository metadata, SDRF, file lists, and file names
- parse mzML headers and scan-level metadata
- inspect workflow, FASTA, and parameter files
- inspect runtime logs and missing outputs
- record system resources
- estimate search space and memory pressure

### L1: Safe Automatic Repair

Allowed without review because these actions do not change biological interpretation:

- retry transient network requests
- resume or redownload interrupted files
- invalidate corrupt cache and redownload once
- switch primary/fallback converter
- retry conversion with safe converter settings
- retry Docker after transient failure
- reduce thread count
- adjust container path mapping or materialized workflow path
- increase or lower runtime resource policy within configured limits

### L2: Evidence-Gated Automatic Adjustment

Allowed only when evidence is sufficient and the action is audited:

- choose database and FASTA source
- choose workflow
- tune precursor and fragment tolerances
- infer special digestion such as Trypsin/Lys-C from semantic protocol evidence
- reduce FASTA scope to reviewed canonical proteome when species evidence is strong
- reduce low-evidence variable modification complexity
- adjust missed cleavages when evidence or recovery requires it
- run a bounded small exploration over two or three safe candidate plans

Gate conditions:

```text
At least two independent evidence sources agree
or
one high-confidence structured source agrees with LLM reasoning
or
the action does not change core biological facts.
```

### L3: Review Required

The agent must stop for review when the action would alter or override high-risk biological interpretation:

- changing species
- changing DDA/DIA assignment under conflicting evidence
- changing LFQ/TMT/SILAC/PTM experiment type
- converting a dual digest into a single digest
- switching to a different organism database
- removing possible host/pathogen/background organisms without evidence
- ignoring explicit project-stated critical modifications
- resolving multiple near-tied repository projects without metadata consistency
- overriding high-confidence structured metadata with LLM-only reasoning

## Core Modules

### Agent Observation

New module:

```text
src/agent/agent_core/observation.py
```

Output:

```text
agent_observation.json
```

Responsibilities:

- summarize repository candidates and selected project
- summarize target file evidence
- summarize metadata evidence for species, instrument, acquisition, enzyme, labeling, modifications, and experiment type
- record resource state, such as CPU, memory, disk, and configured full-workflow availability
- record prior run/retry state

### Agent Decision Trace

New module:

```text
src/agent/agent_core/decision_trace.py
```

Output:

```text
agent_decision_trace.json
```

Each decision contains:

```json
{
  "id": "D001",
  "decision_type": "enzyme_inference",
  "selected_value": "Trypsin/Lys-C",
  "confidence": 0.94,
  "evidence": [],
  "alternatives": [],
  "risk_level": "medium",
  "gate_action": "evidence_gated_accept"
}
```

Required decision types:

- project_selection
- file_matching
- species_inference
- instrument_inference
- acquisition_mode_inference
- enzyme_inference
- labeling_inference
- database_selection
- workflow_selection
- search_parameter_selection
- resource_policy_selection

### Agent Plan

New module:

```text
src/agent/agent_core/plan.py
```

Output:

```text
agent_plan.json
```

Responsibilities:

- summarize selected database and FASTA
- summarize workflow and materialized overrides
- summarize search parameters
- summarize risk assessment
- record whether execution is allowed, blocked, or review-required

### Agent Recovery

New modules:

```text
src/agent/agent_core/recovery.py
src/agent/agent_core/recovery_actions.py
src/agent/agent_core/recovery_policy.py
```

Output:

```text
recovery_audit.json
```

Recovery state machine:

```text
RUN
-> FAILURE_DETECTED
-> DIAGNOSE
-> SELECT_RECOVERY_ACTION
-> SAFETY_GATE
-> APPLY_RECOVERY
-> RETRY
-> VERIFY
-> SUCCESS / REVIEW / FAILED_FINAL
```

Initial failure categories:

- download_failure
- conversion_failure
- mzml_empty_or_corrupt
- fragpipe_oom
- search_space_exploded
- missing_pin
- missing_msdt_output
- docker_unavailable
- path_mapping_error
- fasta_download_error
- parameter_conflict
- unknown

Initial recovery actions:

- retry_download
- invalidate_cache_and_redownload
- retry_conversion_with_fallback
- reduce_threads
- reduce_search_space
- reduce_variable_mod_complexity
- switch_safe_workflow
- mark_review_required

Retry limits:

```text
safe_auto retry: max 2
evidence_gated retry: max 1
bounded exploration: max 2 candidate plans
```

## Bounded Exploration

The system may explore only safe candidate plans. It must not run arbitrary LLM-generated workflows.

LLM output is advisory. The executor may only run actions from the recovery/action allowlist after safety-gate validation. Free-form shell commands, arbitrary workflow files, and unvalidated parameter keys are not executable recovery actions.

Allowed first-phase exploration:

- conservative resource plan vs standard plan
- reviewed canonical FASTA vs project FASTA when organism evidence matches
- default variable modification set vs reduced low-evidence set
- lower thread count and tighter search space for OOM recovery

Disallowed without review:

- species switch
- DDA/DIA switch under conflict
- dual-to-single enzyme switch
- removing experiment-critical modifications
- changing labeling strategy

## Audit And UI

The first UI implementation should be a text tree, not a complex diagram.

Panel:

```text
Agent Reasoning
+-- Data Understanding
+-- Workflow Selection
+-- Recovery Attempts
+-- AI-ready Quality Checks
```

The panel should read from:

- agent_observation.json
- agent_plan.json
- agent_decision_trace.json
- recovery_audit.json
- task_state.json

## Benchmark Design

The first benchmark should be representative rather than huge:

```text
15 representative projects
1-3 files per project
30-40 files total
```

Coverage:

- PRIDE, MassIVE, iProX
- with and without SDRF
- human, mouse, yeast, microbial
- DDA, DIA, LFQ, TMT, PTM
- easy and ambiguous metadata
- known failure/recovery cases

Metrics:

- project resolution accuracy
- file matching accuracy
- species accuracy
- instrument accuracy
- acquisition mode accuracy
- enzyme accuracy
- database correctness
- workflow correctness
- search parameter correctness
- failure diagnosis accuracy
- recovery success rate
- AI-ready output success rate
- manual review rate
- expert time saved

Comparisons:

- rules-only
- LLM-only
- rules + LLM without evidence gates
- evidence-gated agent
- fixed workflow baseline
- expert-curated subset when available

## Implementation Phases

### Phase 1: Agent Audit Skeleton

Generate:

- agent_observation.json
- agent_plan.json
- agent_decision_trace.json

No execution behavior changes.

Acceptance:

- parameters, prepare, and full modes generate agent audit files
- all key decisions have evidence, confidence, source, and risk level
- full test suite passes

### Phase 2: Evidence-Gated Planner

Route key decisions through a unified gate.

Acceptance:

- ambiguous repository choices explain why one project was selected
- semantic digestion decisions are recorded as evidence-gated
- low-confidence database/workflow choices enter review

### Phase 3: Autonomous Recovery

Add bounded recovery to full workflow execution.

Acceptance:

- OOM/search-space failures produce a recovery plan instead of immediate final failure
- missing PIN and missing MSDT output are diagnosed by root cause where possible
- recovery_audit.json records every action
- retry limits prevent loops

### Phase 4: Agent Reasoning UI And Benchmark Export

Expose reasoning and recovery in the web UI and batch reports.

Acceptance:

- task page shows Agent Reasoning tree
- batch export includes gate_action counts and recovery success statistics
- benchmark can compute manual-intervention reduction

## Success Criteria

The project is considered agentic when:

1. Every key decision is represented as structured evidence, not only logs.
2. The system can distinguish biological facts from computational strategy.
3. Common failures trigger bounded recovery actions.
4. Recovery actions preserve biological correctness or enter review.
5. Batch outputs include AI-ready data plus complete audit artifacts.
6. Benchmark results show reduced expert intervention without lower biological correctness.
