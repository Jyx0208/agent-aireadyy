"""Contracts for a future Project orchestration product wave.

These tests are intentionally outside the M1 discovery L2 gate because the
Project core, API, durable store, and workers do not exist in this Git tree.
They remain executable red contracts via pytest -m future_project; no
skip, xfail, or compatibility stub is used to make them pass.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent.web.app as web_app
from test_web_discovery import _manifest


pytestmark = pytest.mark.future_project


def test_web_worker_notifies_project_execution_coordinator(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr(
        web_app,
        "discover_pride_dataset",
        lambda request, memory=None, **_kwargs: _manifest(request),
    )
    observed: list[tuple[str, dict]] = []

    class FakeCoordinator:
        def observe_discovery_completion(self, job_id, record):
            observed.append((job_id, record))
            return None

        def observe_job_failure(self, job_id, error):
            raise AssertionError((job_id, error))

    monkeypatch.setattr(web_app, "_project_execution_coordinator", lambda: FakeCoordinator())

    created = asyncio.run(
        web_app.start_discovery_job(
            {"max_projects": 1, "max_files": 1, "grill_confirmed": True}
        )
    )
    final = _wait_discovery_job(created["job_id"])

    assert final["status"] == "blocked"
    assert len(observed) == 1
    assert observed[0][0] == created["job_id"]
    assert observed[0][1]["file_count"] == 1


def test_project_api_persists_goal_jobs_and_events(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)

    created = asyncio.run(
        web_app.create_project_record(
            {
                "name": "Human DDA RT",
                "objective": "Build a human DDA retention-time dataset",
                "goal": {"task_type": "rt_prediction"},
            }
        )
    )
    project_id = created["project_id"]

    listed = asyncio.run(web_app.list_project_records())
    detail = asyncio.run(web_app.get_project_record(project_id))

    assert [item["project_id"] for item in listed["projects"]] == [project_id]
    assert detail["goal"]["objective"] == "Build a human DDA retention-time dataset"
    assert detail["goal"]["task_type"] == "rt_prediction"
    assert detail["plans"] == []
    assert detail["stage_runs"] == []
    assert detail["jobs"] == []
    assert detail["approvals"] == []
    assert detail["memory"] == []
    assert detail["release_candidates"] == []
    assert detail["dataset_releases"] == []
    assert [event["event_type"] for event in detail["events"]] == ["project_created"]


def test_project_plan_api_uses_saved_llm_config_and_starts_queued_discovery(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    web_app._llm_config_store().save(
        {
            "api_key": "saved-manager-key",
            "base_url": "https://manager.example.test/v1",
            "model": "manager-model",
            "timeout": "1200",
        }
    )
    project = asyncio.run(
        web_app.create_project_record(
            {"name": "Managed project", "objective": "Find human DDA datasets"}
        )
    )
    captured: dict[str, object] = {}
    started: list[str] = []
    monkeypatch.setattr(web_app, "_start_discovery_job_thread", started.append)

    class FakeManagerService:
        def plan_project(self, project_id, *, llm_config, auto_start):
            captured.update(
                project_id=project_id,
                llm_config=llm_config,
                auto_start=auto_start,
            )
            job = web_app._project_store().enqueue_job(
                project_id=project_id,
                job_type="discovery",
                idempotency_key="manager-api-test",
                payload={"project_id": project_id, "prompt": "Find human DDA", "runtime": "openai_agents"},
            )
            return SimpleNamespace(
                project_id=project_id,
                manager_run_id="manager_api_test",
                status="active",
                queued_job_ids=[job.job_id],
            )

    monkeypatch.setattr(web_app, "_project_manager_service", lambda: FakeManagerService())

    response = asyncio.run(web_app.plan_project_record(project["project_id"], {"auto_start": True}))

    assert response["status"] == "active"
    assert started == response["queued_job_ids"]
    assert captured["project_id"] == project["project_id"]
    assert captured["llm_config"]["model"] == "manager-model"  # type: ignore[index]
    assert "saved-manager-key" not in json.dumps(response)


def test_manager_replan_worker_creates_revision_and_starts_new_discovery(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    project = asyncio.run(
        web_app.create_project_record(
            {"name": "Replan project", "objective": "Find human DDA datasets"}
        )
    )
    store = web_app._project_store()
    replan_job = store.enqueue_job(
        project_id=project["project_id"],
        job_type="manager_replan",
        idempotency_key="replan-worker-test",
        payload={
            "source_plan_id": "plan_1",
            "source_plan_revision": 1,
            "source_stage_run_id": "stage_run_1",
            "source_stage_id": "discover",
            "observation": {"issue_codes": ["no_selected_files"]},
            "evidence_refs": ["artifact_1"],
            "auto_start": True,
        },
    )
    captured: dict[str, object] = {}
    started: list[str] = []
    monkeypatch.setattr(
        web_app,
        "_build_llm_config",
        lambda _config: (
            {"api_key": "transient", "base_url": "https://example.test", "model": "test"},
            None,
        ),
    )
    monkeypatch.setattr(web_app, "_start_project_job_thread", started.append)

    class FakeManagerService:
        def plan_project(self, project_id, **kwargs):
            captured.update(project_id=project_id, **kwargs)
            discovery = store.enqueue_job(
                project_id=project_id,
                job_type="discovery",
                idempotency_key="replanned-discovery",
                payload={"project_id": project_id, "prompt": "Broaden discovery", "runtime": "workflow"},
            )
            return SimpleNamespace(
                project_id=project_id,
                manager_run_id="manager_revision_test",
                status="active",
                queued_job_ids=[discovery.job_id],
            )

    monkeypatch.setattr(web_app, "_project_manager_service", lambda: FakeManagerService())

    web_app._run_project_manager_job(replan_job.job_id)

    completed = store.get_job(replan_job.job_id)
    assert completed is not None and completed.status == "completed"
    assert captured["revision_context"]["observation"]["issue_codes"] == ["no_selected_files"]  # type: ignore[index]
    started_types = {
        store.get_job(started_job_id).job_type  # type: ignore[union-attr]
        for started_job_id in started
    }
    assert started_types == {"discovery", "run_reflection"}


def test_startup_restarts_all_queued_supported_project_jobs(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    project = asyncio.run(
        web_app.create_project_record(
            {"name": "Restart project", "objective": "Resume queued work"}
        )
    )
    store = web_app._project_store()
    discovery = store.enqueue_job(
        project_id=project["project_id"],
        job_type="discovery",
        idempotency_key="startup-discovery",
        payload={"project_id": project["project_id"], "prompt": "Find data"},
    )
    replan = store.enqueue_job(
        project_id=project["project_id"],
        job_type="manager_replan",
        idempotency_key="startup-replan",
        payload={"project_id": project["project_id"]},
    )
    candidate_review = store.enqueue_job(
        project_id=project["project_id"],
        job_type="candidate_review",
        idempotency_key="startup-candidate-review",
        payload={"project_id": project["project_id"]},
    )
    build = store.enqueue_job(
        project_id=project["project_id"],
        job_type="build",
        idempotency_key="startup-build",
        payload={"project_id": project["project_id"]},
    )
    build_execution = store.enqueue_job(
        project_id=project["project_id"],
        job_type="build_execution",
        idempotency_key="startup-build-execution",
        payload={"project_id": project["project_id"]},
    )
    reflection = store.enqueue_job(
        project_id=project["project_id"],
        job_type="reflection",
        idempotency_key="startup-reflection",
        payload={"project_id": project["project_id"]},
    )
    run_reflection = store.enqueue_job(
        project_id=project["project_id"],
        job_type="run_reflection",
        idempotency_key="startup-run-reflection",
        payload={"project_id": project["project_id"]},
    )
    release_candidate = store.enqueue_job(
        project_id=project["project_id"],
        job_type="release_candidate",
        idempotency_key="startup-release-candidate",
        payload={"project_id": project["project_id"]},
    )
    started: list[str] = []
    monkeypatch.setattr(web_app, "_start_project_job_thread", started.append)

    result = web_app._start_queued_project_jobs()

    supported = {
        discovery.job_id,
        replan.job_id,
        candidate_review.job_id,
        build.job_id,
        build_execution.job_id,
        reflection.job_id,
        run_reflection.job_id,
        release_candidate.job_id,
    }
    assert set(result) == supported
    assert set(started) == supported


def test_project_build_execution_worker_runs_batch_and_data_scientist_loop(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    project = asyncio.run(
        web_app.create_project_record(
            {"name": "Build execution", "objective": "Build an RT dataset"}
        )
    )
    store = web_app._project_store()
    job = store.enqueue_job(
        project_id=project["project_id"],
        job_type="build_execution",
        idempotency_key="approved-build-execution",
        payload={
            "project_id": project["project_id"],
            "plan_id": "plan_fixture",
            "stage_id": "build",
            "task_type": "rt_prediction",
            "accepted_files": [
                {
                    "file_name": "sample.raw",
                    "project_accession": "PXD_FIXTURE",
                    "download_url": "https://example.test/sample.raw",
                }
            ],
        },
    )
    stage_run = SimpleNamespace(stage_run_id="stage_run_build_execution")
    captured: dict[str, object] = {}

    class FakeBuildService:
        def create_execution_stage_run(self, job_id):
            captured["created_for_job"] = job_id
            return stage_run

        def finalize_execution(self, stage_run_id, **kwargs):
            captured.update(stage_run_id=stage_run_id, finalize=kwargs)
            return SimpleNamespace(
                stage_run_id=stage_run_id,
                status="completed",
                ai_ready_dataset_created=True,
                output_files=kwargs["output_files"],
            )

    def fake_batch(batch_id):
        with web_app._batches_lock:
            batch = web_app._batches[batch_id]
            batch["status"] = "completed"
            for item in batch["items"]:
                item["status"] = "completed"
                item_dir = Path(item["output_dir"])
                item_dir.mkdir(parents=True, exist_ok=True)
                (item_dir / "sample_ai_ready.parquet").write_bytes(b"PAR1batch")
            web_app._write_batch_manifest(batch)

    def fake_loop(**kwargs):
        captured["loop"] = kwargs
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        parquet = output_dir / "rt_train.parquet"
        parquet.write_bytes(b"PAR1fixture")
        return SimpleNamespace(
            status="completed",
            blockers=[],
            warnings=[],
            files={"rt_train_parquet": str(parquet)},
            recipe_status="completed",
            model_loop_status="completed",
            guidance_alignment_status="aligned",
        )

    monkeypatch.setattr(web_app, "_project_store", lambda: store)
    monkeypatch.setattr(web_app, "_project_build_service", lambda: FakeBuildService())
    monkeypatch.setattr(
        web_app,
        "_build_llm_config",
        lambda _config: ({"api_key": "transient", "model": "test"}, None),
    )
    monkeypatch.setattr(web_app, "_run_parameter_batch", fake_batch)
    monkeypatch.setattr(web_app, "run_data_scientist_agent_loop", fake_loop)

    web_app._run_project_build_execution_job(job.job_id)

    completed = store.get_job(job.job_id)
    assert completed is not None and completed.status == "completed"
    assert captured["created_for_job"] == job.job_id
    assert captured["loop"]["split_strategy"] == "project_disjoint"  # type: ignore[index]
    assert captured["finalize"]["pipeline_status"] == "completed"  # type: ignore[index]
    assert any(key.startswith("batch_ai_ready_parquet_") for key in captured["finalize"]["output_files"])  # type: ignore[index]


def test_project_build_execution_preserves_partial_outputs_before_recovery(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    project = asyncio.run(
        web_app.create_project_record(
            {"name": "Partial build", "objective": "Preserve failed build evidence"}
        )
    )
    store = web_app._project_store()
    job = store.enqueue_job(
        project_id=project["project_id"],
        job_type="build_execution",
        idempotency_key="partial-build-execution",
        payload={
            "project_id": project["project_id"],
            "plan_id": "plan_fixture",
            "stage_id": "build",
            "task_type": "rt_prediction",
            "accepted_files": [
                {"file_name": "sample.raw", "project_accession": "PXD_FIXTURE"}
            ],
        },
    )
    stage_run = SimpleNamespace(stage_run_id="stage_run_partial_build")
    captured: dict[str, object] = {}

    class FakeBuildService:
        def create_execution_stage_run(self, _job_id):
            return stage_run

        def finalize_execution(self, stage_run_id, **kwargs):
            captured.update(stage_run_id=stage_run_id, finalize=kwargs)
            return SimpleNamespace(
                stage_run_id=stage_run_id,
                status="blocked",
                blockers=list(kwargs["blockers"]),
                output_files=kwargs["output_files"],
            )

    def fake_batch(batch_id):
        with web_app._batches_lock:
            batch = web_app._batches[batch_id]
            batch["status"] = "completed"
            audit = Path(batch["output_dir"]) / "partial_audit.json"
            audit.write_text('{"status":"partial"}', encoding="utf-8")
            web_app._write_batch_manifest(batch)

    monkeypatch.setattr(web_app, "_project_store", lambda: store)
    monkeypatch.setattr(web_app, "_project_build_service", lambda: FakeBuildService())
    monkeypatch.setattr(
        web_app,
        "_build_llm_config",
        lambda _config: ({"api_key": "transient", "model": "test"}, None),
    )
    monkeypatch.setattr(web_app, "_run_parameter_batch", fake_batch)
    monkeypatch.setattr(
        web_app,
        "run_data_scientist_agent_loop",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("model loop failed")),
    )

    web_app._run_project_build_execution_job(job.job_id)

    completed = store.get_job(job.job_id)
    assert completed is not None and completed.status == "completed"
    assert captured["finalize"]["pipeline_status"] == "failed"  # type: ignore[index]
    assert "build_execution_exception" in captured["finalize"]["blockers"]  # type: ignore[index]
    partial_paths = captured["finalize"]["output_files"].values()  # type: ignore[index]
    assert any(str(path).endswith("partial_audit.json") for path in partial_paths)


def test_project_specialist_worker_completes_candidate_review_job(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    project = asyncio.run(
        web_app.create_project_record(
            {"name": "Specialist project", "objective": "Review selected candidates"}
        )
    )
    store = web_app._project_store()
    job = store.enqueue_job(
        project_id=project["project_id"],
        job_type="candidate_review",
        idempotency_key="candidate-review-worker",
        payload={"stage_run_id": "stage_run_review"},
    )
    monkeypatch.setattr(web_app, "_project_store", lambda: store)
    monkeypatch.setattr(
        store,
        "get_stage_run_by_job",
        lambda _job_id: SimpleNamespace(stage_run_id="stage_run_review"),
    )
    monkeypatch.setattr(
        web_app,
        "_build_llm_config",
        lambda _config: ({"api_key": "transient", "model": "test-model"}, None),
    )
    captured: dict[str, object] = {}

    class FakeSpecialistService:
        def run_candidate_review(self, stage_run_id, *, llm_config):
            captured.update(stage_run_id=stage_run_id, llm_config=llm_config)
            return SimpleNamespace(
                stage_run_id=stage_run_id,
                status="completed",
                output={"status": "accepted"},
            )

    monkeypatch.setattr(web_app, "_project_specialist_service", lambda: FakeSpecialistService())

    web_app._run_project_specialist_job(job.job_id)

    completed = store.get_job(job.job_id)
    assert completed is not None and completed.status == "completed"
    assert completed.output["status"] == "completed"
    assert captured["stage_run_id"] == "stage_run_review"
    assert captured["llm_config"]["model"] == "test-model"  # type: ignore[index]


def test_project_approval_can_be_decided_through_api(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    created = asyncio.run(
        web_app.create_project_record(
            {"name": "Approval project", "objective": "Build a release candidate"}
        )
    )
    store = web_app._project_store()
    job = store.enqueue_job(
        project_id=created["project_id"],
        job_type="release",
        idempotency_key="release:v1",
    )
    gate = store.create_approval_gate(
        project_id=created["project_id"],
        job_id=job.job_id,
        action_type="publish_release",
        summary="Publish release candidate",
        risk="scientific_signoff",
    )

    decided = asyncio.run(
        web_app.decide_project_approval(
            created["project_id"], gate.gate_id, {"decision": "approve", "reason": "reviewed"}
        )
    )
    detail = asyncio.run(web_app.get_project_record(created["project_id"]))

    assert decided["status"] == "approved"
    assert "sdk_state_json" not in decided
    assert "sdk_state_json" not in detail["approvals"][0]
    assert detail["approvals"][0]["decision_reason"] == "reviewed"
    assert detail["jobs"][0]["status"] == "queued"


def test_project_approval_rejection_is_kept_as_human_decision(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    created = asyncio.run(
        web_app.create_project_record(
            {"name": "Rejected build", "objective": "Build a reviewed dataset"}
        )
    )
    store = web_app._project_store()
    job = store.enqueue_job(
        project_id=created["project_id"],
        job_type="build_execution",
        idempotency_key="rejected-build-execution",
        payload={"project_id": created["project_id"]},
    )
    gate = store.create_approval_gate(
        project_id=created["project_id"],
        job_id=job.job_id,
        action_type="execute_full_dataset_build",
        summary="Run the Full Docker build",
        risk="high_cost_external_execution",
    )

    decided = asyncio.run(
        web_app.decide_project_approval(
            created["project_id"],
            gate.gate_id,
            {"decision": "reject", "reason": "Storage is not available this week."},
        )
    )
    detail = asyncio.run(web_app.get_project_record(created["project_id"]))

    assert decided["status"] == "rejected"
    assert detail["jobs"][0]["status"] == "approval_denied"
    assert len(detail["memory"]) == 1
    assert detail["memory"][0]["kind"] == "human_decision"
    assert detail["memory"][0]["statement"] == "Storage is not available this week."


def test_project_artifact_download_requires_matching_checksum(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    created = asyncio.run(
        web_app.create_project_record(
            {"name": "Artifact project", "objective": "Keep traceable evidence"}
        )
    )
    path = tmp_path / "evidence.json"
    path.write_text('{"status":"validated"}', encoding="utf-8")
    store = web_app._project_store()
    artifact = store.register_artifact(
        project_id=created["project_id"],
        artifact_type="test_evidence",
        path=path,
        sha256=web_app.sha256_file(path),
    )

    response = asyncio.run(
        web_app.download_project_artifact(created["project_id"], artifact.artifact_id)
    )
    assert Path(response.path) == path

    path.write_text('{"status":"tampered"}', encoding="utf-8")
    rejected = asyncio.run(
        web_app.download_project_artifact(created["project_id"], artifact.artifact_id)
    )
    assert rejected == {
        "error": "Project artifact is missing or failed checksum verification."
    }


def test_project_release_verify_api_returns_replay_result(monkeypatch):
    release = SimpleNamespace(release_id="release_1", project_id="project_1")
    store = SimpleNamespace(list_dataset_releases=lambda project_id: [release])
    monkeypatch.setattr(web_app, "_project_store", lambda: store)

    class FakeReleaseService:
        def verify_release(self, value):
            assert value is release
            return {"status": "passed", "release_id": value.release_id, "artifact_count": 7}

    monkeypatch.setattr(web_app, "_project_release_service", lambda: FakeReleaseService())

    result = asyncio.run(web_app.verify_project_release("project_1", "release_1"))

    assert result == {"status": "passed", "release_id": "release_1", "artifact_count": 7}


def test_discovery_job_is_durable_idempotent_and_does_not_persist_api_key(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    started: list[str] = []
    monkeypatch.setattr(web_app, "_start_discovery_job_thread", started.append)
    project = asyncio.run(
        web_app.create_project_record(
            {"name": "Durable project", "objective": "Find human DDA projects"}
        )
    )
    body = {
        "grill_confirmed": True,
        "project_id": project["project_id"],
        "idempotency_key": "discovery:plan-revision-1",
        "prompt": "Find human DDA projects",
        "max_projects": 2,
        "llm_config": {
            "api_key": "must-not-be-persisted",
            "base_url": "https://example.test/v1",
            "model": "test-model",
        },
    }

    first = asyncio.run(web_app.start_discovery_job(body))
    duplicate = asyncio.run(web_app.start_discovery_job(body))
    durable_job = web_app._project_store().get_job(first["job_id"])

    assert duplicate["job_id"] == first["job_id"]
    assert first["project_id"] == project["project_id"]
    assert started == [first["job_id"]]
    assert durable_job is not None
    assert durable_job.status == "queued"
    assert durable_job.payload["llm_config"]["model"] == "test-model"
    assert "must-not-be-persisted" not in json.dumps(durable_job.payload)


def test_interrupted_durable_discovery_job_can_be_resumed(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    started: list[str] = []
    monkeypatch.setattr(web_app, "_start_discovery_job_thread", started.append)
    created = asyncio.run(
        web_app.start_discovery_job(
            {
                "prompt": "Find human DDA projects",
                "idempotency_key": "resume-test",
                "grill_confirmed": True,
            }
        )
    )
    job_id = created["job_id"]
    store = web_app._project_store()
    store.claim_job(job_id, worker_id="worker_before_restart")
    store.interrupt_running_jobs(reason="server_restart")
    with web_app._discovery_jobs_lock:
        web_app._discovery_jobs.pop(job_id, None)

    recovered = asyncio.run(web_app.get_discovery_job(job_id))
    resumed = asyncio.run(web_app.resume_discovery_job(job_id))

    assert recovered["status"] == "interrupted"
    assert resumed["status"] == "queued"
    assert store.get_job(job_id).status == "queued"  # type: ignore[union-attr]
    assert started == [job_id, job_id]
