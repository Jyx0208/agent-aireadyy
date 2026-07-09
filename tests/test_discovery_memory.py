from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent.cli import app
from agent.discovery.manifest import write_dataset_manifest
from agent.discovery.memory import (
    DiscoveryMemory,
    build_run_record,
    decisions_from_review_csv,
    memory_feedback_for_candidate,
    memory_prior_for_file,
)
from agent.discovery.models import DatasetManifest, DatasetRequest, DiscoveredFile, DiscoveredProject
from agent.discovery.value_scoring import annotate_manifest_value_scores


def _manifest(run_id: str = "run-001") -> DatasetManifest:
    request = DatasetRequest(max_projects=1, max_files=2)
    project = DiscoveredProject(
        project_accession="PXD000001",
        project_title="Human phosphoproteomics DDA",
        species=["human"],
        acquisition_mode="dda",
        ptm_type="phospho",
        project_score=80,
        confidence=0.9,
        selected_file_count=2,
    )
    files = [
        DiscoveredFile(
            project_accession="PXD000001",
            project_title=project.project_title,
            file_name="HeLa_01.raw",
            download_url="https://ftp.pride.ebi.ac.uk/HeLa_01.raw",
            file_type=".raw",
            expected_size_bytes=1000,
            species=["human"],
            acquisition_mode="dda",
            ptm_type="phospho",
            project_score=80,
            file_score=60,
            confidence=0.9,
        ),
        DiscoveredFile(
            project_accession="PXD000001",
            project_title=project.project_title,
            file_name="HeLa_02.mzML",
            download_url="https://ftp.pride.ebi.ac.uk/HeLa_02.mzML",
            file_type=".mzml",
            expected_size_bytes=2000,
            species=["human"],
            acquisition_mode="dda",
            ptm_type="phospho",
            project_score=80,
            file_score=58,
            confidence=0.86,
            review_decision="needs_review",
            review_reason="unclear",
            review_note="check manually",
        ),
    ]
    return DatasetManifest(
        run_id=run_id,
        request=request,
        projects=[project],
        files=files,
        summary={"run_id": run_id, "queries": ["phosphoproteomics"], "selected_projects": 1, "selected_files": 2},
    )


def _write_review_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["project_accession", "file_name", "decision", "reason", "note"])
        writer.writeheader()
        writer.writerows(rows)


def test_memory_appends_discovery_run_record(tmp_path: Path):
    manifest = _manifest()
    output_dir = tmp_path / "discovery"
    paths = write_dataset_manifest(manifest, output_dir)
    memory = DiscoveryMemory(tmp_path / "memory")

    memory.append_run(
        build_run_record(
            run_id=manifest.run_id or "run-001",
            manifest=manifest,
            output_dir=output_dir,
            manifest_path=paths["dataset_manifest_json"],
        )
    )

    records = memory.load_runs()
    assert len(records) == 1
    assert records[0].run_id == "run-001"
    assert records[0].queries == ["phosphoproteomics"]
    assert memory.discovery_runs_path.exists()


def test_memory_imports_review_csv(tmp_path: Path):
    manifest = _manifest()
    review_csv = tmp_path / "reviewed.csv"
    _write_review_csv(
        review_csv,
        [
            {"project_accession": "PXD000001", "file_name": "HeLa_01.raw", "decision": "keep", "reason": "correct", "note": ""},
            {
                "project_accession": "PXD000001",
                "file_name": "HeLa_02.mzML",
                "decision": "reject",
                "reason": "wrong_acquisition",
                "note": "metadata says DIA",
            },
        ],
    )
    memory = DiscoveryMemory(tmp_path / "memory")

    decisions = decisions_from_review_csv(review_csv=review_csv, manifest=manifest)
    memory.append_review_decisions(decisions)

    loaded = memory.load_review_decisions()
    assert [decision.decision for decision in loaded] == ["keep", "reject"]
    assert loaded[1].reason == "wrong_acquisition"
    assert loaded[1].run_id == "run-001"


def test_review_import_rejects_invalid_decision(tmp_path: Path):
    manifest = _manifest()
    review_csv = tmp_path / "reviewed.csv"
    _write_review_csv(
        review_csv,
        [
            {
                "project_accession": "PXD000001",
                "file_name": "HeLa_01.raw",
                "decision": "maybe",
                "reason": "unclear",
                "note": "",
            }
        ],
    )
    memory = DiscoveryMemory(tmp_path / "memory")

    with pytest.raises(ValueError, match="invalid decision"):
        memory.append_review_decisions(decisions_from_review_csv(review_csv=review_csv, manifest=manifest))

    assert not memory.review_decisions_path.exists()


def test_manifest_writer_adds_review_columns(tmp_path: Path):
    manifest = _manifest()

    paths = write_dataset_manifest(manifest, tmp_path)

    json_payload = json.loads(paths["dataset_manifest_json"].read_text(encoding="utf-8"))
    assert json_payload["run_id"] == "run-001"
    assert json_payload["files"][1]["review_decision"] == "needs_review"

    with paths["dataset_manifest_csv"].open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["run_id"] == "run-001"
    assert "review_decision" in rows[0]
    assert rows[1]["review_reason"] == "unclear"
    assert rows[1]["review_note"] == "check manually"


def test_discovery_memory_summary_counts_decisions(tmp_path: Path):
    manifest = _manifest()
    memory = DiscoveryMemory(tmp_path / "memory")
    memory.append_run(
        build_run_record(
            run_id="run-001",
            manifest=manifest,
            output_dir=tmp_path / "discovery",
            manifest_path=tmp_path / "discovery" / "dataset_manifest.json",
        )
    )
    review_csv = tmp_path / "reviewed.csv"
    _write_review_csv(
        review_csv,
        [
            {"project_accession": "PXD000001", "file_name": "HeLa_01.raw", "decision": "keep", "reason": "correct", "note": ""},
            {"project_accession": "PXD000001", "file_name": "HeLa_02.mzML", "decision": "reject", "reason": "result_file", "note": ""},
        ],
    )
    memory.append_review_decisions(decisions_from_review_csv(review_csv=review_csv, manifest=manifest))

    summary = memory.summary()

    assert summary["discovery_run_count"] == 1
    assert summary["review_decision_count"] == 2
    assert summary["decision_counts"] == {"keep": 1, "reject": 1}
    assert summary["reject_reason_counts"] == {"result_file": 1}
    assert summary["reviewed_project_count"] == 1
    assert summary["reviewed_file_count"] == 2


def test_memory_feedback_preserves_curation_context_and_guides_value_scoring(tmp_path: Path):
    manifest = _manifest()
    review_csv = tmp_path / "reviewed.csv"
    _write_review_csv(
        review_csv,
        [
            {
                "project_accession": "PXD000001",
                "file_name": "HeLa_01.raw",
                "decision": "reject",
                "reason": "other",
                "note": (
                    "curation_type=exclude_low_value_high_risk; "
                    "repository_strategy=multi_repository; "
                    "planned_repositories=pride,massive,iprox"
                ),
            }
        ],
    )
    decisions = decisions_from_review_csv(review_csv=review_csv, manifest=manifest)

    feedback = memory_feedback_for_candidate(decisions, "PXD000001", "HeLa_01.raw")

    assert feedback["recommended_action"] == "skip"
    assert feedback["repository_strategy"] == "multi_repository"
    assert feedback["planned_repositories"] == ["iprox", "massive", "pride"]
    assert "curation_type:exclude_low_value_high_risk" in feedback["evidence"]
    assert memory_prior_for_file(decisions, "PXD000001", "HeLa_01.raw") < -0.1

    file = manifest.files[0].model_copy(
        update={
            "task_readiness_status": "ready",
            "ai_ready_target_schema": "rt_prediction",
            "label_source_status": "available",
            "spectra_requirement_status": "satisfied",
            "metadata_requirement_status": "satisfied",
            "validity_status": "valid",
            "memory_feedback": feedback,
        }
    )
    scored = annotate_manifest_value_scores(manifest.model_copy(update={"files": [file]}))

    assert scored.files[0].data_value_action == "skip"
    assert "discovery_memory_recommends_skip" in scored.files[0].data_value_reasons
    assert "discovery_memory_recommends_skip" in scored.files[0].task_ai_readiness_reasons

    paths = write_dataset_manifest(scored, tmp_path / "manifest_with_memory")
    quality = json.loads(paths["quality_report"].read_text(encoding="utf-8"))
    memory_summary = quality["memory_feedback_summary"]
    assert memory_summary["files_with_memory_feedback"] == 1
    assert memory_summary["action_counts"]["skip"] == 1
    assert memory_summary["curation_type_counts"]["exclude_low_value_high_risk"] == 1
    assert memory_summary["repository_strategy_counts"]["multi_repository"] == 1
    assert memory_summary["planned_repository_counts"] == {"iprox": 1, "massive": 1, "pride": 1}

    ranking = json.loads(paths["data_value_ranking_json"].read_text(encoding="utf-8"))
    assert ranking["rows"][0]["memory_recommended_action"] == "skip"
    assert ranking["rows"][0]["memory_planned_repositories"] == "iprox;massive;pride"
    ranking_md = paths["data_value_report_md"].read_text(encoding="utf-8")
    assert "Candidates with discovery memory feedback: 1" in ranking_md


def test_discover_dataset_cli_save_memory(monkeypatch, tmp_path: Path):
    manifest = _manifest(run_id=None)

    def fake_discover(received_request: DatasetRequest, memory=None) -> DatasetManifest:
        return manifest.model_copy(update={"request": received_request})

    monkeypatch.setattr("agent.cli.discover_pride_dataset", fake_discover)
    runner = CliRunner()
    output_dir = tmp_path / "discovery"
    memory_dir = tmp_path / "memory"

    result = runner.invoke(
        app,
        [
            "discover-dataset",
            "--goal",
            "ptm",
            "--ptm",
            "phospho",
            "--species",
            "human",
            "--acquisition",
            "dda",
            "--max-projects",
            "1",
            "--max-files",
            "2",
            "--output-dir",
            str(output_dir),
            "--save-memory",
            "--memory-dir",
            str(memory_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    assert output["run_id"]
    assert output["memory"]["memory_dir"] == str(memory_dir)
    assert (memory_dir / "discovery_runs.jsonl").exists()

    stored_manifest = json.loads((output_dir / "dataset_manifest.json").read_text(encoding="utf-8"))
    assert stored_manifest["run_id"] == output["run_id"]

    memory = DiscoveryMemory(memory_dir)
    records = memory.load_runs()
    assert len(records) == 1
    assert records[0].run_id == output["run_id"]
