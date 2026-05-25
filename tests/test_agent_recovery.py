from __future__ import annotations

import json
from pathlib import Path

from agent.agent_core.recovery import build_recovery_audit, write_recovery_audit
from agent.agent_core.recovery_policy import recommend_recovery
from agent.execution.outputs import ExecutionFailureEvent


def test_build_recovery_audit_records_schema_failure_evidence_and_redacts_secrets(tmp_path: Path):
    audit = build_recovery_audit(
        task_id="task-1",
        input_file="sample.raw",
        output_dir=tmp_path,
        run_mode="full",
        repository="iprox",
        project_accession="IPX0000753001",
        stage="execution",
        events=[
            ExecutionFailureEvent(
                category="missing_msdt_output",
                reason=f"Missing required output: MSDT parquet: {tmp_path / 'msdt' / 'sample.parquet'}",
                evidence_kind="missing_output",
                path=tmp_path / "msdt" / "sample.parquet",
            ),
            ExecutionFailureEvent(
                category="process_failed",
                reason="MSDT-Converter log marker: miss mzml_fp_pin_path api_key=sk-testsecret",
                evidence_kind="log_marker",
                marker="miss mzml_fp_pin_path",
            ),
        ],
        artifacts={"task_state_json": tmp_path / "task_state.json"},
    )

    data = audit.model_dump(mode="json")

    assert data["schema_version"] == "recovery-audit/v1"
    assert data["task"]["task_id"] == "task-1"
    assert data["task"]["repository"] == "iprox"
    assert data["failure"]["category"] == "missing_msdt_output"
    assert data["failure"]["stage"] == "execution"
    assert data["failure"]["evidence"][0]["kind"] == "missing_output"
    assert data["failure"]["evidence"][0]["path"].endswith("sample.parquet")
    assert "[redacted]" in json.dumps(data, ensure_ascii=False)
    assert "sk-testsecret" not in json.dumps(data, ensure_ascii=False)
    assert data["recovery"]["decision"] == "manual_required"
    assert data["recovery"]["allowed_action"] == "mark_review_required"
    assert data["integrity"]["redaction_applied"] is True


def test_recovery_policy_allows_thread_reduction_for_memory_pressure():
    decision = recommend_recovery(
        category="insufficient_memory",
        current_threads=10,
        requested_action=None,
    )

    assert decision.decision == "retry_scheduled"
    assert decision.allowed_action == "reduce_threads"
    assert decision.requires_human is False
    assert decision.parameters["thread_num"] == 5


def test_recovery_policy_blocks_biological_changes_without_review():
    decision = recommend_recovery(
        category="parameter_conflict",
        current_threads=4,
        requested_action="change_species_database",
    )

    assert decision.decision == "manual_required"
    assert decision.allowed_action == "mark_review_required"
    assert decision.requires_human is True
    assert any("biological" in item.lower() for item in decision.next_manual_actions)


def test_write_recovery_audit_persists_json(tmp_path: Path):
    audit = build_recovery_audit(
        task_id="task-2",
        input_file="sample.raw",
        output_dir=tmp_path,
        run_mode="prepare",
        repository="pride",
        project_accession="PXD000001",
        stage="asset_preparation",
        events=[
            ExecutionFailureEvent(
                category="conversion_failure",
                reason="Primary converter failed",
                evidence_kind="exception",
                marker="Primary converter failed",
            )
        ],
    )

    path = write_recovery_audit(tmp_path, audit)

    assert path.name == "recovery_audit.json"
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["failure"]["category"] == "conversion_failure"
