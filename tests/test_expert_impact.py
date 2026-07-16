from __future__ import annotations

from agent.web.expert_review.impact import compute_impact


def _pool(grade: int) -> dict:
    return {
        "candidates": [
            {
                "candidate_id": "c1",
                "scenario_id": "human_neuron",
                "variant_id": "clear",
                "grade": grade,
                "human_grades": [{"grade": grade, "notes": "n", "reviewer_id": "r"}],
            }
        ]
    }


def _key() -> dict:
    return {
        "candidates": [
            {
                "candidate_id": "c1",
                "scenario_id": "human_neuron",
                "variant_id": "clear",
                "project_accession": "PXD000001",
            }
        ]
    }


def _runs() -> list[dict]:
    return [
        {
            "scenario_id": "human_neuron",
            "variant_id": "clear",
            "runtime": "openai_agents",
            "budget_tier": "baseline",
            "status": "completed",
            "selected_project_accessions": ["PXD000001"],
            "task_ready_precision": 0.9,
            "file_bundle_completeness": 0.9,
            "evidence_completeness": 0.9,
        },
        {
            "scenario_id": "human_neuron",
            "variant_id": "clear",
            "runtime": "workflow",
            "budget_tier": "baseline",
            "status": "completed",
            "selected_project_accessions": ["PXD000001"],
            "task_ready_precision": 0.5,
            "file_bundle_completeness": 0.5,
            "evidence_completeness": 0.5,
        },
    ]


def test_impact_pairs_each_budget_tier() -> None:
    runs = _runs()
    runs.extend([
        {**runs[0], "budget_tier": "2x", "task_ready_precision": 0.8},
        {**runs[1], "budget_tier": "2x", "task_ready_precision": 0.4},
    ])
    result = compute_impact(
        pool_before=_pool(1),
        pool_after=_pool(3),
        key_payload=_key(),
        runs=runs,
    )
    assert result["pair_after"]["pairs"] == 2


def test_impact_degrades_without_key_and_runs() -> None:
    result = compute_impact(
        pool_before=_pool(1),
        pool_after=_pool(3),
        key_payload=None,
        runs=None,
        grade_before=1,
        grade_after=3,
    )
    assert result["mode"] == "degraded"
    assert "judgment_key" in result["missing"]
    assert result["sentences"]


def test_impact_full_recompute_with_key_and_runs() -> None:
    result = compute_impact(
        pool_before=_pool(1),
        pool_after=_pool(3),
        key_payload=_key(),
        runs=_runs(),
        changed_candidate_id="c1",
        grade_before=1,
        grade_after=3,
    )
    assert result["mode"] == "full"
    assert result["metrics_before"] is not None
    assert result["metrics_after"] is not None
    assert result["metrics_after"]["quality_score"] >= result["metrics_before"]["quality_score"]
    assert any("quality" in sentence for sentence in result["sentences"])
