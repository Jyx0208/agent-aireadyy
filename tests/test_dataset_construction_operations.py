from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from agent.dataset_construction.operations import execute_dataset_construction_job
from agent.dataset_construction import (
    build_dataset_release,
    ingest_existing_batch,
    plan_split_suite,
)
from agent.operations import api as operations_api
from agent.operations.runtime import get_operations_repository
from agent.web import app as web_app


def _batch(batch_dir: Path, *, project_count: int = 4) -> None:
    runs = []
    for index in range(1, project_count + 1):
        parquet = batch_dir / f"run-{index}" / "denovo.parquet"
        parquet.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            [
                {
                    "spectrum_id": f"scan={index}",
                    "peptide_sequence": f"PEPTIDE{index}",
                    "modified_sequence": f"PEPTIDE{index}",
                    "q_value": 0.005,
                }
            ]
        ).to_parquet(parquet, index=False)
        runs.append(
            {
                "project_accession": f"PXD{index:06d}",
                "source_file": f"sample-{index}.raw",
                "sample_id": f"sample-{index}",
                "lab_id": f"lab-{index}",
                "instrument_id": f"instrument-{index}",
                "species": f"organism-{index}",
                "acquisition_mode": f"acquisition-{index}",
                "task_files": {
                    "denovo": {"denovo_train_parquet": str(parquet)}
                },
            }
        )
    (batch_dir / "mini_e2e_batch_summary.json").write_text(
        json.dumps({"run_results": runs}),
        encoding="utf-8",
    )


def test_dataset_construction_job_runs_durably_and_publishes_progress_events(
    tmp_path: Path,
) -> None:
    batch_dir = tmp_path / "batch"
    _batch(batch_dir)
    repository = get_operations_repository()
    repository.create_job(
        job_id="dataset-job-1",
        job_type="dataset_construction",
        idempotency_key="dataset-idempotency-1",
        payload={
            "objective": "Construct fair de novo benchmark",
            "batch_dir": str(batch_dir),
            "output_dir": str(tmp_path / "release"),
            "release_id": "release-ops-1",
            "task_spec": {"task_type": "denovo", "version": 1},
            "ratios": [0.5, 0.25, 0.25],
            "seed": 19,
        },
    )

    result = execute_dataset_construction_job("dataset-job-1", repository=repository)

    snapshot = repository.get_job("dataset-job-1")
    assert snapshot is not None
    assert snapshot["status"] == "completed"
    assert snapshot["phase"] == "completed"
    assert snapshot["result"]["release_id"] == "release-ops-1"
    assert snapshot["result"]["observation_count"] == 4
    assert Path(snapshot["result"]["release_manifest_json"]).is_file()
    events = repository.events_after("dataset-job-1", after=0, limit=100)
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert {
        "dataset_ingestion_started",
        "dataset_contract_validated",
        "dataset_identity_ledger_built",
        "dataset_split_suite_planned",
        "dataset_leakage_audited",
        "dataset_release_completed",
    } <= {event["type"] for event in events}
    assert result["release_id"] == "release-ops-1"


def test_operations_api_submits_dataset_job_idempotently_and_uses_generic_queue(
    monkeypatch,
    tmp_path: Path,
) -> None:
    batch_dir = tmp_path / "batch"
    _batch(batch_dir)
    enqueued: list[str] = []
    monkeypatch.setattr(
        operations_api,
        "enqueue_operations_job",
        lambda job_id: enqueued.append(job_id) or "queue-dataset-1",
    )
    body = {
        "batch_dir": str(batch_dir),
        "output_dir": str(tmp_path / "release"),
        "release_id": "release-api-1",
        "task_spec": {"task_type": "denovo"},
        "ratios": [0.5, 0.25, 0.25],
        "seed": 23,
        "idempotency_key": "same-dataset-request",
    }

    with TestClient(web_app.app) as client:
        first = client.post("/api/ops/dataset-construction/jobs", json=body)
        second = client.post("/api/ops/dataset-construction/jobs", json=body)

    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    assert first.json()["job_id"] == second.json()["job_id"]
    assert first.json()["job_type"] == "dataset_construction"
    assert enqueued == [first.json()["job_id"]]


def test_operations_api_rejects_reusing_idempotency_key_for_different_release(
    monkeypatch,
    tmp_path: Path,
) -> None:
    batch_dir = tmp_path / "batch"
    _batch(batch_dir)
    monkeypatch.setattr(operations_api, "enqueue_operations_job", lambda _job_id: "q1")
    body = {
        "batch_dir": str(batch_dir),
        "output_dir": str(tmp_path / "release-a"),
        "release_id": "release-a",
        "task_spec": {"task_type": "denovo"},
        "idempotency_key": "fixed-key",
    }

    with TestClient(web_app.app) as client:
        first = client.post("/api/ops/dataset-construction/jobs", json=body)
        body["release_id"] = "release-b"
        body["output_dir"] = str(tmp_path / "release-b")
        conflicting = client.post("/api/ops/dataset-construction/jobs", json=body)

    assert first.status_code == 202
    assert conflicting.status_code == 409
    assert conflicting.json()["detail"] == "idempotency_key_conflict"


def test_dataset_release_artifact_endpoint_only_serves_registered_result_files(
    tmp_path: Path,
) -> None:
    batch_dir = tmp_path / "batch"
    _batch(batch_dir)
    repository = get_operations_repository()
    repository.create_job(
        job_id="dataset-artifact-job",
        job_type="dataset_construction",
        payload={
            "batch_dir": str(batch_dir),
            "output_dir": str(tmp_path / "release"),
            "release_id": "artifact-release",
            "task_spec": {"task_type": "denovo"},
            "ratios": [0.5, 0.25, 0.25],
            "seed": 4,
        },
    )
    execute_dataset_construction_job("dataset-artifact-job", repository=repository)

    with TestClient(web_app.app) as client:
        manifest = client.get(
            "/api/ops/jobs/dataset-artifact-job/artifacts/release_manifest_json"
        )
        unknown = client.get(
            "/api/ops/jobs/dataset-artifact-job/artifacts/not_registered"
        )

    assert manifest.status_code == 200
    assert manifest.json()["release_id"] == "artifact-release"
    assert unknown.status_code == 404


def test_dataset_job_recovers_release_committed_before_terminal_job_state(
    tmp_path: Path,
) -> None:
    batch_dir = tmp_path / "batch"
    output_dir = tmp_path / "release"
    _batch(batch_dir)
    repository = get_operations_repository()
    catalog = ingest_existing_batch(batch_dir)
    suite = plan_split_suite(catalog, ratios=(0.5, 0.25, 0.25), seed=31)
    build_dataset_release(
        catalog,
        suite,
        output_dir=output_dir,
        release_id="recover-release",
        task_spec={"task_type": "denovo"},
        engine=repository.database.engine,
    )
    repository.create_job(
        job_id="dataset-recovery-job",
        job_type="dataset_construction",
        payload={
            "batch_dir": str(batch_dir),
            "output_dir": str(output_dir),
            "release_id": "recover-release",
            "task_spec": {"task_type": "denovo"},
            "ratios": [0.5, 0.25, 0.25],
            "seed": 31,
        },
    )
    repository.transition_job(
        "dataset-recovery-job",
        "failed",
        phase="failed",
        reason="simulated process loss after release registration",
        resumable=True,
    )

    result = execute_dataset_construction_job(
        "dataset-recovery-job",
        repository=repository,
    )

    assert result["release_id"] == "recover-release"
    assert repository.get_job("dataset-recovery-job")["status"] == "completed"
    event_types = {
        event["type"]
        for event in repository.events_after(
            "dataset-recovery-job",
            after=0,
            limit=100,
        )
    }
    assert "dataset_release_recovered" in event_types
