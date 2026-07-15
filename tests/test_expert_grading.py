from __future__ import annotations

from agent.web.expert_review.grading import (
    append_human_grade,
    apply_human_grades_for_export,
    effective_grade,
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
