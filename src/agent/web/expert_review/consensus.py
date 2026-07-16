from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from typing import Any, Literal

from pydantic import Field

from agent.models import JsonModel


IdentityVerification = Literal["verified", "provider_attested", "unverified"]
HardGateOutcome = Literal["pass", "fail", "unknown"]
JudgmentConfidence = Literal["high", "medium", "low"]
InvestigationStatus = Literal[
    "not_needed",
    "completed",
    "partial",
    "failed",
    "insufficient_evidence",
]
ConsensusStatus = Literal[
    "model_expert_provisional",
    "model_expert_consensus",
    "needs_adjudication",
]


class ExpertModelProfile(JsonModel):
    profile_id: str
    provider: str
    requested_model_id: str
    resolved_model_id: str | None = None
    model_family: str
    endpoint_identity: str
    routing_profile_id: str
    identity_verification: IdentityVerification = "unverified"
    enabled: bool = True
    capabilities: list[str] = Field(default_factory=list)
    config_version: str = "expert-model-profile/v1"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ExpertModelProfile":
        profile_id = str(value.get("profile_id") or value.get("id") or "").strip()
        capabilities = value.get("capabilities") or []
        if isinstance(capabilities, str):
            capabilities = [capabilities]
        return cls(
            profile_id=profile_id,
            provider=str(value.get("provider") or "openai_compatible"),
            requested_model_id=str(value.get("requested_model_id") or value.get("model") or ""),
            resolved_model_id=str(value.get("resolved_model_id") or "") or None,
            model_family=str(value.get("model_family") or value.get("model") or ""),
            endpoint_identity=str(value.get("endpoint_identity") or value.get("base_url") or ""),
            routing_profile_id=str(value.get("routing_profile_id") or profile_id),
            identity_verification=str(value.get("identity_verification") or "unverified"),
            enabled=_as_bool(value.get("enabled"), default=True),
            capabilities=[str(item) for item in capabilities],
            config_version=str(value.get("config_version") or "expert-model-profile/v1"),
        )


class CandidateGenerationIdentity(JsonModel):
    provider: str | None = None
    requested_model_id: str | None = None
    resolved_model_id: str | None = None
    model_family: str | None = None
    runtime: str | None = None
    endpoint_identity: str | None = None
    identity_verification: IdentityVerification = "unverified"


class ExpertJudgment(JsonModel):
    judgment_id: str
    candidate_id: str
    profile: ExpertModelProfile
    hard_gate_outcome: HardGateOutcome
    final_grade: int | None = Field(default=None, ge=0, le=3)
    confidence: JudgmentConfidence
    investigation_status: InvestigationStatus = "not_needed"
    evidence_conflict: bool = False
    summary: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)


class ExpertPanel(JsonModel):
    primary_profiles: list[ExpertModelProfile]
    third_profile: ExpertModelProfile | None = None
    formal_independence: bool
    independence_reasons: list[str] = Field(default_factory=list)
    excluded_profile_ids: list[str] = Field(default_factory=list)
    generator_identity: CandidateGenerationIdentity = Field(default_factory=CandidateGenerationIdentity)
    policy_version: str = "heterogeneous-expert-selection/v1"

    @property
    def primary_profile_ids(self) -> list[str]:
        return [profile.profile_id for profile in self.primary_profiles]

    @property
    def third_profile_id(self) -> str | None:
        return self.third_profile.profile_id if self.third_profile is not None else None


class CandidateConsensusResult(JsonModel):
    candidate_id: str
    status: ConsensusStatus
    hard_gate_outcome: HardGateOutcome
    consensus_grade: int | None = Field(default=None, ge=0, le=3)
    judgments: list[ExpertJudgment]
    trigger_reasons: list[str] = Field(default_factory=list)
    used_third_vote: bool = False
    formal_independence: bool = False
    policy_version: str = "deterministic-model-consensus/v1"


RunExpert = Callable[[ExpertModelProfile, Mapping[str, Any]], ExpertJudgment]


class ExpertConsensusEngine:
    """Select heterogeneous experts and deterministically aggregate their votes."""

    def __init__(self, run_expert: RunExpert) -> None:
        self.run_expert = run_expert

    def select_panel(
        self,
        profiles: Sequence[ExpertModelProfile],
        generator: CandidateGenerationIdentity,
    ) -> ExpertPanel:
        enabled = sorted(
            (profile for profile in profiles if profile.enabled),
            key=lambda profile: profile.profile_id,
        )
        excluded: list[str] = []
        eligible: list[ExpertModelProfile] = []
        generator_family = _normalized(generator.model_family)
        generator_resolved = _normalized(generator.resolved_model_id)
        for profile in enabled:
            if (
                (generator_family and _normalized(profile.model_family) == generator_family)
                or (
                    generator_resolved
                    and _normalized(profile.resolved_model_id) == generator_resolved
                )
            ):
                excluded.append(profile.profile_id)
                continue
            eligible.append(profile)

        pairs = [
            pair
            for pair in combinations(eligible, 2)
            if _normalized(pair[0].model_family) != _normalized(pair[1].model_family)
            and not _same_resolved_model(pair[0], pair[1])
        ]
        if not pairs:
            raise ValueError("insufficient_independent_experts")
        primary = sorted(pairs, key=_pair_sort_key)[0]
        remaining = [
            profile
            for profile in eligible
            if profile.profile_id not in {primary[0].profile_id, primary[1].profile_id}
            and _normalized(profile.model_family)
            not in {_normalized(primary[0].model_family), _normalized(primary[1].model_family)}
            and not any(_same_resolved_model(profile, selected) for selected in primary)
        ]
        third = sorted(remaining, key=lambda profile: _third_sort_key(profile, primary))[0] if remaining else None

        reasons: list[str] = []
        if generator.identity_verification != "verified" or not generator_family:
            reasons.append("unverified_generator_identity")
        if any(profile.identity_verification != "verified" or not profile.resolved_model_id for profile in primary):
            reasons.append("unverified_expert_identity")
        formal = not reasons
        return ExpertPanel(
            primary_profiles=list(primary),
            third_profile=third,
            formal_independence=formal,
            independence_reasons=reasons,
            excluded_profile_ids=sorted(excluded),
            generator_identity=generator,
        )

    def review_candidate(
        self,
        candidate: Mapping[str, Any],
        panel: ExpertPanel,
    ) -> CandidateConsensusResult:
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        if not candidate_id:
            raise ValueError("candidate_id_required")
        primary_judgments = self._run_primary(panel.primary_profiles, candidate)
        _validate_judgments(candidate_id, primary_judgments)
        triggers = _two_vote_triggers(primary_judgments)
        if triggers:
            if panel.third_profile is None:
                return CandidateConsensusResult(
                    candidate_id=candidate_id,
                    status="needs_adjudication",
                    hard_gate_outcome="unknown",
                    consensus_grade=None,
                    judgments=primary_judgments,
                    trigger_reasons=_unique([*triggers, "third_expert_unavailable", *panel.independence_reasons]),
                    used_third_vote=False,
                    formal_independence=panel.formal_independence,
                )
            third = self.run_expert(panel.third_profile, dict(candidate))
            _validate_judgments(candidate_id, [third])
            return _aggregate_three(
                candidate_id,
                [*primary_judgments, third],
                panel=panel,
                initial_triggers=triggers,
            )
        return _aggregate_two(candidate_id, primary_judgments, panel=panel)

    def _run_primary(
        self,
        profiles: Sequence[ExpertModelProfile],
        candidate: Mapping[str, Any],
    ) -> list[ExpertJudgment]:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {
                profile.profile_id: executor.submit(self.run_expert, profile, dict(candidate))
                for profile in profiles
            }
            return [futures[profile.profile_id].result() for profile in profiles]


def _pair_sort_key(pair: tuple[ExpertModelProfile, ExpertModelProfile]) -> tuple[Any, ...]:
    verified = all(profile.identity_verification == "verified" and profile.resolved_model_id for profile in pair)
    cross_provider = _normalized(pair[0].provider) != _normalized(pair[1].provider)
    cross_endpoint = _normalized(pair[0].endpoint_identity) != _normalized(pair[1].endpoint_identity)
    return (
        -int(verified),
        -int(cross_provider),
        -int(cross_endpoint),
        pair[0].profile_id,
        pair[1].profile_id,
    )


def _third_sort_key(
    profile: ExpertModelProfile,
    primary: tuple[ExpertModelProfile, ExpertModelProfile],
) -> tuple[Any, ...]:
    verified = profile.identity_verification == "verified" and bool(profile.resolved_model_id)
    provider_novel = _normalized(profile.provider) not in {_normalized(item.provider) for item in primary}
    endpoint_novel = _normalized(profile.endpoint_identity) not in {
        _normalized(item.endpoint_identity) for item in primary
    }
    return (-int(verified), -int(provider_novel), -int(endpoint_novel), profile.profile_id)


def _same_resolved_model(left: ExpertModelProfile, right: ExpertModelProfile) -> bool:
    left_id = _normalized(left.resolved_model_id)
    right_id = _normalized(right.resolved_model_id)
    return bool(left_id and right_id and left_id == right_id)


def _two_vote_triggers(judgments: Sequence[ExpertJudgment]) -> list[str]:
    reasons: list[str] = []
    hard = {judgment.hard_gate_outcome for judgment in judgments}
    if len(hard) > 1:
        reasons.append("hard_gate_conflict")
    if "unknown" in hard:
        reasons.append("hard_gate_unknown")
    if any(judgment.final_grade is None or judgment.investigation_status == "insufficient_evidence" for judgment in judgments):
        reasons.append("insufficient_evidence")
    if any(judgment.confidence == "low" for judgment in judgments):
        reasons.append("low_confidence")
    if any(judgment.evidence_conflict for judgment in judgments):
        reasons.append("evidence_conflict")
    grades = [judgment.final_grade for judgment in judgments if judgment.final_grade is not None]
    if len(grades) == 2 and max(grades) - min(grades) > 1:
        reasons.append("grade_disagreement")
    return _unique(reasons)


def _aggregate_two(
    candidate_id: str,
    judgments: list[ExpertJudgment],
    *,
    panel: ExpertPanel,
) -> CandidateConsensusResult:
    hard = judgments[0].hard_gate_outcome
    grade = 0 if hard == "fail" else min(int(judgment.final_grade) for judgment in judgments if judgment.final_grade is not None)
    formal, independence_reasons = _judgment_independence(judgments, panel.generator_identity)
    return CandidateConsensusResult(
        candidate_id=candidate_id,
        status="model_expert_consensus" if formal else "model_expert_provisional",
        hard_gate_outcome=hard,
        consensus_grade=grade,
        judgments=judgments,
        trigger_reasons=independence_reasons,
        used_third_vote=False,
        formal_independence=formal,
    )


def _aggregate_three(
    candidate_id: str,
    judgments: list[ExpertJudgment],
    *,
    panel: ExpertPanel,
    initial_triggers: list[str],
) -> CandidateConsensusResult:
    reliable = [
        judgment
        for judgment in judgments
        if judgment.confidence != "low"
        and judgment.investigation_status != "insufficient_evidence"
        and not judgment.evidence_conflict
        and judgment.final_grade is not None
        and judgment.hard_gate_outcome != "unknown"
    ]
    formal, independence_reasons = _judgment_independence(judgments, panel.generator_identity)
    reasons = _unique([*initial_triggers, *independence_reasons])
    if len(reliable) < 2:
        return _unresolved(candidate_id, judgments, panel, [*reasons, "insufficient_reliable_votes"])
    hard_counts = Counter(judgment.hard_gate_outcome for judgment in reliable)
    hard_majority = [outcome for outcome, count in hard_counts.items() if count >= 2]
    if not hard_majority:
        return _unresolved(candidate_id, judgments, panel, reasons, hard_gate="unknown", formal=formal)
    hard_outcome = hard_majority[0]
    majority_judgments = [
        judgment for judgment in reliable if judgment.hard_gate_outcome == hard_outcome
    ]
    if hard_outcome == "fail":
        grade = 0
    else:
        grades = [
            int(judgment.final_grade)
            for judgment in majority_judgments
            if judgment.final_grade is not None
        ]
        counts = Counter(grades)
        majority = sorted(grade for grade, count in counts.items() if count >= 2)
        if majority:
            grade = majority[-1]
        elif len(grades) == 2 and max(grades) - min(grades) <= 1:
            grade = min(grades)
        else:
            return _unresolved(
                candidate_id,
                judgments,
                panel,
                [*reasons, "grade_consensus_unresolved"],
                formal=formal,
            )
    return CandidateConsensusResult(
        candidate_id=candidate_id,
        status="model_expert_consensus" if formal else "model_expert_provisional",
        hard_gate_outcome=hard_outcome,
        consensus_grade=grade,
        judgments=judgments,
        trigger_reasons=reasons,
        used_third_vote=True,
        formal_independence=formal,
    )


def _unresolved(
    candidate_id: str,
    judgments: list[ExpertJudgment],
    panel: ExpertPanel,
    reasons: list[str],
    *,
    hard_gate: HardGateOutcome = "unknown",
    formal: bool | None = None,
) -> CandidateConsensusResult:
    return CandidateConsensusResult(
        candidate_id=candidate_id,
        status="needs_adjudication",
        hard_gate_outcome=hard_gate,
        consensus_grade=None,
        judgments=judgments,
        trigger_reasons=_unique(reasons),
        used_third_vote=True,
        formal_independence=panel.formal_independence if formal is None else formal,
    )


def _validate_judgments(candidate_id: str, judgments: Sequence[ExpertJudgment]) -> None:
    for judgment in judgments:
        if judgment.candidate_id != candidate_id:
            raise ValueError("judgment_candidate_id_mismatch")


def _normalized(value: Any) -> str:
    return str(value or "").strip().casefold()


def _unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value).strip()))


def _judgment_independence(
    judgments: Sequence[ExpertJudgment],
    generator: CandidateGenerationIdentity,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    generator_family = _normalized(generator.model_family)
    generator_resolved = _normalized(generator.resolved_model_id)
    if generator.identity_verification != "verified" or not generator_family:
        reasons.append("unverified_generator_identity")
    profiles = [judgment.profile for judgment in judgments]
    if any(profile.identity_verification != "verified" or not profile.resolved_model_id for profile in profiles):
        reasons.append(
            "unverified_third_expert_identity"
            if len(judgments) == 3 and profiles[-1].identity_verification != "verified"
            else "unverified_expert_identity"
        )
    families = [_normalized(profile.model_family) for profile in profiles]
    resolved = [_normalized(profile.resolved_model_id) for profile in profiles]
    if len(set(families)) != len(families) or len(set(resolved)) != len(resolved):
        reasons.append("expert_identity_conflict")
    if generator_family in families or (generator_resolved and generator_resolved in resolved):
        reasons.append("generator_expert_identity_conflict")
    return not reasons, _unique(reasons)


def _as_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", ""}
    return bool(value)
