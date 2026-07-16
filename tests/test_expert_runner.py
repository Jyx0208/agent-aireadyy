from __future__ import annotations

import json
from typing import Any

from agent.web.expert_review.consensus import ExpertModelProfile
from agent.web.expert_review.expert_runner import (
    AnthropicSdkExpertJudge,
    ModelExpertRunner,
)


def _assessment_json() -> str:
    return json.dumps(
        {
            "hard_gate_outcome": "pass",
            "final_grade": 3,
            "confidence": "high",
            "investigation_status": "completed",
            "evidence_conflict": False,
            "summary": "Strong match",
            "evidence_refs": ["project_description"],
            "missing_information": [],
        }
    )


class _AnthropicStream:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get_final_message(self):
        block = type("TextBlock", (), {"text": _assessment_json()})()
        return type("Message", (), {"content": [block], "model": "claude-opus-4-8-20260701"})()


class _AnthropicMessages:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def stream(self, **kwargs):
        self.kwargs = kwargs
        return _AnthropicStream()


class _AnthropicClient:
    def __init__(self) -> None:
        self.messages = _AnthropicMessages()


def test_anthropic_adapter_uses_official_streaming_adaptive_structured_output() -> None:
    client = _AnthropicClient()
    judge = AnthropicSdkExpertJudge(api_key="secret", client=client)

    payload = judge("system", "user")

    assert payload["final_grade"] == 3
    kwargs = client.messages.kwargs
    assert kwargs["model"] == "claude-opus-4-8"
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["output_config"]["format"]["type"] == "json_schema"
    assert kwargs["messages"] == [{"role": "user", "content": "user"}]
    assert kwargs["system"] == "system"
    assert judge.resolved_model_id == "claude-opus-4-8-20260701"


def test_model_expert_runner_blinds_other_reviews_and_generator_identity() -> None:
    captured: dict[str, Any] = {}
    profile = ExpertModelProfile(
        profile_id="claude",
        provider="anthropic",
        requested_model_id="claude-opus-4-8",
        resolved_model_id="claude-opus-4-8",
        model_family="claude",
        endpoint_identity="anthropic:primary",
        routing_profile_id="claude",
        identity_verification="provider_attested",
    )

    def resolve_profile(_profile_id: str) -> dict[str, Any]:
        return {
            **profile.model_dump(mode="json"),
            "id": profile.profile_id,
            "api_key": "secret",
            "base_url": "https://api.anthropic.com",
            "model": "claude-opus-4-8",
            "timeout": "120",
        }

    def judge_factory(_profile: ExpertModelProfile, _config: dict[str, Any]):
        def judge(system_prompt: str, user_prompt: str):
            captured["system"] = system_prompt
            captured["candidate"] = json.loads(user_prompt)
            return json.loads(_assessment_json())

        judge.resolved_model_id = "claude-opus-4-8-20260701"
        return judge

    runner = ModelExpertRunner(resolve_profile=resolve_profile, judge_factory=judge_factory)
    judgment = runner(
        profile,
        {
            "candidate_id": "candidate-1",
            "project_title": "Visible",
            "project_accession": "PXDPRIVATE",
            "generator_model_family": "gpt",
            "human_grades": [{"grade": 3}],
            "machine_reviews": [{"grade": 1}],
            "model_expert_judgments": [{"final_grade": 0}],
            "model_expert_consensus": {"consensus_grade": 0},
        },
    )

    assert judgment.candidate_id == "candidate-1"
    assert judgment.profile.profile_id == "claude"
    assert judgment.profile.resolved_model_id == "claude-opus-4-8-20260701"
    assert judgment.profile.identity_verification == "verified"
    assert judgment.final_grade == 3
    visible = captured["candidate"]
    assert visible["project_title"] == "Visible"
    for hidden in (
        "project_accession",
        "generator_model_family",
        "human_grades",
        "machine_reviews",
        "model_expert_judgments",
        "model_expert_consensus",
    ):
        assert hidden not in visible
    assert "hard_gate_outcome" in captured["system"]
