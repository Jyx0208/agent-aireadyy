from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agent.web import app as web_app


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
    assert expert_body["candidates"][0]["candidate_id"] == "cand-1"
    assert "project_accession" not in expert_body["candidates"][0]
    assert "machine_reviews" not in expert_body["candidates"][0]

    developer = client.get(
        f"/api/expert-review/pools/{pool_id}/candidates",
        params={"mode": "developer"},
    )
    assert developer.status_code == 200
    assert developer.json()["candidates"][0]["machine_reviews"]


def test_expert_review_grade_export_and_impact_forbidden_for_expert(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_DIR", str(tmp_path / "expert_review"))
    monkeypatch.setenv("AGENT_EXPERT_REVIEW_ENABLED", "1")
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

    exported = client.post(f"/api/expert-review/pools/{pool_id}/export", json={}).json()
    assert exported["ok"] is True
    assert exported["pool"]["judgment_source"] == "human_verified"
    assert exported["pool"]["candidates"][0]["grade"] == 3

    forbidden = client.post(
        "/api/expert-review/impact/session",
        json={"session_id": pool_id, "mode": "expert", "key_path": "x", "run_paths": []},
    ).json()
    assert forbidden["ok"] is False


def test_benchmark_template_has_developer_surfaces() -> None:
    html = Path("src/agent/web/templates/benchmark_review.html").read_text(encoding="utf-8")
    assert 'id="machineRail"' in html
    assert 'id="impactPanel"' in html
    assert 'id="jobsPanel"' in html
    assert 'id="queueBar"' in html
