from __future__ import annotations

from agent.web.expert_review.grading import (
    append_human_grade,
    apply_human_grades_for_export,
    effective_grade,
    merge_model_expert_results,
    merge_machine_reviews,
    queue_bucket,
)


def test_effective_grade_prefers_latest_human() -> None:
    candidate = {
        "grade": 1,
        "machine_reviews": [{"grade": 1, "reason": "m"}],
        "human_grades": [
            {"grade": 2, "notes": "a"},
            {"grade": 3, "notes": "b"},
        ],
    }
    assert effective_grade(candidate) == 3


def test_append_human_grade_preserves_machine_reviews() -> None:
    candidate = {
        "candidate_id": "c1",
        "grade": 1,
        "machine_reviews": [{"grade": 1, "reason": "m", "supporting_evidence": [], "constraint_conflicts": []}],
    }
    updated = append_human_grade(candidate, grade=3, notes="ok", reviewer_id="r1")
    assert updated["grade"] == 3
    assert len(updated["human_grades"]) == 1
    assert updated["machine_reviews"]
    again = append_human_grade(updated, grade=2, notes="revise", reviewer_id="r1")
    assert len(again["human_grades"]) == 2
    assert again["grade"] == 2
    assert again["machine_reviews"]


def test_clearing_human_grade_restores_machine_consensus() -> None:
    candidate = {
        "candidate_id": "c1",
        "grade": 3,
        "review_notes": "human",
        "reviewer_id": "reviewer",
        "human_grades": [{"grade": 3, "notes": "human", "reviewer_id": "reviewer"}],
        "machine_reviews": [{"grade": 1}, {"grade": 2}, {"grade": 2}],
    }
    cleared = append_human_grade(candidate, grade=None, reviewer_id="reviewer", clear=True)
    assert cleared["grade"] == 2
    assert effective_grade(cleared) == 2


def test_export_sets_human_verified_and_effective_grades() -> None:
    pool = {
        "candidates": [
            {
                "candidate_id": "c1",
                "grade": 1,
                "machine_reviews": [{"grade": 1, "reason": "m"}],
                "human_grades": [{"grade": 3, "notes": "h", "reviewer_id": "r"}],
            }
        ]
    }
    exported = apply_human_grades_for_export(pool)
    assert exported["judgment_source"] == "human_verified"
    assert exported["candidates"][0]["grade"] == 3


def test_export_does_not_promote_machine_only_candidates_to_human_verified() -> None:
    pool = {
        "judgment_source": "provisional_same_family",
        "candidates": [
            {
                "candidate_id": "human",
                "grade": 3,
                "human_grades": [{"grade": 3, "notes": "h", "reviewer_id": "r"}],
            },
            {
                "candidate_id": "machine",
                "grade": 1,
                "machine_reviews": [{"grade": 1, "reason": "m"}],
                "reviewer_id": "llm:model-a",
            },
        ],
    }
    exported = apply_human_grades_for_export(pool)
    assert exported["judgment_source"] == "human_verified"
    by_id = {item["candidate_id"]: item for item in exported["candidates"]}
    assert by_id["human"]["grade"] == 3
    assert by_id["machine"]["grade"] is None
    assert "reviewer_id" not in by_id["machine"]


def test_merge_machine_reviews_accumulates_model_runs() -> None:
    existing = {
        "candidates": [
            {
                "candidate_id": "c1",
                "grade": 1,
                "machine_reviews": [{"grade": 1, "reason": "a"}],
                "machine_review_runs": [
                    {
                        "job_id": "job-a",
                        "profile_id": "profile-a",
                        "model": "model-a",
                        "grade": 1,
                        "confidence": "medium",
                        "votes": [{"grade": 1, "reason": "a"}],
                    }
                ],
            }
        ]
    }
    machine = {
        "review_model": "model-b",
        "candidates": [
            {
                "candidate_id": "c1",
                "grade": 3,
                "machine_reviews": [{"grade": 3, "reason": "b"}],
                "judgment_confidence": "medium",
            }
        ],
    }
    merged = merge_machine_reviews(
        existing,
        machine,
        job_id="job-b",
        profile_id="profile-b",
        model="model-b",
    )
    item = merged["candidates"][0]
    assert [run["model"] for run in item["machine_review_runs"]] == ["model-a", "model-b"]
    assert item["grade"] == 3
    assert item["machine_reviews"][0]["reason"] == "b"


def test_merge_machine_reviews_replaces_prior_run_from_same_model() -> None:
    existing = {
        "candidates": [
            {
                "candidate_id": "c1",
                "grade": 1,
                "machine_review_runs": [
                    {
                        "job_id": "old-job",
                        "profile_id": "old-profile",
                        "model": "gpt-5.6-sol",
                        "grade": 1,
                        "votes": [{"grade": 1, "reason": "old"}],
                    }
                ],
            }
        ]
    }
    machine = {
        "review_model": "GPT-5.6-SOL",
        "candidates": [
            {
                "candidate_id": "c1",
                "grade": 3,
                "machine_reviews": [{"grade": 3, "reason": "new"}],
            }
        ],
    }

    merged = merge_machine_reviews(
        existing,
        machine,
        job_id="new-job",
        profile_id="new-profile",
        model="GPT-5.6-SOL",
    )

    runs = merged["candidates"][0]["machine_review_runs"]
    assert len(runs) == 1
    assert runs[0]["job_id"] == "new-job"
    assert runs[0]["grade"] == 3


def test_export_strips_blind_identity_and_machine_fields() -> None:
    pool = {
        "candidates": [
            {
                "candidate_id": "c1",
                "project_accession": "PXDSECRET",
                "runtime": "openai_agents",
                "source_system": "private-source",
                "grade": 3,
                "human_grades": [{"grade": 3, "notes": "h", "reviewer_id": "r"}],
                "machine_reviews": [{"grade": 1, "reason": "m"}],
            }
        ]
    }
    candidate = apply_human_grades_for_export(pool)["candidates"][0]
    for hidden in ("project_accession", "runtime", "source_system", "machine_reviews", "human_grades"):
        assert hidden not in candidate


def test_export_is_scoped_to_requested_reviewer() -> None:
    pool = {
        "candidates": [
            {
                "candidate_id": "c1",
                "human_grades": [
                    {"grade": 1, "notes": "other", "reviewer_id": "other"},
                    {"grade": 3, "notes": "mine", "reviewer_id": "mine"},
                ],
            }
        ]
    }
    mine = apply_human_grades_for_export(pool, reviewer_id="mine")["candidates"][0]
    other = apply_human_grades_for_export(pool, reviewer_id="other")["candidates"][0]
    assert (mine["grade"], mine["review_notes"]) == (3, "mine")
    assert (other["grade"], other["review_notes"]) == (1, "other")


def test_queue_bucket_developer_paths() -> None:
    ungraded = {"candidate_id": "a"}
    assert queue_bucket(ungraded, mode="expert") == "ungraded"
    low = {
        "grade": 2,
        "judgment_confidence": "low",
        "machine_reviews": [{"grade": 2, "reason": "x"}],
    }
    assert queue_bucket(low, mode="developer") == "low_confidence"
    disagree = {
        "grade": 2,
        "machine_reviews": [
            {"grade": 0, "reason": "a", "constraint_conflicts": []},
            {"grade": 3, "reason": "b", "constraint_conflicts": []},
        ],
    }
    assert queue_bucket(disagree, mode="developer") == "vote_disagreement"


def test_merge_machine_reviews_keeps_humans() -> None:
    existing = {
        "candidates": [
            {
                "candidate_id": "c1",
                "grade": 3,
                "human_grades": [{"grade": 3, "notes": "h"}],
                "review_notes": "h",
            }
        ]
    }
    machine = {
        "judgment_source": "provisional_independent_model",
        "candidates": [
            {
                "candidate_id": "c1",
                "grade": 1,
                "machine_reviews": [{"grade": 1, "reason": "m"}],
                "judgment_confidence": "low",
            }
        ],
    }
    merged = merge_machine_reviews(existing, machine)
    item = merged["candidates"][0]
    assert item["human_grades"]
    assert item["grade"] == 3
    assert item["machine_reviews"]


def test_merge_model_expert_results_is_idempotent_and_never_creates_human_grades() -> None:
    existing = {
        "schema_version": "discovery-judgment-pool-blinded/v2",
        "candidates": [{"candidate_id": "c1", "project_title": "Visible"}],
    }
    result = {
        "candidate_id": "c1",
        "status": "model_expert_consensus",
        "hard_gate_outcome": "pass",
        "consensus_grade": 2,
        "trigger_reasons": [],
        "used_third_vote": False,
        "formal_independence": True,
        "policy_version": "deterministic-model-consensus/v1",
        "judgments": [
            {
                "judgment_id": "j1",
                "candidate_id": "c1",
                "profile": {"profile_id": "a", "model_family": "claude"},
                "hard_gate_outcome": "pass",
                "final_grade": 2,
                "confidence": "high",
            },
            {
                "judgment_id": "j2",
                "candidate_id": "c1",
                "profile": {"profile_id": "b", "model_family": "gemini"},
                "hard_gate_outcome": "pass",
                "final_grade": 2,
                "confidence": "high",
            },
        ],
    }

    merged = merge_model_expert_results(existing, {"c1": result}, job_id="consensus-job")
    repeated = merge_model_expert_results(merged, {"c1": result}, job_id="consensus-job")
    candidate = repeated["candidates"][0]

    assert candidate["grade"] == 2
    assert "human_grades" not in candidate
    assert "reviewer_id" not in candidate
    assert [item["judgment_id"] for item in candidate["model_expert_judgments"]] == ["j1", "j2"]
    assert candidate["model_expert_consensus"]["status"] == "model_expert_consensus"
    assert candidate["model_expert_consensus"]["job_id"] == "consensus-job"
    assert repeated["judgment_source"] == "model_expert_consensus"


def test_merge_model_expert_results_preserves_human_effective_grade() -> None:
    existing = {
        "schema_version": "discovery-judgment-pool-reviewed/v2",
        "judgment_source": "human_verified",
        "candidates": [
            {
                "candidate_id": "c1",
                "grade": 3,
                "human_grades": [{"grade": 3, "reviewer_id": "human"}],
            }
        ],
    }
    result = {
        "candidate_id": "c1",
        "status": "needs_adjudication",
        "hard_gate_outcome": "unknown",
        "consensus_grade": None,
        "trigger_reasons": ["hard_gate_conflict"],
        "used_third_vote": True,
        "formal_independence": True,
        "policy_version": "deterministic-model-consensus/v1",
        "judgments": [],
    }

    merged = merge_model_expert_results(existing, {"c1": result}, job_id="consensus-job")
    candidate = merged["candidates"][0]

    assert candidate["grade"] == 3
    assert candidate["human_grades"] == [{"grade": 3, "reviewer_id": "human"}]
    assert candidate["model_expert_consensus"]["status"] == "needs_adjudication"
    assert merged["judgment_source"] == "human_verified"


def test_human_export_without_human_grades_never_claims_human_verified() -> None:
    pool = {
        "schema_version": "discovery-judgment-pool-reviewed/v2",
        "judgment_source": "model_expert_consensus",
        "candidates": [
            {
                "candidate_id": "c1",
                "grade": 3,
                "model_expert_judgments": [{"judgment_id": "j1"}],
                "model_expert_consensus": {"status": "model_expert_consensus"},
            }
        ],
    }

    exported = apply_human_grades_for_export(pool)
    candidate = exported["candidates"][0]

    assert exported["judgment_source"] == "model_expert_consensus"
    assert exported["review_summary"]["graded_candidates"] == 0
    assert candidate["grade"] is None
    assert "model_expert_judgments" not in candidate
    assert "model_expert_consensus" not in candidate
