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


def test_blind_candidate_view_strips_leaks_and_prior_judgments_in_expert_mode() -> None:
    candidate = {
        **_sample_pool()["candidates"][0],
        "review_notes": "machine reason",
        "reviewer_id": "llm:model-a",
        "human_grades": [{"grade": 3, "reviewer_id": "other-reviewer", "notes": "prior"}],
        "machine_review_runs": [{"model": "model-a", "grade": 2}],
    }
    expert = blind_candidate_view(candidate, mode="expert")
    assert "project_accession" not in expert
    assert "machine_reviews" not in expert
    assert "machine_review_runs" not in expert
    assert "judgment_confidence" not in expert
    assert "grade" not in expert
    assert "review_notes" not in expert
    assert "reviewer_id" not in expert
    assert "human_grades" not in expert
    assert expert["candidate_id"] == "c-1"

    developer = blind_candidate_view(candidate, mode="developer")
    assert "project_accession" not in developer
    assert developer["machine_reviews"]
    assert developer["judgment_confidence"] == "low"


def test_expert_projection_restores_only_current_reviewer_and_strips_nested_leaks() -> None:
    candidate = {
        "candidate_id": "c1",
        "metadata": {"source_system": "private", "safe": "visible"},
        "human_grades": [
            {"grade": 1, "notes": "other", "reviewer_id": "other"},
            {"grade": 3, "notes": "mine", "reviewer_id": "mine"},
        ],
    }
    projected = blind_candidate_view(candidate, mode="expert", reviewer_id="mine")
    assert projected["grade"] == 3
    assert projected["review_notes"] == "mine"
    assert projected["reviewer_id"] == "mine"
    assert projected["metadata"] == {"safe": "visible"}
    other = blind_candidate_view(candidate, mode="expert", reviewer_id="new")
    assert "grade" not in other
    assert "review_notes" not in other


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


def test_registry_import_generated_pool_persists_private_key(tmp_path: Path) -> None:
    registry = ExpertPoolRegistry(tmp_path / "expert_review")
    pool = {
        "schema_version": "discovery-judgment-pool-blinded/v2",
        "candidates": [{"candidate_id": "c1", "project_title": "Visible"}],
    }
    private_key = {
        "schema_version": "discovery-judgment-key/v1",
        "candidates": [{"candidate_id": "c1", "project_accession": "PXDSECRET"}],
    }
    record = registry.import_generated_pool(pool, private_key=private_key, label="Prompt pool")
    pool_dir = registry.root / record["pool_id"]
    assert (pool_dir / "pool.blinded.json").is_file()
    assert (pool_dir / "private" / "judgment.key.json").is_file()
    assert "private" not in str(record)
    page = registry.candidates(record["pool_id"], mode="expert")
    assert page is not None
    assert "PXDSECRET" not in str(page)


def test_pool_registry_rejects_duplicate_candidate_ids(tmp_path: Path) -> None:
    registry = ExpertPoolRegistry(tmp_path / "expert_review")
    pool = _sample_pool()
    pool["candidates"].append(dict(pool["candidates"][0]))
    try:
        registry.import_pool(pool)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "duplicate_candidate_id" in str(exc)


def test_pool_registry_rejects_empty_pool(tmp_path: Path) -> None:
    registry = ExpertPoolRegistry(tmp_path / "expert_review")
    try:
        registry.import_pool({"candidates": []})
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "candidates" in str(exc)
