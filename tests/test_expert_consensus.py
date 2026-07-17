from __future__ import annotations

from typing import Any

import pytest

from agent.web.expert_review.consensus import (
    CandidateGenerationIdentity,
    ExpertConsensusEngine,
    ExpertJudgment,
    ExpertModelProfile,
)


def _profile(
    profile_id: str,
    *,
    provider: str,
    family: str,
    resolved: str | None,
    verification: str = "verified",
) -> ExpertModelProfile:
    return ExpertModelProfile(
        profile_id=profile_id,
        provider=provider,
        requested_model_id=resolved or f"{family}-alias",
        resolved_model_id=resolved,
        model_family=family,
        endpoint_identity=f"{provider}:primary",
        routing_profile_id=profile_id,
        identity_verification=verification,
    )


def _judgment(
    profile: ExpertModelProfile,
    *,
    grade: int | None,
    hard: str = "pass",
    confidence: str = "high",
    investigation: str = "completed",
    evidence_conflict: bool = False,
) -> ExpertJudgment:
    return ExpertJudgment(
        judgment_id=f"judgment-{profile.profile_id}",
        candidate_id="candidate-1",
        profile=profile,
        hard_gate_outcome=hard,
        final_grade=grade,
        confidence=confidence,
        investigation_status=investigation,
        evidence_conflict=evidence_conflict,
        summary=f"{profile.profile_id} result",
    )


def test_panel_selection_excludes_generator_family_and_same_resolved_model() -> None:
    profiles = [
        _profile("self", provider="openai", family="gpt", resolved="gpt-5.4"),
        _profile("a", provider="anthropic", family="claude", resolved="claude-opus-4-8"),
        _profile("alias-a", provider="proxy", family="claude", resolved="claude-opus-4-8"),
        _profile("b", provider="google", family="gemini", resolved="gemini-3-pro"),
        _profile("c", provider="xai", family="grok", resolved="grok-4.1"),
    ]
    engine = ExpertConsensusEngine(lambda _profile, _candidate: None)
    panel = engine.select_panel(
        profiles,
        CandidateGenerationIdentity(model_family="gpt", identity_verification="verified"),
    )

    assert panel.primary_profile_ids == ["a", "b"]
    assert panel.third_profile_id == "c"
    assert panel.formal_independence is True
    assert "self" in panel.excluded_profile_ids
    assert "alias-a" not in panel.primary_profile_ids


def test_panel_excludes_prompt_parser_contributor_family() -> None:
    profiles = [
        _profile("parser-self", provider="openai", family="gpt", resolved="gpt-5.4"),
        _profile("a", provider="anthropic", family="claude", resolved="claude-opus-4-8"),
        _profile("b", provider="google", family="gemini", resolved="gemini-3-pro"),
    ]
    engine = ExpertConsensusEngine(lambda _profile, _candidate: None)
    panel = engine.select_panel(
        profiles,
        CandidateGenerationIdentity.model_validate(
            {
                "model_family": "workflow-discovery",
                "resolved_model_id": "workflow-discovery/v1",
                "identity_verification": "verified",
                "contributors": [
                    {
                        "role": "prompt_parser",
                        "model_family": "gpt",
                        "requested_model_id": "gpt-5.4",
                        "identity_verification": "unverified",
                    }
                ],
            }
        ),
    )

    assert panel.primary_profile_ids == ["a", "b"]
    assert "parser-self" in panel.excluded_profile_ids
    assert panel.formal_independence is False
    assert "unverified_generator_identity" in panel.independence_reasons


def test_panel_requires_two_distinct_model_families() -> None:
    engine = ExpertConsensusEngine(lambda _profile, _candidate: None)
    profiles = [
        _profile("a", provider="anthropic", family="claude", resolved="claude-opus-4-8"),
        _profile("b", provider="proxy", family="claude", resolved="claude-sonnet-4-6"),
    ]
    with pytest.raises(ValueError, match="insufficient_independent_experts"):
        engine.select_panel(profiles, CandidateGenerationIdentity())


def test_verified_two_vote_agreement_forms_model_consensus() -> None:
    a = _profile("a", provider="anthropic", family="claude", resolved="claude-opus-4-8")
    b = _profile("b", provider="google", family="gemini", resolved="gemini-3-pro")
    calls: list[str] = []

    def run(profile: ExpertModelProfile, _candidate: dict[str, Any]) -> ExpertJudgment:
        calls.append(profile.profile_id)
        return _judgment(profile, grade=3)

    engine = ExpertConsensusEngine(run)
    panel = engine.select_panel(
        [a, b],
        CandidateGenerationIdentity(model_family="gpt", identity_verification="verified"),
    )
    result = engine.review_candidate({"candidate_id": "candidate-1"}, panel)

    assert sorted(calls) == ["a", "b"]
    assert result.status == "model_expert_consensus"
    assert result.consensus_grade == 3
    assert result.hard_gate_outcome == "pass"
    assert result.used_third_vote is False


def test_unused_unverified_third_profile_does_not_downgrade_verified_primary_consensus() -> None:
    a = _profile("a", provider="anthropic", family="claude", resolved="claude-opus-4-8")
    b = _profile("b", provider="google", family="gemini", resolved="gemini-3-pro")
    c = _profile("c", provider="proxy", family="grok", resolved=None, verification="unverified")
    engine = ExpertConsensusEngine(lambda profile, _candidate: _judgment(profile, grade=2))
    panel = engine.select_panel(
        [a, b, c],
        CandidateGenerationIdentity(model_family="gpt", identity_verification="verified"),
    )
    result = engine.review_candidate({"candidate_id": "candidate-1"}, panel)

    assert panel.formal_independence is True
    assert result.status == "model_expert_consensus"
    assert result.used_third_vote is False


def test_used_unverified_third_profile_downgrades_result_to_provisional() -> None:
    a = _profile("a", provider="anthropic", family="claude", resolved="claude-opus-4-8")
    b = _profile("b", provider="google", family="gemini", resolved="gemini-3-pro")
    c = _profile("c", provider="proxy", family="grok", resolved=None, verification="unverified")
    grades = {"a": 3, "b": 1, "c": 3}
    engine = ExpertConsensusEngine(lambda profile, _candidate: _judgment(profile, grade=grades[profile.profile_id]))
    panel = engine.select_panel(
        [a, b, c],
        CandidateGenerationIdentity(model_family="gpt", identity_verification="verified"),
    )
    result = engine.review_candidate({"candidate_id": "candidate-1"}, panel)

    assert result.used_third_vote is True
    assert result.status == "model_expert_provisional"
    assert result.formal_independence is False
    assert "unverified_third_expert_identity" in result.trigger_reasons


def test_unverified_identity_can_only_form_provisional_result() -> None:
    a = _profile(
        "a",
        provider="anthropic",
        family="claude",
        resolved=None,
        verification="unverified",
    )
    b = _profile("b", provider="google", family="gemini", resolved="gemini-3-pro")
    engine = ExpertConsensusEngine(lambda profile, _candidate: _judgment(profile, grade=2))
    panel = engine.select_panel([a, b], CandidateGenerationIdentity())
    result = engine.review_candidate({"candidate_id": "candidate-1"}, panel)

    assert panel.formal_independence is False
    assert result.status == "model_expert_provisional"
    assert result.consensus_grade == 2
    assert "unverified_expert_identity" in result.trigger_reasons
    assert "unverified_generator_identity" in result.trigger_reasons


def test_runtime_verified_judgment_profiles_can_upgrade_attested_panel_to_consensus() -> None:
    a = _profile(
        "a",
        provider="anthropic",
        family="claude",
        resolved="claude-opus-4-8",
        verification="provider_attested",
    )
    b = _profile(
        "b",
        provider="google",
        family="gemini",
        resolved="gemini-3-pro",
        verification="provider_attested",
    )

    def run(profile: ExpertModelProfile, _candidate: dict[str, Any]) -> ExpertJudgment:
        verified = profile.model_copy(update={"identity_verification": "verified"})
        return _judgment(verified, grade=2)

    engine = ExpertConsensusEngine(run)
    panel = engine.select_panel(
        [a, b],
        CandidateGenerationIdentity(model_family="workflow-discovery", identity_verification="verified"),
    )
    result = engine.review_candidate({"candidate_id": "candidate-1"}, panel)

    assert panel.formal_independence is False
    assert result.formal_independence is True
    assert result.status == "model_expert_consensus"


def test_large_grade_disagreement_triggers_third_vote_and_majority() -> None:
    a = _profile("a", provider="anthropic", family="claude", resolved="claude-opus-4-8")
    b = _profile("b", provider="google", family="gemini", resolved="gemini-3-pro")
    c = _profile("c", provider="xai", family="grok", resolved="grok-4.1")
    grades = {"a": 3, "b": 1, "c": 3}
    engine = ExpertConsensusEngine(lambda profile, _candidate: _judgment(profile, grade=grades[profile.profile_id]))
    panel = engine.select_panel(
        [a, b, c],
        CandidateGenerationIdentity(model_family="gpt", identity_verification="verified"),
    )
    result = engine.review_candidate({"candidate_id": "candidate-1"}, panel)

    assert result.used_third_vote is True
    assert len(result.judgments) == 3
    assert result.status == "model_expert_consensus"
    assert result.consensus_grade == 3
    assert "grade_disagreement" in result.trigger_reasons


def test_low_confidence_without_third_expert_needs_adjudication() -> None:
    a = _profile("a", provider="anthropic", family="claude", resolved="claude-opus-4-8")
    b = _profile("b", provider="google", family="gemini", resolved="gemini-3-pro")

    def run(profile: ExpertModelProfile, _candidate: dict[str, Any]) -> ExpertJudgment:
        return _judgment(profile, grade=2, confidence="low" if profile.profile_id == "a" else "high")

    engine = ExpertConsensusEngine(run)
    panel = engine.select_panel(
        [a, b],
        CandidateGenerationIdentity(model_family="gpt", identity_verification="verified"),
    )
    result = engine.review_candidate({"candidate_id": "candidate-1"}, panel)

    assert result.status == "needs_adjudication"
    assert result.consensus_grade is None
    assert "low_confidence" in result.trigger_reasons
    assert "third_expert_unavailable" in result.trigger_reasons


def test_hard_fail_cannot_be_compensated_by_high_grade() -> None:
    a = _profile("a", provider="anthropic", family="claude", resolved="claude-opus-4-8")
    b = _profile("b", provider="google", family="gemini", resolved="gemini-3-pro")
    engine = ExpertConsensusEngine(lambda profile, _candidate: _judgment(profile, grade=3, hard="fail"))
    panel = engine.select_panel(
        [a, b],
        CandidateGenerationIdentity(model_family="gpt", identity_verification="verified"),
    )
    result = engine.review_candidate({"candidate_id": "candidate-1"}, panel)

    assert result.status == "model_expert_consensus"
    assert result.hard_gate_outcome == "fail"
    assert result.consensus_grade == 0


def test_three_vote_hard_gate_uses_majority_without_soft_compensation() -> None:
    a = _profile("a", provider="anthropic", family="claude", resolved="claude-opus-4-8")
    b = _profile("b", provider="google", family="gemini", resolved="gemini-3-pro")
    c = _profile("c", provider="xai", family="grok", resolved="grok-4.1")
    hard = {"a": "fail", "b": "pass", "c": "pass"}
    engine = ExpertConsensusEngine(
        lambda profile, _candidate: _judgment(profile, grade=3, hard=hard[profile.profile_id])
    )
    panel = engine.select_panel(
        [a, b, c],
        CandidateGenerationIdentity(model_family="gpt", identity_verification="verified"),
    )
    result = engine.review_candidate({"candidate_id": "candidate-1"}, panel)

    assert result.used_third_vote is True
    assert result.status == "model_expert_consensus"
    assert result.hard_gate_outcome == "pass"
    assert result.consensus_grade == 3
    assert "hard_gate_conflict" in result.trigger_reasons
