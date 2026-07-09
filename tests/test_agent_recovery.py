from __future__ import annotations

import json
from pathlib import Path

from agent.agent_core.recovery import build_recovery_audit, write_recovery_audit
from agent.agent_core.recovery_policy import recommend_recovery
from agent.agent_core.recovery_report import analyze_agent_recovery, build_agent_recovery_report
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


def test_recovery_report_prioritizes_corrupt_raw_over_generic_conversion(tmp_path: Path):
    (tmp_path / "review_queue.json").write_text(
        json.dumps(
            {
                "status": "needs_review",
                "reason": "Local asset preparation failed: [RawFileImpl::ctor()] Corrupt RAW file Z:\\data\\sample.raw",
            }
        ),
        encoding="utf-8",
    )

    report = build_agent_recovery_report(tmp_path)

    assert report.status == "needs_action"
    assert report.primary_issue == "corrupt_raw_file"
    assert report.signals[0].category == "corrupt_raw_file"
    assert report.signals[0].requires_human is False


def test_recovery_report_detects_missing_peaklist_as_recoverable(tmp_path: Path):
    (tmp_path / "mini_e2e_summary.json").write_text(
        json.dumps(
            {
                "tasks": {
                    "denovo": {"status": "blocked", "blockers": ["needs_peaklist"]},
                    "fragment_intensity_prediction": {"status": "blocked", "blockers": ["missing_peaklist"]},
                }
            }
        ),
        encoding="utf-8",
    )

    report = build_agent_recovery_report(tmp_path)

    assert report.primary_issue == "missing_peaklist"
    assert report.signals[0].auto_executable is True
    assert "generate MGF" in report.recommended_next_step


def test_recovery_report_prefers_low_psm_msbooster_over_task_specific_mismatch(tmp_path: Path):
    (tmp_path / "run.log").write_text(
        "Warning: not enough target PSMs are available for regression\n"
        "RT regression using 0 PSMs\n"
        "downstream task reported spectrum_not_matched\n",
        encoding="utf-8",
    )

    report = build_agent_recovery_report(tmp_path)

    assert report.primary_issue == "low_psm_msbooster"
    assert [signal.category for signal in report.signals][:2] == ["low_psm_msbooster", "spectrum_mismatch"]


def test_recovery_report_detects_partial_outputs_after_fragpipe_postprocess_failure(tmp_path: Path):
    (tmp_path / "task_state.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "notes": [
                    "MSDT-Converter internal process exited non-zero.",
                    "MSDT-Converter log marker: Process returned non-zero exit code",
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "task_history.json").write_text(
        json.dumps({"events": [{"message": "Process 'PhilosopherFilter' finished, exit code: 2"}]}),
        encoding="utf-8",
    )
    for path in [
        tmp_path / "fragpipe" / "exp" / "sample_edited.pin",
        tmp_path / "rawspectrum" / "sample_rawspectrum.parquet",
        tmp_path / "msdt" / "sample_fp_msdt.parquet",
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not-empty", encoding="utf-8")

    report = build_agent_recovery_report(tmp_path)

    assert report.status == "needs_action"
    assert report.workflow_outcome == "failed_with_usable_partial_outputs"
    assert report.usable_partial_outputs is True
    assert report.primary_issue == "partial_outputs_available"
    assert report.signals[0].auto_executable is True
    assert report.signals[0].requires_human is False
    assert "partial AI-ready export" in report.recommended_next_step
    assert sorted(report.summary["partial_outputs"]) == ["msdt_parquet", "pin", "rawspectrum_parquet"]
    assert report.summary["workflow_outcome"] == "failed_with_usable_partial_outputs"
    assert report.summary["usable_partial_outputs"] is True


def test_recovery_report_does_not_treat_project_resolution_artifact_as_review_gate(tmp_path: Path):
    (tmp_path / "task_state.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "notes": [
                    "workflow_path: /workspace/workflows/TMT10-phospho.workflow",
                    "MSDT-Converter log marker: miss mzml_fp_pin_path",
                    "Missing required output: FragPipe PIN: sample_edited.pin",
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "project_resolution.json").write_text(
        json.dumps({"project_accession": "PXD123456", "resolution_confidence": 1.0}),
        encoding="utf-8",
    )
    for path in [
        tmp_path / "fragpipe" / "exp" / "sample.pin",
        tmp_path / "fragpipe" / "exp" / "psm.tsv",
        tmp_path / "rawspectrum" / "sample_rawspectrum.parquet",
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not-empty", encoding="utf-8")

    report = build_agent_recovery_report(tmp_path)

    assert report.workflow_outcome == "failed_with_usable_partial_outputs"
    assert report.primary_issue == "partial_outputs_available"
    assert "review_gate_blocked" not in [signal.category for signal in report.signals]


def test_recovery_report_detects_msdt_feature_missing_with_partial_outputs(tmp_path: Path):
    (tmp_path / "task_state.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "notes": [
                    "Error occurs when generate sample_fp_msdt.parquet: Usecols do not match columns, "
                    "columns expected but not found: ['delta_RT_loess', 'unweighted_spectral_entropy']",
                    "generate msdt fail",
                ],
            }
        ),
        encoding="utf-8",
    )
    for path in [
        tmp_path / "fragpipe" / "exp" / "sample.pin",
        tmp_path / "fragpipe" / "exp" / "psm.tsv",
        tmp_path / "rawspectrum" / "sample_rawspectrum.parquet",
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not-empty", encoding="utf-8")

    report = build_agent_recovery_report(tmp_path)

    assert report.workflow_outcome == "failed_with_usable_partial_outputs"
    assert report.primary_issue == "msdt_feature_missing"
    assert "msdt_feature_missing" in [signal.category for signal in report.signals]
    assert "partial_outputs_available" in [signal.category for signal in report.signals]
    assert report.usable_partial_outputs is True


def test_recovery_report_completed_run_ignores_failure_like_log_terms(tmp_path: Path):
    (tmp_path / "task_state.json").write_text(
        json.dumps({"status": "completed"}),
        encoding="utf-8",
    )
    (tmp_path / "run.log").write_text(
        "MSBooster output columns include delta_RT_loess and unweighted_spectral_entropy\n",
        encoding="utf-8",
    )
    for path in [
        tmp_path / "fragpipe" / "exp" / "sample_edited.pin",
        tmp_path / "rawspectrum" / "sample_rawspectrum.parquet",
        tmp_path / "msdt" / "sample_fp_msdt.parquet",
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not-empty", encoding="utf-8")

    report = build_agent_recovery_report(tmp_path)

    assert report.status == "no_recovery_needed"
    assert report.workflow_outcome == "completed"
    assert report.usable_partial_outputs is False
    assert report.primary_issue is None
    assert report.signals == []


def test_analyze_agent_recovery_writes_json_and_markdown(tmp_path: Path):
    (tmp_path / "ai_ready_validation_report.json").write_text(
        json.dumps({"tasks": {"psm_scoring": {"status": "blocked", "blockers": ["needs_target_decoy_labels"]}}}),
        encoding="utf-8",
    )
    output_dir = tmp_path / "recovery"

    paths = analyze_agent_recovery(tmp_path, output_dir=output_dir)

    assert paths["agent_recovery_report_json"].exists()
    assert paths["agent_recovery_report_md"].exists()
    data = json.loads(paths["agent_recovery_report_json"].read_text(encoding="utf-8"))
    assert data["primary_issue"] == "missing_target_decoy"
    assert "needs_target_decoy_labels" in paths["agent_recovery_report_md"].read_text(encoding="utf-8")
