from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agent.ai_ready.validation import validate_ai_ready_build
from agent.cli import app


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_validate_ai_ready_build_reads_active_exporter_report(tmp_path: Path):
    build_dir = tmp_path / "build"
    parquet = build_dir / "fragment_intensity_ai_ready" / "fragment_intensity_train.parquet"
    parquet.parent.mkdir(parents=True)
    parquet.write_text("placeholder", encoding="utf-8")
    _write_json(
        build_dir / "discovery_task_build_plan.json",
        {
            "required_labels": ["fragment_intensity_labels"],
            "next_pipeline_steps": ["search", "fragment_annotation"],
            "quality_gate": ["matched_fragment_quality"],
            "summary": {"missing_requirement_counts": {"fragment_intensity_labels": 3}},
        },
    )
    _write_json(
        build_dir / "fragment_intensity_ai_ready" / "fragment_intensity_export_report.json",
        {
            "status": "completed",
            "rows_in": 4,
            "rows_out": 2,
            "rows_filtered": 2,
            "filter_counts": {"spectrum_not_matched": 2},
            "warnings": ["spectrum_not_matched"],
            "spectrum_evidence": {
                "fragmentation_methods": ["HCD"],
                "fragmentation_method_counts": {"HCD": 3},
                "fragmentation_evidence_level": "spectrum",
                "spectra_scanned": 3,
            },
            "outputs": {"fragment_intensity_train_parquet": str(parquet)},
            "inputs": [],
        },
    )

    paths = validate_ai_ready_build(build_dir, "fragment_intensity_prediction")

    report = json.loads(paths["ai_ready_validation_report_json"].read_text(encoding="utf-8"))
    assert report["status"] == "completed"
    row = report["rows"][0]
    assert row["status"] == "export_completed"
    assert row["rows_in"] == 4
    assert row["rows_out"] == 2
    assert row["parquet_exists"] is True
    assert row["filter_counts"] == {"spectrum_not_matched": 2}
    assert row["spectrum_evidence"]["fragmentation_methods"] == ["HCD"]
    assert paths["ai_ready_validation_report_csv"].exists()
    summary = json.loads(paths["ai_ready_build_summary_json"].read_text(encoding="utf-8"))
    assert summary["spectrum_evidence"][0]["fragmentation_evidence_level"] == "spectrum"
    assert "fragmentation_evidence_confirmed" in summary["next_recommendations"]
    assert paths["ai_ready_build_report_md"].exists()


def test_validate_ai_ready_build_reports_missing_export(tmp_path: Path):
    build_dir = tmp_path / "build"
    _write_json(
        build_dir / "discovery_task_build_plan.json",
        {
            "required_labels": ["target_decoy_psm_labels"],
            "summary": {"missing_requirement_counts": {"target_decoy_psm_labels": 1}},
        },
    )

    paths = validate_ai_ready_build(build_dir, "psm_scoring")

    report = json.loads(paths["ai_ready_validation_report_json"].read_text(encoding="utf-8"))
    assert report["status"] == "export_missing"
    row = report["rows"][0]
    assert row["status"] == "export_missing"
    assert row["missing_task_requirements"] == {"target_decoy_psm_labels": 1}


def test_validate_ai_ready_build_reports_missing_chimeric_export(tmp_path: Path):
    build_dir = tmp_path / "build"
    _write_json(
        build_dir / "discovery_task_build_plan.json",
        {
            "required_labels": ["multi_peptide_spectrum_labels"],
            "summary": {"missing_requirement_counts": {"multi_peptide_spectrum_labels": 2}},
        },
    )

    paths = validate_ai_ready_build(build_dir, "chimeric_interpretation")

    report = json.loads(paths["ai_ready_validation_report_json"].read_text(encoding="utf-8"))
    assert report["status"] == "export_missing"
    row = report["rows"][0]
    assert row["status"] == "export_missing"
    assert row["missing_task_requirements"] == {"multi_peptide_spectrum_labels": 2}


def test_validate_ai_ready_build_reads_chimeric_exporter_report(tmp_path: Path):
    build_dir = tmp_path / "build"
    parquet = build_dir / "chimeric_ai_ready" / "chimeric_train.parquet"
    parquet.parent.mkdir(parents=True)
    parquet.write_text("placeholder", encoding="utf-8")
    _write_json(
        build_dir / "discovery_task_build_plan.json",
        {
            "required_labels": ["multi_peptide_spectrum_labels", "component_intensity_labels"],
            "next_pipeline_steps": ["search_with_chimeric_support", "multi_component_labeling"],
            "quality_gate": ["multi_peptide_label_confidence"],
            "summary": {"missing_requirement_counts": {"multi_peptide_spectrum_labels": 1}},
        },
    )
    _write_json(
        build_dir / "chimeric_ai_ready" / "chimeric_export_report.json",
        {
            "status": "completed",
            "rows_in": 3,
            "rows_out": 1,
            "rows_filtered": 2,
            "filter_counts": {"no_multi_peptide_assignment": 2},
            "warnings": [],
            "outputs": {"chimeric_train_parquet": str(parquet)},
        },
    )

    paths = validate_ai_ready_build(build_dir, "chimeric_interpretation")

    report = json.loads(paths["ai_ready_validation_report_json"].read_text(encoding="utf-8"))
    assert report["status"] == "completed"
    row = report["rows"][0]
    assert row["status"] == "export_completed"
    assert row["rows_out"] == 1
    assert row["parquet_exists"] is True


def test_validate_ai_ready_build_reads_denovo_exporter_report(tmp_path: Path):
    build_dir = tmp_path / "build"
    parquet = build_dir / "denovo_ai_ready" / "denovo_train.parquet"
    parquet.parent.mkdir(parents=True)
    parquet.write_text("placeholder", encoding="utf-8")
    _write_json(
        build_dir / "discovery_task_build_plan.json",
        {
            "required_labels": ["peptide_sequence_labels"],
            "next_pipeline_steps": ["search", "spectrum_sequence_pair_export"],
            "quality_gate": ["sequence_label_quality"],
            "summary": {"missing_requirement_counts": {"peptide_sequence_labels": 1}},
        },
    )
    _write_json(
        build_dir / "denovo_ai_ready" / "denovo_export_report.json",
        {
            "status": "completed",
            "rows_in": 2,
            "rows_out": 1,
            "rows_filtered": 1,
            "filter_counts": {"spectrum_not_matched": 1},
            "warnings": ["spectrum_not_matched"],
            "outputs": {"denovo_train_parquet": str(parquet)},
        },
    )

    paths = validate_ai_ready_build(build_dir, "denovo")

    report = json.loads(paths["ai_ready_validation_report_json"].read_text(encoding="utf-8"))
    assert report["status"] == "completed"
    row = report["rows"][0]
    assert row["status"] == "export_completed"
    assert row["rows_out"] == 1
    assert row["parquet_exists"] is True


def test_validate_ai_ready_build_reads_ptm_denovo_exporter_report(tmp_path: Path):
    build_dir = tmp_path / "build"
    parquet = build_dir / "ptm_denovo_ai_ready" / "ptm_denovo_train.parquet"
    parquet.parent.mkdir(parents=True)
    parquet.write_text("placeholder", encoding="utf-8")
    _write_json(
        build_dir / "discovery_task_build_plan.json",
        {
            "required_labels": ["modified_peptide_sequence_labels", "ptm_localization_labels"],
            "next_pipeline_steps": ["ptm_search", "modified_sequence_export"],
            "quality_gate": ["ptm_localization_confidence"],
            "summary": {"missing_requirement_counts": {"ptm_localization_labels": 1}},
        },
    )
    _write_json(
        build_dir / "ptm_denovo_ai_ready" / "ptm_denovo_export_report.json",
        {
            "status": "completed",
            "rows_in": 2,
            "rows_out": 1,
            "rows_filtered": 1,
            "filter_counts": {"missing_modified_sequence": 1},
            "warnings": ["localization_confidence_missing"],
            "outputs": {"ptm_denovo_train_parquet": str(parquet)},
        },
    )

    paths = validate_ai_ready_build(build_dir, "ptm_denovo")

    report = json.loads(paths["ai_ready_validation_report_json"].read_text(encoding="utf-8"))
    assert report["status"] == "completed"
    row = report["rows"][0]
    assert row["status"] == "export_completed"
    assert row["rows_out"] == 1
    assert row["parquet_exists"] is True


def test_validate_ai_ready_build_cli_writes_reports(tmp_path: Path):
    build_dir = tmp_path / "build"
    _write_json(build_dir / "discovery_task_build_plan.json", {"summary": {}})
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "validate-ai-ready-build",
            "--build-dir",
            str(build_dir),
            "--task-type",
            "chimeric_interpretation",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "export_missing"
    assert (build_dir / "ai_ready_validation_report.json").exists()
    assert (build_dir / "ai_ready_validation_report.csv").exists()
    assert (build_dir / "ai_ready_build_report.md").exists()
