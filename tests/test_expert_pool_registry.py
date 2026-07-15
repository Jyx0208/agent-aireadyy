from __future__ import annotations

from pathlib import Path

from agent.web.expert_review.pool_registry import ExpertPoolRegistry, blind_candidate_view


def _sample_pool() -> dict:
    return {
        "schema_version": "discovery-judgment-pool-blinded/v2",
        "tasks": {
            "human_neuron:clear": {
                "visible_prompt": "Find neuron proteomics datasets",
            }
        },
        "candidates": [
            {
                "candidate_id": "c-1",
                "scenario_id": "human_neuron",
                "variant_id": "clear",
                "project_title": "Neuron study",
                "project_description": "A blind description",
                "species": "Homo sapiens",
                "project_accession": "PXD000001",
                "machine_reviews": [{"grade": 2, "reason": "looks good"}],
                "judgment_confidence": "low",
                "grade": 2,
            }
        ],
    }


def test_blind_candidate_view_strips_leaks_and_machine_fields_in_expert_mode() -> None:
    expert = blind_candidate_view(_sample_pool()["candidates"][0], mode="expert")
    assert "project_accession" not in expert
    assert "machine_reviews" not in expert
    assert "judgment_confidence" not in expert
    assert expert["candidate_id"] == "c-1"

    developer = blind_candidate_view(_sample_pool()["candidates"][0], mode="developer")
    assert "project_accession" not in developer
    assert developer["machine_reviews"]
    assert developer["judgment_confidence"] == "low"


def test_pool_registry_import_list_and_candidates(tmp_path: Path) -> None:
    registry = ExpertPoolRegistry(tmp_path / "expert_review")
    record = registry.import_pool(_sample_pool(), label="pilot-pool")
    assert record["pool_id"]
    assert record["stats"]["candidate_count"] == 1

    listed = registry.list_pools()
    assert len(listed) == 1
    assert listed[0]["label"] == "pilot-pool"

    page = registry.candidates(record["pool_id"], mode="expert")
    assert page is not None
    assert page["total"] == 1
    assert page["candidates"][0]["candidate_id"] == "c-1"
    assert "project_accession" not in page["candidates"][0]
    assert "machine_reviews" not in page["candidates"][0]

    dev_page = registry.candidates(record["pool_id"], mode="developer")
    assert dev_page is not None
    assert dev_page["candidates"][0]["machine_reviews"]


def test_pool_registry_rejects_empty_pool(tmp_path: Path) -> None:
    registry = ExpertPoolRegistry(tmp_path / "expert_review")
    try:
        registry.import_pool({"candidates": []})
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "candidates" in str(exc)
