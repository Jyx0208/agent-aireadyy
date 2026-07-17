from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Mapping

import pytest

from agent.web.expert_review.consensus import ExpertJudgment, ExpertModelProfile
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


def test_job_manager_deletes_queued_and_terminal_job_artifacts_without_deleting_pool(tmp_path: Path) -> None:
    reset_jobs_for_tests()
    registry = ExpertPoolRegistry(tmp_path / "expert_review")
    record = registry.import_pool(
        {
            "schema_version": "discovery-judgment-pool-blinded/v2",
            "candidates": [{"candidate_id": "c1", "project_title": "Visible"}],
        },
        label="delete-jobs",
    )

    manager = ExpertJudgeJobManager(
        registry,
        resolve_profile=lambda _profile_id: {
            "api_key": "secret-key",
            "base_url": "https://example.com/v1",
            "model": "mock-model",
            "timeout": "30",
        },
        max_running=0,
    )

    for target_status in ("queued", "cancelled", "failed"):
        job = manager.start_job(pool_id=record["pool_id"], profile_id="default", workers=1)
        assert job["status"] == "queued"
        if target_status == "cancelled":
            cancelled = manager.cancel_job(job["job_id"])
            assert cancelled is not None
            assert cancelled["status"] == "cancelled"
        elif target_status == "failed":
            manager._finish(job["job_id"], status="failed", error="test failure")

        jobs_dir = registry.root / record["pool_id"] / "jobs"
        artifact_suffixes = (
            ".progress.jsonl",
            ".consensus.progress.jsonl",
            ".judgments.progress.jsonl",
            ".reviewed.json",
        )
        artifacts = [jobs_dir / f"{job['job_id']}{suffix}" for suffix in artifact_suffixes]
        for artifact in artifacts:
            artifact.write_text("artifact\n", encoding="utf-8")
        shared_reviewed = registry.root / record["pool_id"] / "pool.reviewed.json"
        shared_reviewed.write_text("{}\n", encoding="utf-8")

        deleted = manager.delete_job(job["job_id"])

        assert deleted == {
            "job_id": job["job_id"],
            "pool_id": record["pool_id"],
            "status": target_status,
        }
        assert manager.get_job(job["job_id"]) is None
        assert not (jobs_dir / f"{job['job_id']}.json").exists()
        assert all(not artifact.exists() for artifact in artifacts)
        assert shared_reviewed.exists()
        assert registry.get_pool(record["pool_id"]) is not None

        manager.max_running = 1
        manager._kick()
        assert manager.get_job(job["job_id"]) is None
        manager.max_running = 0


def test_job_manager_rejects_deleting_running_job(tmp_path: Path) -> None:
    reset_jobs_for_tests()
    registry = ExpertPoolRegistry(tmp_path / "expert_review")
    record = registry.import_pool(
        {
            "schema_version": "discovery-judgment-pool-blinded/v2",
            "candidates": [{"candidate_id": "c1", "project_title": "Visible"}],
        },
        label="running-delete",
    )
    release = threading.Event()

    def blocking_judge(_system_prompt: str, _user_prompt: str) -> dict[str, Any]:
        release.wait(timeout=5)
        return {
            "grade": 2,
            "reason": "ok",
            "supporting_evidence": ["e"],
            "constraint_conflicts": [],
        }

    manager = ExpertJudgeJobManager(
        registry,
        resolve_profile=lambda _profile_id: {
            "api_key": "secret-key",
            "base_url": "https://example.com/v1",
            "model": "mock-model",
            "timeout": "30",
        },
        judge_factory=lambda **_kwargs: blocking_judge,
        max_running=1,
    )
    job = manager.start_job(pool_id=record["pool_id"], profile_id="default", workers=1)
    assert job["status"] == "running"
    deadline = time.time() + 5
    detail = None
    while time.time() < deadline:
        detail = manager.get_job(job["job_id"], detail=True)
        if detail and detail.get("items", {}).get("c1") == "running":
            break
        time.sleep(0.01)
    assert detail is not None
    assert detail["items"]["c1"] == "running"

    with pytest.raises(ValueError, match="job_running_cancel_before_delete"):
        manager.delete_job(job["job_id"])

    manager.cancel_job(job["job_id"])
    release.set()
    deadline = time.time() + 5
    final = None
    while time.time() < deadline:
        final = manager.get_job(job["job_id"])
        if final and final["status"] in {"cancelled", "completed", "completed_with_errors", "failed"}:
            break
        time.sleep(0.01)
    assert final is not None
    assert final["status"] == "cancelled"


def test_job_manager_rejects_delete_path_traversal_without_touching_pool(tmp_path: Path) -> None:
    reset_jobs_for_tests()
    registry = ExpertPoolRegistry(tmp_path / "expert_review")
    record = registry.import_pool(
        {
            "schema_version": "discovery-judgment-pool-blinded/v2",
            "candidates": [{"candidate_id": "c1", "project_title": "Visible"}],
        },
        label="delete-path-safety",
    )
    shared_reviewed = registry.root / record["pool_id"] / "pool.reviewed.json"
    shared_reviewed.write_text('{"pool_id":"' + record["pool_id"] + '"}\n', encoding="utf-8")
    manager = ExpertJudgeJobManager(
        registry,
        resolve_profile=lambda _profile_id: {},
        max_running=0,
    )

    assert manager.delete_job(r"..\pool.reviewed") is None
    assert shared_reviewed.exists()


def test_consensus_job_selects_heterogeneous_panel_and_persists_model_only_results(tmp_path: Path) -> None:
    reset_jobs_for_tests()
    registry = ExpertPoolRegistry(tmp_path / "expert_review")
    record = registry.import_pool(
        {
            "schema_version": "discovery-judgment-pool-blinded/v2",
            "candidates": [
                {
                    "candidate_id": "c1",
                    "scenario_id": "s",
                    "variant_id": "v",
                    "project_title": "Visible",
                    "visible_prompt": "Find a suitable project",
                }
            ],
        },
        label="consensus-pool",
    )
    profiles = [
        {
            "id": "claude",
            "provider": "anthropic",
            "requested_model_id": "claude-opus-4-8",
            "resolved_model_id": "claude-opus-4-8",
            "model_family": "claude",
            "endpoint_identity": "anthropic:primary",
            "routing_profile_id": "claude",
            "identity_verification": "verified",
            "enabled": True,
        },
        {
            "id": "gemini",
            "provider": "google",
            "requested_model_id": "gemini-3-pro",
            "resolved_model_id": "gemini-3-pro",
            "model_family": "gemini",
            "endpoint_identity": "google:primary",
            "routing_profile_id": "gemini",
            "identity_verification": "verified",
            "enabled": True,
        },
        {
            "id": "grok",
            "provider": "xai",
            "requested_model_id": "grok-4.1",
            "resolved_model_id": "grok-4.1",
            "model_family": "grok",
            "endpoint_identity": "xai:primary",
            "routing_profile_id": "grok",
            "identity_verification": "verified",
            "enabled": True,
        },
    ]
    grades = {"claude": 3, "gemini": 1, "grok": 3}
    calls: list[str] = []

    def run(profile: ExpertModelProfile, candidate: Mapping[str, Any]) -> ExpertJudgment:
        calls.append(profile.profile_id)
        return ExpertJudgment(
            judgment_id=f"judgment-{profile.profile_id}",
            candidate_id=str(candidate["candidate_id"]),
            profile=profile,
            hard_gate_outcome="pass",
            final_grade=grades[profile.profile_id],
            confidence="high",
            investigation_status="completed",
            summary="result",
        )

    manager = ExpertJudgeJobManager(
        registry,
        resolve_profile=lambda _profile_id: {},
        list_profiles=lambda: profiles,
        expert_runner=run,
        max_running=1,
    )
    first = manager.start_consensus_job(
        pool_id=record["pool_id"],
        generator_identity={"model_family": "gpt", "identity_verification": "verified"},
        idempotency_key="same-consensus-request",
        output_language="zh-CN",
        scale_mode="exhaustive",
    )
    replay = manager.start_consensus_job(
        pool_id=record["pool_id"],
        generator_identity={"model_family": "gpt", "identity_verification": "verified"},
        idempotency_key="same-consensus-request",
    )
    assert replay["job_id"] == first["job_id"]

    deadline = time.time() + 10
    final = None
    while time.time() < deadline:
        final = manager.get_job(first["job_id"], detail=True)
        if final and final["status"] in {"completed", "failed", "completed_with_errors"}:
            break
        time.sleep(0.05)
    assert final is not None
    assert final["status"] == "completed"
    assert final["job_type"] == "model_expert_consensus"
    assert final["output_language"] == "zh-CN"
    assert final["scale_mode"] == "exhaustive"
    assert final["profile_ids"] == ["claude", "gemini", "grok"]
    assert sorted(calls) == ["claude", "gemini", "grok"]
    assert "api_key" not in str(final)

    reviewed = registry.load_pool_document(record["pool_id"], prefer_reviewed=True)
    assert reviewed is not None
    candidate = reviewed["candidates"][0]
    assert candidate["grade"] == 3
    assert "human_grades" not in candidate
    assert len(candidate["model_expert_judgments"]) == 3
    assert candidate["model_expert_consensus"]["status"] == "model_expert_consensus"
    assert reviewed["judgment_source"] == "model_expert_consensus"


def test_consensus_retry_reuses_checkpointed_primary_judgments(tmp_path: Path) -> None:
    reset_jobs_for_tests()
    registry = ExpertPoolRegistry(tmp_path / "expert_review")
    record = registry.import_pool(
        {
            "schema_version": "discovery-judgment-pool-blinded/v2",
            "candidates": [{"candidate_id": "c1", "project_title": "Visible"}],
        },
        label="retry-consensus",
    )
    profiles = [
        {
            "id": profile_id,
            "provider": provider,
            "requested_model_id": model,
            "resolved_model_id": model,
            "model_family": family,
            "endpoint_identity": f"{provider}:primary",
            "routing_profile_id": profile_id,
            "identity_verification": "verified",
            "enabled": True,
        }
        for profile_id, provider, family, model in (
            ("a", "anthropic", "claude", "claude-opus-4-8"),
            ("b", "google", "gemini", "gemini-3-pro"),
            ("c", "xai", "grok", "grok-4.1"),
        )
    ]
    calls = {"a": 0, "b": 0, "c": 0}

    def run(profile: ExpertModelProfile, candidate: Mapping[str, Any]) -> ExpertJudgment:
        calls[profile.profile_id] += 1
        if profile.profile_id == "c" and calls["c"] == 1:
            raise RuntimeError("temporary third expert failure")
        grade = 3 if profile.profile_id in {"a", "c"} else 1
        return ExpertJudgment(
            judgment_id=f"{candidate['candidate_id']}:{profile.profile_id}",
            candidate_id=str(candidate["candidate_id"]),
            profile=profile,
            hard_gate_outcome="pass",
            final_grade=grade,
            confidence="high",
            investigation_status="completed",
            summary="result",
        )

    manager = ExpertJudgeJobManager(
        registry,
        resolve_profile=lambda _profile_id: {},
        list_profiles=lambda: profiles,
        expert_runner=run,
        max_running=1,
    )
    job = manager.start_consensus_job(
        pool_id=record["pool_id"],
        generator_identity={"model_family": "gpt", "identity_verification": "verified"},
    )

    deadline = time.time() + 10
    while time.time() < deadline:
        failed = manager.get_job(job["job_id"], detail=True)
        if failed and failed["status"] == "completed_with_errors":
            break
        time.sleep(0.05)
    assert failed is not None
    assert failed["status"] == "completed_with_errors"

    manager.retry_failed(job["job_id"])
    deadline = time.time() + 10
    while time.time() < deadline:
        completed = manager.get_job(job["job_id"], detail=True)
        if completed and completed["status"] == "completed":
            break
        time.sleep(0.05)
    assert completed is not None
    assert completed["status"] == "completed"
    assert calls == {"a": 1, "b": 1, "c": 2}
