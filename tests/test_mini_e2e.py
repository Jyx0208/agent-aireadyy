from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from agent.ai_ready.mini_e2e import mini_e2e_parameters_only_placeholder, validate_agent_run_ai_ready_mini
from agent.cli import app


def _write_tsv(path: Path, rows: list[dict[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, sep="\t", index=False)
    return path


def _write_mgf(path: Path, *, scan: int = 101) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "BEGIN IONS",
                f"TITLE=scan={scan}",
                f"SCANS={scan}",
                "PEPMASS=500.2",
                "CHARGE=2+",
                "98.06004 1000",
                "147.11280 500",
                "END IONS",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _write_failed_upstream_log(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "runtime.log").write_text(
        "\n".join(
            [
                "Process 'PhilosopherFilter' finished, exit code: 2",
                "Process returned non-zero exit code",
            ]
        ),
        encoding="utf-8",
    )


def test_mini_e2e_exports_rt_from_agent_run(tmp_path: Path):
    run_dir = tmp_path / "run"
    _write_tsv(
        run_dir / "fragpipe" / "exp" / "peptide.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Retention Time (min)": 12.5, "PSM Q-Value": 0.001}],
    )

    result = validate_agent_run_ai_ready_mini(
        agent_run_dir=run_dir,
        task_types=["rt_prediction"],
        output_dir=tmp_path / "mini",
    )

    assert result.status == "completed"
    assert result.task_results[0].rows_out == 1
    assert result.task_results[0].validation_status == "completed"
    assert (tmp_path / "mini" / "mini_e2e_summary.json").exists()
    assert (tmp_path / "mini" / "agent_run_build" / "task_runs" / "rt_prediction" / "rt_train.parquet").exists()


def test_mini_e2e_reports_upstream_partial_outputs(tmp_path: Path):
    run_dir = tmp_path / "run"
    _write_failed_upstream_log(run_dir)
    _write_tsv(
        run_dir / "fragpipe" / "exp" / "peptide.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Retention Time (min)": 12.5, "PSM Q-Value": 0.001}],
    )

    result = validate_agent_run_ai_ready_mini(
        agent_run_dir=run_dir,
        task_types=["rt_prediction"],
        output_dir=tmp_path / "mini",
    )

    assert result.status == "completed"
    assert result.ai_ready_outcome == "completed_from_usable_partial_outputs"
    assert result.usable_partial_outputs is True
    assert result.primary_issue is None
    assert result.upstream_recovery_status == "needs_action"
    assert result.upstream_workflow_outcome == "failed_with_usable_partial_outputs"
    assert result.upstream_usable_partial_outputs is True
    assert result.upstream_primary_issue == "partial_outputs_available"
    assert result.upstream_recovery_report_json is not None
    assert result.upstream_recovery_report_md is not None
    assert Path(result.upstream_recovery_report_json).exists()
    summary = json.loads((tmp_path / "mini" / "mini_e2e_summary.json").read_text(encoding="utf-8"))
    assert summary["upstream_primary_issue"] == "partial_outputs_available"
    assert summary["ai_ready_outcome"] == "completed_from_usable_partial_outputs"
    assert summary["upstream_workflow_outcome"] == "failed_with_usable_partial_outputs"
    report_text = (tmp_path / "mini" / "mini_e2e_report.md").read_text(encoding="utf-8")
    assert "Upstream Run Recovery" in report_text
    assert "completed_from_usable_partial_outputs" in report_text


def test_mini_e2e_exports_denovo_with_psm_and_mgf(tmp_path: Path):
    run_dir = tmp_path / "run"
    _write_tsv(
        run_dir / "fragpipe" / "exp" / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Modified Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "PSM Q-Value": 0.001}],
    )
    _write_mgf(run_dir / "fragpipe" / "exp" / "spectra.mgf")

    result = validate_agent_run_ai_ready_mini(
        agent_run_dir=run_dir,
        task_types=["denovo"],
        output_dir=tmp_path / "mini",
    )

    assert result.status == "completed"
    assert result.task_results[0].rows_out == 1
    assert result.task_results[0].validation_status == "completed"


def test_mini_e2e_uses_explicit_external_peaklist(tmp_path: Path):
    run_dir = tmp_path / "run"
    _write_tsv(
        run_dir / "fragpipe" / "exp" / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Modified Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "PSM Q-Value": 0.001}],
    )
    peaklist = _write_mgf(tmp_path / "generated" / "spectra.mgf")

    result = validate_agent_run_ai_ready_mini(
        agent_run_dir=run_dir,
        task_types=["denovo"],
        output_dir=tmp_path / "mini",
        peaklists=[peaklist],
    )

    assert result.status == "completed"
    assert result.task_results[0].rows_out == 1
    assert result.task_results[0].validation_status == "completed"


def test_mini_e2e_reports_generic_ai_ready_without_task_labels(tmp_path: Path):
    run_dir = tmp_path / "run"
    (run_dir / "msdt").mkdir(parents=True)
    (run_dir / "msdt" / "sample_fp_msdt.parquet").write_bytes(b"placeholder")
    (run_dir / "ai_ready").mkdir(parents=True)
    (run_dir / "ai_ready" / "sample_ai_ready.parquet").write_bytes(b"placeholder")

    result = validate_agent_run_ai_ready_mini(
        agent_run_dir=run_dir,
        task_types=["rt_prediction"],
        output_dir=tmp_path / "mini",
    )

    assert result.status == "blocked"
    assert result.generic_ai_ready_available is True
    assert "needs_search_results" in result.task_results[0].blockers
    assert "generic_ai_ready_available" in result.task_results[0].warnings


def test_mini_e2e_blocks_denovo_without_mgf(tmp_path: Path):
    run_dir = tmp_path / "run"
    _write_tsv(
        run_dir / "fragpipe" / "exp" / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "PSM Q-Value": 0.001}],
    )

    result = validate_agent_run_ai_ready_mini(
        agent_run_dir=run_dir,
        task_types=["denovo"],
        output_dir=tmp_path / "mini",
    )

    assert result.status == "blocked"
    assert "needs_peaklist" in result.task_results[0].blockers
    assert result.recovery_status == "needs_action"
    assert result.primary_issue == "missing_peaklist"
    assert result.recovery_actions[0].action_type == "generate_peaklist_and_retry"
    assert result.recovery_actions[0].status == "blocked"
    assert "source_parquet_not_found" in result.recovery_actions[0].blockers
    assert result.recovery_report_json is not None
    assert Path(result.recovery_report_json).exists()
    summary = json.loads((tmp_path / "mini" / "mini_e2e_summary.json").read_text(encoding="utf-8"))
    assert summary["primary_issue"] == "missing_peaklist"


def test_mini_e2e_auto_generates_peaklist_and_recovers_denovo(tmp_path: Path):
    run_dir = tmp_path / "run"
    _write_tsv(
        run_dir / "fragpipe" / "exp" / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Modified Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "PSM Q-Value": 0.001}],
    )
    (run_dir / "rawspectrum").mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "scan": 101,
                "charge": 2,
                "precursor_mz": 500.2,
                "mz_array": [98.06004, 147.1128],
                "intensity_array": [1000.0, 500.0],
            }
        ]
    ).to_parquet(run_dir / "rawspectrum" / "sample_rawspectrum.parquet", index=False)

    result = validate_agent_run_ai_ready_mini(
        agent_run_dir=run_dir,
        task_types=["denovo"],
        output_dir=tmp_path / "mini",
    )

    assert result.status == "completed"
    assert result.task_results[0].rows_out == 1
    assert result.recovery_actions[0].status == "completed"
    assert "peaklist_mgf" in result.recovery_actions[0].files
    assert result.primary_issue is None
    assert (tmp_path / "mini" / "recovery_generate_peaklist" / "peaklists" / "sample.mgf").exists()


def test_mini_e2e_blocks_psm_scoring_without_target_decoy(tmp_path: Path):
    run_dir = tmp_path / "run"
    _write_tsv(
        run_dir / "fragpipe" / "exp" / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "Hyperscore": 42.0}],
    )

    result = validate_agent_run_ai_ready_mini(
        agent_run_dir=run_dir,
        task_types=["psm_scoring"],
        output_dir=tmp_path / "mini",
    )

    assert result.status == "blocked"
    assert "needs_target_decoy_labels" in result.task_results[0].blockers


def test_mini_e2e_placeholder_does_not_execute_workflow(tmp_path: Path):
    result = mini_e2e_parameters_only_placeholder(
        input_value="sample.raw",
        mode="parameters",
        output_dir=tmp_path / "mini",
    )

    assert result.status == "blocked"
    assert result.task_results == []
    assert "input_value_mini_run_not_implemented" in result.blockers
    assert (tmp_path / "mini" / "mini_e2e_summary.json").exists()


def test_validate_agent_run_ai_ready_mini_cli_writes_summary(tmp_path: Path):
    run_dir = tmp_path / "run"
    _write_tsv(
        run_dir / "fragpipe" / "exp" / "peptide.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Retention Time (min)": 12.5, "PSM Q-Value": 0.001}],
    )
    output_dir = tmp_path / "mini"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "validate-agent-run-ai-ready-mini",
            "--agent-run-dir",
            str(run_dir),
            "--task-type",
            "rt_prediction",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "completed"
    assert payload["primary_issue"] is None
    assert payload["recovery_actions"] == []
    assert (output_dir / "mini_e2e_summary.json").exists()
    assert (output_dir / "mini_e2e_report.md").exists()
    assert (output_dir / "agent_recovery_report.json").exists()


def test_validate_agent_run_ai_ready_mini_cli_blocks_full_without_allow_full(tmp_path: Path):
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "validate-agent-run-ai-ready-mini",
            "--input-value",
            "sample.raw",
            "--mode",
            "full",
            "--output-dir",
            str(tmp_path / "mini"),
        ],
    )

    assert result.exit_code != 0
    assert "--mode full is blocked" in result.output
