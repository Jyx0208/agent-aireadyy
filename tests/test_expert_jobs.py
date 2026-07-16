from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Mapping

from agent.web.expert_review.jobs import ExpertJudgeJobManager, reset_jobs_for_tests
from agent.web.expert_review.openai_judge import OpenAISdkJudge, redact_text
from agent.web.expert_review.pool_registry import ExpertPoolRegistry


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = type("M", (), {"content": content})()


class _FakeCompletions:
    def create(self, **kwargs):  # noqa: ANN003
        content = '{"grade": 2, "reason": "ok", "supporting_evidence": ["e"], "constraint_conflicts": []}'
        return type("R", (), {"choices": [_FakeChoice(content)]})()


class _FakeChat:
    def __init__(self) -> None:
        self.completions = _FakeCompletions()


class _FakeClient:
    def __init__(self) -> None:
        self.chat = _FakeChat()


def test_redact_text_hides_keys() -> None:
    assert "sk-***" in redact_text("token sk-abcdefghi123456")
    redacted = redact_text("Authorization: Bearer secret-token-value")
    assert "secret-token-value" not in redacted
    assert "Bearer ***" in redacted


def test_openai_sdk_judge_parses_json() -> None:
    judge = OpenAISdkJudge(
        api_key="k",
        base_url="https://example.com/v1",
        model="m",
        client=_FakeClient(),
    )
    payload = judge("system", "user")
    assert payload["grade"] == 2


def test_job_manager_runs_with_mock_judge(tmp_path: Path) -> None:
    reset_jobs_for_tests()
    registry = ExpertPoolRegistry(tmp_path / "expert_review")
    pool = {
        "schema_version": "discovery-judgment-pool-blinded/v2",
        "candidates": [
            {
                "candidate_id": "c1",
                "scenario_id": "s",
                "variant_id": "v",
                "project_title": "t",
                "project_description": "d",
                "visible_prompt": "p",
            },
            {
                "candidate_id": "c2",
                "scenario_id": "s",
                "variant_id": "v",
                "project_title": "t2",
                "project_description": "d2",
                "visible_prompt": "p2",
                "human_grades": [{"grade": 3, "notes": "human", "reviewer_id": "r"}],
                "grade": 3,
            },
        ],
    }
    record = registry.import_pool(pool, label="job-pool")

    def resolve_profile(_profile_id: str) -> dict[str, str]:
        return {
            "api_key": "secret-key",
            "base_url": "https://example.com/v1",
            "model": "mock-model",
            "timeout": "30",
        }

    def factory(**kwargs):  # noqa: ANN003
        return OpenAISdkJudge(client=_FakeClient(), **{k: kwargs[k] for k in ("api_key", "base_url", "model", "timeout") if k in kwargs})

    manager = ExpertJudgeJobManager(registry, resolve_profile=resolve_profile, judge_factory=factory, max_running=2)
    job = manager.start_job(pool_id=record["pool_id"], profile_id="default", independent_model=True, workers=1)
    assert job["status"] in {"queued", "running", "completed"}
    assert "api_key" not in job

    deadline = time.time() + 10
    final = None
    while time.time() < deadline:
        final = manager.get_job(job["job_id"], detail=True)
        if final and final["status"] in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.05)
    assert final is not None
    assert final["status"] == "completed"
    assert final.get("progress", {}).get("done", 0) >= 1

    reviewed = registry.load_pool_document(record["pool_id"], prefer_reviewed=True)
    assert reviewed is not None
    by_id = {item["candidate_id"]: item for item in reviewed["candidates"]}
    assert by_id["c2"]["human_grades"]
    assert by_id["c2"]["grade"] == 3
    assert by_id["c1"].get("machine_reviews")

    listed = manager.list_jobs(pool_id=record["pool_id"])
    assert any(item["job_id"] == job["job_id"] for item in listed)
    for item in listed:
        assert "api_key" not in item
