from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agent.cli import app
from agent.discovery.manifest import write_dataset_manifest
from agent.discovery.models import DatasetManifest, DatasetRequest, DiscoveredFile
from agent.discovery.outcomes import build_discovery_batch_outcome_report, write_discovery_batch_outcome_report


def _file(
    name: str,
    *,
    project_accession: str = "PXD000001",
    validity_status: str = "valid",
    task_readiness_status: str = "weak_ready",
) -> DiscoveredFile:
    return DiscoveredFile(
        project_accession=project_accession,
        project_title="Human phosphoproteomics",
        file_name=name,
        download_url=f"https://example.test/{project_accession}/{name}",
        file_type=Path(name).suffix.lower(),
        file_role="raw_acquisition",
        species=["human"],
        acquisition_mode="dda",
        ptm_type="phospho",
        validity_status=validity_status,  # type: ignore[arg-type]
        task_type="rt_prediction",
        task_readiness_status=task_readiness_status,  # type: ignore[arg-type]
        evidence_level="mixed",
        sdrf_match_status="no_sdrf",
        trust_score=0.9,
        file_score=50.0,
    )


def _manifest() -> DatasetManifest:
    return DatasetManifest(
        run_id="run-outcome",
        request=DatasetRequest(max_projects=2, max_files=3),
        files=[
            _file("valid.raw", project_accession="PXD_A", validity_status="valid"),
            _file("weak.raw", project_accession="PXD_B", validity_status="weak_keep"),
            _file("not_ready.raw", project_accession="PXD_C", validity_status="needs_review", task_readiness_status="not_ready"),
        ],
    )


def test_batch_outcomes_match_discovery_context_and_group_metrics():
    batch_manifest = {
        "batch_id": "batch-direct",
        "status": "completed",
        "items": [
            {
                "index": 1,
                "input": "valid.raw",
                "status": "completed",
                "output_dir": "items/001",
                "discovery_context": {"project_accession": "PXD_A", "file_name": "valid.raw"},
            },
            {
                "index": 2,
                "input": "weak.raw",
                "status": "needs_review",
                "output_dir": "items/002",
                "error": "ambiguous",
                "discovery_context": {"project_accession": "PXD_B", "file_name": "weak.raw"},
            },
        ],
    }

    report = build_discovery_batch_outcome_report(_manifest(), batch_manifest)

    assert report.manifest_file_count == 3
    assert report.submitted_files == 2
    assert report.completed_items == 1
    assert report.needs_review_items == 1
    assert report.submitted_success_rate == 0.5
    assert report.by_validity_status["valid"]["submitted_success_rate"] == 1.0
    assert report.by_validity_status["weak_keep"]["needs_review_items"] == 1
    assert report.by_task_readiness_status["not_ready"]["submitted_files"] == 0
    assert {row.file_name: row.matched_by for row in report.rows}["valid.raw"] == "project_accession+file_name"


def test_batch_outcomes_fallback_to_unique_file_name_for_legacy_batch():
    batch_manifest = {
        "batch_id": "batch-legacy",
        "status": "completed",
        "items": [{"index": 1, "input": "valid.raw", "status": "completed", "output_dir": "items/001"}],
    }

    report = build_discovery_batch_outcome_report(_manifest(), batch_manifest)

    row = next(row for row in report.rows if row.file_name == "valid.raw")
    assert row.batch_status == "completed"
    assert row.matched_by == "unique_file_name"
    assert report.unmatched_batch_items == 0


def test_batch_outcomes_reports_unmatched_batch_items():
    batch_manifest = {
        "batch_id": "batch-unmatched",
        "status": "completed",
        "items": [{"index": 1, "input": "missing.raw", "status": "failed", "output_dir": "items/001"}],
    }

    report = build_discovery_batch_outcome_report(_manifest(), batch_manifest)

    assert report.submitted_files == 0
    assert report.unmatched_batch_items == 1
    assert report.unmatched_items[0]["input"] == "missing.raw"


def test_write_discovery_batch_outcome_report_outputs_json_and_csv(tmp_path: Path):
    paths = write_discovery_batch_outcome_report(
        _manifest(),
        {
            "batch_id": "batch-direct",
            "status": "completed",
            "items": [
                {
                    "index": 1,
                    "input": "valid.raw",
                    "status": "completed",
                    "output_dir": "items/001",
                    "discovery_context": {"project_accession": "PXD_A", "file_name": "valid.raw"},
                }
            ],
        },
        tmp_path,
    )

    report = json.loads(paths["discovery_batch_outcome_report"].read_text(encoding="utf-8"))
    csv_text = paths["discovery_batch_outcomes"].read_text(encoding="utf-8")
    assert report["submitted_files"] == 1
    assert "batch_status" in csv_text.splitlines()[0]
    assert "valid.raw" in csv_text


def test_link_discovery_batch_results_cli_writes_report(tmp_path: Path):
    manifest_paths = write_dataset_manifest(_manifest(), tmp_path / "discovery")
    batch_manifest = tmp_path / "batch_manifest.json"
    batch_manifest.write_text(
        json.dumps(
            {
                "batch_id": "batch-direct",
                "status": "completed",
                "items": [
                    {
                        "index": 1,
                        "input": "valid.raw",
                        "status": "completed",
                        "output_dir": "items/001",
                        "discovery_context": {"project_accession": "PXD_A", "file_name": "valid.raw"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "outcomes"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "link-discovery-batch-results",
            "--manifest",
            str(manifest_paths["dataset_manifest_json"]),
            "--batch-manifest",
            str(batch_manifest),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "completed"
    assert payload["submitted_success_rate"] == 1.0
    assert (output_dir / "discovery_batch_outcome_report.json").exists()
    assert (output_dir / "discovery_batch_outcomes.csv").exists()
