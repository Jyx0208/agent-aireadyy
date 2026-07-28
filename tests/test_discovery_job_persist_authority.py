from __future__ import annotations

from pathlib import Path

import pytest

from agent.web import app as web_app


def test_persist_discovery_job_required_raises_and_does_not_claim_success(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    job = {
        "job_id": "discovery_job_test1",
        "status": "completed",
        "created_at": "t0",
        "started_at": "t1",
        "finished_at": "t2",
        "cancel_requested": False,
        "output_language": "en",
        "logs": [],
        "body": {},
        "record": {"status": "completed"},
        "error": None,
    }

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(web_app, "_write_json_atomic", boom)
    with pytest.raises(RuntimeError) as exc:
        web_app._persist_discovery_job(job, required=True)
    assert "discovery_job_persist_failed" in str(exc.value)


def test_persist_discovery_job_writes_atomic_authoritative_json(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    job = {
        "job_id": "discovery_job_test2",
        "status": "running",
        "created_at": "t0",
        "started_at": "t1",
        "finished_at": None,
        "cancel_requested": False,
        "output_language": "en",
        "logs": [{"ts": "t", "level": "info", "message": "hi"}],
        "body": {"goal": "x"},
        "record": None,
        "error": None,
    }
    web_app._persist_discovery_job(job, required=True)
    path = web_app._discovery_job_path("discovery_job_test2")
    assert path.exists()
    loaded = web_app._load_discovery_job("discovery_job_test2")
    assert loaded is not None
    assert loaded["status"] == "running"
    assert loaded["job_id"] == "discovery_job_test2"


def test_mark_interrupted_is_resumable_not_silent_success() -> None:
    job = {
        "job_id": "j1",
        "status": "running",
        "logs": [],
    }
    marked = web_app._mark_interrupted_discovery_job(job)
    assert marked["status"] == "interrupted"
    assert marked.get("resumable") is True
    assert marked["error"] == "discovery_job_interrupted_by_server_reload"
