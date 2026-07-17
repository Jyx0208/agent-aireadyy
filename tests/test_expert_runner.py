from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

from agent.web.expert_review.consensus import ExpertModelProfile
from agent.web.expert_review.expert_runner import (
    AnthropicSdkExpertJudge,
    ModelExpertRunner,
    OpenAISdkExpertJudge,
)


def test_web_runtime_declares_openai_sdk_dependency() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    web_dependencies = project["project"]["optional-dependencies"]["web"]

    assert any(str(dependency).startswith("openai>=") for dependency in web_dependencies)


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


class _OpenAICompletions:
    def __init__(self, content: str) -> None:
        self.content = content

    def create(self, **_kwargs):
        message = type("Message", (), {"content": self.content})()
        choice = type("Choice", (), {"message": message})()
        return type("Response", (), {"choices": [choice], "model": "grok-4.5"})()


class _OpenAIClient:
    def __init__(self, content: str) -> None:
        self.chat = type("Chat", (), {"completions": _OpenAICompletions(content)})()


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


def test_openai_compatible_expert_accepts_fenced_json(monkeypatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    client = _OpenAIClient(f"```json\n{_assessment_json()}\n```")
    judge = OpenAISdkExpertJudge(
        api_key="secret",
        base_url="https://example.test/v1",
        model="grok-4.5",
        client=client,
    )

    payload = judge("system", "user")

    assert payload["final_grade"] == 3
    assert judge.resolved_model_id == "grok-4.5"


def test_openai_compatible_expert_falls_back_after_schema_transport_failure() -> None:
    class SchemaTransportFailureCompletions:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            if "response_format" in kwargs:
                raise json.JSONDecodeError("Expecting value", "", 0)
            message = type("Message", (), {"content": _assessment_json()})()
            choice = type("Choice", (), {"message": message})()
            return type("Response", (), {"choices": [choice], "model": "grok-4.5"})()

    completions = SchemaTransportFailureCompletions()
    client = type(
        "SchemaFallbackClient",
        (),
        {"chat": type("Chat", (), {"completions": completions})()},
    )()
    judge = OpenAISdkExpertJudge(
        api_key="secret",
        base_url="https://proxy.example/v1",
        model="grok-4.5",
        client=client,
    )

    payload = judge("system", "user")

    assert payload["final_grade"] == 3
    assert len(completions.calls) == 2
    assert "response_format" in completions.calls[0]
    assert "response_format" not in completions.calls[1]


def test_openai_compatible_expert_does_not_leak_invalid_response_values(monkeypatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    content = '{"hard_gate_outcome": "PRIVATE_RAW_RESPONSE"}'
    judge = OpenAISdkExpertJudge(
        api_key="secret",
        base_url="https://example.test/v1",
        model="grok-4.5",
        client=_OpenAIClient(content),
    )

    with pytest.raises(RuntimeError) as error:
        judge("system", "user")

    assert "judge_response_schema_invalid" in str(error.value)
    assert "PRIVATE_RAW_RESPONSE" not in str(error.value)


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
            "_output_language": "zh-CN",
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
        "_output_language",
    ):
        assert hidden not in visible
    assert "hard_gate_outcome" in captured["system"]
    assert "Simplified Chinese" in captured["system"]


def test_model_expert_prompt_separates_portfolio_quantity_from_candidate_grade() -> None:
    captured: dict[str, str] = {}
    profile = ExpertModelProfile(
        profile_id="gpt",
        provider="openai_compatible",
        requested_model_id="gpt-test",
        model_family="gpt",
        endpoint_identity="https://example.test/v1",
        routing_profile_id="gpt",
        identity_verification="unverified",
    )

    def resolve_profile(_profile_id: str) -> dict[str, Any]:
        return {
            **profile.model_dump(mode="json"),
            "id": profile.profile_id,
            "api_key": "secret",
            "base_url": "https://example.test/v1",
            "model": "gpt-test",
            "timeout": "120",
        }

    def judge_factory(_profile: ExpertModelProfile, _config: dict[str, Any]):
        def judge(system_prompt: str, _user_prompt: str):
            captured["system"] = system_prompt
            return json.loads(_assessment_json())

        return judge

    runner = ModelExpertRunner(resolve_profile=resolve_profile, judge_factory=judge_factory)
    runner(
        profile,
        {
            "candidate_id": "candidate-small-valid",
            "visible_prompt": "免疫肽数据集，越多越好",
            "selected_file_count": 4,
            "task_semantics": {
                "quantity_scope": "portfolio",
                "portfolio_size_preference": "maximize_usable_projects",
                "per_project_minimum": None,
                "penalize_small_project": False,
            },
        },
    )

    assert "portfolio-level" in captured["system"]
    assert "must not lower" in captured["system"]
    assert "Grade 3" in captured["system"]
