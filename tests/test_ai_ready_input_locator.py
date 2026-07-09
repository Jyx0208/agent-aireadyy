from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from agent.ai_ready.input_locator import locate_ai_ready_inputs, select_ai_ready_inputs
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


def test_locator_identifies_search_tables_and_mgf(tmp_path: Path):
    search_dir = tmp_path / "fragpipe"
    search_dir.mkdir()
    psm = _write_tsv(
        search_dir / "psm.tsv",
        [
            {
                "Peptide": "PEPTIDEK",
                "Modified Peptide": "PEP[+80]TIDEK",
                "Charge": 2,
                "Spectrum": "scan=101",
                "Retention": 12.5,
                "PSM Q-Value": 0.001,
                "Decoy": "false",
            }
        ],
    )
    mgf = _write_mgf(search_dir / "spectra.mgf")

    result = locate_ai_ready_inputs(search_dir=search_dir, output_dir=tmp_path / "locator")

    assert result.status == "completed"
    assert result.summary["search_result_count"] == 1
    assert result.summary["peaklist_count"] == 1
    assert result.summary["has_rt_table"] is True
    assert result.summary["has_target_decoy_table"] is True
    assert result.summary["has_modified_sequence_table"] is True
    search_results, peaklists = select_ai_ready_inputs(result, task_type="denovo")
    assert search_results == [psm]
    assert peaklists == [mgf]
    assert Path(result.json_path).exists()
    assert Path(result.csv_path).exists()


def test_locator_handles_empty_directory(tmp_path: Path):
    result = locate_ai_ready_inputs(search_dir=tmp_path, output_dir=tmp_path / "locator")

    assert result.status == "blocked"
    assert result.summary["located_files"] == 0


def test_locator_identifies_real_style_search_result_tsv(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "sample_search_result.tsv",
        [
            {
                "psm_id": "sample.101.101.2",
                "peptide": "PEPTIDEK",
                "proteins": "sp|P00001|TEST",
                "filename": "sample.mzML",
                "scannr": "controllerType=0 controllerNumber=1 scan=101",
                "charge": 2,
                "rt": 12.5,
                "label": 1,
            }
        ],
    )
    _write_tsv(tmp_path / "sample_rawspectrum.tsv", [{"scan": 101}])
    _write_tsv(tmp_path / "sample.peptide_count.tsv", [{"peptide": "PEPTIDEK"}])

    result = locate_ai_ready_inputs(search_dir=tmp_path, output_dir=tmp_path / "locator")

    assert result.status == "completed"
    assert result.summary["search_result_count"] == 1
    assert result.entries[0].path == str(search_result)
    assert result.entries[0].file_role == "psm_table"
    assert result.entries[0].has_rt is True
    assert result.entries[0].has_target_decoy is True


def test_locate_ai_ready_inputs_cli_writes_reports(tmp_path: Path):
    _write_tsv(tmp_path / "peptide.tsv", [{"Peptide": "PEPTIDEK", "Charge": 2, "Retention": 12.5}])
    output_dir = tmp_path / "locator"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "locate-ai-ready-inputs",
            "--search-dir",
            str(tmp_path),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["search_result_count"] == 1
    assert (output_dir / "ai_ready_input_locations.json").exists()
    assert (output_dir / "ai_ready_input_locations.csv").exists()
