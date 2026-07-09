from __future__ import annotations

import csv
import json
from pathlib import Path

from typer.testing import CliRunner

from agent.cli import app
from agent.discovery.manifest import write_dataset_manifest
from agent.discovery.models import DatasetManifest, DatasetRequest, DiscoveredFile
from agent.discovery.task_build_plan import build_task_build_plan, write_task_build_plan


def _file(
    name: str,
    *,
    project_accession: str = "PXD000001",
    validity_status: str = "valid",
    file_role: str = "raw_acquisition",
    trust_score: float = 0.9,
    file_score: float = 50.0,
    needs_review: bool = False,
) -> DiscoveredFile:
    return DiscoveredFile(
        project_accession=project_accession,
        project_title="Human phosphoproteomics DDA",
        file_name=name,
        download_url=f"https://ftp.pride.ebi.ac.uk/{name}",
        file_type=Path(name).suffix.lower() or ".raw",
        file_role=file_role,  # type: ignore[arg-type]
        species=["human"],
        acquisition_mode="dda",
        ptm_type="phospho",
        validity_status=validity_status,  # type: ignore[arg-type]
        needs_review=needs_review,
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
        run_id="run-task-build",
        request=DatasetRequest(max_projects=2, max_files=4),
        files=[
            _file("good.raw", trust_score=0.95, file_score=60),
            _file("weak.raw", validity_status="weak_keep", trust_score=0.90, file_score=55),
            _file("result.mzid", file_role="search_result", trust_score=0.99, file_score=90),
            _file("review.raw", validity_status="needs_review", needs_review=True, trust_score=0.98, file_score=80),
        ],
        summary={"selected_files": 4},
    )


def test_task_build_plan_marks_rt_files_as_label_generation_candidates():
    plan = build_task_build_plan(_manifest(), "rt_prediction", selection="all")

    by_name = {row.file_name: row for row in plan.files}
    assert plan.task_type == "rt_prediction"
    assert plan.target_schema == "rt_train.parquet"
    assert by_name["good.raw"].candidate_tier == "label_generation_candidate"
    assert by_name["good.raw"].recommended_entrypoint == "batch_parameters"
    assert "retention_time_labels" in by_name["good.raw"].missing_task_requirements
    assert "requires_downstream_label_generation" in by_name["good.raw"].task_build_reasons
    assert by_name["weak.raw"].candidate_tier == "label_generation_candidate"
    assert "weak_keep_should_be_limited_until_outcome_validated" in by_name["weak.raw"].task_build_reasons
    assert by_name["result.mzid"].candidate_tier == "not_candidate"
    assert by_name["review.raw"].candidate_tier == "review_before_use"
    assert plan.summary["candidate_files"] == 2
    assert plan.summary["next_step"] == "run_batch_parameters_then_downstream_label_export"


def test_task_build_plan_marks_ptm_denovo_as_active_candidate():
    plan = build_task_build_plan(_manifest(), "ptm_denovo")

    assert plan.implementation_status == "active"
    assert plan.resolved_selection == "task_ready"
    assert plan.summary["next_step"] == "run_batch_parameters_then_downstream_label_export"
    by_name = {row.file_name: row for row in plan.files}
    assert by_name["good.raw"].candidate_tier == "label_generation_candidate"
    assert "requires_downstream_label_generation" in by_name["good.raw"].task_build_reasons
    assert by_name["good.raw"].task_readiness_status == "weak_ready"
    assert plan.target_schema == "ptm_denovo_train.parquet"


def test_task_build_plan_marks_chimeric_active_but_not_candidate_without_required_metadata():
    plan = build_task_build_plan(_manifest(), "chimeric_interpretation", selection="all")

    assert plan.implementation_status == "active"
    assert plan.resolved_selection == "all"
    assert plan.summary["next_step"] == "refine_discovery_or_review_candidates"
    by_name = {row.file_name: row for row in plan.files}
    assert by_name["good.raw"].candidate_tier == "not_candidate"
    assert "task_not_ready:not_ready" in by_name["good.raw"].task_build_reasons
    assert "isolation_window" in by_name["good.raw"].missing_task_requirements
    assert by_name["good.raw"].task_readiness_status == "not_ready"
    assert plan.target_schema == "chimeric_train.parquet"


def test_task_build_plan_respects_max_files_and_order():
    plan = build_task_build_plan(_manifest(), "rt_prediction", selection="all", max_files=2)

    assert [row.file_name for row in plan.files] == ["result.mzid", "review.raw"]


def test_write_task_build_plan_outputs_json_csv_schema_and_candidates(tmp_path: Path):
    paths = write_task_build_plan(_manifest(), tmp_path / "task_plan", "rt_prediction", selection="all")

    assert paths["task_build_plan"].exists()
    assert paths["task_build_files"].exists()
    assert paths["task_build_summary"].exists()
    assert paths["task_build_candidates"].read_text(encoding="utf-8").splitlines() == [
        "good.raw",
        "weak.raw",
    ]
    schema = json.loads(paths["ai_ready_schema_requirements"].read_text(encoding="utf-8"))
    assert schema["target_schema"] == "rt_train.parquet"
    with paths["task_build_files"].open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert "candidate_tier" in rows[0]
    assert "required_labels" in rows[0]


def test_task_build_plan_cli_writes_outputs(tmp_path: Path):
    manifest_paths = write_dataset_manifest(_manifest(), tmp_path / "discovery")
    output_dir = tmp_path / "task_build"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "make-discovery-task-build-plan",
            "--manifest",
            str(manifest_paths["dataset_manifest_json"]),
            "--output-dir",
            str(output_dir),
            "--task-type",
            "rt_prediction",
            "--selection",
            "all",
            "--max-files",
            "3",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["task_type"] == "rt_prediction"
    assert payload["summary"]["selected_files"] == 3
    assert (output_dir / "discovery_task_build_plan.json").exists()
    assert (output_dir / "discovery_task_build_files.csv").exists()
