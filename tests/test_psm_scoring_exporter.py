from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from agent.ai_ready.psm_scoring_exporter import export_psm_scoring_ai_ready
from agent.cli import app


def _write_tsv(path: Path, rows: list[dict[str, object]]) -> Path:
    pd.DataFrame(rows).to_csv(path, sep="\t", index=False)
    return path


def test_psm_scoring_exporter_exports_target_decoy_rows(tmp_path: Path):
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
                "Hyperscore": 42.0,
                "Delta Score": 5.0,
                "Decoy": "false",
            },
            {
                "Peptide": "DECOYK",
                "Charge": 3,
                "Spectrum": "scan=102",
                "Spectrum File": "sample.raw",
                "PSM Q-Value": 0.5,
                "Hyperscore": 7.0,
                "Decoy": "true",
            },
        ],
    )

    result = export_psm_scoring_ai_ready([search_result], tmp_path / "psm", project_accession="PXD000001")

    assert result.rows_in == 2
    assert result.rows_out == 2
    assert result.target_count == 1
    assert result.decoy_count == 1
    frame = pd.read_parquet(result.output_parquet)
    assert set(frame["target_decoy_label"]) == {0, 1}
    features = json.loads(frame.loc[0, "score_features_json"])
    assert features["Hyperscore"] == 42.0
    assert features["Delta Score"] == 5.0
    assert Path(result.preview_csv).exists()
    assert Path(result.report_json).exists()
    assert Path(result.schema_json_path).exists()


def test_psm_scoring_exporter_can_infer_decoy_from_protein_column(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "psm.tsv",
        [
            {
                "Peptide": "PEPTIDEK",
                "Charge": 2,
                "Spectrum": "scan=101",
                "Protein": "sp|P12345|TARGET",
                "Score": 12.0,
            },
            {
                "Peptide": "DECOYK",
                "Charge": 2,
                "Spectrum": "scan=102",
                "Protein": "rev_sp|P12345|DECOY",
                "Score": 3.0,
            },
        ],
    )

    result = export_psm_scoring_ai_ready([search_result], tmp_path / "psm")

    assert result.target_count == 1
    assert result.decoy_count == 1


def test_psm_scoring_exporter_blocks_when_target_decoy_missing(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "Hyperscore": 42.0}],
    )

    try:
        export_psm_scoring_ai_ready([search_result], tmp_path / "psm")
    except ValueError as exc:
        assert "target_decoy_label_missing" in str(exc)
    else:
        raise AssertionError("Expected target/decoy requirement to block export")


def test_psm_scoring_exporter_uses_task_build_plan_metadata(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "psm.tsv",
        [
            {
                "Peptide": "PEPTIDEK",
                "Charge": 2,
                "Spectrum": "scan=101",
                "Spectrum File": "sample.raw",
                "Decoy": "false",
            }
        ],
    )
    task_build_plan = tmp_path / "discovery_task_build_plan.json"
    task_build_plan.write_text(
        json.dumps(
            {
                "files": [
                    {
                        "project_accession": "PXD123456",
                        "file_name": "sample.raw",
                        "species": ["human"],
                        "instrument_families": ["orbitrap"],
                        "ptm_type": "phospho",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = export_psm_scoring_ai_ready([search_result], tmp_path / "psm", task_build_plan=task_build_plan)

    frame = pd.read_parquet(result.output_parquet)
    assert frame.loc[0, "project_accession"] == "PXD123456"
    assert frame.loc[0, "species"] == "human"
    assert frame.loc[0, "instrument_family"] == "orbitrap"
    assert frame.loc[0, "ptm_type"] == "phospho"


def test_psm_scoring_exporter_cli_writes_outputs(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "Decoy": "false"}],
    )
    output_dir = tmp_path / "psm_cli"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "export-psm-scoring-ai-ready",
            "--search-result",
            str(search_result),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["rows_out"] == 1
    assert (output_dir / "psm_scoring_train.parquet").exists()
    assert (output_dir / "psm_scoring.preview.csv").exists()
    assert (output_dir / "psm_scoring_export_report.json").exists()
    assert (output_dir / "psm_scoring_schema.json").exists()
