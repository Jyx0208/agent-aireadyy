from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from agent.ai_ready.ptm_denovo_exporter import export_ptm_denovo_ai_ready
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
                "200.2 500",
                "END IONS",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_ptm_denovo_exporter_exports_modified_sequence_pairs(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "ptm_psm.tsv",
        [
            {
                "Peptide": "PEPTIDEK",
                "Modified Peptide": "PEP[+80]TIDEK",
                "Charge": 2,
                "Spectrum": "scan=101",
                "Spectrum File": "sample.raw",
                "PSM Q-Value": 0.001,
                "Localization Probability": 0.98,
            }
        ],
    )
    peaklist = _write_mgf(tmp_path / "spectra.mgf")

    result = export_ptm_denovo_ai_ready([search_result], [peaklist], tmp_path / "ptm", project_accession="PXD000001")

    assert result.status == "completed"
    assert result.rows_out == 1
    frame = pd.read_parquet(result.output_parquet)
    assert frame.loc[0, "project_accession"] == "PXD000001"
    assert frame.loc[0, "peptide_sequence"] == "PEPTIDEK"
    assert frame.loc[0, "modified_sequence"] == "PEP[+80]TIDEK"
    assert json.loads(frame.loc[0, "modification_tokens_json"]) == ["[+80]"]
    assert frame.loc[0, "localization_confidence"] == "0.98"
    assert frame.loc[0, "label_source"] == "high_confidence_modified_psm_tsv_plus_mgf"


def test_ptm_denovo_exporter_filters_unmodified_rows_and_warns_when_localization_missing(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "ptm_psm.tsv",
        [
            {"Peptide": "KEEPK", "Modified Peptide": "KEEP[Phospho]K", "Charge": 2, "Spectrum": "scan=101"},
            {"Peptide": "DROPK", "Modified Peptide": "DROPK", "Charge": 2, "Spectrum": "scan=101"},
            {"Peptide": "MISSK", "Modified Peptide": "MISS[+80]K", "Charge": 2, "Spectrum": "scan=999"},
        ],
    )
    peaklist = _write_mgf(tmp_path / "spectra.mgf")

    result = export_ptm_denovo_ai_ready([search_result], [peaklist], tmp_path / "ptm")

    assert result.rows_in == 3
    assert result.rows_out == 1
    assert result.filter_counts["missing_modified_sequence"] == 1
    assert result.filter_counts["spectrum_not_matched"] == 1
    assert "localization_confidence_missing" in result.warnings


def test_ptm_denovo_exporter_cli_writes_outputs(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "ptm_psm.tsv",
        [{"Peptide": "PEPTIDEK", "Modified Peptide": "PEP[+80]TIDEK", "Charge": 2, "Spectrum": "scan=101"}],
    )
    peaklist = _write_mgf(tmp_path / "spectra.mgf")
    output_dir = tmp_path / "ptm"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "export-ptm-denovo-ai-ready",
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
    assert (output_dir / "ptm_denovo_train.parquet").exists()
    assert (output_dir / "ptm_denovo.preview.csv").exists()
    assert (output_dir / "ptm_denovo_export_report.json").exists()
    assert (output_dir / "ptm_denovo_schema.json").exists()
