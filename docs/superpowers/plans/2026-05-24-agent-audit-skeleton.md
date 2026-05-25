# Agent Audit Skeleton Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first testable agentic layer by generating structured `agent_observation.json`, `agent_plan.json`, and `agent_decision_trace.json` artifacts without changing execution behavior.

**Architecture:** Add a focused `agent.agent_core` package that translates existing resolution, context, attributes, asset, and execution plan objects into auditable agent records. Integrate artifact writing at the orchestration/web packaging boundaries so `parameters`, `prepare`, and `full` modes can all emit the same agent audit files.

**Tech Stack:** Python 3.13, Pydantic models from `agent.models`, existing `agent.utils.write_json`, pytest.

---

## Scope Notes

This plan implements Phase 1 only from `docs/superpowers/specs/2026-05-24-agentic-ms-standardization-agent-design.md`.

It does not implement autonomous recovery, retries, workflow changes, or benchmark exports yet. Those are later phases and should not be mixed into this patch.

The user previously asked not to use git for deployment. This plan therefore omits commit steps; each task ends with verification instead.

## File Structure

Create:

- `src/agent/agent_core/__init__.py`
  - Public package marker.
- `src/agent/agent_core/models.py`
  - Pydantic models for decision records, observation, plan summary, and artifact paths.
- `src/agent/agent_core/observation.py`
  - Build `agent_observation.json` payload from `InputTask`, `ProjectResolution`, `ProjectContext`, optional `FileAsset`, and optional resource data.
- `src/agent/agent_core/decision_trace.py`
  - Build structured decision records from existing resolution and attributes.
- `src/agent/agent_core/plan.py`
  - Build `agent_plan.json` from `DdaExecutionPlan` and `AttributeSet`.
- `src/agent/agent_core/audit.py`
  - Write all Phase 1 agent audit artifacts to an output directory.
- `tests/test_agent_core.py`
  - Unit tests for builders and artifact writing.

Modify:

- `src/agent/orchestrator/pipeline.py`
  - Add an optional helper on `AgentService` or a standalone function call after planning to write Phase 1 agent audit artifacts when an output directory is available.
- `src/agent/web/app.py`
  - Ensure parameter-only, prepare, and full web paths call the writer after `result` or `bundle` is available.
- Existing tests:
  - Add focused assertions in `tests/test_web.py` or existing integration tests only if there is already a narrow task-output fixture; otherwise keep Phase 1 integration covered by `tests/test_agent_core.py`.

## Chunk 1: Agent Core Models And Observation Builder

### Task 1: Add Agent Core Pydantic Models

**Files:**
- Create: `src/agent/agent_core/__init__.py`
- Create: `src/agent/agent_core/models.py`
- Test: `tests/test_agent_core.py`

- [ ] **Step 1: Write the failing model serialization test**

Add:

```python
from agent.agent_core.models import AgentDecisionRecord, AgentRisk


def test_agent_decision_record_serializes_as_json_ready_dict():
    record = AgentDecisionRecord(
        id="D001",
        decision_type="species_inference",
        selected_value="Saccharomyces cerevisiae",
        confidence=0.95,
        evidence=["project metadata: Saccharomyces cerevisiae"],
        alternatives=[],
        risk_level=AgentRisk.LOW,
        gate_action="auto_accept",
    )

    data = record.model_dump(mode="json")

    assert data["id"] == "D001"
    assert data["risk_level"] == "low"
    assert data["gate_action"] == "auto_accept"
```

- [ ] **Step 2: Run the failing test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_core.py::test_agent_decision_record_serializes_as_json_ready_dict -q
```

Expected: FAIL because `agent.agent_core.models` does not exist.

- [ ] **Step 3: Implement minimal models**

Create `src/agent/agent_core/__init__.py`:

```python
"""Agent audit and reasoning artifacts."""
```

Create `src/agent/agent_core/models.py`:

```python
from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field

from agent.models import JsonModel


class AgentRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class GateAction(StrEnum):
    AUTO_ACCEPT = "auto_accept"
    EVIDENCE_GATED_ACCEPT = "evidence_gated_accept"
    REVIEW_REQUIRED = "review_required"


class AgentDecisionRecord(JsonModel):
    id: str
    decision_type: str
    selected_value: Any
    confidence: float
    evidence: list[str] = Field(default_factory=list)
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    risk_level: AgentRisk
    gate_action: GateAction | str


class AgentObservation(JsonModel):
    input_file: str
    repository_candidates: list[dict[str, Any]] = Field(default_factory=list)
    selected_project: dict[str, Any] | None = None
    metadata_evidence: dict[str, Any] = Field(default_factory=dict)
    asset_evidence: dict[str, Any] = Field(default_factory=dict)
    resource_state: dict[str, Any] = Field(default_factory=dict)


class AgentPlanSummary(JsonModel):
    selected_database: dict[str, Any] = Field(default_factory=dict)
    selected_workflow: dict[str, Any] = Field(default_factory=dict)
    search_parameters: dict[str, Any] = Field(default_factory=dict)
    resource_policy: dict[str, Any] = Field(default_factory=dict)
    risk_assessment: dict[str, Any] = Field(default_factory=dict)
    execution_gate: str = "allowed"
    blocking_issues: list[str] = Field(default_factory=list)


class AgentAuditArtifactPaths(JsonModel):
    observation: Path
    plan: Path
    decision_trace: Path
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_core.py::test_agent_decision_record_serializes_as_json_ready_dict -q
```

Expected: PASS.

### Task 2: Build Observation Payload

**Files:**
- Create: `src/agent/agent_core/observation.py`
- Test: `tests/test_agent_core.py`

- [ ] **Step 1: Write the failing observation builder test**

Add:

```python
from agent.agent_core.observation import build_agent_observation
from agent.models import FileAsset, MetadataValue, ProjectCandidate, ProjectContext, ProjectResolution


def test_build_agent_observation_summarizes_project_metadata_and_asset():
    resolution = ProjectResolution(
        primary_project=ProjectCandidate(
            repository="iprox",
            project_accession="IPX0000753001",
            matched_file="Yeast_R3.raw",
            match_type="exact",
            match_score=100,
            metadata_consistency=0.95,
            evidence=["exact file match"],
        ),
        alternative_projects=[
            ProjectCandidate(
                repository="pride",
                project_accession="PXD071918",
                matched_file="Yeast_R3.raw",
                match_type="exact",
                match_score=100,
                metadata_consistency=0.6,
            )
        ],
        resolution_reason="Selected iProX by metadata consistency",
        resolution_confidence=1.0,
    )
    context = ProjectContext(
        repository="iprox",
        project_accession="IPX0000753001",
        file_name="Yeast_R3.raw",
        metadata={
            "organisms": MetadataValue(value=["Saccharomyces cerevisiae"], source="iprox.project", source_level="project", completeness=1.0),
            "instruments": MetadataValue(value=["Q Exactive"], source="iprox.project", source_level="project", completeness=1.0),
            "experimentTypes": MetadataValue(value=["DDA"], source="iprox.project", source_level="project", completeness=0.9),
        },
        sdrf_rows=[],
    )
    asset = FileAsset(
        repository="iprox",
        original_file_name="Yeast_R3.raw",
        resolved_asset_type="raw",
        matched_project_file="Yeast_R3.raw",
        requires_conversion=True,
        asset_confidence=0.87,
        match_type="exact",
    )

    observation = build_agent_observation("Yeast_R3.raw", resolution, context, asset=asset)

    assert observation.input_file == "Yeast_R3.raw"
    assert observation.selected_project["project_accession"] == "IPX0000753001"
    assert observation.repository_candidates[1]["project_accession"] == "PXD071918"
    assert observation.metadata_evidence["species"]["value"] == ["Saccharomyces cerevisiae"]
    assert observation.asset_evidence["resolved_asset_type"] == "raw"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_core.py::test_build_agent_observation_summarizes_project_metadata_and_asset -q
```

Expected: FAIL because `build_agent_observation` does not exist.

- [ ] **Step 3: Implement observation builder**

Create `src/agent/agent_core/observation.py`:

```python
from __future__ import annotations

from typing import Any

from agent.agent_core.models import AgentObservation
from agent.models import FileAsset, ProjectCandidate, ProjectContext, ProjectResolution


def _candidate_summary(candidate: ProjectCandidate) -> dict[str, Any]:
    return {
        "repository": candidate.repository,
        "project_accession": candidate.project_accession,
        "matched_file": candidate.matched_file,
        "match_type": candidate.match_type,
        "match_score": candidate.match_score,
        "metadata_consistency": candidate.metadata_consistency,
        "evidence": candidate.evidence,
    }


def _metadata_entry(context: ProjectContext, key: str) -> dict[str, Any] | None:
    metadata = context.metadata.get(key)
    if metadata is None:
        return None
    return {
        "value": metadata.value,
        "source": metadata.source,
        "source_level": metadata.source_level,
        "completeness": metadata.completeness,
    }


def _asset_summary(asset: FileAsset | None) -> dict[str, Any]:
    if asset is None:
        return {}
    return {
        "repository": asset.repository,
        "original_file_name": asset.original_file_name,
        "resolved_asset_type": asset.resolved_asset_type,
        "matched_project_file": asset.matched_project_file,
        "requires_conversion": asset.requires_conversion,
        "asset_confidence": asset.asset_confidence,
        "match_type": asset.match_type,
    }


def build_agent_observation(
    input_file: str,
    resolution: ProjectResolution,
    context: ProjectContext,
    *,
    asset: FileAsset | None = None,
    resource_state: dict[str, Any] | None = None,
) -> AgentObservation:
    primary = resolution.primary_project
    metadata_evidence = {
        label: entry
        for label, entry in {
            "species": _metadata_entry(context, "organisms"),
            "instrument": _metadata_entry(context, "instruments"),
            "experiment_type": _metadata_entry(context, "experimentTypes"),
            "project_description": _metadata_entry(context, "projectDescription"),
            "sample_processing": _metadata_entry(context, "sampleProcessingProtocol"),
        }.items()
        if entry is not None
    }
    candidates = [
        *([_candidate_summary(primary)] if primary is not None else []),
        *[_candidate_summary(candidate) for candidate in resolution.alternative_projects],
    ]
    return AgentObservation(
        input_file=input_file,
        repository_candidates=candidates,
        selected_project=_candidate_summary(primary) if primary is not None else None,
        metadata_evidence=metadata_evidence,
        asset_evidence=_asset_summary(asset),
        resource_state=resource_state or {},
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_core.py::test_build_agent_observation_summarizes_project_metadata_and_asset -q
```

Expected: PASS.

## Chunk 2: Decision Trace And Plan Builders

### Task 3: Build Decision Trace From Existing Results

**Files:**
- Create: `src/agent/agent_core/decision_trace.py`
- Test: `tests/test_agent_core.py`

- [ ] **Step 1: Write failing decision trace test**

Add:

```python
from agent.agent_core.decision_trace import build_agent_decision_trace
from agent.models import AttributeSet, AttributeValue


def _attr(value, confidence=0.9, source="llm_confirmed", evidence="evidence"):
    return AttributeValue(value=value, confidence=confidence, source=source, evidence_excerpt=evidence, conflict_flag=False)


def test_build_agent_decision_trace_records_resolution_and_attributes():
    resolution = ProjectResolution(
        primary_project=ProjectCandidate(
            repository="iprox",
            project_accession="IPX0000753001",
            matched_file="Yeast_R3.raw",
            match_type="exact",
            match_score=100,
            metadata_consistency=0.95,
            evidence=["exact file match"],
        ),
        resolution_reason="Selected iProX",
        resolution_confidence=1.0,
    )
    attributes = AttributeSet(
        acquisition_mode=_attr("DDA", 1.0, evidence="DDA metadata"),
        species=_attr("Saccharomyces cerevisiae", 1.0, evidence="yeast metadata"),
        instrument_name=_attr("Q Exactive", 0.9, source="iprox.project_xml", evidence="instrument metadata"),
        instrument_family=_attr("orbitrap", 0.9),
        enzyme=_attr("Trypsin/Lys-C", 0.94, evidence="lysine-specific endoproteinase and trypsin"),
        labeling_strategy=_attr("label-free", 0.8),
        fixed_mods=_attr([], 0.8),
        variable_mods=_attr(["M[15.99]"], 0.8),
        fractionation_hint=_attr(None, 0.0, source="none"),
        search_parameter_hints=_attr({"recommended_workflow_name": "Default.workflow"}, 0.9),
    )

    trace = build_agent_decision_trace(resolution, attributes)

    assert trace[0].decision_type == "project_selection"
    assert trace[0].gate_action == "auto_accept"
    enzyme = next(item for item in trace if item.decision_type == "enzyme_inference")
    assert enzyme.selected_value == "Trypsin/Lys-C"
    assert enzyme.gate_action == "evidence_gated_accept"
    assert enzyme.risk_level == "medium"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_core.py::test_build_agent_decision_trace_records_resolution_and_attributes -q
```

Expected: FAIL because builder does not exist.

- [ ] **Step 3: Implement decision trace builder**

Create `src/agent/agent_core/decision_trace.py`:

```python
from __future__ import annotations

from typing import Any

from agent.agent_core.models import AgentDecisionRecord, AgentRisk, GateAction
from agent.models import AttributeSet, AttributeValue, ProjectResolution


def _risk_for_attribute(name: str, attribute: AttributeValue) -> AgentRisk:
    if attribute.conflict_flag or attribute.confidence < 0.7:
        return AgentRisk.HIGH
    if name in {"species", "acquisition_mode", "enzyme"} and attribute.confidence < 0.95:
        return AgentRisk.MEDIUM
    if name == "enzyme" and any(token in str(attribute.value).lower() for token in ["/", "+", ";"]):
        return AgentRisk.MEDIUM
    return AgentRisk.LOW


def _gate_for_risk(risk: AgentRisk, confidence: float) -> GateAction:
    if risk == AgentRisk.HIGH:
        return GateAction.REVIEW_REQUIRED
    if risk == AgentRisk.MEDIUM:
        return GateAction.EVIDENCE_GATED_ACCEPT if confidence >= 0.85 else GateAction.REVIEW_REQUIRED
    return GateAction.AUTO_ACCEPT


def _attribute_decision(index: int, decision_type: str, attribute: AttributeValue) -> AgentDecisionRecord:
    risk = _risk_for_attribute(decision_type.replace("_inference", ""), attribute)
    return AgentDecisionRecord(
        id=f"D{index:03d}",
        decision_type=decision_type,
        selected_value=attribute.value,
        confidence=attribute.confidence,
        evidence=[attribute.evidence_excerpt] if attribute.evidence_excerpt else [],
        alternatives=[],
        risk_level=risk,
        gate_action=_gate_for_risk(risk, attribute.confidence),
    )


def build_agent_decision_trace(
    resolution: ProjectResolution,
    attributes: AttributeSet,
) -> list[AgentDecisionRecord]:
    decisions: list[AgentDecisionRecord] = []
    primary = resolution.primary_project
    project_risk = AgentRisk.HIGH if resolution.needs_review or resolution.resolution_confidence < 0.85 else AgentRisk.LOW
    decisions.append(
        AgentDecisionRecord(
            id="D001",
            decision_type="project_selection",
            selected_value=primary.project_accession if primary else None,
            confidence=resolution.resolution_confidence,
            evidence=[resolution.resolution_reason, *(primary.evidence if primary else [])],
            alternatives=[
                {
                    "value": candidate.project_accession,
                    "repository": candidate.repository,
                    "confidence": candidate.metadata_consistency,
                    "reason_rejected": "not selected as primary project",
                }
                for candidate in resolution.alternative_projects
            ],
            risk_level=project_risk,
            gate_action=_gate_for_risk(project_risk, resolution.resolution_confidence),
        )
    )
    attribute_map: list[tuple[str, AttributeValue]] = [
        ("acquisition_mode_inference", attributes.acquisition_mode),
        ("species_inference", attributes.species),
        ("instrument_inference", attributes.instrument_name),
        ("enzyme_inference", attributes.enzyme),
        ("labeling_inference", attributes.labeling_strategy),
        ("search_parameter_selection", attributes.search_parameter_hints),
    ]
    for offset, (decision_type, attribute) in enumerate(attribute_map, start=2):
        decisions.append(_attribute_decision(offset, decision_type, attribute))
    return decisions
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_core.py::test_build_agent_decision_trace_records_resolution_and_attributes -q
```

Expected: PASS.

### Task 4: Build Agent Plan Summary

**Files:**
- Create: `src/agent/agent_core/plan.py`
- Test: `tests/test_agent_core.py`

- [ ] **Step 1: Write failing plan builder test**

Add:

```python
from pathlib import Path

from agent.agent_core.plan import build_agent_plan_summary
from agent.models import DdaExecutionPlan


def test_build_agent_plan_summary_records_workflow_database_and_parameters(tmp_path: Path):
    attributes = AttributeSet(
        acquisition_mode=_attr("DDA", 1.0),
        species=_attr("Saccharomyces cerevisiae", 1.0),
        instrument_name=_attr("Q Exactive", 0.9),
        instrument_family=_attr("orbitrap", 0.9),
        enzyme=_attr("Trypsin/Lys-C", 0.94),
        labeling_strategy=_attr("label-free", 0.8),
        fixed_mods=_attr([], 0.8),
        variable_mods=_attr(["M[15.99]"], 0.8),
        fractionation_hint=_attr(None, 0.0, source="none"),
        search_parameter_hints=_attr(
            {
                "recommended_workflow_name": "Default.workflow",
                "recommended_fasta_name": "uniprot_yeast.fasta",
                "recommended_fasta_source": "UniProt",
                "precursor_tol": "20ppm",
                "fragment_tol": "20ppm",
                "missed_cleavages": 3,
            },
            0.9,
        ),
    )
    plan = DdaExecutionPlan(
        task_id="task-1",
        source_file_name="Yeast_R3.raw",
        source_data_path=tmp_path / "Yeast_R3.mzML",
        raw_data_type="mzml",
        fasta_path=tmp_path / "uniprot_yeast.fasta",
        fasta_selection_mode="inferred",
        fasta_download_url="https://example.test/yeast.fasta",
        fragpipe_workflow_path=tmp_path / "Default.workflow",
        manifest_path=tmp_path / "fragpipe.fp-manifest",
        converter_config_path=tmp_path / "converter_config.json",
        rawspectrum_output_path=tmp_path / "rawspectrum.parquet",
        fragpipe_workdir=tmp_path / "fragpipe",
        expected_pin_path=tmp_path / "exp" / "Yeast_R3_edited.pin",
        expected_pin_glob=str(tmp_path / "exp" / "*_edited.pin"),
        output_paths={"fp_msdt": tmp_path / "msdt.parquet"},
        thread_num=4,
    )

    summary = build_agent_plan_summary(plan, attributes)

    assert summary.selected_workflow["name"] == "Default.workflow"
    assert summary.selected_database["fasta_selection_mode"] == "inferred"
    assert summary.search_parameters["enzyme"] == "Trypsin/Lys-C"
    assert summary.search_parameters["missed_cleavages"] == 3
    assert summary.execution_gate == "allowed"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_core.py::test_build_agent_plan_summary_records_workflow_database_and_parameters -q
```

Expected: FAIL because builder does not exist.

- [ ] **Step 3: Implement plan builder**

Create `src/agent/agent_core/plan.py`:

```python
from __future__ import annotations

from typing import Any

from agent.agent_core.models import AgentPlanSummary
from agent.models import AttributeSet, DdaExecutionPlan


def _hints(attributes: AttributeSet) -> dict[str, Any]:
    value = attributes.search_parameter_hints.value
    return dict(value) if isinstance(value, dict) else {}


def build_agent_plan_summary(plan: DdaExecutionPlan, attributes: AttributeSet) -> AgentPlanSummary:
    hints = _hints(attributes)
    return AgentPlanSummary(
        selected_database={
            "fasta_path": plan.fasta_path,
            "fasta_name": plan.fasta_path.name,
            "fasta_selection_mode": plan.fasta_selection_mode,
            "fasta_download_url": plan.fasta_download_url,
            "recommended_fasta_name": hints.get("recommended_fasta_name"),
            "recommended_fasta_source": hints.get("recommended_fasta_source"),
        },
        selected_workflow={
            "path": plan.fragpipe_workflow_path,
            "name": plan.fragpipe_workflow_path.name,
            "recommended_workflow_name": hints.get("recommended_workflow_name"),
            "workflow_parameter_overrides": hints.get("workflow_parameter_overrides") or {},
        },
        search_parameters={
            "acquisition_mode": attributes.acquisition_mode.value,
            "species": attributes.species.value,
            "instrument": attributes.instrument_name.value,
            "enzyme": attributes.enzyme.value,
            "labeling_strategy": attributes.labeling_strategy.value,
            "fixed_mods": attributes.fixed_mods.value,
            "variable_mods": attributes.variable_mods.value,
            "precursor_tol": hints.get("precursor_tol") or hints.get("precursor_tolerance"),
            "fragment_tol": hints.get("fragment_tol") or hints.get("fragment_tolerance"),
            "missed_cleavages": hints.get("missed_cleavages"),
        },
        resource_policy={
            "thread_num": plan.thread_num,
            "raw_data_type": plan.raw_data_type,
        },
        risk_assessment={
            "needs_review": plan.needs_review,
            "blocking_issue_count": len(plan.blocking_issues),
        },
        execution_gate="review_required" if plan.needs_review else "allowed",
        blocking_issues=plan.blocking_issues,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_core.py::test_build_agent_plan_summary_records_workflow_database_and_parameters -q
```

Expected: PASS.

## Chunk 3: Artifact Writer And Integration

### Task 5: Write Agent Audit Artifacts

**Files:**
- Create: `src/agent/agent_core/audit.py`
- Test: `tests/test_agent_core.py`

- [ ] **Step 1: Write failing artifact writer test**

Add:

```python
from agent.agent_core.audit import write_agent_audit_artifacts


def test_write_agent_audit_artifacts_writes_three_json_files(tmp_path: Path):
    observation = build_agent_observation("Yeast_R3.raw", resolution, context, asset=asset)
    decisions = build_agent_decision_trace(resolution, attributes)
    plan_summary = build_agent_plan_summary(plan, attributes)

    paths = write_agent_audit_artifacts(tmp_path, observation, plan_summary, decisions)

    assert paths.observation.exists()
    assert paths.plan.exists()
    assert paths.decision_trace.exists()
    assert paths.observation.name == "agent_observation.json"
    assert "Trypsin/Lys-C" in paths.decision_trace.read_text(encoding="utf-8")
```

In this test, reuse fixtures/helper constructors already created in previous tests. If the file becomes repetitive, extract small local helpers inside `tests/test_agent_core.py`.

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_core.py::test_write_agent_audit_artifacts_writes_three_json_files -q
```

Expected: FAIL because writer does not exist.

- [ ] **Step 3: Implement artifact writer**

Create `src/agent/agent_core/audit.py`:

```python
from __future__ import annotations

from pathlib import Path

from agent.agent_core.models import AgentAuditArtifactPaths, AgentDecisionRecord, AgentObservation, AgentPlanSummary
from agent.utils import write_json


def write_agent_audit_artifacts(
    output_dir: str | Path,
    observation: AgentObservation,
    plan: AgentPlanSummary,
    decision_trace: list[AgentDecisionRecord],
) -> AgentAuditArtifactPaths:
    output_dir = Path(output_dir)
    observation_path = write_json(output_dir / "agent_observation.json", observation)
    plan_path = write_json(output_dir / "agent_plan.json", plan)
    trace_path = write_json(output_dir / "agent_decision_trace.json", {"decisions": decision_trace})
    return AgentAuditArtifactPaths(
        observation=observation_path,
        plan=plan_path,
        decision_trace=trace_path,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_core.py::test_write_agent_audit_artifacts_writes_three_json_files -q
```

Expected: PASS.

### Task 6: Add Convenience Builder For Existing Pipeline Results

**Files:**
- Modify: `src/agent/agent_core/audit.py`
- Test: `tests/test_agent_core.py`

- [ ] **Step 1: Write failing convenience builder test**

Add:

```python
from agent.agent_core.audit import write_agent_audit_for_result
from agent.models import PridePlanResult


def test_write_agent_audit_for_result_uses_existing_pipeline_objects(tmp_path: Path):
    result = PridePlanResult(
        resolution=resolution,
        context=context,
        asset=asset,
        attributes=attributes,
        plan=plan,
    )

    paths = write_agent_audit_for_result(tmp_path, result)

    assert paths.observation.exists()
    assert paths.plan.exists()
    assert paths.decision_trace.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_core.py::test_write_agent_audit_for_result_uses_existing_pipeline_objects -q
```

Expected: FAIL because convenience function does not exist.

- [ ] **Step 3: Implement convenience builder**

Append to `src/agent/agent_core/audit.py`:

```python
from agent.agent_core.decision_trace import build_agent_decision_trace
from agent.agent_core.observation import build_agent_observation
from agent.agent_core.plan import build_agent_plan_summary
from agent.models import PridePlanResult


def write_agent_audit_for_result(
    output_dir: str | Path,
    result: PridePlanResult,
    *,
    resource_state: dict | None = None,
) -> AgentAuditArtifactPaths:
    observation = build_agent_observation(
        result.context.file_name,
        result.resolution,
        result.context,
        asset=result.asset,
        resource_state=resource_state,
    )
    plan = build_agent_plan_summary(result.plan, result.attributes)
    decisions = build_agent_decision_trace(result.resolution, result.attributes)
    return write_agent_audit_artifacts(output_dir, observation, plan, decisions)
```

- [ ] **Step 4: Run focused agent core tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_core.py -q
```

Expected: all `test_agent_core.py` tests pass.

### Task 7: Integrate Agent Audit Writing Into Web Task Flow

**Files:**
- Modify: `src/agent/web/app.py`
- Test: Prefer an existing focused web test if available; otherwise add a small unit-style test around a helper.

- [ ] **Step 1: Extract a web helper test if direct full route testing is too heavy**

Search for existing output package tests:

```powershell
rg -n "parameter_audit|decision_trace|task_state|converter_config" tests/test_web.py
```

If there is an existing helper for package generation, extend it to assert:

```python
assert (output_dir / "agent_observation.json").exists()
assert (output_dir / "agent_plan.json").exists()
assert (output_dir / "agent_decision_trace.json").exists()
```

If no suitable helper exists, add a pure helper in `src/agent/web/app.py`:

```python
def _write_agent_audit_package(output_dir: Path, result: Any) -> None:
    from agent.agent_core.audit import write_agent_audit_for_result

    write_agent_audit_for_result(output_dir, result)
```

Then write a test that monkeypatches `write_agent_audit_for_result` and verifies the helper calls it.

- [ ] **Step 2: Run the failing test**

Run the selected test, for example:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web.py::<selected_test_name> -q
```

Expected: FAIL because audit files/helper call are missing.

- [ ] **Step 3: Implement integration**

In `src/agent/web/app.py`, after `result = service.run_planning(...)` or equivalent planning result creation, call:

```python
from agent.agent_core.audit import write_agent_audit_for_result

write_agent_audit_for_result(output_dir, result)
```

Place it after `result` has `resolution`, `context`, `asset`, `attributes`, and `plan`, and before packaging ZIPs or returning task completion. Wrap only the call in a small `try/except` that logs a debug message if audit writing fails; do not fail the whole task in Phase 1.

Expected locations:

- parameter-only completion path
- prepare package path
- full workflow path before Docker or before final packaging

- [ ] **Step 4: Run selected web tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web.py::<selected_test_name> -q
```

Expected: PASS.

### Task 8: Integrate Agent Audit Writing Into CLI/Orchestrator If Needed

**Files:**
- Modify: `src/agent/orchestrator/pipeline.py` or `src/agent/cli.py`
- Test: existing CLI or pipeline tests if narrow.

- [ ] **Step 1: Check where CLI writes package artifacts**

Inspect:

```powershell
rg -n "parameter_audit|decision_trace|write_json|task_state|one-click-run" src/agent/cli.py src/agent/orchestrator/pipeline.py
```

- [ ] **Step 2: Add focused failing test only if CLI artifact writing is covered**

If a CLI test already checks output files, extend it to assert the three agent files. If not, skip CLI integration for Phase 1 and document that web/orchestrator integration is the first supported path.

- [ ] **Step 3: Implement the smallest integration**

Prefer calling `write_agent_audit_for_result(output_dir, result)` from the one-click path after planning and before returning.

- [ ] **Step 4: Run selected CLI/pipeline tests**

Run the narrow tests discovered in Step 1.

Expected: PASS.

## Chunk 4: Final Verification

### Task 9: Run Focused Regression

**Files:**
- No edits.

- [ ] **Step 1: Run agent core tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_core.py -q
```

Expected: PASS.

- [ ] **Step 2: Run affected existing suites**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_llm.py tests/test_runtime.py tests/test_web.py tests/test_inference.py -q
```

Expected: PASS.

- [ ] **Step 3: Run full suite**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: PASS.

### Task 10: Manual Artifact Sanity Check

**Files:**
- No edits unless a bug is found.

- [ ] **Step 1: Run a lightweight parameter-only task**

Use an existing low-cost example that does not trigger full Docker. Example command may need adjustment to the current CLI:

```powershell
.\.venv\Scripts\python.exe -m agent.cli one-click-run "Yeast_R3.raw" --mode parameters --repository iprox
```

Expected: output directory is created without Docker execution.

- [ ] **Step 2: Inspect generated agent files**

Confirm these exist in the run directory:

```text
agent_observation.json
agent_plan.json
agent_decision_trace.json
```

Confirm:

- `agent_observation.json` lists selected project and metadata evidence.
- `agent_plan.json` lists workflow, FASTA, parameters, and execution gate.
- `agent_decision_trace.json` contains project, species, acquisition, enzyme, and search parameter decisions.

## Non-Goals For This Plan

Do not implement:

- `recovery_audit.json`
- automatic retry actions
- search-space optimization
- LLM-guided recovery
- front-end Agent Reasoning panel
- benchmark export

Those belong to later plans after Phase 1 is merged and stable.
