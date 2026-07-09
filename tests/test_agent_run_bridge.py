from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from agent.ai_ready.agent_run_bridge import build_ai_ready_from_agent_run
from agent.ai_ready.agent_run_locator import locate_agent_run_inputs
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


def test_agent_run_locator_detects_original_outputs(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "task_state.json").write_text("{}", encoding="utf-8")
    (run_dir / "decision_trace.json").write_text("{}", encoding="utf-8")
    (run_dir / "converter_config.json").write_text("{}", encoding="utf-8")
    _write_tsv(
        run_dir / "fragpipe" / "exp" / "peptide.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Retention": 12.5}],
    )
    (run_dir / "fragpipe" / "exp" / "sample_edited.pin").write_text("SpecId\tLabel\nscan=1\t1\n", encoding="utf-8")
    (run_dir / "msdt" / "sample_fp_msdt.parquet").parent.mkdir(parents=True)
    (run_dir / "msdt" / "sample_fp_msdt.parquet").write_bytes(b"placeholder")
    (run_dir / "ai_ready" / "sample_ai_ready.parquet").parent.mkdir(parents=True)
    (run_dir / "ai_ready" / "sample_ai_ready.parquet").write_bytes(b"placeholder")

    result = locate_agent_run_inputs(agent_run_dir=run_dir, output_dir=tmp_path / "located")

    assert result.status == "completed"
    assert result.summary["role_counts"]["msdt_parquet"] == 1
    assert result.summary["role_counts"]["generic_ai_ready_parquet"] == 1
    assert result.summary["search_result_count"] == 2
    assert result.summary["generic_ai_ready_available"] is True
    assert (tmp_path / "located" / "agent_run_input_locations.json").exists()
    assert (tmp_path / "located" / "agent_run_input_locations.csv").exists()


def test_agent_run_locator_reports_downloaded_acquisition_without_marking_search_input(tmp_path: Path):
    run_dir = tmp_path / "run"
    raw_path = run_dir / "assets" / "downloads" / "sample.raw"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"raw-bytes")

    result = locate_agent_run_inputs(agent_run_dir=run_dir, output_dir=tmp_path / "located")

    assert result.status == "completed"
    assert result.summary["role_counts"]["downloaded_acquisition"] == 1
    assert result.summary["downloaded_acquisition_count"] == 1
    assert result.summary["search_result_count"] == 0
    assert result.artifacts[0].usable_for_tasks == ["needs_prepare_or_full"]


def test_build_from_agent_run_exports_rt_from_peptide_tsv(tmp_path: Path):
    run_dir = tmp_path / "run"
    _write_tsv(
        run_dir / "fragpipe" / "exp" / "peptide.tsv",
        [
            {
                "Peptide": "PEPTIDEK",
                "Modified Peptide": "PEPTIDEK",
                "Charge": 2,
                "Retention": 12.5,
                "Spectrum File": "sample.raw",
                "PSM Q-Value": 0.001,
            }
        ],
    )

    result = build_ai_ready_from_agent_run(
        agent_run_dir=run_dir,
        task_types=["rt_prediction"],
        output_dir=tmp_path / "bridge",
    )

    assert result.status == "completed"
    assert result.task_results[0].task_type == "rt_prediction"
    assert result.task_results[0].rows_out == 1
    assert (tmp_path / "bridge" / "task_runs" / "rt_prediction" / "rt_train.parquet").exists()
    assert (tmp_path / "bridge" / "agent_run_build_summary.json").exists()


def test_build_from_agent_run_blocks_denovo_without_mgf(tmp_path: Path):
    run_dir = tmp_path / "run"
    _write_tsv(
        run_dir / "fragpipe" / "exp" / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "PSM Q-Value": 0.001}],
    )

    result = build_ai_ready_from_agent_run(
        agent_run_dir=run_dir,
        task_types=["denovo"],
        output_dir=tmp_path / "bridge",
    )

    assert result.status == "blocked"
    assert "needs_peaklist" in result.task_results[0].blockers


def test_build_from_agent_run_exports_denovo_with_psm_and_mgf(tmp_path: Path):
    run_dir = tmp_path / "run"
    _write_tsv(
        run_dir / "fragpipe" / "exp" / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Modified Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "PSM Q-Value": 0.001}],
    )
    _write_mgf(run_dir / "fragpipe" / "exp" / "spectra.mgf")

    result = build_ai_ready_from_agent_run(
        agent_run_dir=run_dir,
        task_types=["denovo"],
        output_dir=tmp_path / "bridge",
    )

    assert result.status == "completed"
    assert result.task_results[0].rows_out == 1
    assert (tmp_path / "bridge" / "task_runs" / "denovo" / "denovo_train.parquet").exists()


def test_build_from_agent_run_uses_explicit_external_peaklist(tmp_path: Path):
    run_dir = tmp_path / "run"
    _write_tsv(
        run_dir / "fragpipe" / "exp" / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Modified Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "PSM Q-Value": 0.001}],
    )
    peaklist = _write_mgf(tmp_path / "generated" / "spectra.mgf")

    result = build_ai_ready_from_agent_run(
        agent_run_dir=run_dir,
        task_types=["denovo"],
        output_dir=tmp_path / "bridge",
        peaklists=[peaklist],
    )

    assert result.status == "completed"
    assert result.locator_summary["peaklist_count"] == 1
    assert result.task_results[0].peaklists == [str(peaklist)]
    assert result.task_results[0].rows_out == 1


def test_build_from_agent_run_reports_generic_ai_ready_without_task_labels(tmp_path: Path):
    run_dir = tmp_path / "run"
    (run_dir / "msdt").mkdir(parents=True)
    (run_dir / "msdt" / "sample_fp_msdt.parquet").write_bytes(b"placeholder")
    (run_dir / "ai_ready").mkdir(parents=True)
    (run_dir / "ai_ready" / "sample_ai_ready.parquet").write_bytes(b"placeholder")

    result = build_ai_ready_from_agent_run(
        agent_run_dir=run_dir,
        task_types=["rt_prediction"],
        output_dir=tmp_path / "bridge",
    )

    assert result.status == "blocked"
    assert result.locator_summary["generic_ai_ready_available"] is True
    assert "needs_search_results" in result.task_results[0].blockers
    assert "generic_ai_ready_available" in result.task_results[0].warnings


def test_agent_run_locator_blocks_large_candidate_table(tmp_path: Path):
    run_dir = tmp_path / "run"
    psm = run_dir / "fragpipe" / "exp" / "psm.tsv"
    psm.parent.mkdir(parents=True)
    psm.write_text("Peptide\tCharge\tSpectrum\n" + ("PEPTIDEK\t2\tscan=1\n" * 200), encoding="utf-8")

    result = locate_agent_run_inputs(
        agent_run_dir=run_dir,
        output_dir=tmp_path / "located",
        max_input_file_mb=1,
    )

    assert result.summary["search_result_count"] == 1
    assert result.ai_ready_inputs

    psm.write_text("Peptide\tCharge\tSpectrum\n" + ("PEPTIDEK\t2\tscan=1\n" * 70000), encoding="utf-8")
    strict = locate_agent_run_inputs(
        agent_run_dir=run_dir,
        output_dir=tmp_path / "strict",
        max_input_file_mb=1,
    )

    assert strict.summary["search_result_count"] == 0
    assert strict.summary["warnings"]


def test_build_ai_ready_from_agent_run_cli_writes_report(tmp_path: Path):
    run_dir = tmp_path / "run"
    _write_tsv(
        run_dir / "fragpipe" / "exp" / "peptide.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Retention": 12.5, "PSM Q-Value": 0.001}],
    )
    output_dir = tmp_path / "bridge"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "build-ai-ready-from-agent-run",
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
    assert (output_dir / "agent_run_build_summary.json").exists()
    assert (output_dir / "agent_run_build_report.md").exists()
