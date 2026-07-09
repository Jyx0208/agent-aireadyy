from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from agent.ai_ready.input_profile import profile_ai_ready_inputs
from agent.cli import app


def _write_tsv(path: Path, rows: list[dict[str, object]]) -> Path:
    pd.DataFrame(rows).to_csv(path, sep="\t", index=False)
    return path


def _write_mgf(path: Path) -> Path:
    path.write_text(
        "\n".join(
            [
                "BEGIN IONS",
                "TITLE=scan=101",
                "SCANS=101",
                "PEPMASS=500.2",
                "CHARGE=2+",
                "100.1 1000",
                "END IONS",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_input_profile_marks_tasks_ready_and_blocked_from_columns_and_mgf(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "psm.tsv",
        [
            {
                "Peptide": "PEPTIDEK",
                "Modified Peptide": "PEP[+80]TIDEK",
                "Charge": 2,
                "Spectrum": "scan=101",
                "Retention": 12.5,
                "PSM Q-Value": 0.001,
            }
        ],
    )
    peaklist = _write_mgf(tmp_path / "spectra.mgf")

    result = profile_ai_ready_inputs(
        search_results=[search_result],
        peaklists=[peaklist],
        task_types=["rt_prediction", "denovo", "psm_scoring", "ptm_denovo"],
        output_dir=tmp_path / "profile",
    )

    by_task = {item.task_type: item for item in result.task_profiles}
    assert by_task["rt_prediction"].input_status == "ready"
    assert by_task["denovo"].input_status == "ready"
    assert by_task["denovo"].matched_spectrum_count == 1
    assert by_task["psm_scoring"].input_status == "blocked"
    assert "needs_target_decoy_labels" in by_task["psm_scoring"].blockers
    assert by_task["ptm_denovo"].input_status == "ready"
    assert by_task["ptm_denovo"].modification_token_rows == 1
    assert Path(result.json_path).exists()
    assert Path(result.csv_path).exists()


def test_input_profile_blocks_peaklist_tasks_when_mgf_missing(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "PSM Q-Value": 0.001}],
    )

    result = profile_ai_ready_inputs(
        search_results=[search_result],
        peaklists=[],
        task_types=["fragment_intensity_prediction", "denovo"],
        output_dir=tmp_path / "profile",
    )

    for item in result.task_profiles:
        assert item.input_status == "blocked"
        assert "needs_peaklist" in item.blockers


def test_input_profile_matches_sage_scannr_and_q_aliases(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "sample_search_result.tsv",
        [
            {
                "psm_id": "1",
                "peptide": "PEPTIDEK",
                "filename": "sample.mzML",
                "scannr": "controllerType=0 controllerNumber=1 scan=101",
                "charge": 2,
                "rt": 12.5,
                "spectrum_q": 0.001,
                "label": 1,
            }
        ],
    )
    peaklist = _write_mgf(tmp_path / "spectra.mgf")

    result = profile_ai_ready_inputs(
        search_results=[search_result],
        peaklists=[peaklist],
        task_types=["denovo"],
        output_dir=tmp_path / "profile",
    )

    profile = result.task_profiles[0]
    assert profile.input_status == "ready"
    assert profile.detected_columns["spectrum_id"] == "scannr"
    assert profile.detected_columns["q_value"] == "spectrum_q"
    assert profile.matched_spectrum_count == 1


def test_profile_ai_ready_inputs_cli_writes_reports(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "Retention": 12.5}],
    )
    output_dir = tmp_path / "profile"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "profile-ai-ready-inputs",
            "--search-result",
            str(search_result),
            "--task-type",
            "rt_prediction",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["rows_in"] == 1
    assert (output_dir / "ai_ready_input_profile.json").exists()
    assert (output_dir / "ai_ready_input_profile.csv").exists()
