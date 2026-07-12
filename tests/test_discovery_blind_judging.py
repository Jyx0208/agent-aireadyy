from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.discovery.blind_judging import judge_blinded_pool, judge_candidate


def _candidate() -> dict[str, Any]:
    return {
        "candidate_id": "candidate_abc",
        "visible_prompt": "Find human DDA data.",
        "visible_hard_constraint_fields": ["species", "acquisition_mode"],
        "project_title": "Human DDA proteome",
        "species": ["Homo sapiens"],
        "acquisition_mode": "dda",
        "project_accession": "PXD_SECRET",
        "runtime": "openai_agents",
    }


def test_two_agreeing_votes_stop_early() -> None:
    calls: list[str] = []

    def judge(system: str, user: str) -> Mapping[str, Any]:
        calls.append(user)
        return {"grade": 3, "reason": "Direct match."}

    result = judge_candidate(_candidate(), judge)

    assert result.grade == 3
    assert result.confidence == "medium"
    assert len(result.votes) == 2
    assert len(calls) == 2
    assert all("PXD_SECRET" not in call for call in calls)
    assert all("openai_agents" not in call for call in calls)


def test_disagreement_expands_to_five_and_uses_median() -> None:
    grades = iter([3, 1, 2, 3, 2])

    def judge(_system: str, _user: str) -> Mapping[str, Any]:
        return {"grade": next(grades), "reason": "Observed evidence."}

    result = judge_candidate(_candidate(), judge)

    assert result.grade == 2
    assert result.confidence == "low"
    assert len(result.votes) == 5


def test_four_matching_votes_are_high_confidence() -> None:
    grades = iter([3, 2, 3, 3, 3])

    def judge(_system: str, _user: str) -> Mapping[str, Any]:
        return {"grade": next(grades), "reason": "Strong task match."}

    result = judge_candidate(_candidate(), judge)

    assert result.grade == 3
    assert result.confidence == "high"


def test_reviewed_pool_records_provisional_source_and_votes() -> None:
    def judge(_system: str, _user: str) -> Mapping[str, Any]:
        return {"grade": 2, "reason": "Useful with a metadata gap."}

    reviewed = judge_blinded_pool(
        {"candidates": [_candidate()]},
        judge,
        model_name="judge-model",
    )

    assert reviewed["judgment_source"] == "provisional_same_family"
    assert reviewed["review_summary"]["formal_replacement_evidence"] is False
    assert reviewed["candidates"][0]["grade"] == 2
    assert len(reviewed["candidates"][0]["machine_reviews"]) == 2
    assert reviewed["review_summary"]["two_vote_candidates"] == 1
    assert reviewed["review_summary"]["five_vote_candidates"] == 0


def test_pool_judging_preserves_order_with_parallel_workers() -> None:
    candidates = [
        {**_candidate(), "candidate_id": f"candidate_{index}"}
        for index in range(4)
    ]

    def judge(_system: str, _user: str) -> Mapping[str, Any]:
        return {"grade": 2, "reason": "Relevant."}

    reviewed = judge_blinded_pool(
        {"candidates": candidates},
        judge,
        model_name="judge-model",
        workers=2,
    )

    assert [item["candidate_id"] for item in reviewed["candidates"]] == [
        f"candidate_{index}" for index in range(4)
    ]


def test_judge_accepts_single_evidence_string_from_compatible_models() -> None:
    def judge(_system: str, _user: str) -> Mapping[str, Any]:
        return {
            "grade": 2,
            "reason": "Relevant.",
            "supporting_evidence": "High-resolution raw spectra are present.",
            "constraint_conflicts": "",
        }

    result = judge_candidate(_candidate(), judge)

    assert result.votes[0].supporting_evidence == [
        "High-resolution raw spectra are present."
    ]
    assert result.votes[0].constraint_conflicts == []


def test_pool_judging_resumes_matching_candidate_reviews() -> None:
    calls = 0

    def judge(_system: str, _user: str) -> Mapping[str, Any]:
        nonlocal calls
        calls += 1
        return {"grade": 3, "reason": "Direct match."}

    first = judge_blinded_pool(
        {"candidates": [_candidate()]},
        judge,
        model_name="judge-model",
    )
    existing = {"candidate_abc": first["candidates"][0]}
    second = judge_blinded_pool(
        {"candidates": [_candidate()]},
        judge,
        model_name="judge-model",
        existing_reviews=existing,
    )

    assert calls == 2
    assert second["candidates"] == first["candidates"]
