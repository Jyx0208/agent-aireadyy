from __future__ import annotations

import json
from pathlib import Path

from agent.audit.review import append_review_item, build_review_item, build_task_state_snapshot, write_task_state
from agent.models import (
    AttributeSet,
    AttributeValue,
    DdaExecutionPlan,
    ProjectCandidate,
    ProjectContext,
    ProjectResolution,
)
from agent.orchestrator.pipeline import AgentService


def _attributes() -> AttributeSet:
    return AttributeSet(
        acquisition_mode=AttributeValue(value="unknown", confidence=0.0, source="none", evidence_excerpt="", conflict_flag=False),
        species=AttributeValue(value="Homo sapiens", confidence=0.9, source="rule", evidence_excerpt="human", conflict_flag=False),
        instrument_name=AttributeValue(value="Orbitrap Fusion Lumos", confidence=0.9, source="rule", evidence_excerpt="instrument", conflict_flag=False),
        instrument_family=AttributeValue(value="orbitrap", confidence=0.9, source="rule", evidence_excerpt="family", conflict_flag=False),
        enzyme=AttributeValue(value="Lys-C", confidence=0.9, source="rule", evidence_excerpt="enzyme", conflict_flag=False),
        labeling_strategy=AttributeValue(value="label-free", confidence=0.8, source="default", evidence_excerpt="default", conflict_flag=False),
        fixed_mods=AttributeValue(value=["C[57.02]"], confidence=0.7, source="default", evidence_excerpt="mods", conflict_flag=False),
        variable_mods=AttributeValue(value=["M[15.99]"], confidence=0.7, source="default", evidence_excerpt="mods", conflict_flag=False),
        fractionation_hint=AttributeValue(value=None, confidence=0.0, source="none", evidence_excerpt="", conflict_flag=False),
        search_parameter_hints=AttributeValue(value={}, confidence=0.0, source="none", evidence_excerpt="", conflict_flag=False),
    )


def test_append_review_item_persists_review_queue(tmp_path: Path):
    item = build_review_item(
        task_id="task-001",
        source_file="WT_5_Lys-c.raw",
        project_accession="PXD123456",
        stage="planning",
        reasons=["No validated FragPipe workflow profile matches the inferred attributes."],
    )

    queue_path = tmp_path / "review_queue.json"
    append_review_item(queue_path, item)
    data = json.loads(queue_path.read_text(encoding="utf-8"))

    assert len(data) == 1
    assert data[0]["task_id"] == "task-001"
    assert data[0]["stage"] == "planning"


def test_write_task_state_snapshot_persists_json(tmp_path: Path):
    snapshot = build_task_state_snapshot(
        task_id="task-001",
        status="needs_review",
        stage="planning",
        source_file="WT_5_Lys-c.raw",
        project_accession="PXD123456",
        notes=["missing acquisition mode"],
    )

    state_path = tmp_path / "task_state.json"
    write_task_state(state_path, snapshot)
    data = json.loads(state_path.read_text(encoding="utf-8"))

    assert data["status"] == "needs_review"
    assert data["stage"] == "planning"
    assert data["source_file"] == "WT_5_Lys-c.raw"


def test_write_task_bundle_writes_review_queue_when_plan_needs_review(tmp_path: Path):
    service = AgentService(pride_client=None)
    resolution = ProjectResolution(
        primary_project=ProjectCandidate(
            project_accession="PXD123456",
            matched_file="WT_5_Lys-c.raw",
            match_type="exact",
            match_score=100,
            evidence=["exact"],
            metadata_consistency=1.0,
        )
    )
    context = ProjectContext(project_accession="PXD123456", file_name="WT_5_Lys-c.raw")
    attributes = _attributes()
    plan = DdaExecutionPlan(
        task_id="task-001",
        source_file_name="WT_5_Lys-c.raw",
        source_data_path=tmp_path / "assets" / "prepared" / "WT_5_Lys-c.mzML",
        raw_data_type="mzml",
        fasta_path=tmp_path / "fasta" / "ref.fasta",
        fasta_selection_mode="defaulted",
        fragpipe_workflow_path=tmp_path / "fragpipe" / "workflow.workflow",
        manifest_path=tmp_path / "fragpipe" / "fragpipe-files.fp-manifest",
        converter_config_path=tmp_path / "converter_config.json",
        rawspectrum_output_path=tmp_path / "rawspectrum" / "rawspectrum.parquet",
        fragpipe_workdir=tmp_path / "fragpipe",
        expected_pin_path=tmp_path / "fragpipe" / "exp" / "file.pin",
        expected_pin_glob=str(tmp_path / "fragpipe" / "exp" / "file.pin"),
        output_paths={"fp_msdt": tmp_path / "msdt" / "file.parquet"},
        needs_review=True,
        blocking_issues=["No validated FragPipe workflow profile matches the inferred attributes."],
    )

    service.write_task_bundle(tmp_path, resolution, context, attributes, plan)

    assert (tmp_path / "task_state.json").exists()
    assert (tmp_path / "review_queue.json").exists()
    state = json.loads((tmp_path / "task_state.json").read_text(encoding="utf-8"))
    queue = json.loads((tmp_path / "review_queue.json").read_text(encoding="utf-8"))

    assert state["status"] == "needs_review"
    assert queue[0]["task_id"] == "task-001"
