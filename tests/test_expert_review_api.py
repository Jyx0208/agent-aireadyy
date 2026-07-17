from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.web import app as web_app


def test_developer_mode_requires_token_by_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_DIR", str(tmp_path / "expert_review"))
    monkeypatch.delenv("AGENT_EXPERT_REVIEW_DEVELOPER_TOKEN", raising=False)
    monkeypatch.delenv("AGENT_EXPERT_REVIEW_ALLOW_LOCAL_DEVELOPER", raising=False)
    client = TestClient(web_app.app)
    status = client.get("/api/expert-review/status").json()
    assert status["developer_allowed"] is False
    assert client.get("/api/llm/profiles").json()["error"] == "developer_access_required"


def test_benchmark_review_template_supports_registry_shell() -> None:
    html = Path("src/agent/web/templates/benchmark_review.html").read_text(encoding="utf-8")
    assert 'id="serverPoolSelect"' in html
    assert 'id="openServerPoolButton"' in html
    assert 'id="importServerButton"' in html
    assert 'id="reviewMode"' in html
    assert "/api/expert-review/pools" in html
    assert "judgment_pool.reviewed.json" in html
    assert "project_accession" not in html


def test_expert_review_pool_api_import_and_list(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_DIR", str(tmp_path / "expert_review"))
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_ENABLED", "1")
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_ALLOW_LOCAL_DEVELOPER", "1")
    client = TestClient(web_app.app)

    status = client.get("/api/expert-review/status")
    assert status.status_code == 200
    assert status.json()["enabled"] is True

    pool = {
        "schema_version": "discovery-judgment-pool-blinded/v2",
        "tasks": {"s:v": {"visible_prompt": "prompt"}},
        "candidates": [
            {
                "candidate_id": "cand-1",
                "scenario_id": "s",
                "variant_id": "v",
                "project_title": "Title",
                "project_description": "Desc",
                "project_accession": "PXD999",
                "machine_reviews": [{"grade": 1, "reason": "x"}],
                "judgment_confidence": "medium",
            }
        ],
    }
    imported = client.post(
        "/api/expert-review/pools/import",
        json={"pool": pool, "label": "api-pilot"},
    )
    assert imported.status_code == 200
    body = imported.json()
    assert body["ok"] is True
    pool_id = body["pool"]["pool_id"]

    listed = client.get("/api/expert-review/pools")
    assert listed.status_code == 200
    assert any(item["pool_id"] == pool_id for item in listed.json()["pools"])

    expert = client.get(f"/api/expert-review/pools/{pool_id}/candidates", params={"mode": "expert"})
    assert expert.status_code == 200
    expert_body = expert.json()
    assert expert_body["ok"] is True
    expert_candidate = expert_body["candidates"][0]
    assert expert_candidate["candidate_id"] == "cand-1"
    for hidden_field in (
        "project_accession",
        "machine_reviews",
        "machine_review_runs",
        "grade",
        "review_notes",
        "reviewer_id",
        "human_grades",
    ):
        assert hidden_field not in expert_candidate

    developer = client.get(
        f"/api/expert-review/pools/{pool_id}/candidates",
        params={"mode": "developer"},
    )
    assert developer.status_code == 200
    assert developer.json()["candidates"][0]["machine_reviews"]


def test_expert_review_workspace_zip_round_trip(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "expert_review"
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_DIR", str(root))
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_ENABLED", "1")
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_ALLOW_LOCAL_DEVELOPER", "1")
    client = TestClient(web_app.app)
    pool = {
        "schema_version": "discovery-judgment-pool-reviewed/v2",
        "label": "portable-pool",
        "candidates": [
            {
                "candidate_id": "cand-zip",
                "scenario_id": "s",
                "variant_id": "v",
                "project_title": "Portable",
                "grade": 2,
                "machine_review_runs": [{"model": "model-a", "grade": 2}],
            }
        ],
    }
    imported = client.post("/api/expert-review/pools/import", json={"pool": pool, "label": "portable-pool"}).json()
    pool_id = imported["pool"]["pool_id"]
    pool_dir = root / pool_id
    blinded = json.loads((pool_dir / "pool.blinded.json").read_text(encoding="utf-8"))
    blinded["candidates"][0]["project_description"] = "original blind description"
    (pool_dir / "pool.blinded.json").write_text(json.dumps(blinded), encoding="utf-8")
    registry_record = json.loads((pool_dir / "registry.json").read_text(encoding="utf-8"))
    registry_record["created_at"] = "2025-01-02T03:04:05Z"
    registry_record["tags"] = ["portable", "reviewed"]
    (pool_dir / "registry.json").write_text(json.dumps(registry_record), encoding="utf-8")
    (pool_dir / "private").mkdir(exist_ok=True)
    (pool_dir / "private" / "judgment.key.json").write_text(
        json.dumps({"candidates": [{"candidate_id": "cand-zip", "project_accession": "PXDZIP"}]}),
        encoding="utf-8",
    )
    (pool_dir / "jobs").mkdir(exist_ok=True)
    (pool_dir / "jobs" / "judge_zip.json").write_text(
        json.dumps({"job_id": "judge_zip", "pool_id": pool_id, "status": "queued", "items": {"cand-zip": "running"}, "logs": [{"level": "error", "message": "provider rejected xai-1234567890"}], "api_key": "sk-secret"}),
        encoding="utf-8",
    )

    exported = client.post(
        f"/api/expert-review/pools/{pool_id}/workspace.zip",
        json={"workspace": {"candidate_id": "cand-zip", "completion_filter": "graded", "score_filter": "2"}},
    )
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("application/zip")
    assert b"sk-secret" not in exported.content
    assert b"xai-1234567890" not in exported.content
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        manifest = json.loads(archive.read("workspace.json"))
        assert manifest["schema_version"] == "benchmark-review-workspace/v1"
        assert manifest["workspace"]["candidate_id"] == "cand-zip"
        assert "api_key" not in archive.read("pool/jobs/judge_zip.json").decode("utf-8")

    restored = client.post(
        "/api/expert-review/workspaces/import",
        content=exported.content,
        headers={"Content-Type": "application/zip"},
    ).json()
    assert restored["ok"] is True
    assert restored["pool"]["pool_id"] != pool_id
    assert restored["workspace"]["candidate_id"] == "cand-zip"
    assert restored["restored_jobs"] == 1
    restored_id = restored["pool"]["pool_id"]
    assert restored["pool"]["created_at"] == "2025-01-02T03:04:05Z"
    assert restored["pool"]["tags"] == ["portable", "reviewed"]
    restored_pool = client.get(f"/api/expert-review/pools/{restored_id}/candidates", params={"mode": "developer"}).json()
    assert restored_pool["candidates"][0]["grade"] == 2
    restored_blind = json.loads((root / restored_id / "pool.blinded.json").read_text(encoding="utf-8"))
    assert restored_blind["candidates"][0]["project_description"] == "original blind description"
    restored_jobs = list((root / restored_id / "jobs").glob("*.json"))
    assert len(restored_jobs) == 1
    restored_job = json.loads(restored_jobs[0].read_text(encoding="utf-8"))
    assert restored_job["pool_id"] == restored_id
    assert restored_job["status"] == "cancelled"
    assert restored_job["items"]["cand-zip"] == "pending"
    assert "manual_resume" in restored_job["error"]
    assert "api_key" not in restored_job


def test_expert_review_workspace_zip_rejects_traversal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_DIR", str(tmp_path / "expert_review"))
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_ENABLED", "1")
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_ALLOW_LOCAL_DEVELOPER", "1")
    client = TestClient(web_app.app)
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("../escape.json", "{}")
    response = client.post(
        "/api/expert-review/workspaces/import",
        content=data.getvalue(),
        headers={"Content-Type": "application/zip"},
    ).json()
    assert response == {"ok": False, "error": "workspace_unsafe_path"}

    double_slash = io.BytesIO()
    with zipfile.ZipFile(double_slash, "w") as archive:
        archive.writestr("pool/jobs//escape.json", "{}")
    response = client.post(
        "/api/expert-review/workspaces/import",
        content=double_slash.getvalue(),
        headers={"Content-Type": "application/zip"},
    ).json()
    assert response == {"ok": False, "error": "workspace_unsafe_path"}

    drive_path = io.BytesIO()
    with zipfile.ZipFile(drive_path, "w") as archive:
        archive.writestr("pool/jobs/C:/escape.json", "{}")
    response = client.post(
        "/api/expert-review/workspaces/import",
        content=drive_path.getvalue(),
        headers={"Content-Type": "application/zip"},
    ).json()
    assert response == {"ok": False, "error": "workspace_unsafe_path"}


def test_expert_review_grade_export_and_impact_forbidden_for_expert(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_DIR", str(tmp_path / "expert_review"))
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_ENABLED", "1")
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_ALLOW_LOCAL_DEVELOPER", "1")
    client = TestClient(web_app.app)
    pool = {
        "schema_version": "discovery-judgment-pool-blinded/v2",
        "candidates": [
            {
                "candidate_id": "cand-1",
                "scenario_id": "s",
                "variant_id": "v",
                "project_title": "Title",
                "project_description": "Desc",
                "machine_reviews": [{"grade": 1, "reason": "x", "supporting_evidence": [], "constraint_conflicts": []}],
                "judgment_confidence": "low",
            }
        ],
    }
    imported = client.post("/api/expert-review/pools/import", json={"pool": pool, "label": "grade-pilot"}).json()
    pool_id = imported["pool"]["pool_id"]
    graded = client.put(
        f"/api/expert-review/pools/{pool_id}/grades/cand-1",
        json={"grade": 3, "notes": "good", "reviewer_id": "r1", "mode": "developer"},
    ).json()
    assert graded["ok"] is True
    assert graded["candidate"]["grade"] == 3
    assert graded["candidate"]["machine_reviews"]

    expert_view = client.get(f"/api/expert-review/pools/{pool_id}/candidates", params={"mode": "expert"}).json()
    assert "machine_reviews" not in expert_view["candidates"][0]

    exported = client.post(f"/api/expert-review/pools/{pool_id}/export", json={"reviewer_id": "r1"}).json()
    assert exported["ok"] is True
    assert exported["pool"]["judgment_source"] == "human_verified"
    assert exported["pool"]["candidates"][0]["grade"] == 3

    forbidden = client.post(
        "/api/expert-review/impact/session",
        json={"session_id": pool_id, "mode": "expert", "key_path": "x", "run_paths": []},
    ).json()
    assert forbidden["ok"] is False


def test_pool_build_apis_are_developer_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_DIR", str(tmp_path / "expert_review"))
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_ENABLED", "1")
    monkeypatch.delenv("AGENT_EXPERT_REVIEW_DEVELOPER_TOKEN", raising=False)
    monkeypatch.delenv("AGENT_EXPERT_REVIEW_ALLOW_LOCAL_DEVELOPER", raising=False)
    client = TestClient(web_app.app)

    forbidden = client.get("/api/benchmark-review/builds").json()
    assert forbidden["ok"] is False
    assert forbidden["error"] == "developer_access_required"

    monkeypatch.setenv("AGENT_EXPERT_REVIEW_ALLOW_LOCAL_DEVELOPER", "1")
    allowed = client.get("/api/expert-review/pool-builds").json()
    assert allowed == {"ok": True, "builds": []}


def test_discovery_job_start_is_idempotent_for_build_handoff(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path / "runs")
    monkeypatch.setattr(web_app, "_start_discovery_job_thread", lambda _job_id: None)
    web_app._discovery_jobs.clear()
    client = TestClient(web_app.app)

    first = client.post(
        "/api/discovery/jobs",
        json={"prompt": "general", "idempotency_key": "pool-build-1:discovery"},
    ).json()
    replay = client.post(
        "/api/discovery/jobs",
        json={"prompt": "general", "idempotency_key": "pool-build-1:discovery"},
    ).json()

    assert replay["job_id"] == first["job_id"]
    assert replay["idempotency_key"] == "pool-build-1:discovery"
    assert len(web_app._discovery_jobs) == 1


def test_pool_build_list_projects_live_review_job_progress(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_ENABLED", "1")
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_ALLOW_LOCAL_DEVELOPER", "1")

    class _Builds:
        def list_builds(self):
            return [
                {
                    "build_id": "build-1",
                    "status": "completed",
                    "review_job_id": "review-1",
                }
            ]

    class _Jobs:
        def list_jobs(self):
            return [
                {
                    "job_id": "review-1",
                    "status": "running",
                    "progress": {"total": 20, "done": 7, "failed": 1},
                    "error": None,
                    "log_tail": [{"level": "info", "message": "reviewing"}],
                }
            ]

    monkeypatch.setattr(web_app, "_expert_pool_build_manager", lambda: _Builds())
    monkeypatch.setattr(web_app, "_expert_job_manager", lambda: _Jobs())
    response = TestClient(web_app.app).get("/api/benchmark-review/builds").json()

    progress = response["builds"][0]["review_progress"]
    assert progress["status"] == "running"
    assert progress["progress"] == {"total": 20, "done": 7, "failed": 1}
    assert progress["log_tail"][-1]["message"] == "reviewing"


def test_pool_build_api_requires_only_prompt_and_defaults_to_review(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_DIR", str(tmp_path / "expert_review"))
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_ENABLED", "1")
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_ALLOW_LOCAL_DEVELOPER", "1")
    captured: dict = {}

    class _Manager:
        def start_build(self, **kwargs):
            captured.update(kwargs)
            return {"build_id": "build-1", "status": "discovering"}

    monkeypatch.setattr(web_app, "_expert_pool_build_manager", lambda: _Manager())
    client = TestClient(web_app.app)
    response = client.post(
        "/api/benchmark-review/builds",
        json={"prompt": "Find human DDA proteomics", "idempotency_key": "request-1"},
    ).json()

    assert response["ok"] is True
    assert response["build"]["build_id"] == "build-1"
    assert captured["discovery_request"] == {
        "prompt": "Find human DDA proteomics",
        "output_language": "en",
        "scale_mode": "auto",
    }
    assert captured["action"] == "build_and_review"
    assert captured["label"] is None
    assert captured["preset_id"] == "default/v1"
    assert captured["review"] == {"output_language": "en", "scale_mode": "auto"}
    assert captured["idempotency_key"] == "request-1"


def test_pool_build_llm_config_api_persists_separately_from_expert_profiles(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_ENABLED", "1")
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_ALLOW_LOCAL_DEVELOPER", "1")
    monkeypatch.setenv("AGENT_POOL_BUILD_LLM_CONFIG_PATH", str(tmp_path / "pool_builder_llm.json"))
    monkeypatch.setenv("AGENT_LLM_CONFIG_PATH", str(tmp_path / "expert_profiles.json"))
    client = TestClient(web_app.app)

    saved = client.put(
        "/api/benchmark-review/build-llm-config",
        json={
            "provider": "google",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
            "model": "gemini-3-pro",
            "api_key": "pool-builder-secret",
            "timeout": "90",
        },
    ).json()

    assert saved["ok"] is True
    assert saved["configured"] is True
    assert saved["profile"]["id"] == "pool-builder"
    assert saved["profile"]["provider"] == "google"
    assert saved["profile"]["model"] == "gemini-3-pro"
    assert saved["profile"]["api_key_set"] is True
    assert "api_key" not in saved["profile"]
    assert web_app._llm_config_store().list_profiles(include_secrets=False) == []

    loaded = client.get("/api/benchmark-review/build-llm-config").json()
    assert loaded["ok"] is True
    assert loaded["profile"]["model"] == "gemini-3-pro"
    assert "api_key" not in loaded["profile"]

    updated = client.put(
        "/api/benchmark-review/build-llm-config",
        json={
            "provider": "google",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
            "model": "gemini-3.1-pro",
            "timeout": "120",
        },
    ).json()
    assert updated["ok"] is True
    assert updated["profile"]["model"] == "gemini-3.1-pro"
    assert updated["profile"]["api_key_set"] is True

    changed_base_without_key = client.put(
        "/api/benchmark-review/build-llm-config",
        json={
            "provider": "openai",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-5",
            "timeout": "120",
        },
    ).json()
    assert changed_base_without_key == {
        "ok": False,
        "error": "api_key_required_for_new_base_url",
    }

    unsupported = client.put(
        "/api/benchmark-review/build-llm-config",
        json={
            "provider": "anthropic",
            "base_url": "https://api.anthropic.com",
            "model": "claude-opus",
            "api_key": "not-used",
            "timeout": "120",
        },
    ).json()
    assert unsupported == {
        "ok": False,
        "error": "pool_build_provider_requires_openai_compatible_protocol",
    }


def test_pool_build_llm_models_and_check_use_dedicated_saved_secret(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_ALLOW_LOCAL_DEVELOPER", "1")
    monkeypatch.setenv("AGENT_POOL_BUILD_LLM_CONFIG_PATH", str(tmp_path / "pool_builder_llm.json"))
    captured: dict[str, dict] = {}

    async def fetch_models(config: dict[str, str]) -> list[str]:
        captured["models"] = dict(config)
        return ["gemini-3-pro", "gemini-3-flash", "gemini-3-pro"]

    async def check_connection(config: dict[str, str]) -> tuple[bool, str]:
        captured["check"] = dict(config)
        return True, "API 连接成功"

    monkeypatch.setattr(web_app, "_fetch_llm_models", fetch_models)
    monkeypatch.setattr(web_app, "_run_llm_check", check_connection)
    client = TestClient(web_app.app)
    saved = client.put(
        "/api/benchmark-review/build-llm-config",
        json={
            "provider": "google",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
            "model": "gemini-3-pro",
            "api_key": "pool-builder-secret",
            "timeout": "90",
        },
    ).json()
    assert saved["ok"] is True

    models = client.post(
        "/api/benchmark-review/build-llm-config/models",
        json={
            "provider": "google",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
            "model": "gemini-3-flash",
            "timeout": "90",
        },
    ).json()
    assert models == {
        "ok": True,
        "models": ["gemini-3-pro", "gemini-3-flash"],
        "selected": "gemini-3-flash",
    }
    assert captured["models"]["api_key"] == "pool-builder-secret"
    assert captured["models"]["model"] == "gemini-3-flash"

    checked = client.post(
        "/api/benchmark-review/build-llm-config/check",
        json={
            "provider": "google",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
            "model": "gemini-3-flash",
            "timeout": "90",
        },
    ).json()
    assert checked == {"ok": True, "message": "API 连接成功"}
    assert captured["check"]["api_key"] == "pool-builder-secret"
    assert captured["check"]["model"] == "gemini-3-flash"

    changed_base = client.post(
        "/api/benchmark-review/build-llm-config/models",
        json={
            "provider": "openai",
            "base_url": "https://api.openai.com/v1",
            "timeout": "90",
        },
    ).json()
    assert changed_base == {
        "ok": False,
        "error": "api_key_required_for_new_base_url",
        "models": [],
    }


def test_pool_build_llm_check_accepts_unsaved_explicit_key_and_reports_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_ALLOW_LOCAL_DEVELOPER", "1")
    monkeypatch.setenv("AGENT_POOL_BUILD_LLM_CONFIG_PATH", str(tmp_path / "pool_builder_llm.json"))
    captured: dict = {}

    async def check_connection(config: dict[str, str]) -> tuple[bool, str]:
        captured.update(config)
        return False, f"provider echoed {config['api_key']} at https://api.deepseek.com/chat/completions"

    monkeypatch.setattr(web_app, "_run_llm_check", check_connection)
    response = TestClient(web_app.app).post(
        "/api/benchmark-review/build-llm-config/check",
        json={
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "api_key": "new-unsaved-key",
            "timeout": "60",
        },
    ).json()

    assert response == {
        "ok": False,
        "error": "provider echoed [redacted-api-key] at [provider endpoint]",
        "message": "provider echoed [redacted-api-key] at [provider endpoint]",
    }
    assert captured["api_key"] == "new-unsaved-key"
    assert web_app._pool_build_llm_config_store().get_profile(
        "pool-builder",
        include_secrets=False,
    ) is None


def test_pool_build_api_propagates_scale_and_language_without_trusting_generator_identity(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_DIR", str(tmp_path / "expert_review"))
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_ENABLED", "1")
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_ALLOW_LOCAL_DEVELOPER", "1")
    captured: dict = {}

    class _Manager:
        def start_build(self, **kwargs):
            captured.update(kwargs)
            return {"build_id": "build-cn", "status": "discovering"}

    monkeypatch.setattr(web_app, "_expert_pool_build_manager", lambda: _Manager())
    response = TestClient(web_app.app).post(
        "/api/benchmark-review/builds",
        json={
            "prompt": "寻找免疫肽相关公开数据，越多越好",
            "action": "build_only",
            "output_language": "zh",
            "scale_mode": "exhaustive",
            "review": {
                "generator_identity": {
                    "model_family": "forged-family",
                    "identity_verification": "verified",
                }
            },
        },
    ).json()

    assert response["ok"] is True
    assert captured["discovery_request"]["output_language"] == "zh-CN"
    assert captured["discovery_request"]["scale_mode"] == "exhaustive"
    assert captured["review"] == {"output_language": "zh-CN", "scale_mode": "exhaustive"}


def test_pool_build_prompt_preparation_uses_dedicated_builder_config(monkeypatch) -> None:
    captured: dict = {}

    class _Store:
        def get_profile(self, profile_id: str, *, include_secrets: bool = False):
            assert profile_id == "pool-builder"
            assert include_secrets is True
            return {
                "id": "pool-builder",
                "label": "Pool builder",
                "api_key": "parser-secret",
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                "model": "gemini-3-pro",
                "timeout": "90",
                "provider": "google",
                "requested_model_id": "gemini-3-pro",
                "model_family": "gemini",
                "endpoint_identity": "google:primary",
                "identity_verification": "provider_attested",
            }

    def parse(payload: dict) -> dict:
        captured.update(payload)
        return {
            "parser": "llm",
            "fields": {"goal": "general", "query_terms": ["human DDA proteomics"]},
            "warnings": [],
            "reasoning": "parsed",
        }

    monkeypatch.setattr(web_app, "_pool_build_llm_config_store", lambda: _Store())
    monkeypatch.setattr(web_app, "_run_discovery_goal_parse", parse)
    prepared = web_app._prepare_expert_pool_discovery_request(
        {
            "prompt": "寻找人类 DDA 蛋白质组",
            "output_language": "zh-CN",
        }
    )

    assert captured["llm_config"]["model"] == "gemini-3-pro"
    assert captured["llm_config"]["api_key"] == "parser-secret"
    assert prepared["request"]["pool_builder_profile_id"] == "pool-builder"
    assert prepared["request"]["_generation_contributors"][0]["model_family"] == "gemini"


def test_pool_build_does_not_silently_use_general_default_llm(monkeypatch) -> None:
    class _Store:
        def get_profile(self, _profile_id: str, *, include_secrets: bool = False):
            return None

    monkeypatch.setattr(web_app, "_pool_build_llm_config_store", lambda: _Store())
    monkeypatch.setattr(
        web_app,
        "_server_llm_config",
        lambda: (_ for _ in ()).throw(AssertionError("general default must not be used")),
    )

    with pytest.raises(ValueError) as exc_info:
        web_app._prepare_expert_pool_discovery_request(
            {
                "prompt": "寻找人类 DDA 蛋白质组",
                "output_language": "zh-CN",
                "llm_config": {
                    "api_key": "forged-request-key",
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-chat",
                    "timeout": "120",
                },
            }
        )

    assert "评审池构建模型配置" in str(exc_info.value)


def test_pool_build_strips_request_supplied_model_identity_and_credentials(monkeypatch) -> None:
    class _Store:
        def get_profile(self, _profile_id: str, *, include_secrets: bool = False):
            return None

    monkeypatch.setattr(web_app, "_pool_build_llm_config_store", lambda: _Store())
    prepared = web_app._prepare_expert_pool_discovery_request(
        {
            "prompt": "Find human DDA proteomics projects",
            "query_terms": ["human DDA proteomics"],
            "llm_config": {
                "api_key": "forged-request-key",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-chat",
                "timeout": "120",
            },
            "_generation_contributors": [
                {"model_family": "forged-family", "identity_verification": "verified"}
            ],
            "pool_builder_profile_id": "forged-profile",
        }
    )

    request = prepared["request"]
    assert prepared["parser"] == "deterministic_english_fallback"
    assert "llm_config" not in request
    assert "_generation_contributors" not in request
    assert "pool_builder_profile_id" not in request


def test_pool_build_strict_llm_config_never_borrows_global_api_key(monkeypatch) -> None:
    monkeypatch.setattr(
        web_app,
        "_server_llm_config",
        lambda: (_ for _ in ()).throw(AssertionError("global config must not be read")),
    )

    with pytest.raises(ValueError) as exc_info:
        web_app._agentic_discovery_planner(
            {
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                "model": "gemini-3-pro",
                "timeout": "120",
            },
            allow_server_default=False,
        )

    assert "API Key" in str(exc_info.value)


def test_pool_build_parser_auth_failure_is_actionable_and_hides_raw_url(monkeypatch) -> None:
    class _Store:
        def get_profile(self, _profile_id: str, *, include_secrets: bool = False):
            return {
                "id": "deepseek-parser",
                "api_key": "expired-key",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-chat",
                "timeout": "90",
                "provider": "deepseek",
                "model_family": "deepseek",
                "identity_verification": "unverified",
            }

    monkeypatch.setattr(web_app, "_pool_build_llm_config_store", lambda: _Store())
    monkeypatch.setattr(
        web_app,
        "_run_discovery_goal_parse",
        lambda _payload: (_ for _ in ()).throw(
            RuntimeError("Client error '401 Authorization Required' for url 'https://api.deepseek.com/chat/completions'")
        ),
    )

    with pytest.raises(ValueError) as exc_info:
        web_app._prepare_expert_pool_discovery_request(
            {
                "prompt": "寻找人类 DDA 蛋白质组",
                "output_language": "zh-CN",
            }
        )

    message = str(exc_info.value)
    assert "评审池构建模型" in message
    assert "API Key" in message
    assert "401" not in message
    assert "api.deepseek.com" not in message


def test_pool_build_prompt_preparation_uses_english_terms_scale_preset_and_explicit_agentic_only(monkeypatch) -> None:
    def parse(_payload: dict) -> dict:
        return {
            "status": "completed",
            "parser": "llm",
            "fields": {
                "repository": "pride",
                "goal": "immunopeptidomics",
                "ptm_type": "unknown_ptm",
                "ptm_types": [],
                "query_terms": ["immunopeptidomics", "免疫肽", "HLA ligandome"],
                "scale_mode": "exhaustive",
                "agentic": True,
            },
            "warnings": [],
            "reasoning": "已转换为英文检索概念。",
        }

    monkeypatch.setattr(web_app, "_run_discovery_goal_parse", parse)
    monkeypatch.setattr(
        web_app,
        "_prompt_parser_generation_identity",
        lambda _config: {
            "role": "prompt_parser",
            "provider": "openai_compatible",
            "requested_model_id": "parser-model",
            "model_family": "parser-family",
            "identity_verification": "unverified",
        },
    )
    prepared = web_app._prepare_expert_pool_discovery_request(
        {
            "prompt": "寻找免疫肽相关公开数据，越多越好",
            "output_language": "zh-CN",
            "scale_mode": "auto",
        }
    )

    request = prepared["request"]
    assert prepared["parser"] == "llm"
    assert request["query_terms"] == ["immunopeptidomics", "HLA ligandome"]
    assert request["scale_mode"] == "exhaustive"
    assert request["max_projects"] == 200
    assert request["max_candidate_projects"] == 600
    assert request["max_files"] == 5000
    assert request["agentic"] is False
    assert request["output_language"] == "zh-CN"
    assert request["_generation_contributors"][0]["model_family"] == "parser-family"
    assert any("安全上限" in warning for warning in prepared["warnings"])


def test_repository_query_terms_reject_non_ascii_language_fragments() -> None:
    assert web_app._english_discovery_query_terms(
        ["HLA ligandome", "HLA 리간드", "免疫肽", "DDA proteomics"]
    ) == ["HLA ligandome", "DDA proteomics"]


def test_pool_build_explicit_scale_and_agentic_override_parser(monkeypatch) -> None:
    monkeypatch.setattr(
        web_app,
        "_run_discovery_goal_parse",
        lambda _payload: {
            "parser": "llm",
            "fields": {
                "goal": "general",
                "query_terms": ["human DDA proteomics"],
                "scale_mode": "exhaustive",
            },
            "warnings": [],
            "reasoning": "parsed",
        },
    )
    prepared = web_app._prepare_expert_pool_discovery_request(
        {
            "prompt": "Find a balanced human DDA set",
            "scale_mode": "balanced",
            "agentic": True,
            "output_language": "en",
        }
    )

    request = prepared["request"]
    assert request["scale_mode"] == "balanced"
    assert request["max_projects"] == 75
    assert request["max_candidate_projects"] == 300
    assert request["agentic"] is True


def test_pool_build_localizes_common_discovery_logs_but_keeps_search_terms_english() -> None:
    job = {
        "job_id": "disc-1",
        "status": "running",
        "output_language": "zh-CN",
        "logs": [
            {"level": "info", "message": "Searching PRIDE projects: HLA ligandome"},
            {"level": "info", "message": "Project search returned 25 raw records so far."},
            {"level": "info", "message": "Observe: candidate coverage is still low"},
            {"level": "info", "message": "Unmapped internal discovery detail"},
        ],
        "record": None,
    }

    public = web_app._discovery_job_public(job, detail=True)

    assert public["logs"][0]["message"] == "正在检索 PRIDE 项目：HLA ligandome"
    assert public["logs"][1]["message"] == "项目检索目前返回 25 条原始记录。"
    assert public["logs"][2]["message"] == "观察：已记录当前数据发现状态。"
    assert public["logs"][3]["message"] == "数据发现进度已更新。"
    assert "HLA ligandome" in public["logs"][0]["message"]


def test_prompt_parse_warning_and_reasoning_follow_selected_language(monkeypatch) -> None:
    monkeypatch.setattr(
        web_app,
        "_run_discovery_goal_parse",
        lambda _payload: {
            "parser": "llm",
            "fields": {"goal": "general", "query_terms": ["human DDA proteomics"]},
            "warnings": ["Unsupported repository 'example' was ignored; using PRIDE."],
            "reasoning": "Converted the request into repository search terms.",
        },
    )
    monkeypatch.setattr(web_app, "_prompt_parser_generation_identity", lambda _config: None)

    prepared = web_app._prepare_expert_pool_discovery_request(
        {"prompt": "寻找人类 DDA 蛋白质组", "output_language": "zh-CN"}
    )

    assert prepared["warnings"] == ["不支持数据仓库“example”，已改用 PRIDE。"]
    assert prepared["reasoning"] == "请求已解析，并生成英文仓库检索词。"


def test_build_review_handoff_defaults_to_automatic_consensus(monkeypatch) -> None:
    captured: dict = {}

    class _Jobs:
        def start_consensus_job(self, **kwargs):
            captured.update(kwargs)
            return {"job_id": "consensus-1", "status": "queued"}

    monkeypatch.setattr(web_app, "_expert_job_manager", lambda: _Jobs())
    manager = web_app._expert_pool_build_manager()
    started = manager.start_review("pool-1", {"workers": 3})

    assert started["job_id"] == "consensus-1"
    assert captured["pool_id"] == "pool-1"
    assert captured["workers"] == 3
    assert captured["generator_identity"] == {}
    assert captured["idempotency_key"] == "pool-1:model-expert-consensus"
    assert captured["output_language"] == "en"
    assert captured["scale_mode"] == "auto"


def test_consensus_job_api_does_not_require_single_profile(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_DIR", str(tmp_path / "expert_review"))
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_ENABLED", "1")
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_ALLOW_LOCAL_DEVELOPER", "1")
    captured: dict = {}

    class _Jobs:
        def start_consensus_job(self, **kwargs):
            captured.update(kwargs)
            return {"job_id": "consensus-1", "status": "queued", "job_type": "model_expert_consensus"}

    monkeypatch.setattr(web_app, "_expert_job_manager", lambda: _Jobs())
    client = TestClient(web_app.app)
    response = client.post(
        "/api/expert-review/jobs",
        json={
            "pool_id": "pool-1",
            "job_type": "model_expert_consensus",
            "generator_identity": {"model_family": "gpt", "identity_verification": "verified"},
            "output_language": "zh-CN",
        },
    ).json()

    assert response["ok"] is True
    assert response["job"]["job_id"] == "consensus-1"
    assert captured["pool_id"] == "pool-1"
    assert captured["generator_identity"]["model_family"] == "gpt"
    assert captured["output_language"] == "zh-CN"
    assert captured["scale_mode"] == "auto"


def test_delete_expert_job_api_requires_developer_and_reports_running_or_missing(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_ENABLED", "1")
    monkeypatch.delenv("AGENT_EXPERT_REVIEW_DEVELOPER_TOKEN", raising=False)
    monkeypatch.delenv("AGENT_EXPERT_REVIEW_ALLOW_LOCAL_DEVELOPER", raising=False)
    calls: list[str] = []

    class _Jobs:
        def delete_job(self, job_id: str):
            calls.append(job_id)
            if job_id == "running-job":
                raise ValueError("job_running_cancel_before_delete")
            if job_id == "missing-job":
                return None
            return {"job_id": job_id, "pool_id": "pool-1", "status": "completed"}

    monkeypatch.setattr(web_app, "_expert_job_manager", lambda: _Jobs())
    client = TestClient(web_app.app)

    forbidden = client.delete("/api/expert-review/jobs/completed-job").json()
    assert forbidden == {"ok": False, "error": "developer_access_required"}
    assert calls == []

    monkeypatch.setenv("AGENT_EXPERT_REVIEW_ALLOW_LOCAL_DEVELOPER", "1")
    deleted = client.delete("/api/expert-review/jobs/completed-job").json()
    assert deleted == {
        "ok": True,
        "deleted": True,
        "job": {"job_id": "completed-job", "pool_id": "pool-1", "status": "completed"},
    }
    running = client.delete("/api/expert-review/jobs/running-job").json()
    assert running == {"ok": False, "error": "job_running_cancel_before_delete"}
    missing = client.delete("/api/expert-review/jobs/missing-job").json()
    assert missing == {"ok": False, "error": "job_not_found"}


def test_retry_expert_job_api_accepts_worker_override(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_ENABLED", "1")
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_ALLOW_LOCAL_DEVELOPER", "1")
    captured: dict[str, Any] = {}

    class _Jobs:
        def retry_failed(self, job_id: str, *, workers: int | None = None):
            captured.update(job_id=job_id, workers=workers)
            return {"job_id": job_id, "status": "queued", "workers": workers}

    monkeypatch.setattr(web_app, "_expert_job_manager", lambda: _Jobs())
    client = TestClient(web_app.app)

    response = client.post("/api/expert-review/jobs/job-1/retry-failed", json={"workers": 6}).json()

    assert response["ok"] is True
    assert captured == {"job_id": "job-1", "workers": 6}
    assert response["job"]["workers"] == 6


def test_benchmark_template_has_developer_surfaces() -> None:
    html = Path("src/agent/web/templates/benchmark_review.html").read_text(encoding="utf-8")
    assert 'id="machineRail"' in html
    assert 'id="impactPanel"' in html
    assert 'id="jobsPanel"' in html
    assert 'id="queueBar"' in html
    assert 'id="fetchModelsButton"' in html
    assert 'id="saveProfileButton"' in html
    assert 'id="profileModelSelect"' in html
