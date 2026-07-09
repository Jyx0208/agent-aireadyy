from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from agent.ai_ready.rt_exporter import export_rt_ai_ready
from agent.cli import app


def _write_tsv(path: Path, rows: list[dict[str, object]]) -> Path:
    pd.DataFrame(rows).to_csv(path, sep="\t", index=False)
    return path


def _fragpipe_rows() -> list[dict[str, object]]:
    return [
        {
            "Peptide": "PEPTIDEK",
            "Modified Peptide": "PEPTIDEK",
            "Charge": 2,
            "Retention": 12.5,
            "Spectrum": "scan=101",
            "Spectrum File": "sample.raw",
            "PSM Q-Value": 0.005,
            "PeptideProphet Probability": 0.95,
            "Search Engine": "fragpipe",
        },
        {
            "Peptide": "FILTERQ",
            "Modified Peptide": "FILTERQ",
            "Charge": 3,
            "Retention": 15.2,
            "Spectrum": "scan=102",
            "Spectrum File": "sample.raw",
            "PSM Q-Value": 0.05,
            "PeptideProphet Probability": 0.99,
            "Search Engine": "fragpipe",
        },
        {
            "Peptide": "NOPROB",
            "Modified Peptide": "NOPROB",
            "Charge": 2,
            "Retention": 16.1,
            "Spectrum": "scan=103",
            "Spectrum File": "sample.raw",
            "PSM Q-Value": 0.001,
            "PeptideProphet Probability": 0.2,
            "Search Engine": "fragpipe",
        },
        {
            "Peptide": "",
            "Modified Peptide": "",
            "Charge": 2,
            "Retention": 18.1,
            "Spectrum": "scan=104",
            "Spectrum File": "sample.raw",
            "PSM Q-Value": 0.001,
            "PeptideProphet Probability": 0.99,
            "Search Engine": "fragpipe",
        },
    ]


def test_export_rt_ai_ready_exports_parquet_and_filters_rows(tmp_path: Path):
    search_result = _write_tsv(tmp_path / "fragpipe_psm.tsv", _fragpipe_rows())

    result = export_rt_ai_ready(
        [search_result],
        tmp_path / "rt",
        project_accession="PXD000001",
        source_file="sample.raw",
    )

    assert result.rows_in == 4
    assert result.rows_out == 1
    assert result.filter_counts["q_value_above_threshold"] == 1
    assert result.filter_counts["probability_below_threshold"] == 1
    assert result.filter_counts["missing_peptide_sequence"] == 1
    frame = pd.read_parquet(result.output_parquet)
    assert list(frame["peptide_sequence"]) == ["PEPTIDEK"]
    assert frame.loc[0, "project_accession"] == "PXD000001"
    assert frame.loc[0, "source_file"] == "sample.raw"
    assert frame.loc[0, "retention_time_unit"] == "minute"
    assert Path(result.preview_csv).exists()
    assert Path(result.peptide_parquet).exists()
    assert Path(result.peptide_preview_csv).exists()
    assert Path(result.peptide_report_json).exists()
    assert Path(result.validation_report_json).exists()
    assert Path(result.schema_json_path).exists()


def test_export_rt_ai_ready_warns_when_confidence_missing_by_default(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "no_confidence.tsv",
        [
            {
                "Sequence": "PEPTIDEK",
                "Charge": 2,
                "Retention Time (min)": 11.0,
                "Spectrum ID": "scan=1",
            }
        ],
    )

    result = export_rt_ai_ready([search_result], tmp_path / "rt")

    assert result.rows_out == 1
    assert "confidence_column_missing" in result.warnings
    report = json.loads(Path(result.report_json).read_text(encoding="utf-8"))
    assert report["warnings"] == ["confidence_column_missing"]


def test_export_rt_ai_ready_writes_peptide_level_aggregation(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "fragpipe_psm.tsv",
        [
            {
                "Peptide": "PEPTIDEK",
                "Modified Peptide": "PEPTIDEK",
                "Charge": 2,
                "Retention": 10.0,
                "Spectrum": "scan=101",
                "Spectrum File": "sample.raw",
                "PSM Q-Value": 0.008,
                "PeptideProphet Probability": 0.91,
            },
            {
                "Peptide": "PEPTIDEK",
                "Modified Peptide": "PEPTIDEK",
                "Charge": 2,
                "Retention": 14.0,
                "Spectrum": "scan=102",
                "Spectrum File": "sample.raw",
                "PSM Q-Value": 0.002,
                "PeptideProphet Probability": 0.97,
            },
            {
                "Peptide": "OTHERK",
                "Modified Peptide": "OTHERK",
                "Charge": 3,
                "Retention": 20.0,
                "Spectrum": "scan=103",
                "Spectrum File": "sample.raw",
                "PSM Q-Value": 0.001,
                "PeptideProphet Probability": 0.99,
            },
        ],
    )

    result = export_rt_ai_ready([search_result], tmp_path / "rt")

    peptide = pd.read_parquet(result.peptide_parquet)
    by_sequence = {row.modified_sequence: row for row in peptide.itertuples()}
    assert result.rows_out == 3
    assert result.peptide_rows_out == 2
    assert by_sequence["PEPTIDEK"].psm_count == 2
    assert by_sequence["PEPTIDEK"].retention_time_median == 12.0
    assert by_sequence["PEPTIDEK"].retention_time_mean == 12.0
    assert by_sequence["PEPTIDEK"].best_q_value == 0.002
    assert by_sequence["PEPTIDEK"].best_psm_probability == 0.97
    report = json.loads(Path(result.peptide_report_json).read_text(encoding="utf-8"))
    assert report["peptide_rows"] == 2
    assert report["aggregation_key"] == ["modified_sequence", "charge", "source_file"]


def test_export_rt_ai_ready_requires_confidence_when_requested(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "no_confidence.tsv",
        [{"Sequence": "PEPTIDEK", "Charge": 2, "Retention Time": 11.0}],
    )

    try:
        export_rt_ai_ready([search_result], tmp_path / "rt", require_confidence=True)
    except ValueError as exc:
        assert "confidence_column_missing" in str(exc)
    else:
        raise AssertionError("Expected confidence requirement to block export")


def test_export_rt_ai_ready_uses_task_build_plan_metadata(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "sample.tsv",
        [
            {
                "Peptide": "PEPTIDEK",
                "Charge": 2,
                "Retention": 12.5,
                "Spectrum File": "sample.raw",
                "PSM Q-Value": 0.001,
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
                        "lc_gradient_minutes": 90.0,
                        "ptm_type": "phospho",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = export_rt_ai_ready([search_result], tmp_path / "rt", task_build_plan=task_build_plan)

    frame = pd.read_parquet(result.output_parquet)
    assert frame.loc[0, "project_accession"] == "PXD123456"
    assert frame.loc[0, "species"] == "human"
    assert frame.loc[0, "instrument_family"] == "orbitrap"
    assert frame.loc[0, "lc_gradient_minutes"] == 90.0
    assert frame.loc[0, "ptm_type"] == "phospho"
    validation = json.loads(Path(result.validation_report_json).read_text(encoding="utf-8"))
    assert validation["task_build_plan"]["matched_sources"] == ["sample.raw"]
    assert validation["task_build_plan"]["unmatched_sources"] == []


def test_export_rt_ai_ready_warns_when_task_build_plan_source_is_unmatched(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "sample.tsv",
        [
            {
                "Peptide": "PEPTIDEK",
                "Charge": 2,
                "Retention": 12.5,
                "Spectrum File": "unlisted.raw",
                "PSM Q-Value": 0.001,
            }
        ],
    )
    task_build_plan = tmp_path / "discovery_task_build_plan.json"
    task_build_plan.write_text(
        json.dumps({"files": [{"project_accession": "PXD123456", "file_name": "sample.raw"}]}),
        encoding="utf-8",
    )

    result = export_rt_ai_ready([search_result], tmp_path / "rt", task_build_plan=task_build_plan)

    assert "task_build_plan_source_unmatched:unlisted.raw" in result.warnings
    validation = json.loads(Path(result.validation_report_json).read_text(encoding="utf-8"))
    assert validation["task_build_plan"]["unmatched_sources"] == ["unlisted.raw"]


def test_export_rt_ai_ready_cli_writes_outputs(tmp_path: Path):
    search_result = _write_tsv(tmp_path / "fragpipe_psm.tsv", _fragpipe_rows()[:1])
    output_dir = tmp_path / "rt_cli"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "export-rt-ai-ready",
            "--search-result",
            str(search_result),
            "--output-dir",
            str(output_dir),
            "--project-accession",
            "PXD000001",
            "--source-file",
            "sample.raw",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["rows_out"] == 1
    assert payload["peptide_rows_out"] == 1
    assert (output_dir / "rt_train.parquet").exists()
    assert (output_dir / "rt_train.preview.csv").exists()
    assert (output_dir / "rt_train_peptide.parquet").exists()
    assert (output_dir / "rt_train_peptide.preview.csv").exists()
    assert (output_dir / "rt_peptide_aggregation_report.json").exists()
    assert (output_dir / "rt_export_report.json").exists()
    assert (output_dir / "rt_validation_report.json").exists()
    assert (output_dir / "rt_schema.json").exists()
