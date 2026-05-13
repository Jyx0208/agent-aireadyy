from __future__ import annotations

from pathlib import Path

from agent.execution.outputs import execution_failure_reasons, missing_required_execution_outputs
from agent.models import DdaExecutionPlan


def _mzml_plan(tmp_path: Path) -> DdaExecutionPlan:
    return DdaExecutionPlan(
        task_id="task-output-check",
        source_file_name="sample.raw",
        source_data_path=tmp_path / "assets" / "prepared" / "sample.mzML",
        raw_data_type="mzml",
        fasta_path=tmp_path / "fasta" / "reference.fasta",
        fasta_selection_mode="reviewed",
        fasta_download_url=None,
        fragpipe_workflow_path=tmp_path / "workflows" / "Default.workflow",
        manifest_path=tmp_path / "fragpipe" / "fragpipe-files.fp-manifest",
        converter_config_path=tmp_path / "converter_config.json",
        rawspectrum_output_path=tmp_path / "rawspectrum" / "sample_rawspectrum.parquet",
        fragpipe_workdir=tmp_path / "fragpipe",
        expected_pin_path=tmp_path / "fragpipe" / "exp" / "sample_edited.pin",
        expected_pin_glob=str(tmp_path / "fragpipe" / "exp" / "sample_edited.pin"),
        output_paths={"fp_msdt": tmp_path / "msdt" / "sample_fp_msdt.parquet"},
    )


def test_missing_required_execution_outputs_reports_pin_and_msdt_after_partial_success(tmp_path: Path):
    plan = _mzml_plan(tmp_path)
    plan.rawspectrum_output_path.parent.mkdir(parents=True, exist_ok=True)
    plan.rawspectrum_output_path.write_text("rawspectrum", encoding="utf-8")

    missing = missing_required_execution_outputs(plan)

    assert any("FragPipe PIN" in item and str(plan.expected_pin_path) in item for item in missing)
    assert any("MSDT parquet" in item and str(plan.output_paths["fp_msdt"]) in item for item in missing)
    assert not any("raw spectrum" in item for item in missing)


def test_execution_failure_reasons_flags_internal_converter_failure_with_zero_exit(tmp_path: Path):
    plan = _mzml_plan(tmp_path)
    for path in (plan.rawspectrum_output_path, plan.expected_pin_path, plan.output_paths["fp_msdt"]):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok", encoding="utf-8")

    reasons = execution_failure_reasons(
        plan,
        returncode=0,
        stdout="MSFragger\nInsufficient memory!\nProcess 'MSFragger' finished, exit code: 1\n",
        stderr="",
    )

    assert any("insufficient memory" in reason.lower() for reason in reasons)
    assert any("internal process exited non-zero" in reason.lower() for reason in reasons)
