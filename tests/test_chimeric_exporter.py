from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from agent.ai_ready.chimeric_exporter import export_chimeric_ai_ready
from agent.cli import app


def _write_tsv(path: Path, rows: list[dict[str, object]]) -> Path:
    pd.DataFrame(rows).to_csv(path, sep="\t", index=False)
    return path


def _write_mgf(path: Path, *, scan: int = 101) -> Path:
    path.write_text(
        "\n".join(
            [
                "BEGIN IONS",
                f"TITLE=scan={scan}",
                f"SCANS={scan}",
                "PEPMASS=500.2",
                "CHARGE=2+",
                "100.1 1000",
                "200.2 500",
                "END IONS",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_chimeric_exporter_exports_multi_peptide_spectrum(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "psm.tsv",
        [
            {
                "Peptide": "PEPTIDEK",
                "Modified Peptide": "PEPTIDEK",
                "Charge": 2,
                "Spectrum": "scan=101",
                "Spectrum File": "sample.raw",
                "PSM Q-Value": 0.001,
                "PeptideProphet Probability": 0.95,
            },
            {
                "Peptide": "SECONDK",
                "Modified Peptide": "SECONDK",
                "Charge": 3,
                "Spectrum": "scan=101",
                "Spectrum File": "sample.raw",
                "PSM Q-Value": 0.002,
                "PeptideProphet Probability": 0.96,
            },
        ],
    )
    peaklist = _write_mgf(tmp_path / "spectra.mgf")

    result = export_chimeric_ai_ready([search_result], [peaklist], tmp_path / "chimeric", project_accession="PXD000001")

    assert result.status == "completed"
    assert result.rows_in == 2
    assert result.rows_out == 1
    frame = pd.read_parquet(result.output_parquet)
    assert frame.loc[0, "project_accession"] == "PXD000001"
    assert frame.loc[0, "component_count"] == 2
    assert json.loads(frame.loc[0, "component_peptides_json"]) == ["PEPTIDEK", "SECONDK"]
    assert json.loads(frame.loc[0, "component_charges_json"]) == [2, 3]
    assert frame.loc[0, "label_source"] == "multi_peptide_psm_tsv_plus_mgf"
    assert Path(result.preview_csv).exists()
    assert Path(result.report_json).exists()
    assert Path(result.schema_json_path).exists()


def test_chimeric_exporter_does_not_fabricate_labels_for_single_peptide_spectra(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "psm.tsv",
        [
            {"Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "PSM Q-Value": 0.001},
            {"Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "PSM Q-Value": 0.001},
        ],
    )
    peaklist = _write_mgf(tmp_path / "spectra.mgf")

    result = export_chimeric_ai_ready([search_result], [peaklist], tmp_path / "chimeric")

    assert result.rows_out == 0
    assert result.filter_counts["no_multi_peptide_assignment"] == 2
    assert "no_multi_peptide_assignment" in result.warnings
    assert pd.read_parquet(result.output_parquet).empty


def test_chimeric_exporter_filters_threshold_and_unmatched_spectra(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "psm.tsv",
        [
            {"Peptide": "KEEPK", "Charge": 2, "Spectrum": "scan=999", "PSM Q-Value": 0.001},
            {"Peptide": "SECONDK", "Charge": 2, "Spectrum": "scan=999", "PSM Q-Value": 0.001},
            {"Peptide": "DROPQ", "Charge": 2, "Spectrum": "scan=101", "PSM Q-Value": 0.2},
        ],
    )
    peaklist = _write_mgf(tmp_path / "spectra.mgf")

    result = export_chimeric_ai_ready([search_result], [peaklist], tmp_path / "chimeric")

    assert result.rows_out == 0
    assert result.filter_counts["q_value_above_threshold"] == 1
    assert result.filter_counts["spectrum_not_matched"] == 2
    assert "spectrum_not_matched" in result.warnings


def test_chimeric_exporter_cli_writes_outputs(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "psm.tsv",
        [
            {"Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "PSM Q-Value": 0.001},
            {"Peptide": "SECONDK", "Charge": 3, "Spectrum": "scan=101", "PSM Q-Value": 0.001},
        ],
    )
    peaklist = _write_mgf(tmp_path / "spectra.mgf")
    output_dir = tmp_path / "chimeric_cli"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "export-chimeric-ai-ready",
            "--search-result",
            str(search_result),
            "--peaklist",
            str(peaklist),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["rows_out"] == 1
    assert (output_dir / "chimeric_train.parquet").exists()
    assert (output_dir / "chimeric.preview.csv").exists()
    assert (output_dir / "chimeric_export_report.json").exists()
    assert (output_dir / "chimeric_schema.json").exists()
