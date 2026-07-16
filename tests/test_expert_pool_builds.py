from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from agent.web.expert_review.pool_builds import ExpertPoolBuildManager
from agent.web.expert_review.pool_registry import ExpertPoolRegistry


def _wait(
    manager: ExpertPoolBuildManager,
    build_id: str,
    expected: set[str],
    *,
    field: str = "status",
) -> dict[str, Any]:
    deadline = time.time() + 5
    while time.time() < deadline:
        build = manager.get_build(build_id)
        assert build is not None
        if build[field] in expected:
            return build
        time.sleep(0.01)
    raise AssertionError(f"{field} did not reach {expected}: {manager.get_build(build_id)}")


def _completed_discovery(job_id: str) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "status": "completed",
        "record": {
            "discovery_id": "disc-1",
            "projects": [
                {
                    "project_accession": "PXD123456",
                    "project_title": "Candidate project",
                    "project_description": "Visible candidate evidence",
                    "generator": "candidate-generator-model",
                    "runtime": "private-runtime",
                    "api_key": "discovery-secret",
                },
                {
                    "project_accession": "pxd123456",
                    "project_title": "Candidate project",
                    "project_description": "Visible candidate evidence with a longer description",
                },
            ],
            "files": [
                {
                    "project_accession": "PXD123456",
                    "file_role": "raw_acquisition",
                    "file_type": "raw",
                    "task_readiness_status": "ready",
                },
                {
                    "project_accession": "PXD123456",
                    "file_role": "search_result",
                    "file_type": "mzidentml",
                    "task_readiness_status": "ready",
                },
            ],
        },
    }


def test_pool_build_registers_once_and_idempotent_replay_hides_secrets(tmp_path: Path) -> None:
    registry = ExpertPoolRegistry(tmp_path / "expert_review")
    starts: list[dict[str, Any]] = []

    def start_discovery(payload: dict[str, Any]) -> dict[str, Any]:
        starts.append(payload)
        return {"job_id": "disc-job", "status": "queued"}

    manager = ExpertPoolBuildManager(
        registry,
        start_discovery=start_discovery,
        get_discovery=lambda job_id: _completed_discovery(job_id),
        cancel_discovery=lambda _job_id: None,
    )
    first = manager.start_build(
        discovery_request={
            "prompt": "Find human DDA proteomics projects",
            "repository": "pride",
            "species": "Homo sapiens",
            "runtime": "private-runtime",
            "llm_config": {"api_key": "secret-key"},
        },
        label="pilot",
        idempotency_key="same-request",
    )
    replay = manager.start_build(
        discovery_request={"prompt": "Find human DDA proteomics projects", "llm_config": {"api_key": "different-secret"}},
        label="ignored",
        idempotency_key="same-request",
    )
    assert replay["build_id"] == first["build_id"]
    build = _wait(manager, first["build_id"], {"pool_ready"})
    assert len(starts) == 1
    assert starts[0]["llm_config"]["api_key"] == "secret-key"
    assert build["pool_id"]
    assert "api_key" not in json.dumps(build)
    assert registry.get_pool(build["pool_id"]) is not None
    pool_dir = registry.root / str(build["pool_id"])
    pool = json.loads((pool_dir / "pool.blinded.json").read_text(encoding="utf-8"))
    private_key = json.loads((pool_dir / "private" / "judgment.key.json").read_text(encoding="utf-8"))
    assert len(pool["candidates"]) == 1
    candidate = pool["candidates"][0]
    assert candidate["visible_prompt"] == "Find human DDA proteomics projects"
    assert candidate["selected_file_count"] == 2
    assert candidate["paired_raw_and_results"] is True
    candidate_suffix = candidate["candidate_id"].removeprefix("candidate_")
    assert len(candidate_suffix) == 12
    assert all(character in "0123456789abcdef" for character in candidate_suffix)
    task = next(iter(pool["tasks"].values()))
    assert task["visible_prompt"] == "Find human DDA proteomics projects"
    assert task["visible_constraints"] == {"repository": "pride", "species": "Homo sapiens"}
    assert private_key["build_id"] == first["build_id"]
    assert private_key["candidates"] == [
        {
            "candidate_id": candidate["candidate_id"],
            "scenario_id": candidate["scenario_id"],
            "variant_id": candidate["variant_id"],
            "project_accession": "PXD123456",
        }
    ]
    expert_payload = json.dumps(pool, ensure_ascii=False)
    for hidden_value in ("PXD123456", "candidate-generator-model", "private-runtime", "secret-key", "discovery-secret"):
        assert hidden_value not in expert_payload
    persisted = (registry.root / "_pool_builds" / f"{first['build_id']}.json").read_text(encoding="utf-8")
    assert "secret-key" not in persisted


def test_build_and_review_failure_keeps_pool_and_reconciles_without_discovery(tmp_path: Path) -> None:
    registry = ExpertPoolRegistry(tmp_path / "expert_review")
    discovery_starts = 0
    review_starts = 0

    def start_discovery(_payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal discovery_starts
        discovery_starts += 1
        return {"job_id": "disc-job", "status": "queued"}

    def start_review(pool_id: str, options: dict[str, Any]) -> dict[str, Any]:
        nonlocal review_starts
        review_starts += 1
        assert pool_id
        assert options == {
            "profile_id": "profile-1",
            "generator_identity": {
                "provider": "local",
                "requested_model_id": "workflow-discovery/v1",
                "resolved_model_id": "workflow-discovery/v1",
                "model_family": "workflow-discovery",
                "runtime": "workflow",
                "endpoint_identity": "local:workflow-discovery",
                "identity_verification": "verified",
            },
        }
        if review_starts == 1:
            raise RuntimeError("review service unavailable")
        return {"job_id": "review-job", "status": "queued", "api_key": "must-not-leak"}

    manager = ExpertPoolBuildManager(
        registry,
        start_discovery=start_discovery,
        get_discovery=lambda job_id: _completed_discovery(job_id),
        cancel_discovery=lambda _job_id: None,
        start_review=start_review,
    )
    started = manager.start_build(
        discovery_request={"prompt": "general"},
        action="build_and_review",
        review={"profile_id": "profile-1", "api_key": "secret-key"},
    )
    failed_handoff = _wait(manager, started["build_id"], {"failed"}, field="review_status")
    assert failed_handoff["pool_id"]
    assert failed_handoff["review_status"] == "failed"
    assert discovery_starts == 1
    assert review_starts == 1

    reconciled = manager.reconcile_review(started["build_id"])
    assert reconciled is not None
    completed = _wait(manager, started["build_id"], {"completed"})
    assert completed["pool_id"] == failed_handoff["pool_id"]
    assert completed["review_job_id"] == "review-job"
    assert discovery_starts == 1
    assert review_starts == 2
    assert "secret-key" not in json.dumps(completed)
    assert "must-not-leak" not in json.dumps(completed)


def test_build_only_completes_at_durable_pool_boundary(tmp_path: Path) -> None:
    registry = ExpertPoolRegistry(tmp_path / "expert_review")
    manager = ExpertPoolBuildManager(
        registry,
        start_discovery=lambda _payload: {"job_id": "disc-job", "status": "queued"},
        get_discovery=lambda job_id: _completed_discovery(job_id),
        cancel_discovery=lambda _job_id: None,
    )
    started = manager.start_build(discovery_request={"prompt": "general"})
    completed = _wait(manager, started["build_id"], {"pool_ready"})
    assert completed["pool_id"]
    assert completed["review_status"] == "not_requested"


def test_duplicate_candidate_ids_are_rejected_before_registration(tmp_path: Path) -> None:
    registry = ExpertPoolRegistry(tmp_path / "expert_review")
    discovery = {
        "status": "completed",
        "record": {
            "pool": {
                "schema_version": "discovery-judgment-pool-blinded/v2",
                "candidates": [
                    {"candidate_id": "same", "scenario_id": "s", "variant_id": "v"},
                    {"candidate_id": "same", "scenario_id": "s", "variant_id": "v2"},
                ],
            }
        },
    }
    manager = ExpertPoolBuildManager(
        registry,
        start_discovery=lambda _payload: {"job_id": "disc-job", "status": "queued"},
        get_discovery=lambda _job_id: discovery,
        cancel_discovery=lambda _job_id: None,
    )
    started = manager.start_build(discovery_request={"prompt": "general"})
    failed = _wait(manager, started["build_id"], {"failed"})
    assert "duplicate_candidate_ids" in str(failed["error"])
    assert registry.list_pools() == []


def test_supplied_pool_without_private_key_is_not_registered(tmp_path: Path) -> None:
    registry = ExpertPoolRegistry(tmp_path / "expert_review")
    discovery = {
        "status": "completed",
        "record": {
            "pool": {
                "schema_version": "discovery-judgment-pool-blinded/v2",
                "candidates": [{"candidate_id": "candidate-1", "scenario_id": "s", "variant_id": "v"}],
            }
        },
    }
    manager = ExpertPoolBuildManager(
        registry,
        start_discovery=lambda _payload: {"job_id": "disc-job", "status": "queued"},
        get_discovery=lambda _job_id: discovery,
        cancel_discovery=lambda _job_id: None,
    )
    started = manager.start_build(discovery_request={"prompt": "general"})
    failed = _wait(manager, started["build_id"], {"failed"})
    assert "supplied_pool_requires_private_key" in str(failed["error"])
    assert registry.list_pools() == []


def test_supplied_pool_is_blinded_and_private_key_must_match(tmp_path: Path) -> None:
    registry = ExpertPoolRegistry(tmp_path / "expert_review")
    pool = {
        "schema_version": "discovery-judgment-pool-blinded/v2",
        "project_accession": "TOPLEVELPRIVATE",
        "generator_provider": "top-level-generator",
        "runtime": "top-level-runtime",
        "candidate_generation_identity": {"generator_model_family": "top-level-family"},
        "candidates": [
            {
                "candidate_id": "candidate-1",
                "scenario_id": "s",
                "variant_id": "v",
                "project_title": "Visible",
                "project_accession": "PXDPRIVATE",
                "generator_model_family": "private-generator",
                "runtime": "private-runtime",
                "api_key": "pool-secret",
            }
        ],
    }
    private_key = {
        "schema_version": "discovery-judgment-key/v1",
        "candidates": [{"candidate_id": "candidate-1", "project_accession": "PXDPRIVATE"}],
    }
    manager = ExpertPoolBuildManager(
        registry,
        start_discovery=lambda _payload: {"job_id": "disc-job", "status": "queued"},
        get_discovery=lambda _job_id: {
            "status": "completed",
            "record": {"pool": pool, "private_key": private_key},
        },
        cancel_discovery=lambda _job_id: None,
    )
    started = manager.start_build(discovery_request={"prompt": "general"})
    completed = _wait(manager, started["build_id"], {"pool_ready"})
    pool_dir = registry.root / str(completed["pool_id"])
    persisted_pool = (pool_dir / "pool.blinded.json").read_text(encoding="utf-8")
    persisted_key = (pool_dir / "private" / "judgment.key.json").read_text(encoding="utf-8")
    assert "Visible" in persisted_pool
    for hidden_value in (
        "TOPLEVELPRIVATE",
        "top-level-generator",
        "top-level-runtime",
        "top-level-family",
        "PXDPRIVATE",
        "private-generator",
        "private-runtime",
        "pool-secret",
    ):
        assert hidden_value not in persisted_pool
    assert "PXDPRIVATE" in persisted_key

    mismatched_manager = ExpertPoolBuildManager(
        ExpertPoolRegistry(tmp_path / "mismatched"),
        start_discovery=lambda _payload: {"job_id": "disc-job-2", "status": "queued"},
        get_discovery=lambda _job_id: {
            "status": "completed",
            "record": {
                "pool": pool,
                "private_key": {
                    "schema_version": "discovery-judgment-key/v1",
                    "candidates": [{"candidate_id": "other", "project_accession": "PXDPRIVATE"}],
                },
            },
        },
        cancel_discovery=lambda _job_id: None,
    )
    mismatched = mismatched_manager.start_build(discovery_request={"prompt": "general"})
    failed = _wait(mismatched_manager, mismatched["build_id"], {"failed"})
    assert "private_key_candidate_ids_mismatch" in str(failed["error"])


def test_secret_variants_and_prompt_credentials_never_reach_disk(tmp_path: Path) -> None:
    registry = ExpertPoolRegistry(tmp_path / "expert_review")
    starts: list[dict[str, Any]] = []

    def start_discovery(payload: dict[str, Any]) -> dict[str, Any]:
        starts.append(payload)
        return {"job_id": "disc-job", "status": "queued"}

    manager = ExpertPoolBuildManager(
        registry,
        start_discovery=start_discovery,
        get_discovery=lambda job_id: _completed_discovery(job_id),
        cancel_discovery=lambda _job_id: None,
    )
    prompt = "Find projects api_key=prompt-key password=prompt-pass sk-promptsecret"
    started = manager.start_build(
        discovery_request={
            "prompt": prompt,
            "access_token": "access-secret",
            "client_secret": "client-secret",
            "proxy_password": "proxy-secret",
        }
    )
    completed = _wait(manager, started["build_id"], {"pool_ready"})
    assert starts[0]["access_token"] == "access-secret"
    pool_dir = registry.root / str(completed["pool_id"])
    disk_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in registry.root.rglob("*.json")
    )
    for secret in (
        "prompt-key",
        "prompt-pass",
        "sk-promptsecret",
        "access-secret",
        "client-secret",
        "proxy-secret",
    ):
        assert secret not in disk_text
    pool = json.loads((pool_dir / "pool.blinded.json").read_text(encoding="utf-8"))
    assert "[redacted]" in next(iter(pool["tasks"].values()))["visible_prompt"]


def test_empty_discovery_result_is_rejected_before_registration(tmp_path: Path) -> None:
    registry = ExpertPoolRegistry(tmp_path / "expert_review")
    manager = ExpertPoolBuildManager(
        registry,
        start_discovery=lambda _payload: {"job_id": "disc-job", "status": "queued"},
        get_discovery=lambda _job_id: {"status": "completed", "record": {"projects": []}},
        cancel_discovery=lambda _job_id: None,
    )
    started = manager.start_build(discovery_request={"prompt": "general"})
    failed = _wait(manager, started["build_id"], {"failed"})
    assert "discovery_result_has_no_candidates" in str(failed["error"])
    assert registry.list_pools() == []


def test_agentic_workflow_generator_identity_remains_unverified() -> None:
    identity = ExpertPoolBuildManager._candidate_generation_identity(
        {
            "runtime": "workflow",
            "summary": {"agentic": {"enabled": True, "model": "planner-alias"}},
        }
    )

    assert identity["runtime"] == "agentic_workflow"
    assert identity["model_family"] == "planner-alias"
    assert identity["identity_verification"] == "unverified"
