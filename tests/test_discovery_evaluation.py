from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent.cli import app
from agent.discovery.evaluation import (
    build_validation_report,
    evaluate_data_value_selection,
    load_validation_reviews,
    select_review_files,
    write_review_sheet,
    write_validation_report,
)
from agent.discovery.manifest import write_dataset_manifest
from agent.discovery.memory import DiscoveryMemory
from agent.discovery.models import DatasetManifest, DatasetRequest, DiscoveredFile, DiscoveredProject


def _file(
    name: str,
    *,
    status: str,
    trust: float,
    score: float,
    reasons: list[str] | None = None,
    species: list[str] | None = None,
    acquisition_mode: str | None = "dda",
    instrument_families: list[str] | None = None,
    fragmentation_methods: list[str] | None = None,
    project_accession: str = "PXD000001",
    data_value_score: float | None = None,
    task_ai_readiness_score: float | None = None,
    components: dict[str, float] | None = None,
    expected_size_mb: float | None = None,
    task_type: str | None = None,
) -> DiscoveredFile:
    return DiscoveredFile(
        project_accession=project_accession,
        project_title="Human phosphoproteomics DDA",
        file_name=name,
        download_url=f"https://ftp.pride.ebi.ac.uk/{name}",
        file_type=Path(name).suffix.lower() or ".raw",
        expected_size_bytes=int(expected_size_mb * 1024 * 1024) if expected_size_mb is not None else None,
        species=["human"] if species is None else species,
        canonical_species=["human"] if species is None else species,
        acquisition_mode=acquisition_mode,
        ptm_type="phospho",
        file_score=score,
        trust_score=trust,
        validity_status=status,  # type: ignore[arg-type]
        validity_reasons=reasons or ["strong_ptm_evidence"],
        instrument_families=["orbitrap"] if instrument_families is None else instrument_families,
        fragmentation_methods=["HCD"] if fragmentation_methods is None else fragmentation_methods,
        lc_gradient_minutes=90.0,
        task_type=task_type,
        task_ai_readiness_score=task_ai_readiness_score,
        data_value_score=data_value_score,
        data_value_components=components or {},
    )


def _manifest() -> DatasetManifest:
    request = DatasetRequest(max_projects=1, max_files=4, max_files_per_project=4)
    project = DiscoveredProject(
        project_accession="PXD000001",
        project_title="Human phosphoproteomics DDA",
        project_score=80,
        selected_file_count=4,
    )
    return DatasetManifest(
        run_id="run-001",
        request=request,
        projects=[project],
        files=[
            _file("valid_high.raw", status="valid", trust=0.95, score=60),
            _file("weak.raw", status="weak_keep", trust=0.88, score=58, reasons=["missing_fragmentation"], fragmentation_methods=[]),
            _file("valid_low.raw", status="valid", trust=0.70, score=59),
            _file("review.raw", status="needs_review", trust=0.99, score=61, reasons=["missing_species_evidence"], species=[]),
        ],
        summary={"run_id": "run-001", "selected_projects": 1, "selected_files": 4},
    )


def _write_review_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["project_accession", "file_name", "review_decision", "review_reason", "review_note"],
        )
        writer.writeheader()
        writer.writerows(rows)


def test_make_review_sheet_selects_usable_files(tmp_path: Path):
    manifest = _manifest()
    output_csv = tmp_path / "review_sheet.csv"

    write_review_sheet(manifest, output_csv)

    with output_csv.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["file_name"] for row in rows] == ["valid_high.raw", "weak.raw", "valid_low.raw"]
    assert "file_role" in rows[0]
    assert "file_role_reasons" in rows[0]
    assert "sdrf_match_status" in rows[0]
    assert "evidence_level" in rows[0]
    assert "evidence_warnings" in rows[0]
    assert "review_decision" in rows[0]
    assert "evidence" in rows[0]


def test_make_review_sheet_respects_max_files_and_order():
    selected = select_review_files(_manifest(), selection="all", max_files=2)

    assert [file.file_name for file in selected] == ["review.raw", "valid_high.raw"]


def test_eval_discovery_counts_review_decisions(tmp_path: Path):
    manifest = _manifest()
    review_csv = tmp_path / "reviewed.csv"
    _write_review_csv(
        review_csv,
        [
            {"project_accession": "PXD000001", "file_name": "valid_high.raw", "review_decision": "keep", "review_reason": "correct", "review_note": ""},
            {"project_accession": "PXD000001", "file_name": "weak.raw", "review_decision": "reject", "review_reason": "wrong_acquisition", "review_note": ""},
            {"project_accession": "PXD000001", "file_name": "review.raw", "review_decision": "needs_review", "review_reason": "unclear", "review_note": ""},
        ],
    )

    reviews = load_validation_reviews(review_csv=review_csv, manifest=manifest)
    report = build_validation_report(manifest, reviews)

    assert report["reviewed_files"] == 3
    assert report["keep_count"] == 1
    assert report["reject_count"] == 1
    assert report["needs_review_count"] == 1
    assert report["keep_rate"] == pytest.approx(1 / 3)
    assert report["reject_rate"] == pytest.approx(1 / 3)
    assert report["needs_review_rate"] == pytest.approx(1 / 3)


def test_eval_discovery_counts_false_positive_reasons(tmp_path: Path):
    manifest = _manifest()
    review_csv = tmp_path / "reviewed.csv"
    _write_review_csv(
        review_csv,
        [
            {"project_accession": "PXD000001", "file_name": "weak.raw", "review_decision": "reject", "review_reason": "wrong_ptm", "review_note": ""},
            {"project_accession": "PXD000001", "file_name": "valid_low.raw", "review_decision": "reject", "review_reason": "result_file", "review_note": ""},
            {"project_accession": "PXD000001", "file_name": "review.raw", "review_decision": "reject", "review_reason": "sdrf_mismatch", "review_note": ""},
        ],
    )

    report = build_validation_report(manifest, load_validation_reviews(review_csv=review_csv, manifest=manifest))

    assert report["false_positive_reason_counts"] == {"result_file": 1, "sdrf_mismatch": 1, "wrong_ptm": 1}
    assert report["sdrf_related_issue_count"] == 1


def test_eval_discovery_reports_valid_and_usable_keep_rate(tmp_path: Path):
    manifest = _manifest()
    review_csv = tmp_path / "reviewed.csv"
    _write_review_csv(
        review_csv,
        [
            {"project_accession": "PXD000001", "file_name": "valid_high.raw", "review_decision": "keep", "review_reason": "correct", "review_note": ""},
            {"project_accession": "PXD000001", "file_name": "valid_low.raw", "review_decision": "reject", "review_reason": "project_level_overused", "review_note": ""},
            {"project_accession": "PXD000001", "file_name": "weak.raw", "review_decision": "keep", "review_reason": "unknown_acceptable", "review_note": ""},
        ],
    )

    report = build_validation_report(manifest, load_validation_reviews(review_csv=review_csv, manifest=manifest))

    assert report["valid_keep_rate"] == 0.5
    assert report["usable_keep_rate"] == pytest.approx(2 / 3)
    assert report["project_level_overused_count"] == 1
    assert report["validity_status_by_review_decision"]["valid"] == {"keep": 1, "reject": 1}
    assert report["unknown_field_counts"]["fragmentation_methods"] == 1


def test_eval_discovery_rejects_invalid_review_decision(tmp_path: Path):
    manifest = _manifest()
    review_csv = tmp_path / "reviewed.csv"
    _write_review_csv(
        review_csv,
        [
            {"project_accession": "PXD000001", "file_name": "valid_high.raw", "review_decision": "maybe", "review_reason": "unclear", "review_note": ""}
        ],
    )

    with pytest.raises(ValueError, match="invalid decision"):
        load_validation_reviews(review_csv=review_csv, manifest=manifest)


def test_eval_discovery_cli_writes_report_files(tmp_path: Path):
    manifest = _manifest()
    manifest_paths = write_dataset_manifest(manifest, tmp_path / "discovery")
    review_csv = tmp_path / "reviewed.csv"
    _write_review_csv(
        review_csv,
        [
            {"project_accession": "PXD000001", "file_name": "valid_high.raw", "review_decision": "keep", "review_reason": "correct", "review_note": ""},
            {"project_accession": "PXD000001", "file_name": "weak.raw", "review_decision": "reject", "review_reason": "wrong_ptm", "review_note": ""},
        ],
    )
    runner = CliRunner()
    output_dir = tmp_path / "validation"

    result = runner.invoke(
        app,
        [
            "eval-discovery",
            "--manifest",
            str(manifest_paths["dataset_manifest_json"]),
            "--review-csv",
            str(review_csv),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    assert output["reviewed_files"] == 2
    assert (output_dir / "discovery_validation_report.json").exists()
    assert (output_dir / "discovery_validation_report.csv").exists()
    assert (output_dir / "false_positive_reasons.csv").exists()

    report = json.loads((output_dir / "discovery_validation_report.json").read_text(encoding="utf-8"))
    assert report["false_positive_reason_counts"] == {"wrong_ptm": 1}


def test_eval_discovery_can_save_memory(tmp_path: Path):
    manifest = _manifest()
    manifest_paths = write_dataset_manifest(manifest, tmp_path / "discovery")
    review_csv = tmp_path / "reviewed.csv"
    _write_review_csv(
        review_csv,
        [
            {"project_accession": "PXD000001", "file_name": "valid_high.raw", "review_decision": "keep", "review_reason": "correct", "review_note": ""}
        ],
    )
    runner = CliRunner()
    memory_dir = tmp_path / "memory"

    result = runner.invoke(
        app,
        [
            "eval-discovery",
            "--manifest",
            str(manifest_paths["dataset_manifest_json"]),
            "--review-csv",
            str(review_csv),
            "--output-dir",
            str(tmp_path / "validation"),
            "--save-memory",
            "--memory-dir",
            str(memory_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    memory = DiscoveryMemory(memory_dir)
    decisions = memory.load_review_decisions()
    assert len(decisions) == 1
    assert decisions[0].decision == "keep"
    assert decisions[0].reason == "correct"


def _value_manifest() -> DatasetManifest:
    request = DatasetRequest(
        goal="fragment intensity phospho DDA with diversity",
        max_projects=3,
        max_files=4,
        max_files_per_project=2,
        task_type="fragment_intensity_prediction",
    )
    project = DiscoveredProject(
        project_accession="PXD000001",
        project_title="Diverse phosphoproteomics DDA",
        project_score=80,
        selected_file_count=4,
    )
    return DatasetManifest(
        run_id="value-run-001",
        request=request,
        projects=[project],
        files=[
            _file(
                "best_value.mzML",
                project_accession="PXD000001",
                status="valid",
                trust=0.95,
                score=80,
                data_value_score=0.94,
                task_ai_readiness_score=0.91,
                expected_size_mb=40,
                task_type="fragment_intensity_prediction",
                components={"estimated_label_yield": 0.9, "cost_efficiency": 0.8, "risk_penalty": 0.05},
            ),
            _file(
                "diverse_value.mzML",
                project_accession="PXD000002",
                status="weak_keep",
                trust=0.88,
                score=75,
                data_value_score=0.86,
                task_ai_readiness_score=0.80,
                expected_size_mb=55,
                species=["mouse"],
                instrument_families=["timsTOF"],
                fragmentation_methods=["CID"],
                task_type="fragment_intensity_prediction",
                components={"estimated_label_yield": 0.75, "cost_efficiency": 0.7, "risk_penalty": 0.12},
            ),
            _file(
                "keyword_only.raw",
                project_accession="PXD000003",
                status="needs_review",
                trust=0.65,
                score=70,
                data_value_score=0.31,
                task_ai_readiness_score=0.35,
                expected_size_mb=450,
                task_type="fragment_intensity_prediction",
                components={"estimated_label_yield": 0.2, "cost_efficiency": 0.15, "risk_penalty": 0.55},
            ),
            _file(
                "manual_rule_small.raw",
                project_accession="PXD000004",
                status="valid",
                trust=0.70,
                score=60,
                data_value_score=0.48,
                task_ai_readiness_score=0.45,
                expected_size_mb=8,
                task_type="fragment_intensity_prediction",
                components={"estimated_label_yield": 0.25, "cost_efficiency": 0.95, "risk_penalty": 0.2},
            ),
        ],
        summary={"run_id": "value-run-001", "selected_projects": 3, "selected_files": 4},
    )


def test_evaluate_data_value_selection_compares_agent_to_baselines(tmp_path: Path):
    manifest = _value_manifest()

    paths = evaluate_data_value_selection(manifest=manifest, output_dir=tmp_path / "eval", max_files=2)

    for path in paths.values():
        assert path.exists()
    summary = json.loads(paths["data_value_strategy_eval_json"].read_text(encoding="utf-8"))
    strategies = {row["strategy"]: row for row in summary["strategies"]}
    assert set(strategies) == {
        "agent_data_value",
        "random_baseline",
        "repository_keyword_baseline",
        "manual_rule_baseline",
    }
    assert strategies["agent_data_value"]["top_files"][0]["file_name"] == "best_value.mzML"
    assert strategies["agent_data_value"]["selected_count"] == 2
    assert "interpretation" in summary
    assert "offline proxy evaluation" in paths["data_value_strategy_eval_md"].read_text(encoding="utf-8")


def test_eval_data_value_selection_cli_writes_report_files(tmp_path: Path):
    manifest = _value_manifest()
    manifest_paths = write_dataset_manifest(manifest, tmp_path / "discovery")
    output_dir = tmp_path / "value_eval"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "eval-data-value-selection",
            "--manifest",
            str(manifest_paths["dataset_manifest_json"]),
            "--output-dir",
            str(output_dir),
            "--max-files",
            "2",
        ],
    )

    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    assert output["status"] == "completed"
    assert output["task_type"] == "fragment_intensity_prediction"
    assert (output_dir / "data_value_strategy_eval.json").exists()
    assert (output_dir / "data_value_strategy_eval.csv").exists()
    assert (output_dir / "data_value_strategy_eval.md").exists()
