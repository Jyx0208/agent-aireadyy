from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from agent.ai_ready.fragment_intensity_exporter import export_fragment_intensity_ai_ready
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
                "ACTIVATION=HCD",
                "98.06004 1000",
                "147.11280 500",
                "227.10263 250",
                "END IONS",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_fragment_intensity_exporter_exports_parquet_from_psm_and_mgf(tmp_path: Path):
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
            }
        ],
    )
    peaklist = _write_mgf(tmp_path / "spectra.mgf")

    result = export_fragment_intensity_ai_ready(
        [search_result],
        [peaklist],
        tmp_path / "fragment",
        project_accession="PXD000001",
    )

    assert result.rows_in == 1
    assert result.rows_out == 1
    frame = pd.read_parquet(result.output_parquet)
    assert frame.loc[0, "project_accession"] == "PXD000001"
    assert frame.loc[0, "fragmentation_method"] == "HCD"
    matched = json.loads(frame.loc[0, "matched_ions_json"])
    assert any(item["ion"].startswith("b") for item in matched)
    assert any(item["ion"].startswith("y") for item in matched)
    assert Path(result.preview_csv).exists()
    assert Path(result.report_json).exists()
    assert Path(result.schema_json_path).exists()
    report = json.loads(Path(result.report_json).read_text(encoding="utf-8"))
    assert report["spectrum_evidence"]["fragmentation_methods"] == ["HCD"]


def test_fragment_intensity_exporter_filters_threshold_and_unmatched_spectra(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "psm.tsv",
        [
            {"Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "PSM Q-Value": 0.05},
            {"Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=999", "PSM Q-Value": 0.001},
        ],
    )
    peaklist = _write_mgf(tmp_path / "spectra.mgf")

    result = export_fragment_intensity_ai_ready([search_result], [peaklist], tmp_path / "fragment")

    assert result.rows_out == 0
    assert result.filter_counts["q_value_above_threshold"] == 1
    assert result.filter_counts["spectrum_not_matched"] == 1
    assert "spectrum_not_matched" in result.warnings


def test_fragment_intensity_exporter_does_not_annotate_modified_peptide(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "psm.tsv",
        [
            {
                "Peptide": "PEPTIDEK",
                "Modified Peptide": "PEPTIDEK[+79.966]",
                "Charge": 2,
                "Spectrum": "scan=101",
                "PSM Q-Value": 0.001,
            }
        ],
    )
    peaklist = _write_mgf(tmp_path / "spectra.mgf")

    result = export_fragment_intensity_ai_ready([search_result], [peaklist], tmp_path / "fragment")

    assert result.rows_out == 0
    assert result.filter_counts["unsupported_modified_peptide"] == 1
    assert "modified_peptide_not_annotated" in result.warnings


def test_fragment_intensity_exporter_cli_writes_outputs(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "PSM Q-Value": 0.001}],
    )
    peaklist = _write_mgf(tmp_path / "spectra.mgf")
    output_dir = tmp_path / "fragment_cli"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "export-fragment-intensity-ai-ready",
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
    assert (output_dir / "fragment_intensity_train.parquet").exists()
    assert (output_dir / "fragment_intensity.preview.csv").exists()
    assert (output_dir / "fragment_intensity_export_report.json").exists()
    assert (output_dir / "fragment_intensity_schema.json").exists()
