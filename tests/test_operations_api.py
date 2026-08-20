from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agent.operations.runtime import get_operations_repository
from agent.operations import api as operations_api
from agent.web import app as web_app


def _seed_terminal_job(job_id: str = "api-job") -> None:
    repository = get_operations_repository()
    repository.create_job(
        job_id=job_id,
        payload={
            "objective": "检索人类免疫肽组学数据",
            "repository": "pride",
            "species": ["Homo sapiens"],
        },
        terms=[
            {"term": "immunopeptidomics", "role": "primary_theme"},
            {"term": "HLA ligandome", "role": "theme_synonym"},
        ],
    )
    repository.transition_job(
        job_id,
        "searching",
        phase="searching",
        reason="worker claimed",
        event_type="job_started",
    )
    repository.append_event(
        job_id,
        event_type="repository_term_task_completed",
        phase="searching",
        message="核心词已读到末尾。",
        payload={
            "term": "immunopeptidomics",
            "term_index": 1,
            "chunks_completed": 3,
            "raw_result_count": 305,
            "new_candidate_count": 305,
            "candidate_count": 305,
        },
    )
    repository.transition_job(
        job_id,
        "finalizing",
        phase="finalizing",
        reason="freeze",
        event_type="job_finalization_started",
    )
    repository.transition_job(
        job_id,
        "completed",
        phase="completed",
        reason="done",
        event_type="job_completed",
    )


def test_operations_snapshot_terms_events_and_history(
    monkeypatch,
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    monkeypatch.setattr(web_app, "_runs_dir", runs)
    _seed_terminal_job()
    with TestClient(web_app.app) as client:
        snapshot = client.get("/api/ops/jobs/api-job")
        assert snapshot.status_code == 200
        payload = snapshot.json()
        assert payload["status"] == "completed"
        assert payload["progress"]["candidate_count"] == 305
        assert len(snapshot.content) < 100_000

        terms = client.get("/api/ops/jobs/api-job/terms").json()
        assert terms["items"][0]["page_count"] == 3
        assert terms["items"][0]["raw_count"] == 305

        events = client.get(
            "/api/ops/jobs/api-job/events/page",
            params={"after": 0, "limit": 2},
        ).json()
        assert len(events["items"]) == 2
        assert events["has_more"] is True
        assert events["items"][0]["sequence"] < events["items"][1]["sequence"]

        history = client.get(
            "/api/ops/history",
            params={"page": 1, "page_size": 25, "query": "免疫肽"},
        ).json()
        assert history["total"] == 1
        assert history["items"][0]["source_id"] == "api-job"


def test_operations_sse_replays_sequence_and_closes_on_terminal(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path / "runs")
    _seed_terminal_job("sse-job")
    with TestClient(web_app.app) as client:
        with client.stream(
            "GET",
            "/api/ops/jobs/sse-job/events",
            params={"after": 0},
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith(
                "text/event-stream"
            )
            body = "\n".join(response.iter_lines())
        assert "event: connected" in body
        assert "event: job-event" in body
        assert "event: snapshot" in body
        assert "event: complete" in body
        assert "id: 1" in body


def test_operations_cancel_queued_job_is_immediately_terminal(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path / "runs")
    repository = get_operations_repository()
    repository.create_job(
        job_id="queued-job",
        payload={"objective": "queued"},
    )
    repository.append_event(
        "queued-job",
        event_type="job_enqueued",
        actor="queue",
        payload={"queue_task_id": "queue-task-123"},
    )
    revoked: list[str] = []
    monkeypatch.setattr(
        operations_api,
        "revoke_discovery_job",
        lambda task_id: revoked.append(task_id) or True,
    )
    with TestClient(web_app.app) as client:
        cancelled = client.post("/api/ops/jobs/queued-job/cancel")
        assert cancelled.status_code == 200
        payload = cancelled.json()
        assert payload["status"] == "cancelled"
        assert payload["cancel_requested"] is True
        assert payload["resumable"] is True
        assert revoked == ["queue-task-123"]
