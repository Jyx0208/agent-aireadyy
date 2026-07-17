from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from agent.web.expert_review import pool_builds as pool_builds_module
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


def test_public_build_sanitizes_legacy_prompt_parser_auth_errors() -> None:
    public = ExpertPoolBuildManager._public(
        {
            "build_id": "legacy-build",
            "status": "failed",
            "error": (
                "prompt_parse_failed:Client error '401 Authorization Required' for url "
                "'https://api.deepseek.com/chat/completions' For more information check: "
                "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/401"
            ),
        }
    )

    message = str(public["error"])
    assert "评审池构建模型配置" in message
    assert "API Key" in message
    assert "401" not in message
    assert "api.deepseek.com" not in message


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


def test_pool_build_prepares_chinese_prompt_before_discovery(tmp_path: Path) -> None:
    registry = ExpertPoolRegistry(tmp_path / "expert_review")
    discovery_starts: list[dict[str, Any]] = []

    def prepare_discovery_request(payload: dict[str, Any]) -> dict[str, Any]:
        assert payload["prompt"] == "寻找免疫肽相关公开数据，越多越好"
        return {
            "request": {
                **payload,
                "goal": "immunopeptidomics",
                "query_terms": ["immunopeptidomics", "HLA ligandome", "MHC ligandome"],
                "max_projects": 20,
            },
            "parser": "llm",
            "warnings": [],
            "reasoning": "Translated the Chinese request into repository-searchable English terms.",
        }

    def start_discovery(payload: dict[str, Any]) -> dict[str, Any]:
        discovery_starts.append(payload)
        return {"job_id": "disc-job", "status": "queued"}

    manager = ExpertPoolBuildManager(
        registry,
        start_discovery=start_discovery,
        get_discovery=lambda job_id: _completed_discovery(job_id),
        cancel_discovery=lambda _job_id: None,
        prepare_discovery_request=prepare_discovery_request,
    )
    started = manager.start_build(
        discovery_request={"prompt": "寻找免疫肽相关公开数据，越多越好"}
    )
    completed = _wait(manager, started["build_id"], {"pool_ready"})

    assert discovery_starts[0]["goal"] == "immunopeptidomics"
    assert discovery_starts[0]["query_terms"] == [
        "immunopeptidomics",
        "HLA ligandome",
        "MHC ligandome",
    ]
    assert completed["prompt_parse"]["parser"] == "llm"
    assert completed["prompt_parse"]["query_terms"] == discovery_starts[0]["query_terms"]


def test_pool_build_exposes_live_discovery_progress(tmp_path: Path) -> None:
    registry = ExpertPoolRegistry(tmp_path / "expert_review")
    release_discovery = threading.Event()

    def get_discovery(job_id: str) -> dict[str, Any]:
        if not release_discovery.is_set():
            return {
                "job_id": job_id,
                "status": "running",
                "logs": [
                    {
                        "ts": "2026-07-16T13:00:00Z",
                        "level": "info",
                        "type": "candidate_search_completed",
                        "source_sequence": 8,
                        "message": "Project search returned 7 raw records so far.",
                        "payload": {
                            "observation": {
                                "candidate_count": 7,
                                "new_candidate_count": 7,
                            }
                        },
                    },
                    {
                        "ts": "2026-07-16T13:00:01Z",
                        "level": "info",
                        "type": "candidate_inspection_completed",
                        "source_sequence": 9,
                        "message": "Inspection round 2 completed.",
                        "payload": {
                            "round_index": 2,
                            "observation": {
                                "selected_projects": 2,
                                "selected_files": 11,
                            },
                        },
                    }
                ],
                "record": {
                    "summary": {
                        "candidate_projects_seen": 7,
                        "selected_projects": 2,
                        "selected_files": 11,
                    }
                },
            }
        completed = _completed_discovery(job_id)
        completed["logs"] = [
            {
                "ts": "2026-07-16T13:00:01Z",
                "level": "info",
                "message": "Discovery job completed.",
            }
        ]
        completed["record"]["summary"] = {
            "candidate_projects_seen": 7,
            "selected_projects": 1,
            "selected_files": 2,
            "agent_runtime": {
                "runtime": "openai_agents",
                "mode": "quality",
                "discovery_rounds": 2,
                "candidate_searches": 1,
                "stop_reason": "manifest_selected",
            },
        }
        completed["record"]["runtime"] = "openai_agents"
        completed["record"]["agent"] = completed["record"]["summary"]["agent_runtime"]
        return completed

    manager = ExpertPoolBuildManager(
        registry,
        start_discovery=lambda _payload: {"job_id": "disc-job", "status": "queued"},
        get_discovery=get_discovery,
        cancel_discovery=lambda _job_id: None,
        poll_interval=0.02,
    )
    started = manager.start_build(discovery_request={"prompt": "寻找免疫肽公开数据"})

    deadline = time.time() + 5
    live = None
    while time.time() < deadline:
        current = manager.get_build(started["build_id"])
        if current and (current.get("progress") or {}).get("counts", {}).get("candidate_projects_seen") == 7:
            live = current
            break
        time.sleep(0.01)
    assert live is not None
    assert live["progress"]["phase"] == "discovering"
    assert live["progress"]["percent"] == 25
    assert live["progress"]["counts"] == {
        "candidate_projects_seen": 7,
        "selected_projects": 2,
        "selected_files": 11,
    }
    assert live["progress"]["runtime"] == "openai_agents"
    assert live["progress"]["current_stage"] == "evaluating"
    assert live["progress"]["search_round"] == 1
    assert live["progress"]["discovery_round"] == 2
    assert live["progress"]["stop_reason"] is None
    assert live["progress"]["log_tail"][-1]["message"] == "Inspection round 2 completed."

    release_discovery.set()
    completed = _wait(manager, started["build_id"], {"pool_ready"})
    assert completed["progress"]["phase"] == "pool_ready"
    assert completed["progress"]["percent"] == 100
    assert completed["progress"]["counts"]["selected_projects"] == 1
    assert completed["progress"]["runtime"] == "openai_agents"
    assert completed["progress"]["discovery_round"] == 2
    assert completed["progress"]["stop_reason"] == "manifest_selected"


def test_pool_build_exposes_pre_sdk_failure_stop_reason() -> None:
    execution = ExpertPoolBuildManager._discovery_execution(
        {"discovery_request": {"runtime": "openai_agents"}},
        {
            "status": "failed",
            "error": "openai_agents_sdk_unavailable",
            "logs": [],
            "record": None,
        },
    )

    assert execution["runtime"] == "openai_agents"
    assert execution["current_stage"] == "failed"
    assert execution["stop_reason"] == "openai_agents_sdk_unavailable"


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


def test_prompt_preparation_failure_does_not_start_discovery(tmp_path: Path) -> None:
    starts = 0

    def start_discovery(_payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal starts
        starts += 1
        return {"job_id": "unexpected", "status": "queued"}

    manager = ExpertPoolBuildManager(
        ExpertPoolRegistry(tmp_path / "expert_review"),
        start_discovery=start_discovery,
        get_discovery=lambda _job_id: None,
        cancel_discovery=lambda _job_id: None,
        prepare_discovery_request=lambda _payload: (_ for _ in ()).throw(ValueError("parser unavailable")),
    )
    started = manager.start_build(discovery_request={"prompt": "寻找公开数据"})
    failed = _wait(manager, started["build_id"], {"failed"})

    assert starts == 0
    assert failed["prompt_parse"]["status"] == "failed"
    assert "prompt_parse_failed" in str(failed["error"])


def test_malformed_prompt_preparation_marks_parse_failed(tmp_path: Path) -> None:
    malformed_results: list[Any] = [[], {"request": []}]
    for index, prepared in enumerate(malformed_results):
        manager = ExpertPoolBuildManager(
            ExpertPoolRegistry(tmp_path / f"expert_review_{index}"),
            start_discovery=lambda _payload: {"job_id": "unexpected", "status": "queued"},
            get_discovery=lambda _job_id: None,
            cancel_discovery=lambda _job_id: None,
            prepare_discovery_request=lambda _payload, result=prepared: result,
        )
        started = manager.start_build(discovery_request={"prompt": "寻找公开数据"})
        failed = _wait(manager, started["build_id"], {"failed"})

        assert failed["prompt_parse"]["status"] == "failed"
        assert "prompt_parse_failed" in str(failed["error"])


def test_slow_prompt_preparation_does_not_block_build_reads(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    def prepare(payload: dict[str, Any]) -> dict[str, Any]:
        entered.set()
        assert release.wait(timeout=2)
        return {"request": payload, "parser": "test", "warnings": [], "reasoning": ""}

    manager = ExpertPoolBuildManager(
        ExpertPoolRegistry(tmp_path / "expert_review"),
        start_discovery=lambda _payload: {"job_id": "disc-job", "status": "queued"},
        get_discovery=lambda job_id: _completed_discovery(job_id),
        cancel_discovery=lambda _job_id: None,
        prepare_discovery_request=prepare,
    )
    started = manager.start_build(discovery_request={"prompt": "general"})
    assert entered.wait(timeout=2)
    observed: dict[str, Any] = {}

    def read_build() -> None:
        observed["build"] = manager.get_build(started["build_id"])

    reader = threading.Thread(target=read_build)
    reader.start()
    reader.join(timeout=0.25)
    try:
        assert not reader.is_alive()
        assert observed["build"]["status"] == "parsing_prompt"
    finally:
        release.set()
        reader.join(timeout=2)
    _wait(manager, started["build_id"], {"pool_ready"})


def test_cancel_during_prompt_preparation_reaches_terminal_cancelled(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    starts = 0

    def prepare(payload: dict[str, Any]) -> dict[str, Any]:
        entered.set()
        assert release.wait(timeout=2)
        return {"request": payload, "parser": "test", "warnings": [], "reasoning": ""}

    def start_discovery(_payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal starts
        starts += 1
        return {"job_id": "unexpected", "status": "queued"}

    manager = ExpertPoolBuildManager(
        ExpertPoolRegistry(tmp_path / "expert_review"),
        start_discovery=start_discovery,
        get_discovery=lambda _job_id: None,
        cancel_discovery=lambda _job_id: None,
        prepare_discovery_request=prepare,
    )
    started = manager.start_build(discovery_request={"prompt": "general"})
    assert entered.wait(timeout=2)
    cancelled = manager.cancel_build(started["build_id"])
    assert cancelled is not None
    release.set()
    terminal = _wait(manager, started["build_id"], {"cancelled"})

    assert terminal["progress"]["phase"] == "cancelled"
    assert starts == 0


def test_invalid_discovery_progress_counts_are_safely_normalized(tmp_path: Path) -> None:
    def completed(job_id: str) -> dict[str, Any]:
        discovery = _completed_discovery(job_id)
        discovery["record"]["summary"] = {
            "candidate_projects_seen": "unknown",
            "selected_projects": -4,
            "selected_files": None,
        }
        return discovery

    manager = ExpertPoolBuildManager(
        ExpertPoolRegistry(tmp_path / "expert_review"),
        start_discovery=lambda _payload: {"job_id": "disc-job", "status": "queued"},
        get_discovery=completed,
        cancel_discovery=lambda _job_id: None,
    )
    started = manager.start_build(discovery_request={"prompt": "general"})
    completed_build = _wait(manager, started["build_id"], {"pool_ready"})

    assert completed_build["progress"]["counts"] == {
        "candidate_projects_seen": 0,
        "selected_projects": 0,
        "selected_files": 0,
    }


def test_build_overwrites_caller_generator_identity_with_discovery_evidence(tmp_path: Path) -> None:
    reviews: list[dict[str, Any]] = []

    def start_review(_pool_id: str, review: dict[str, Any]) -> dict[str, Any]:
        reviews.append(review)
        return {"job_id": "review-job", "status": "queued"}

    manager = ExpertPoolBuildManager(
        ExpertPoolRegistry(tmp_path / "expert_review"),
        start_discovery=lambda _payload: {"job_id": "disc-job", "status": "queued"},
        get_discovery=lambda job_id: _completed_discovery(job_id),
        cancel_discovery=lambda _job_id: None,
        start_review=start_review,
    )
    started = manager.start_build(
        discovery_request={"prompt": "general"},
        action="build_and_review",
        review={
            "generator_identity": {
                "model_family": "forged-family",
                "identity_verification": "verified",
            }
        },
    )
    _wait(manager, started["build_id"], {"completed"})

    assert reviews[0]["generator_identity"]["model_family"] == "workflow-discovery"
    assert reviews[0]["generator_identity"]["identity_verification"] == "verified"


def test_build_includes_prompt_parser_as_generation_contributor(tmp_path: Path) -> None:
    reviews: list[dict[str, Any]] = []

    def prepare(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "request": {
                **payload,
                "_generation_contributors": [
                    {
                        "role": "prompt_parser",
                        "provider": "openai_compatible",
                        "requested_model_id": "parser-model",
                        "model_family": "parser-family",
                        "identity_verification": "unverified",
                    }
                ],
            },
            "parser": "llm",
            "warnings": [],
            "reasoning": "parsed",
        }

    manager = ExpertPoolBuildManager(
        ExpertPoolRegistry(tmp_path / "expert_review"),
        start_discovery=lambda _payload: {"job_id": "disc-job", "status": "queued"},
        get_discovery=lambda job_id: _completed_discovery(job_id),
        cancel_discovery=lambda _job_id: None,
        start_review=lambda _pool_id, review: reviews.append(review) or {"job_id": "review-job", "status": "queued"},
        prepare_discovery_request=prepare,
    )
    started = manager.start_build(
        discovery_request={"prompt": "general"},
        action="build_and_review",
    )
    _wait(manager, started["build_id"], {"completed"})

    contributor = reviews[0]["generator_identity"]["contributors"][0]
    assert contributor["role"] == "prompt_parser"
    assert contributor["model_family"] == "parser-family"
    assert contributor["identity_verification"] == "unverified"


def test_atomic_persist_retries_transient_windows_permission_error(tmp_path: Path, monkeypatch) -> None:
    original_replace = pool_builds_module.os.replace
    replace_calls = 0
    discovery_starts = 0

    def flaky_replace(source: Path, destination: Path) -> None:
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls <= 2:
            raise PermissionError("temporarily locked")
        original_replace(source, destination)

    def start_discovery(_payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal discovery_starts
        discovery_starts += 1
        return {"job_id": "disc-job", "status": "queued"}

    monkeypatch.setattr(pool_builds_module.os, "replace", flaky_replace)
    manager = ExpertPoolBuildManager(
        ExpertPoolRegistry(tmp_path / "expert_review"),
        start_discovery=start_discovery,
        get_discovery=lambda job_id: _completed_discovery(job_id),
        cancel_discovery=lambda _job_id: None,
    )
    started = manager.start_build(discovery_request={"prompt": "general"})
    completed = _wait(manager, started["build_id"], {"pool_ready"})

    assert completed["pool_id"]
    assert replace_calls >= 3
    assert discovery_starts == 1


def test_resume_reuses_discovery_idempotency_key_after_checkpoint_failure(tmp_path: Path, monkeypatch) -> None:
    downstream_jobs: dict[str, str] = {}
    start_calls: list[str] = []

    def start_discovery(payload: dict[str, Any]) -> dict[str, Any]:
        key = str(payload.get("idempotency_key") or "")
        start_calls.append(key)
        job_id = downstream_jobs.setdefault(key, f"disc-{len(downstream_jobs) + 1}")
        return {"job_id": job_id, "status": "queued"}

    manager = ExpertPoolBuildManager(
        ExpertPoolRegistry(tmp_path / "expert_review"),
        start_discovery=start_discovery,
        get_discovery=lambda job_id: _completed_discovery(job_id),
        cancel_discovery=lambda _job_id: None,
    )
    original_persist = manager._persist
    failed_after_start = False

    def fail_first_job_id_checkpoint(record: dict[str, Any]) -> None:
        nonlocal failed_after_start
        if record.get("discovery_job_id") and not failed_after_start:
            failed_after_start = True
            raise PermissionError("checkpoint unavailable")
        original_persist(record)

    monkeypatch.setattr(manager, "_persist", fail_first_job_id_checkpoint)
    started = manager.start_build(discovery_request={"prompt": "general"})
    completed = _wait(manager, started["build_id"], {"pool_ready"})

    assert completed["pool_id"]
    assert failed_after_start is True
    assert len(start_calls) >= 2
    assert len(set(start_calls)) == 1
    assert start_calls[0] == f"{started['build_id']}:discovery"
    assert len(downstream_jobs) == 1
