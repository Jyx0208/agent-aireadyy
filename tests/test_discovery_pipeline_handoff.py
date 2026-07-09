from __future__ import annotations

import csv
import json
from pathlib import Path

from typer.testing import CliRunner

from agent.cli import app
from agent.discovery.manifest import write_dataset_manifest
from agent.discovery.models import DatasetManifest, DatasetRequest, DiscoveredFile
from agent.discovery.pipeline_handoff import (
    batch_parameters_request,
    build_handoff_preflight,
    build_pipeline_handoff,
    load_pipeline_handoff,
    select_handoff_files,
    write_handoff_batch_preflight,
    write_pipeline_handoff,
)


def _file(
    name: str,
    *,
    validity_status: str = "valid",
    file_role: str = "raw_acquisition",
    task_readiness_status: str | None = "weak_ready",
    trust_score: float = 0.9,
    file_score: float = 50.0,
    needs_review: bool = False,
    download_url: str | None = None,
) -> DiscoveredFile:
    return DiscoveredFile(
        project_accession="PXD000001",
        project_title="Human phosphoproteomics DDA",
        file_name=name,
        download_url=download_url or f"https://ftp.pride.ebi.ac.uk/{name}",
        file_type=Path(name).suffix.lower() or ".raw",
        file_role=file_role,  # type: ignore[arg-type]
        species=["human"],
        acquisition_mode="dda",
        ptm_type="phospho",
        validity_status=validity_status,  # type: ignore[arg-type]
        needs_review=needs_review,
        task_type="rt_prediction" if task_readiness_status else None,
        task_profile="Retention time prediction" if task_readiness_status else None,
        task_readiness_status=task_readiness_status,  # type: ignore[arg-type]
        task_readiness_reasons=["requires_downstream_search_export_for_rt_labels"] if task_readiness_status else [],
        missing_task_requirements=["retention_time_labels"] if task_readiness_status else [],
        label_source_status="requires_downstream_generation" if task_readiness_status else None,
        spectra_requirement_status="satisfied" if task_readiness_status else None,
        metadata_requirement_status="partial" if task_readiness_status else None,
        next_pipeline_steps=["search", "rt_export"] if task_readiness_status else [],
        ai_ready_target_schema="rt_train.parquet" if task_readiness_status else None,
        trust_score=trust_score,
        file_score=file_score,
        evidence_level="mixed",
        sdrf_match_status="matched",
        instrument_families=["orbitrap"],
        fragmentation_methods=["HCD"],
        lc_gradient_minutes=90.0,
    )


def _manifest() -> DatasetManifest:
    return DatasetManifest(
        run_id="run-001",
        request=DatasetRequest(max_projects=1, max_files=6, max_files_per_project=6),
        files=[
            _file("ready_high.raw", task_readiness_status="ready", trust_score=0.95, file_score=60),
            _file("weak_ready.raw", task_readiness_status="weak_ready", trust_score=0.90, file_score=58),
            _file("not_ready.raw", task_readiness_status="not_ready", trust_score=0.99, file_score=65),
            _file("usable_no_task.raw", task_readiness_status=None, trust_score=0.88, file_score=57),
            _file("result.mzid", file_role="search_result", task_readiness_status="weak_ready", trust_score=0.93),
            _file("review.raw", validity_status="needs_review", task_readiness_status="weak_ready", trust_score=0.97, needs_review=True),
        ],
        summary={"run_id": "run-001", "selected_files": 6},
    )


def test_pipeline_handoff_selects_task_ready_files():
    selected = select_handoff_files(_manifest(), selection="task_ready")

    assert [file.file_name for file in selected] == ["review.raw", "ready_high.raw", "result.mzid", "weak_ready.raw"]


def test_pipeline_handoff_auto_uses_task_ready_when_available():
    handoff = build_pipeline_handoff(_manifest(), selection="auto")

    assert handoff.resolved_selection == "task_ready"
    assert handoff.summary["selected_files"] == 4
    assert handoff.summary["ready_for_batch_parameters"] == 2
    assert handoff.summary["needs_review"] == 1
    assert handoff.summary["not_ready"] == 1
    by_name = {row.file_name: row for row in handoff.files}
    assert by_name["ready_high.raw"].handoff_status == "ready_for_batch_parameters"
    assert by_name["weak_ready.raw"].recommended_run_mode == "parameters"
    assert by_name["weak_ready.raw"].handoff_reasons == [
        "task_weak_ready_requires_downstream_label_generation"
    ]
    assert by_name["result.mzid"].handoff_status == "not_ready"
    assert "not_raw_or_peaklist:search_result" in by_name["result.mzid"].handoff_reasons
    assert by_name["review.raw"].handoff_status == "needs_review"


def test_pipeline_handoff_auto_falls_back_to_usable_without_task_readiness():
    manifest = DatasetManifest(
        run_id="run-002",
        request=DatasetRequest(max_projects=1, max_files=2),
        files=[
            _file("usable.raw", task_readiness_status=None, validity_status="valid", trust_score=0.8),
            _file("review.raw", task_readiness_status=None, validity_status="needs_review", trust_score=0.9),
        ],
    )

    handoff = build_pipeline_handoff(manifest, selection="auto")

    assert handoff.resolved_selection == "usable"
    assert [row.file_name for row in handoff.files] == ["usable.raw"]
    assert handoff.files[0].handoff_status == "ready_for_batch_parameters"


def test_pipeline_handoff_respects_max_files_and_order():
    selected = select_handoff_files(_manifest(), selection="all", max_files=2)

    assert [file.file_name for file in selected] == ["not_ready.raw", "review.raw"]


def test_pipeline_handoff_writes_json_csv_and_input_files(tmp_path: Path):
    output_dir = tmp_path / "handoff"

    paths = write_pipeline_handoff(_manifest(), output_dir, selection="task_ready")

    assert paths["pipeline_handoff_json"].exists()
    assert paths["pipeline_handoff_csv"].exists()
    assert paths["batch_parameters_inputs"].read_text(encoding="utf-8").splitlines() == [
        "ready_high.raw",
        "weak_ready.raw",
    ]
    assert paths["prepare_candidates"].read_text(encoding="utf-8").splitlines() == [
        "ready_high.raw",
        "weak_ready.raw",
    ]
    payload = json.loads(paths["pipeline_handoff_json"].read_text(encoding="utf-8"))
    assert payload["summary"]["ready_for_batch_parameters"] == 2
    with paths["pipeline_handoff_csv"].open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert "recommended_entrypoint" in rows[0]
    assert "ai_ready_target_schema" in rows[0]


def test_pipeline_handoff_cli_writes_outputs(tmp_path: Path):
    manifest_paths = write_dataset_manifest(_manifest(), tmp_path / "discovery")
    output_dir = tmp_path / "handoff"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "make-discovery-pipeline-handoff",
            "--manifest",
            str(manifest_paths["dataset_manifest_json"]),
            "--output-dir",
            str(output_dir),
            "--selection",
            "task_ready",
            "--max-files",
            "3",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["selected_files"] == 3
    assert (output_dir / "discovery_pipeline_handoff.json").exists()
    assert (output_dir / "batch_parameters_inputs.txt").exists()


def test_batch_parameters_request_uses_ready_handoff_rows_only():
    handoff = build_pipeline_handoff(_manifest(), selection="task_ready")

    request = batch_parameters_request(handoff, submitter="tester", jobs=2)

    assert request["run_mode"] == "parameters"
    assert request["submitter"] == "tester"
    assert request["jobs"] == 2
    assert request["inputs"] == ["ready_high.raw", "weak_ready.raw"]
    assert request["input_record_mode"] == "discovery_handoff_v1"
    assert [record["file_name"] for record in request["input_records"]] == ["ready_high.raw", "weak_ready.raw"]
    assert request["input_records"][0]["project_accession"] == "PXD000001"
    assert request["input_records"][0]["download_url"].endswith("ready_high.raw")
    assert request["input_records"][0]["task_readiness_status"] == "ready"


def test_handoff_preflight_blocks_when_no_ready_inputs(tmp_path: Path):
    manifest = DatasetManifest(
        run_id="run-empty",
        request=DatasetRequest(max_projects=1, max_files=2),
        files=[
            _file("result.mzid", file_role="search_result", validity_status="valid", task_readiness_status="weak_ready"),
            _file("review.raw", validity_status="needs_review", task_readiness_status="weak_ready", needs_review=True),
        ],
    )
    handoff = build_pipeline_handoff(manifest, selection="task_ready")

    report, request = build_handoff_preflight(handoff, output_root=tmp_path)

    assert request["inputs"] == []
    assert report.status == "blocked"
    assert report.ready_input_count == 0
    assert report.skipped_count == 2
    assert report.preflight["blocking_issues"] == ["No ready_for_batch_parameters files in handoff."]


def test_handoff_batch_preflight_writes_request_and_report(tmp_path: Path):
    handoff = build_pipeline_handoff(_manifest(), selection="task_ready")

    paths = write_handoff_batch_preflight(handoff, tmp_path / "preflight", jobs=3)

    request = json.loads(paths["batch_parameters_request"].read_text(encoding="utf-8"))
    report = json.loads(paths["batch_preflight_report"].read_text(encoding="utf-8"))
    assert request["inputs"] == ["ready_high.raw", "weak_ready.raw"]
    assert [record["project_accession"] for record in request["input_records"]] == ["PXD000001", "PXD000001"]
    assert request["jobs"] == 3
    assert report["status"] in {"ok", "warning"}
    assert report["summary"]["ready_input_count"] == 2
    assert paths["batch_parameters_inputs"].read_text(encoding="utf-8").splitlines() == [
        "ready_high.raw",
        "weak_ready.raw",
    ]
    with paths["skipped_files_csv"].open("r", encoding="utf-8", newline="") as handle:
        skipped = list(csv.DictReader(handle))
    assert {row["file_name"] for row in skipped} == {"review.raw", "result.mzid"}


def test_pipeline_handoff_round_trips_from_json(tmp_path: Path):
    handoff = build_pipeline_handoff(_manifest(), selection="task_ready")
    path = tmp_path / "handoff.json"
    path.write_text(handoff.model_dump_json(), encoding="utf-8")

    loaded = load_pipeline_handoff(path)

    assert loaded.run_id == "run-001"
    assert loaded.files[0].file_name == handoff.files[0].file_name


def test_validate_discovery_pipeline_handoff_cli_writes_preflight(tmp_path: Path):
    handoff_paths = write_pipeline_handoff(_manifest(), tmp_path / "handoff", selection="task_ready")
    output_dir = tmp_path / "preflight"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "validate-discovery-pipeline-handoff",
            "--handoff",
            str(handoff_paths["pipeline_handoff_json"]),
            "--output-dir",
            str(output_dir),
            "--jobs",
            "2",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] in {"ok", "warning"}
    assert payload["summary"]["ready_input_count"] == 2
    assert (output_dir / "batch_parameters_request.json").exists()
    assert (output_dir / "batch_preflight_report.json").exists()
