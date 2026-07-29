from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace
from typing import Any

import pytest
from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

import agent.web.app as web_app
from agent.discovery.agentic import OpenAICompatibleDiscoveryLLM


class _TurnLLM:
    def __init__(self, responses: list[dict[str, Any] | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, str]] = []
        self.timeout = 999.0

    def complete_json(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        if not self.responses:
            raise AssertionError("Unexpected extra LLM call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _RoleAwareTurnLLM(_TurnLLM):
    def __init__(self, responses: list[dict[str, Any] | Exception]) -> None:
        super().__init__(responses)
        self.message_calls: list[list[dict[str, str]]] = []

    def complete_json_messages(self, *, messages: list[dict[str, str]]) -> dict[str, Any]:
        self.message_calls.append(messages)
        if not self.responses:
            raise AssertionError("Unexpected extra LLM call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _DialogueScriptedModel(Model):
    def __init__(self, actions: list[tuple[str, dict[str, Any] | str]]) -> None:
        self.actions = actions
        self.calls = 0
        self.requests: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
        self.requests.append((args, kwargs))
        action, payload = self.actions[self.calls]
        self.calls += 1
        if action == "final":
            output = [
                ResponseOutputMessage(
                    id=f"message_{self.calls}",
                    content=[
                        ResponseOutputText(
                            annotations=[],
                            text=str(payload),
                            type="output_text",
                        )
                    ],
                    role="assistant",
                    status="completed",
                    type="message",
                )
            ]
        else:
            output = [
                ResponseFunctionToolCall(
                    arguments=json.dumps(payload),
                    call_id=f"call_{self.calls}",
                    name=action,
                    type="function_call",
                    status="completed",
                )
            ]
        return ModelResponse(output=output, usage=Usage(requests=1), response_id=None)

    async def stream_response(self, *args: Any, **kwargs: Any):
        if False:
            yield None


def _run_turn(
    monkeypatch: pytest.MonkeyPatch,
    response: dict[str, Any] | Exception,
    *,
    user_message: str = "请按我刚才的选择更新。",
    role_aware: bool = False,
    **body: Any,
) -> tuple[dict[str, Any], _TurnLLM]:
    llm: _TurnLLM
    if role_aware:
        llm = _RoleAwareTurnLLM([response])
    else:
        llm = _TurnLLM([response])
    monkeypatch.setattr(web_app, "_discovery_llm_client", lambda *_args, **_kwargs: llm)
    result = asyncio.run(
        web_app.discovery_grill_turn(
            {
                "user_message": user_message,
                "allow_server_default": False,
                **body,
            }
        )
    )
    return result, llm


def _decision() -> dict[str, Any]:
    return {
        "focus": "horizon",
        "target_fields": ["run_horizon"],
        "question": "候选找到后，要不要再做一轮证据复核？",
        "recommendation": {
            "id": "review",
            "label": "候选后复核",
            "reason": "跨物种注释常需要项目级证据核验。",
        },
        "options": [
            {
                "id": "review",
                "label": "候选后复核",
                "reason": "质量更稳",
                "strategy_patch": {"run_horizon": "candidates_reviewed"},
            },
            {
                "id": "stop",
                "label": "候选即停",
                "reason": "速度更快",
                "strategy_patch": {"run_horizon": "candidates_only"},
            },
        ],
        "allow_free_text": True,
    }


def _ready_context() -> dict[str, Any]:
    return {
        "phase": "awaiting_confirm",
        "intent_snapshot": {
            "objective": "比较海胆不同发育阶段的蛋白质组",
            "task_type": "browse_only",
            "run_horizon": "candidates_only",
            "species": ["Strongylocentrotus purpuratus"],
            "species_policy": "include_only",
            "coverage_mode": "curated",
            "target_project_count": 18,
        },
        "gap_report": {
            "required_missing": [],
            "optional_missing": ["instrument"],
            "ready_for_confirm": True,
        },
    }


def test_training_agenda_prioritizes_search_scale_before_optional_labeling():
    agenda = web_app._discovery_critical_decision_agenda(
        {
            "objective": "构建免疫肽 de novo 训练集",
            "task_type": "denovo",
            "run_horizon": "candidates_reviewed",
            "target_project_count": None,
            "coverage_mode": "",
            "quota_flexibility": "recommended",
            "acquisition_mode": "dda",
            "species": [],
            "species_policy": "open",
            "labeling_strategy": "unknown",
        },
        {
            "required_missing": ["coverage"],
            "optional_missing": ["species", "labeling"],
            "ready_for_confirm": False,
        },
        {"acquisition_mode"},
    )

    ids = [item["id"] for item in agenda]
    assert ids.index("search_scale") < ids.index("labeling_compatibility")
    assert next(item for item in agenda if item["id"] == "search_scale")["critical"] is True


def test_web_critical_agenda_is_a_thin_profile_engine_delegate(monkeypatch):
    snapshot = {"task_type": "browse_only", "objective": "Browse public data"}
    gaps = {"required_missing": [], "optional_missing": []}
    resolved = {"run_horizon"}
    expected = [
        {
            "id": "delegated",
            "priority": 1,
            "critical": False,
            "target_fields": [],
            "source": "ask_user_preference",
        }
    ]
    captured = {}

    def fake_agenda_for_manager(intent_snapshot, gap_report, resolved_fields):
        captured.update(
            snapshot=intent_snapshot,
            gaps=gap_report,
            resolved=resolved_fields,
        )
        return expected

    monkeypatch.setattr(web_app, "agenda_for_manager", fake_agenda_for_manager)

    actual = web_app._discovery_critical_decision_agenda(snapshot, gaps, resolved)

    assert actual == expected
    assert captured == {
        "snapshot": {**snapshot, "run_horizon": "candidates_reviewed"},
        "gaps": gaps,
        "resolved": resolved,
    }


def test_explicit_search_term_extension_bypasses_the_language_model(monkeypatch):
    monkeypatch.setattr(
        web_app,
        "_discovery_llm_client",
        lambda *_args, **_kwargs: pytest.fail(
            "Explicit repository search terms must use the deterministic fast path"
        ),
    )
    current = [
        "immunopeptidomics",
        "immunopeptidome",
        "HLA ligandome",
    ]

    result = web_app._run_discovery_grill_turn(
        {
            "user_message": (
                "请扩充检索主题词。保留当前核心词，并新增："
                "HLA peptidomics、MHC peptidomics、HLA-bound peptides。"
                "最后使用 HLA、MHC、W6/32 进行宽泛补漏"
            ),
            "intent_snapshot": {
                "selected_search_terms": current,
                "run_horizon": "candidates_reviewed",
            },
            "resolved_fields": ["selected_search_terms", "run_horizon"],
        }
    )

    expected = [
        *current,
        "HLA peptidomics",
        "MHC peptidomics",
        "HLA-bound peptides",
        "HLA",
        "MHC",
        "W6/32",
    ]
    assert result["action"] == "update_strategy"
    assert result["extra_fields"] == {"selected_search_terms": expected}
    assert result["llm_used"] is False
    assert result["parser"] == "deterministic_search_terms"


def test_web_browse_only_agenda_does_not_load_training_questions():
    agenda = web_app._discovery_critical_decision_agenda(
        {
            "objective": "Browse a bounded public proteomics landscape",
            "task_type": "browse_only",
            "run_horizon": "candidates_only",
            "target_project_count": 12,
            "quota_flexibility": "recommended",
            "acquisition_mode": "",
            "species": [],
            "species_policy": "",
            "labeling_strategy": "",
        },
        {"required_missing": [], "optional_missing": []},
        set(),
    )

    assert agenda == []


def test_web_explicit_open_training_choices_are_not_reasked():
    # Explicit open values for scale / acquisition / labeling resolve; empty
    # species still surfaces generalization_scope (P0-C) until the user
    # chooses open or lists taxa (resolved_fields or non-empty species).
    agenda = web_app._discovery_critical_decision_agenda(
        {
            "objective": "Build a de novo training table",
            "task_type": "denovo",
            "run_horizon": "ai_ready_table",
            "target_project_count": None,
            "quota_flexibility": "open_ended",
            "acquisition_mode": "unknown",
            "species": [],
            "species_policy": "open",
            "labeling_strategy": "any",
        },
        {"required_missing": [], "optional_missing": []},
        set(),
    )

    ids = [item["id"] for item in agenda]
    assert ids == ["generalization_scope"]

    suppressed = web_app._discovery_critical_decision_agenda(
        {
            "objective": "Build a de novo training table",
            "task_type": "denovo",
            "run_horizon": "ai_ready_table",
            "target_project_count": None,
            "quota_flexibility": "open_ended",
            "acquisition_mode": "unknown",
            "species": [],
            "species_policy": "open",
            "labeling_strategy": "any",
        },
        {"required_missing": [], "optional_missing": []},
        {"species", "species_policy", "species_coverage"},
    )
    assert suppressed == []


def test_web_chimeric_agenda_prioritizes_label_feasibility_over_optional_labeling():
    agenda = web_app._discovery_critical_decision_agenda(
        {
            "objective": "Build a chimeric-spectrum benchmark",
            "task_type": "chimeric_interpretation",
            "run_horizon": "ai_ready_table",
            "target_project_count": 20,
            "quota_flexibility": "recommended",
            "acquisition_mode": "dda",
            "species": [],
            "species_policy": "open",
            "labeling_strategy": "unknown",
            "scientific_constraints": [],
        },
        {"required_missing": [], "optional_missing": ["labeling"]},
        {"acquisition_mode", "species_policy"},
    )

    ids = [item["id"] for item in agenda]
    assert ids.index("chimeric_label_feasibility") < ids.index(
        "labeling_compatibility"
    )
    feasibility = next(
        item for item in agenda if item["id"] == "chimeric_label_feasibility"
    )
    assert feasibility["critical"] is True
    assert feasibility["target_fields"] == ["scientific_constraints"]


def test_confirmation_rejects_training_strategy_with_unresolved_search_scale():
    snapshot = {
        "objective": "构建免疫肽 de novo 训练集",
        "task_type": "denovo",
        "run_horizon": "candidates_reviewed",
        "target_project_count": None,
        "coverage_mode": "",
        "quota_flexibility": "recommended",
        "acquisition_mode": "dda",
        "species": ["Homo sapiens"],
        "species_policy": "include_only",
    }
    eligible, reason, _fingerprint = web_app._discovery_confirmation_context(
        {
            "pending_strategy_snapshot": snapshot,
        },
        phase="awaiting_confirm",
        intent_snapshot=snapshot,
        gap_report={
            "required_missing": [],
            "optional_missing": [],
            "ready_for_confirm": True,
        },
    )

    assert eligible is False
    assert "search_scale" in reason


@pytest.mark.parametrize(
    "raw_decision",
    [
        {
            "focus": "horizon",
            "question": "复核吗？",
            "recommendation": {"id": "review", "label": "复核"},
            "options": [
                {"id": "review", "label": "复核"},
                {"id": "stop", "label": "停止"},
            ],
        },
        {
            "focus": "horizon",
            "question": "复核吗？",
            "recommendation": {
                "id": "review",
                "label": "复核",
                "reason": "证据更稳",
            },
            "options": [{"id": "review", "label": "复核"}],
        },
    ],
)
def test_next_decision_contract_rejects_missing_reason_or_two_options(raw_decision):
    assert web_app._normalise_discovery_next_decision(raw_decision) is None


def test_invalid_next_decision_is_repaired_from_critical_agenda(monkeypatch):
    """P0-A: broken next_decision must not leave the user with only a contract string.

    When critical agenda items remain, the server synthesizes a full menu instead of
    permanently silencing the grill after an incomplete model next_decision.
    """
    invalid = {
        "focus": "horizon",
        "question": "复核吗？",
        "recommendation": {"id": "review", "label": "复核"},
        "options": [{"id": "review", "label": "复核"}],
    }
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "clarify",
            "assistant_message": "这是一个不完整的下一问。",
            "tool_calls": [],
            "next_decision": invalid,
        },
    )

    assert result["action"] == "clarify"
    next_decision = result["next_decision"]
    assert next_decision["question"]
    assert next_decision["recommendation"].get("reason")
    assert 2 <= len(next_decision["options"]) <= 8
    for option in next_decision["options"]:
        assert isinstance(option.get("strategy_patch"), dict)
    assert "下一问结构不完整" not in result["assistant_message"]
    # Successful agenda repair clears the next_decision schema contract error.
    assert not any(
        "next_decision requires" in error
        for error in result.get("contract_errors") or []
    )


def test_update_strategy_with_broken_next_decision_still_grills_species(monkeypatch):
    """P0-A/C: write card + invalid next_decision still asks generalization_scope."""

    invalid = {
        "focus": "species",
        "question": "物种？",
        "recommendation": {"id": "human", "label": "人"},
        "options": [{"id": "human", "label": "人"}],
    }
    patch = {
        "objective": "人源 denovo 训练数据",
        "task_type": "denovo",
        "acquisition_mode": "dda",
        "run_horizon": "candidates_reviewed",
        "target_project_count": 20,
        "quota_flexibility": "recommended",
    }
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "update_strategy",
            "assistant_message": "已写入 denovo + DDA。",
            "tool_calls": [
                {"name": "update_strategy", "arguments": {"patch": patch}}
            ],
            "next_decision": invalid,
        },
        user_message="denovo，DDA，约20个项目",
        intent_snapshot={
            "objective": "",
            "task_type": "",
            "run_horizon": "",
            "species": [],
            "species_policy": "open",
            "acquisition_mode": "",
        },
    )

    assert result["action"] == "update_strategy"
    assert result["extra_fields"]["task_type"] == "denovo"
    next_decision = result["next_decision"]
    assert next_decision is not None
    assert 2 <= len(next_decision["options"]) <= 8
    assert next_decision["recommendation"].get("reason")
    # Prefer species/generalization template when that gap remains after write.
    joined = " ".join(
        [
            str(next_decision.get("focus") or ""),
            str(next_decision.get("question") or ""),
            " ".join(str(f) for f in (next_decision.get("target_fields") or [])),
        ]
    )
    assert any(
        token in joined.lower()
        for token in ("species", "物种", "generalization")
    ) or any(
        "species" in (opt.get("strategy_patch") or {})
        for opt in next_decision["options"]
    )
    assert "下一问结构不完整" not in result["assistant_message"]
    assert "关键点" in result["assistant_message"]
    # Repair must not leave a stale next_decision contract_errors entry.
    assert not any(
        "next_decision requires" in error
        for error in result.get("contract_errors") or []
    )


def test_complete_strategy_ignores_malformed_optional_next_decision(monkeypatch):
    invalid = {
        "focus": "labeling",
        "question": "还有其他要求吗？",
        "recommendation": {"id": "any", "label": "不限"},
        "options": [{"id": "any", "label": "不限"}],
    }
    patch = {
        "objective": "免疫肽组学数据发现",
        "task_type": "browse_only",
        "run_horizon": "candidates_reviewed",
        "species": ["human"],
        "species_policy": "include_only",
        "acquisition_mode": "dda",
        "mixed_acquisition_policy": "reject_mixed",
        "special_themes": ["immunopeptidomics"],
        "labeling_strategy": "any",
        "labeling_hard": False,
        "coverage_mode": "exhaustive",
        "quota_flexibility": "open_ended",
    }
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "update_strategy",
            "assistant_message": "已记录完整策略。",
            "tool_calls": [
                {"name": "update_strategy", "arguments": {"patch": patch}}
            ],
            "next_decision": invalid,
            "gap_report": {
                "required_missing": [],
                "optional_missing": [],
                "ready_for_confirm": True,
            },
        },
        user_message=(
            "科学目标：免疫肽组学数据发现；研究主题：immunopeptidomics；"
            "物种：仅限人；采集模式：仅 DDA；下游任务：纯浏览探索；"
            "交付终点：候选加审查；规模：越多越好，开放上限；"
            "标记方式：不限。"
        ),
    )

    assert result["action"] == "update_strategy"
    assert {
        field: result["extra_fields"].get(field)
        for field in patch
        if field != "run_horizon"
    } == {
        field: value
        for field, value in patch.items()
        if field != "run_horizon"
    }
    assert result["extra_fields"].get("target_project_count") is None
    assert result.get("next_decision") is None
    assert not any(
        "next_decision requires" in error
        for error in result.get("contract_errors") or []
    )


def test_clarify_what_does_that_mean_repairs_with_friendly_copy(monkeypatch):
    """P1-B: user asks 什么意思 after a broken menu; explain and re-ask."""

    invalid = {
        "focus": "horizon",
        "question": "复核吗？",
        "recommendation": {"id": "review", "label": "复核"},
        "options": [{"id": "review", "label": "复核"}],
    }
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "advise",
            "assistant_message": "",
            "tool_calls": [],
            "next_decision": invalid,
        },
        user_message="什么意思",
    )

    assert result["action"] == "clarify"
    assert result.get("next_decision") is not None
    assert len(result["next_decision"]["options"]) >= 2
    msg = result["assistant_message"]
    assert "菜单" in msg or "选项" in msg
    assert "刚才的提示是说" in msg or "下一问" in msg
    assert "下一问结构不完整" not in msg
    assert "next_decision requires" not in msg


def test_what_does_that_mean_after_contract_noise_injects_manager_hint(monkeypatch):
    """P1-B OWN C: history contract noise + 什么意思 injects Manager hint + friendly reply."""

    result, llm = _run_turn(
        monkeypatch,
        {
            "action": "advise",
            "assistant_message": "随便回一句。",
            "tool_calls": [],
            "next_decision": None,
        },
        user_message="什么意思？",
        dialogue_history=[
            {"role": "user", "content": "denovo，DDA"},
            {
                "role": "assistant",
                "content": (
                    "已写入部分策略。"
                    "上一轮的选项菜单不完整，我先不把它当作有效提问。"
                ),
            },
        ],
        intent_snapshot={
            "objective": "denovo training",
            "task_type": "denovo",
            "acquisition_mode": "dda",
            "run_horizon": "candidates_reviewed",
            "species": [],
            "species_policy": "open",
            "target_project_count": 20,
            "quota_flexibility": "recommended",
        },
        resolved_fields=[
            "objective",
            "task_type",
            "acquisition_mode",
            "run_horizon",
            "target_project_count",
            "quota_flexibility",
        ],
    )

    prompts = "\n".join(llm.calls[0].values())
    assert "server_hint" in prompts
    assert "incomplete next_decision" in prompts or "contract" in prompts.casefold()
    assert "什么意思" in prompts or "user_message: 什么意思" in prompts
    msg = result["assistant_message"]
    assert "下一问结构不完整" not in msg
    assert "next_decision requires" not in msg
    assert "刚才的提示是说" in msg or "选项不完整" in msg or "菜单" in msg
    # Species remains critical for denovo; prefer a repaired menu when possible.
    if result.get("next_decision") is not None:
        assert len(result["next_decision"]["options"]) >= 2

def test_grill_turn_exposes_agent_owned_update_and_preserves_next_decision(monkeypatch):
    patch = {
        "task_type": "browse_only",
        "species": ["Strongylocentrotus purpuratus"],
        "species_policy": "prefer",
        "coverage_mode": "curated",
        "target_project_count": 18,
        "special_themes": ["developmental proteome"],
    }
    result, llm = _run_turn(
        monkeypatch,
        {
            "action": "update_strategy",
            "assistant_message": "已把海胆发育蛋白质组写入策略；下一步只需要决定复核深度。",
            "tool_calls": [
                {"name": "update_strategy", "arguments": {"patch": patch}}
            ],
            "next_decision": _decision(),
        },
        user_message="先限定紫海胆，18 个左右；然后你建议候选后要不要复核？",
    )

    assert result["action"] == "update_strategy"
    assert result["mode"] == "update_strategy"
    assert result["tool_calls"] == [
        {"name": "update_strategy", "arguments": {"patch": patch}}
    ]
    assert result["extra_fields"] == patch
    assert result.get("next_decision") is None
    assert len(llm.calls) == 1

    prompts = "\n".join(llm.calls[0].values()).lower()
    # Stable intent checks (wording tracks multi-commitment Manager guidance).
    assert "highest-value" in prompts
    assert "guidance, not a" in prompts
    assert "confirm_strategy" in prompts
    assert "multi-commitment" in prompts or "compound" in prompts


def test_discovery_agent_guidance_uses_repository_file_with_safe_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    guidance_path = tmp_path / "discovery-agent-guidance.md"
    monkeypatch.setattr(web_app, "_DISCOVERY_AGENT_GUIDANCE_PATH", guidance_path)

    guidance_path.write_text("repository-specific scientific guidance", encoding="utf-8")
    assert web_app._discovery_agent_guidance() == "repository-specific scientific guidance"

    guidance_path.unlink()
    fallback = web_app._discovery_agent_guidance().lower()
    assert "human-prioritized" in fallback
    assert "ptm de novo" in fallback


def test_history_is_sent_once_as_native_chat_roles(monkeypatch):
    previous_user = "先比较两种方案，这轮不要改卡。"
    previous_assistant = "第一种偏精确，第二种覆盖更广。"
    latest = "采用第二种，但标记方式继续开放。"
    result, llm = _run_turn(
        monkeypatch,
        {
            "action": "chat",
            "assistant_message": "我会继续讨论，不修改策略。",
            "tool_calls": [],
        },
        role_aware=True,
        user_message=latest,
        dialogue_history=[
            {"role": "user", "content": previous_user},
            {"role": "assistant", "content": previous_assistant},
        ],
    )

    assert result["action"] == "chat"
    role_llm = llm
    assert isinstance(role_llm, _RoleAwareTurnLLM)
    assert len(role_llm.message_calls) == 1
    messages = role_llm.message_calls[0]
    assert [item["role"] for item in messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert messages[1]["content"] == previous_user
    assert messages[2]["content"] == previous_assistant
    assert latest in messages[-1]["content"]
    assert previous_user not in messages[-1]["content"]
    assert previous_assistant not in messages[-1]["content"]
    assert "recent_dialogue_history" not in messages[-1]["content"]


def test_social_chat_does_not_turn_into_a_structured_questionnaire(monkeypatch):
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "chat",
            "assistant_message": "Hello! Tell me what you would like to explore.",
            "tool_calls": [],
            "next_decision": _decision(),
        },
        user_message="Hello",
    )

    assert result["action"] == "chat"
    assert "next_decision" not in result


def test_notebook_system_prompt_is_notebook_partner_not_form_wizard(monkeypatch):
    """NI-1: system prompt is notebook-style; MUST-menu grilling is weakened."""

    result, llm = _run_turn(
        monkeypatch,
        {
            "action": "chat",
            "assistant_message": "你好，我可以帮你记策略笔记。",
            "tool_calls": [],
            "next_decision": None,
        },
        user_message="你好",
    )

    system = llm.calls[0]["system_prompt"]
    user = llm.calls[0]["user_prompt"]
    assert "notebook partner" in system.casefold() or "记笔记" in system or "notebook" in system.casefold()
    assert "MUST continue grilling" not in user
    assert "MUST continue grilling" not in system
    assert "natural-language follow-up" in user or "natural language" in user.casefold()
    assert result["action"] == "chat"
    assert result.get("contract_errors") in (None, [])
    assert result.get("semantic_verification") is None


@pytest.mark.parametrize("action", ["chat", "advise"])
def test_chat_advise_never_surfaces_contract_or_sv_chrome(monkeypatch, action: str):
    """NI-1: pure chat/advise fail-soft — no contract_errors chrome in user text."""

    raw_next = (
        {
            "focus": "broken",
            "question": "不完整菜单",
        }
        if action == "chat"
        else None
    )
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": action,
            "assistant_message": "免疫肽一般指 MHC 呈递肽段的蛋白质组学，不是固定问卷。",
            "tool_calls": [],
            "next_decision": raw_next,
            "intent": "chitchat" if action == "chat" else "explain",
        },
        user_message="免疫肽是什么？",
    )

    assert result["action"] == action
    assert result.get("contract_errors") in (None, [])
    assert "可验证" not in result["assistant_message"]
    assert "契约" not in result["assistant_message"]
    assert "next_decision requires" not in result["assistant_message"]
    assert "免疫肽" in result["assistant_message"] or "MHC" in result["assistant_message"]
    if action == "chat":
        assert "next_decision" not in result



def test_failed_update_still_reports_when_demoted_from_write(monkeypatch):
    """NI-1: demoted failed update_strategy keeps honest non-write notice (not silent green)."""

    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "update_strategy",
            "assistant_message": "我已经把物种写成人类了。",
            "tool_calls": [],  # no tool → mutation failure
            "extra_fields": {"species": ["Homo sapiens"]},
        },
        user_message="物种改成人类",
    )

    assert result["extra_fields"] == {}
    assert result["tool_calls"] == []
    # Still not a successful write; message must not claim the card was updated via tool.
    assert result["action"] in {"advise", "clarify"}
    # Failed write path may keep contract_errors (not pure chat fail-soft).
    assert result.get("contract_errors") not in (None, []) or "可验证" in result["assistant_message"] or "保持不变" in result["assistant_message"]


@pytest.mark.parametrize("action", ["chat", "advise", "clarify", "ready_to_confirm"])
def test_non_update_actions_cannot_mutate_strategy(monkeypatch, action: str):
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": action,
            "assistant_message": "这轮只讨论，不修改策略。",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {"patch": {"instrument_preference": "newer"}},
                }
            ],
            "extra_fields": {"instrument_preference": "newer"},
            **({"next_decision": _decision()} if action == "clarify" else {}),
        },
        user_message="如果只偏好新仪器，会牺牲什么？先别改。",
    )

    assert result["tool_calls"] == []
    assert result["extra_fields"] == {}
    if action == "clarify":
        assert result.get("next_decision") is None


@pytest.mark.parametrize(
    "response",
    [
        {
            "assistant_message": "旧字段声称已更新。",
            "extra_fields": {"species": ["Arabidopsis thaliana"]},
        },
        {
            "action": "update_strategy",
            "assistant_message": "缺少工具调用。",
            "extra_fields": {"species": ["Arabidopsis thaliana"]},
            "tool_calls": [],
        },
        {
            "action": "update_strategy",
            "assistant_message": "缺少显式 patch。",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {"species": ["Arabidopsis thaliana"]},
                }
            ],
        },
        {
            "action": "update_strategy",
            "assistant_message": "patch 不是对象。",
            "tool_calls": [
                {"name": "update_strategy", "arguments": {"patch": "not-an-object"}}
            ],
        },
    ],
)
def test_malformed_or_missing_update_envelope_fails_closed(monkeypatch, response):
    result, llm = _run_turn(monkeypatch, response)

    assert result["action"] in {"advise", "clarify"}
    assert result["tool_calls"] == []
    assert result["extra_fields"] == {}
    assert len(llm.calls) == 1


def test_malformed_contract_never_falls_back_to_phrase_specific_card_mutation(monkeypatch):
    result, _llm = _run_turn(
        monkeypatch,
        {
            # No D1 action and no typed tool event: the old lexical fallback
            # used to turn these motivating examples into production branches.
            "assistant_message": "模型返回了不完整的旧格式。",
        },
        user_message="改成鱼类、DIA、15 个，顺便换成斑马鱼。",
    )

    assert result["action"] in {"advise", "clarify"}
    assert result["tool_calls"] == []
    assert result["extra_fields"] == {}
    assert "策略保持不变" in result["assistant_message"]


def test_invalid_supported_values_reject_the_entire_patch_and_keep_next_decision(monkeypatch):
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "update_strategy",
            "assistant_message": "模型给出了形状错误和未知枚举。",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {
                        "patch": {
                            "species": "Danio rerio",
                            "run_horizon": "review_candidates",
                            "coverage_mode": "broad",
                            "target_project_count": True,
                        }
                    },
                }
            ],
            "next_decision": _decision(),
        },
    )

    assert result["action"] == "advise"
    assert result["tool_calls"] == []
    assert result["extra_fields"] == {}
    assert result.get("next_decision") is None
    assert result["contract_errors"]


def test_update_and_confirmation_in_one_model_envelope_fail_closed(monkeypatch):
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "update_strategy",
            "assistant_message": "模型试图同时更新并确认。",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {"patch": {"species": ["mouse"]}},
                },
                {"name": "confirm_strategy", "arguments": {}},
            ],
        },
        **_ready_context(),
    )

    assert result["action"] == "advise"
    assert result["tool_calls"] == []
    assert result["extra_fields"] == {}
    assert result["ready_for_confirm"] is False


def test_generic_multi_field_patch_accepts_unseen_categories_without_phrase_branches(monkeypatch):
    patch = {
        "objective": "构建线虫不同生命周期的跨实验室探索语料库",
        "task_type": "browse_only",
        "run_horizon": "candidates_reviewed",
        "species": ["Caenorhabditis elegans"],
        "species_policy": "include_only",
        "species_coverage": "prefer_listed",
        "acquisition_mode": "dia",
        "mixed_acquisition_policy": "review_mixed",
        "special_themes": ["dauer stage", "aging"],
        "labeling_strategy": "any",
        "labeling_hard": False,
        "coverage_mode": "balanced",
        "target_project_count": 47,
        "max_candidate_projects": 190,
        "quota_flexibility": "fixed",
        "time_budget": "multi_round",
        "instrument_preference": "none",
        "exclude_rules": ["cross-linking-only studies"],
        "success_criteria": ["生命周期注释可核验"],
    }
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "update_strategy",
            "assistant_message": "已一次写入你明确给出的线虫研究约束。",
            "tool_calls": [
                {"name": "update_strategy", "arguments": {"patch": patch}}
            ],
        },
        user_message=(
            "用秀丽隐杆线虫做生命周期探索；DIA，标记不限，候选后复核，"
            "目标 47 个并排除纯交联研究。"
        ),
    )

    assert result["action"] == "update_strategy"
    expected_patch = {key: value for key, value in patch.items() if key != "run_horizon"}
    assert result["tool_calls"] == [
        {"name": "update_strategy", "arguments": {"patch": expected_patch}}
    ]
    assert result["extra_fields"] == expected_patch


def test_patch_preserves_explicit_clear_and_unset_values(monkeypatch):
    patch = {
        "species": [],
        "ptm_types": [],
        "special_themes": [],
        "exclude_rules": [],
        "success_criteria": [],
        "open_risks": [],
        "target_project_count": None,
        "max_candidate_projects": None,
        "legacy_floor_ratio": None,
        "notes": None,
        "quota_flexibility": "open_ended",
        "species_policy": "open",
    }
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "update_strategy",
            "assistant_message": "已清空这些限制，并把规模改为开放。",
            "tool_calls": [
                {"name": "update_strategy", "arguments": {"patch": patch}}
            ],
        },
        user_message="清空物种、主题、排除项和固定数量，其余规模开放。",
        intent_snapshot={
            "species": ["human"],
            "ptm_types": ["phospho"],
            "target_project_count": 20,
            "max_candidate_projects": 80,
            "legacy_floor_ratio": 0.2,
            "notes": "old note",
        },
    )

    assert result["action"] == "update_strategy"
    assert result["tool_calls"][0]["arguments"]["patch"] == patch


@pytest.mark.parametrize("field", sorted(web_app._DISCOVERY_STRATEGY_FIRST_CLASS_FIELDS))
def test_every_first_class_strategy_field_accepts_null_as_canonical_clear(field: str):
    patch, errors = web_app._validate_discovery_strategy_patch({field: None})

    assert errors == []
    assert patch == {
        field: (
            "candidates_reviewed"
            if field == "run_horizon"
            else None
        )
    }


def test_d1_canonical_fields_are_strictly_isomorphic_with_frontend_strategy_fields():
    assert web_app._DISCOVERY_STRATEGY_PATCH_FIELDS == {
        "objective",
        "task_type",
        "run_horizon",
        "species",
        "species_policy",
        "species_coverage",
        "acquisition_mode",
        "mixed_acquisition_policy",
        "ptm_types",
        "special_themes",
        "selected_search_terms",
        "labeling_strategy",
        "labeling_hard",
        "coverage_mode",
        "target_project_count",
        "max_candidate_projects",
        "quota_flexibility",
        "time_budget",
        "on_safety_ceiling",
        "instrument_preference",
        "legacy_floor_ratio",
        "exclude_rules",
            "success_criteria",
            "scientific_constraints",
            "notes",
        "open_risks",
        "repository",
    }


@pytest.mark.parametrize(
    ("field", "limit"),
    [("target_project_count", 5000), ("max_candidate_projects", 20000)],
)
def test_numeric_contract_limits_match_frontend_product_ceilings(field: str, limit: int):
    accepted, accepted_errors = web_app._validate_discovery_strategy_patch(
        {field: limit}
    )
    rejected, rejected_errors = web_app._validate_discovery_strategy_patch(
        {field: limit + 1}
    )

    assert accepted_errors == []
    assert accepted == {field: limit}
    assert rejected == {}
    assert rejected_errors


@pytest.mark.parametrize(("field", "limit"), [("objective", 120), ("notes", 4000)])
def test_text_contract_limits_match_frontend_decoder(field: str, limit: int):
    accepted, accepted_errors = web_app._validate_discovery_strategy_patch(
        {field: "x" * limit}
    )
    rejected, rejected_errors = web_app._validate_discovery_strategy_patch(
        {field: "x" * (limit + 1)}
    )

    assert accepted_errors == []
    assert accepted == {field: "x" * limit}
    assert rejected == {}
    assert rejected_errors


def test_string_array_contract_matches_frontend_item_and_length_limits():
    accepted_items = [f"species-{index}" for index in range(100)]
    accepted, accepted_errors = web_app._validate_discovery_strategy_patch(
        {"species": accepted_items}
    )
    too_many, too_many_errors = web_app._validate_discovery_strategy_patch(
        {"species": [*accepted_items, "one-more"]}
    )
    item_at_limit, item_at_limit_errors = web_app._validate_discovery_strategy_patch(
        {"species": ["x" * 240]}
    )
    item_too_long, item_too_long_errors = web_app._validate_discovery_strategy_patch(
        {"species": ["x" * 241]}
    )

    assert accepted_errors == []
    assert accepted == {"species": accepted_items}
    assert too_many == {}
    assert too_many_errors
    assert item_at_limit_errors == []
    assert item_at_limit == {"species": ["x" * 240]}
    assert item_too_long == {}
    assert item_too_long_errors


def test_runtime_payload_fields_never_escape_as_canonical_card_keys(monkeypatch):
    internal_fields = {
        "query_terms": ["dauer proteomics"],
        "diversity_strategy": "high",
        "constraints_enabled": True,
        "hard_constraint_fields": ["species"],
        "constraint_provenance": {"species": "user"},
        "agentic_rounds": 7,
        "max_files": 5000,
        "original_prompt": "internal transport copy",
    }
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "update_strategy",
            "assistant_message": "科学要求保留为备注，运行字段不写卡。",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {
                        "patch": {
                            "objective": "线虫生命周期探索",
                            **internal_fields,
                        }
                    },
                }
            ],
        },
    )

    emitted = result["extra_fields"]
    assert emitted["objective"] == "线虫生命周期探索"
    assert set(emitted).isdisjoint(internal_fields)
    assert "scientific_constraints" not in emitted


def test_aliases_are_validated_then_emitted_as_canonical_strategy_fields(monkeypatch):
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "update_strategy",
            "assistant_message": "已更新规模和预算。",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {
                        "patch": {
                            "goal": "建立跨实验室线虫目录",
                            "maxProjects": 33,
                            "scaleMode": "balanced",
                            "timeBudgetPreference": "multi_round",
                        }
                    },
                }
            ],
        },
    )

    assert result["extra_fields"] == {
        "objective": "建立跨实验室线虫目录",
        "target_project_count": 33,
        "coverage_mode": "balanced",
        "time_budget": "multi_round",
    }


def test_unknown_constraint_and_unsupported_repository_are_preserved_for_review(monkeypatch):
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "update_strategy",
            "assistant_message": "未映射条件会保留供后续审查。",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {
                        "patch": {
                            "repository": "unregistered-repository",
                            "custom_fractionation_constraint": "FAIMS-only",
                        }
                    },
                }
            ],
        },
    )

    normalized = result["extra_fields"]
    assert "repository" not in normalized
    assert "custom_fractionation_constraint" not in normalized
    [constraint] = normalized["scientific_constraints"]
    assert constraint["dimension"] == "custom_fractionation_constraint"
    assert constraint["value"] == "FAIMS-only"
    assert constraint["evidence_required"] is True
    assert any("unregistered-repository" in risk for risk in normalized["open_risks"])


def test_arbitrary_scientific_constraints_survive_strategy_into_execution_request() -> None:
    constraints = [
        {
            "id": "separation.faims",
            "label": "Only FAIMS-enabled acquisitions",
            "dimension": "ion_mobility_separation",
            "operator": "equals",
            "value": "FAIMS",
            "strength": "hard",
            "scope": "file",
            "evidence_required": True,
            "source": "user",
        },
        {
            "id": "sample.exclude-cell-lines",
            "label": "Exclude immortalized cell lines",
            "dimension": "sample_model",
            "operator": "not_matches",
            "value": "immortalized cell line",
            "strength": "hard",
            "scope": "sample",
            "evidence_required": True,
            "source": "user",
        },
        {
            "id": "cohort.minimum-participants",
            "label": "At least 30 participants per project",
            "dimension": "participant_count",
            "operator": "gte",
            "value": 30,
            "strength": "hard",
            "scope": "project",
            "evidence_required": True,
            "source": "user",
        },
    ]
    patch = web_app._normalise_discovery_strategy_patch(
        {
            "scientific_constraints": constraints,
            "instrument_preference": "newer",
        }
    )
    request = web_app._clean_dataset_request(
        {
            "repository": "pride",
            "goal": "general",
            "max_projects": 15,
            "max_files": 500,
            **patch,
        }
    )

    by_id = {constraint.id: constraint for constraint in request.scientific_constraints}
    assert set(by_id) >= {
        "separation.faims",
        "sample.exclude-cell-lines",
        "cohort.minimum-participants",
        "builtin.instrument-era",
    }
    assert by_id["cohort.minimum-participants"].value == 30
    assert by_id["sample.exclude-cell-lines"].scope == "sample"
    assert request.instrument_preference == "newer"
    assert "constraint:separation.faims" in request.hard_constraint_fields
    serialized = request.model_dump(mode="json")
    assert serialized["scientific_constraints"][2]["operator"] == "gte"


def test_species_patch_canonicalizes_taxon_synonyms_and_trivial_inflections():
    assert web_app._normalise_discovery_strategy_patch(
        {"species": ["human", "Homo sapiens"]}
    )["species"] == ["human"]
    assert web_app._normalise_discovery_strategy_patch(
        {"species": ["fish", "fishes"]}
    )["species"] == ["fish"]

    # Strategy arrays are already structured values.  They must use exact
    # aliases rather than the fuzzy free-text matcher, otherwise a qualifier
    # such as ``non-human`` can silently reverse the user's constraint.
    assert web_app._normalise_discovery_strategy_patch(
        {"species": ["non-human primate"]}
    )["species"] == ["non-human primate"]
    assert web_app._normalise_discovery_strategy_patch(
        {"species": ["human and mouse"]}
    )["species"] == ["human and mouse"]


def test_execution_request_deduplicates_same_exclusion_across_first_class_and_constraint():
    request = web_app._clean_dataset_request(
        {
            "repository": "pride",
            "goal": "general",
            "exclude_rules": ["Exclude immortalized cell lines"],
            "scientific_constraints": [
                {
                    "id": "sample.exclude-cell-lines",
                    "label": "Exclude immortalized cell lines",
                    "dimension": "sample_model",
                    "operator": "not_matches",
                    "value": "immortalized cell line",
                    "strength": "hard",
                    "scope": "sample",
                    "evidence_required": True,
                    "source": "user",
                }
            ],
        }
    )

    matching = [
        item
        for item in request.scientific_constraints
        if item.label == "Exclude immortalized cell lines"
    ]
    assert len(matching) == 1


def test_defaults_can_update_strategy_and_offer_confirmation_without_starting_search(
    monkeypatch,
):
    patch = {
        "objective": "先摸清人源免疫肽公开数据",
        "task_type": "browse_only",
        "run_horizon": "candidates_only",
        "species": ["human"],
        "species_policy": "prefer",
        "special_themes": ["immunopeptidomics"],
        "coverage_mode": "curated",
        "target_project_count": 20,
        "quota_flexibility": "recommended",
    }
    started: list[dict[str, Any]] = []
    monkeypatch.setattr(web_app, "_run_web_discovery", lambda body, **_kwargs: started.append(body))
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "update_strategy",
            "assistant_message": "已应用推荐默认；请确认这张策略后再搜索。",
            "tool_calls": [
                {"name": "update_strategy", "arguments": {"patch": patch}}
            ],
            "ready_for_confirm": True,
            "gap_report": {
                "required_missing": [],
                "optional_missing": [],
                "ready_for_confirm": True,
            },
        },
        user_message="按你推荐的探索默认填好，但先别搜索。",
    )

    assert result["action"] == "update_strategy"
    assert result["ready_for_confirm"] is True
    assert result["gap_report"]["ready_for_confirm"] is True
    assert started == []


def test_natural_language_confirmation_is_an_explicit_agent_action_only_in_context(monkeypatch):
    result, llm = _run_turn(
        monkeypatch,
        {
            "action": "confirm_strategy",
            "assistant_message": "已确认当前这版策略，可以交给搜索入口。",
            "tool_calls": [],
        },
        user_message="就照刚才展示的这一版执行，不需要再改。",
        **_ready_context(),
    )

    assert result["action"] == "confirm_strategy"
    assert result["mode"] == "confirm_strategy"
    assert result["tool_calls"] == [
        {
            "name": "confirm_strategy",
            "arguments": {"strategy_fingerprint": result["strategy_fingerprint"]},
        }
    ]
    assert len(result["strategy_fingerprint"]) == 64
    assert result["extra_fields"] == {}
    assert len(llm.calls) == 1


@pytest.mark.parametrize(
    "context",
    [
        {
            "phase": "grilling",
            "intent_snapshot": {"task_type": "browse_only"},
            "gap_report": {"required_missing": [], "ready_for_confirm": True},
        },
        {
            "phase": "awaiting_confirm",
            "intent_snapshot": {},
            "gap_report": {"required_missing": [], "ready_for_confirm": True},
        },
        {
            "phase": "awaiting_confirm",
            "intent_snapshot": {"task_type": "browse_only"},
            "gap_report": {"required_missing": ["coverage"], "ready_for_confirm": False},
        },
        {
            **_ready_context(),
            "pending_strategy_fingerprint": "stale-snapshot-token",
        },
    ],
)
def test_confirmation_action_fails_closed_outside_current_awaiting_snapshot(
    monkeypatch,
    context,
):
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "confirm_strategy",
            "assistant_message": "模型试图确认。",
            "tool_calls": [],
        },
        user_message="好的",
        **context,
    )

    assert result["action"] in {"advise", "clarify"}
    assert result["tool_calls"] == []
    assert result["extra_fields"] == {}
    assert result["confirmation_rejected_reason"]


def test_confirmation_words_are_not_inferred_when_model_returns_chat(monkeypatch):
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "chat",
            "assistant_message": "继续聊这版策略。",
            "tool_calls": [],
        },
        user_message="确认并开始",
        **_ready_context(),
    )

    assert result["action"] == "chat"
    assert result["tool_calls"] == []


def _agent_generated_task_decision() -> dict[str, Any]:
    return {
        "focus": "task_type",
        "question": "Which task should we use?",
        "recommendation": {
            "id": "browse",
            "label": "Browse first",
            "reason": "Safest exploratory start",
        },
        "options": [
            {
                "id": "browse",
                "label": "Browse first",
                "reason": "Safest exploratory start",
                "strategy_patch": {"task_type": "browse_only"},
            },
            {
                "id": "rt",
                "label": "RT prediction",
                "reason": "Build an RT model",
                "strategy_patch": {"task_type": "rt_prediction"},
            },
        ],
        "allow_free_text": True,
    }


def test_numeric_reply_resolves_agent_generated_option_without_repeating_it(monkeypatch):
    pending = _agent_generated_task_decision()
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "update_strategy",
            "assistant_message": "Applied the selected task option.",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {"patch": {"task_type": "browse_only"}},
                }
            ],
            "next_decision": pending,
        },
        user_message="1",
        phase="grilling",
        pending_decision=pending,
        intent_snapshot={
            "objective": "Browse public proteomics projects",
            "task_type": "browse_only",
            "run_horizon": "candidates_only",
            "target_project_count": 20,
        },
        gap_report={"required_missing": [], "optional_missing": [], "ready_for_confirm": True},
    )

    assert result["action"] == "ready_to_confirm"
    assert "next_decision" not in result
    assert "Browse first" in result["assistant_message"]


def test_numeric_reply_can_drive_a_generic_strategy_tool_event(monkeypatch):
    pending = _agent_generated_task_decision()
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "update_strategy",
            "assistant_message": "Applied the selected option.",
            "turn_interpretation": {
                "commitments": [
                    {"field": "task_type", "value": "browse_only", "source": "1"}
                ]
            },
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {"patch": {"task_type": "browse_only"}},
                }
            ],
        },
        user_message="1",
        phase="grilling",
        pending_decision=pending,
        intent_snapshot={},
        gap_report={
            "required_missing": ["task", "horizon"],
            "optional_missing": [],
            "ready_for_confirm": False,
        },
    )

    assert result["action"] == "update_strategy"
    assert result["extra_fields"] == {"task_type": "browse_only"}
    assert result["resolved_decision"]["selected_option_id"] == "browse"
    assert result["decision_memory"][0]["selected_option_id"] == "browse"


def test_numeric_selected_option_is_grounded_by_its_active_decision_context(monkeypatch):
    pending = {
        "focus": "immunopeptidomics use",
        "target_fields": ["task_type", "objective"],
        "question": "Browse first or train a model?",
        "recommendation": {
            "id": "browse_explore",
            "label": "Browse first",
            "reason": "Survey the available projects before modeling.",
        },
        "options": [
            {"id": "browse_explore", "label": "Browse first"},
            {"id": "denovo", "label": "Train de novo"},
        ],
        "allow_free_text": True,
    }
    explicit_patch = {
        "task_type": "browse_only",
        "objective": "Survey public immunopeptidomics projects",
    }
    monkeypatch.setattr(
        web_app,
        "_run_discovery_patch_verifier_agents_sdk",
        lambda *_args, **_kwargs: pytest.fail(
            "A server-resolved option must not be re-read as a context-free number"
        ),
    )

    result, _llm = _run_turn(
        monkeypatch,
        {
            "_agent_runtime": "openai_agents",
            "action": "update_strategy",
            "assistant_message": "We will browse first.",
            "turn_interpretation": {
                "commitments": [
                    {"field": "task_type", "value": "browse_only", "source": "1"},
                    {
                        "field": "objective",
                        "value": "Survey public immunopeptidomics projects",
                        "source": "1",
                    },
                ]
            },
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {"patch": explicit_patch},
                }
            ],
        },
        user_message="1",
        phase="grilling",
        pending_decision=pending,
        intent_snapshot={"task_type": "", "objective": ""},
        decision_memory=[],
        resolved_fields=[],
        gap_report={
            "required_missing": ["task_type", "run_horizon"],
            "optional_missing": [],
            "ready_for_confirm": False,
        },
    )

    assert result["action"] == "update_strategy"
    assert result["extra_fields"] == explicit_patch
    assert result["resolved_decision"]["selected_option_id"] == "browse_explore"
    assert result.get("contract_errors") in (None, [])


def test_predeclared_option_patch_blocks_model_from_inventing_plan_only(monkeypatch):
    """Regression for the captured build-training -> plan_only production bug."""

    pending = {
        "focus": "immunopeptidomics use",
        # Deliberately reproduce the unsafe model-authored scope from the real
        # session. The server must derive authority from option patches instead.
        "target_fields": ["objective", "task_type", "run_horizon"],
        "question": "先浏览还是构建训练集？",
        "recommendation": {
            "id": "browse",
            "label": "先浏览",
            "reason": "先了解数据范围。",
        },
        "options": [
            {
                "id": "browse",
                "label": "先浏览",
                "strategy_patch": {
                    "task_type": "browse_only",
                    "objective": "浏览免疫肽组学公开项目",
                },
            },
            {
                "id": "build_training",
                "label": "构建训练集",
                "strategy_patch": {
                    "task_type": "other",
                    "objective": "构建免疫肽组学机器学习训练集",
                },
            },
        ],
    }
    result, _llm = _run_turn(
        monkeypatch,
        {
            "_agent_runtime": "openai_agents",
            "action": "update_strategy",
            "assistant_message": "已选择构建训练集，并只做计划。",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {
                        "patch": {
                            "task_type": "other",
                            "objective": "构建免疫肽组学机器学习训练集",
                            "run_horizon": "plan_only",
                        }
                    },
                }
            ],
        },
        user_message="2",
        phase="grilling",
        pending_decision=pending,
        intent_snapshot={
            "task_type": "",
            "objective": "免疫肽组学",
            "run_horizon": "",
        },
        gap_report={
            "required_missing": ["task", "horizon", "coverage"],
            "optional_missing": [],
            "ready_for_confirm": False,
        },
    )

    assert result["action"] == "update_strategy"
    assert result["extra_fields"] == {
        "task_type": "other",
        "objective": "构建免疫肽组学机器学习训练集",
    }
    assert "run_horizon" not in result["extra_fields"]
    assert result["option_resolution"]["contract"] == "predeclared_v1"
    assert result["option_resolution"]["discarded_model_fields"] == ["run_horizon"]
    assert result["resolved_decision"]["target_fields"] == ["task_type", "objective"]


def test_next_decision_derives_scope_from_predeclared_option_patches():
    decision = web_app._normalise_discovery_next_decision(
        {
            "focus": "training direction",
            "target_fields": ["task_type", "objective", "run_horizon"],
            "question": "Which training direction?",
            "recommendation": {
                "id": "denovo",
                "label": "De novo",
                "reason": "It needs high-quality MS/MS labels.",
            },
            "options": [
                {
                    "id": "denovo",
                    "label": "De novo",
                    "strategy_patch": {
                        "task_type": "denovo",
                        "objective": "Build an immunopeptide de novo training set",
                    },
                },
                {
                    "id": "psm",
                    "label": "PSM scoring",
                    "strategy_patch": {
                        "task_type": "psm_scoring",
                        "objective": "Build an immunopeptide PSM scoring set",
                    },
                },
            ],
        }
    )

    assert decision is not None
    assert decision["target_fields"] == ["task_type", "objective"]
    assert decision["option_patch_contract"] == "predeclared_v1"
    assert decision["recommendation"]["strategy_patch"]["task_type"] == "denovo"


def test_task_decision_cannot_overwrite_resolved_open_ended_search_scale(monkeypatch):
    """A downstream-task choice must preserve the user's explicit exhaustive scope."""

    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "clarify",
            "assistant_message": "Choose the downstream analysis.",
            "tool_calls": [],
            "next_decision": {
                "focus": "downstream_task",
                "target_fields": [
                    "task_type",
                    "coverage_mode",
                    "target_project_count",
                    "quota_flexibility",
                ],
                "question": "What will you do with these data?",
                "recommendation": {
                    "id": "browse_only",
                    "label": "Browse first",
                    "reason": "Explore the public data landscape first.",
                },
                "options": [
                    {
                        "id": "browse_only",
                        "label": "Browse first",
                        "reason": "Explore the public data landscape first.",
                        "strategy_patch": {
                            "task_type": "browse_only",
                            "coverage_mode": "curated",
                            "target_project_count": 20,
                            "quota_flexibility": "recommended",
                        },
                    },
                    {
                        "id": "denovo",
                        "label": "De novo",
                        "reason": "Train a de novo sequencing model.",
                        "strategy_patch": {"task_type": "denovo"},
                    },
                ],
                "revisit_existing": False,
            },
        },
        user_message="Find all human immunopeptidomics data in PRIDE.",
        intent_snapshot={
            "task_type": "",
            "coverage_mode": "exhaustive",
            "target_project_count": None,
            "quota_flexibility": "open_ended",
        },
        resolved_fields=[
            "coverage_mode",
            "target_project_count",
            "quota_flexibility",
        ],
        gap_report={
            "required_missing": ["task_type"],
            "optional_missing": [],
            "ready_for_confirm": False,
        },
    )

    decision = result["next_decision"]
    assert decision["target_fields"] == ["task_type"]
    assert decision["options"][0]["strategy_patch"] == {
        "task_type": "browse_only"
    }


def test_numeric_task_choice_preserves_resolved_open_ended_search_scale(monkeypatch):
    pending = {
        "focus": "downstream_task",
        "question": "What will you do with these data?",
        "recommendation": {
            "id": "browse_only",
            "label": "Browse first",
            "reason": "Explore the public data landscape first.",
        },
        "options": [
            {
                "id": "browse_only",
                "label": "Browse first",
                "reason": "Explore the public data landscape first.",
                "strategy_patch": {
                    "task_type": "browse_only",
                    "coverage_mode": "curated",
                    "target_project_count": 20,
                    "quota_flexibility": "recommended",
                },
            },
            {
                "id": "denovo",
                "label": "De novo",
                "reason": "Train a de novo sequencing model.",
                "strategy_patch": {"task_type": "denovo"},
            },
        ],
    }
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "update_strategy",
            "assistant_message": "Applied browse-only.",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {
                        "patch": {
                            "task_type": "browse_only",
                            "coverage_mode": "curated",
                            "target_project_count": 20,
                            "quota_flexibility": "recommended",
                        }
                    },
                }
            ],
        },
        user_message="1",
        pending_decision=pending,
        intent_snapshot={
            "task_type": "",
            "coverage_mode": "exhaustive",
            "target_project_count": None,
            "quota_flexibility": "open_ended",
        },
        resolved_fields=[
            "coverage_mode",
            "target_project_count",
            "quota_flexibility",
        ],
        gap_report={
            "required_missing": ["task_type"],
            "optional_missing": [],
            "ready_for_confirm": False,
        },
    )

    assert result["extra_fields"] == {"task_type": "browse_only"}
    assert result["option_resolution"]["discarded_model_fields"] == [
        "coverage_mode",
        "quota_flexibility",
        "target_project_count",
    ]


def test_numeric_selected_option_cannot_authorize_out_of_scope_fields(monkeypatch):
    pending = {
        "focus": "task choice",
        "target_fields": ["task_type"],
        "question": "Browse or model?",
        "recommendation": {
            "id": "browse",
            "label": "Browse",
            "reason": "Explore first.",
        },
        "options": [
            {"id": "browse", "label": "Browse"},
            {"id": "denovo", "label": "De novo"},
        ],
        "allow_free_text": True,
    }
    verifier_calls: list[dict[str, Any]] = []

    def reject_out_of_scope(*_args, **kwargs):
        verifier_calls.append(kwargs)
        return {
            "verified": False,
            "verdict": "reject",
            "patch": {},
            "rationale": "The project count was not authorized by this option.",
        }

    # Force critic: multi-field low-risk whitelist would otherwise skip.
    monkeypatch.setattr(
        web_app,
        "_discovery_low_risk_single_field_verifier_skip",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        web_app,
        "_run_discovery_patch_verifier_agents_sdk",
        reject_out_of_scope,
    )
    result, _llm = _run_turn(
        monkeypatch,
        {
            "_agent_runtime": "openai_agents",
            "action": "update_strategy",
            "assistant_message": "Browse first and silently change the quota.",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {
                        "patch": {
                            "task_type": "browse_only",
                            "target_project_count": 25,
                        }
                    },
                }
            ],
        },
        user_message="1",
        phase="grilling",
        pending_decision=pending,
        intent_snapshot={"task_type": "", "target_project_count": None},
        decision_memory=[],
        resolved_fields=[],
        gap_report={
            "required_missing": ["task_type"],
            "optional_missing": [],
            "ready_for_confirm": False,
        },
    )

    # Product: option-scoped numeric replies keep only target_fields; out-of-scope
    # quota must not write, but the authorized task_type may still apply.
    assert result["action"] == "update_strategy"
    assert result["extra_fields"] == {"task_type": "browse_only"}
    assert "target_project_count" not in result["extra_fields"]
    assert result["tool_calls"] == [
        {
            "name": "update_strategy",
            "arguments": {"patch": {"task_type": "browse_only"}},
        }
    ]


@pytest.mark.parametrize(
    ("pending", "explicit_patch", "intent_snapshot", "selected_option_id"),
    [
        (
            {
                "focus": "species scope",
                "target_fields": ["species", "species_policy"],
                "question": "Which species scope should be used?",
                "recommendation": {
                    "id": "all_species",
                    "label": "Keep species open",
                    "reason": "This is an exploratory search.",
                },
                "options": [
                    {"id": "all_species", "label": "Keep species open"},
                    {"id": "human_only", "label": "Human only"},
                ],
                "allow_free_text": True,
            },
            {"species": [], "species_policy": "open"},
            {"species": [], "species_policy": "open"},
            "all_species",
        ),
        (
            {
                "focus": "MHC class scope",
                "target_fields": ["scientific_constraints"],
                "question": "Which MHC classes should be included?",
                "recommendation": {
                    "id": "both_open",
                    "label": "Keep both classes open",
                    "reason": "This preserves exploratory coverage.",
                },
                "options": [
                    {"id": "both_open", "label": "Keep both classes open"},
                    {"id": "mhc_i_only", "label": "MHC-I only"},
                    {"id": "mhc_ii_only", "label": "MHC-II only"},
                ],
                "allow_free_text": True,
            },
            {"scientific_constraints": []},
            {"scientific_constraints": []},
            "both_open",
        ),
    ],
    ids=["open-species", "open-scientific-constraint"],
)
def test_selected_default_value_is_kept_as_an_explicit_resolution_delta(
    monkeypatch,
    pending,
    explicit_patch,
    intent_snapshot,
    selected_option_id,
):
    """An accepted open/default option must resolve even when its value is unchanged."""

    monkeypatch.setattr(
        web_app,
        "_run_discovery_patch_verifier_agents_sdk",
        lambda *_args, **_kwargs: pytest.fail(
            "A pure decision-state delta must not need semantic re-interpretation"
        ),
    )
    result, _llm = _run_turn(
        monkeypatch,
        {
            "_agent_runtime": "openai_agents",
            "action": "update_strategy",
            "assistant_message": "The open choice is recorded.",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {"patch": explicit_patch},
                }
            ],
        },
        user_message="1",
        phase="grilling",
        pending_decision=pending,
        intent_snapshot=intent_snapshot,
        decision_memory=[],
        resolved_fields=[],
        gap_report={
            "required_missing": ["run_horizon"],
            "optional_missing": [],
            "ready_for_confirm": False,
        },
    )

    assert result["action"] == "update_strategy"
    assert result["extra_fields"] == explicit_patch
    assert result["resolved_decision"]["selected_option_id"] == selected_option_id
    assert result["resolved_decision"]["selected_values"] == explicit_patch
    assert result["decision_memory"][0]["selected_option_id"] == selected_option_id

    repeated, _llm = _run_turn(
        monkeypatch,
        {
            "action": "clarify",
            "assistant_message": "Choose the same scope again.",
            "tool_calls": [],
            "next_decision": pending,
        },
        user_message="continue",
        phase="grilling",
        intent_snapshot=intent_snapshot,
        decision_memory=result["decision_memory"],
        resolved_fields=list(explicit_patch),
        gap_report={
            "required_missing": ["run_horizon"],
            "optional_missing": [],
            "ready_for_confirm": False,
        },
    )

    assert "next_decision" not in repeated
    assert repeated["action"] == "advise"


def test_mixed_value_and_resolution_delta_survives_semantic_verification(monkeypatch):
    pending = {
        "focus": "species scope",
        "target_fields": ["species", "species_policy"],
        "question": "How should human studies be prioritized?",
        "recommendation": {
            "id": "human_prefer",
            "label": "Prefer human, keep others",
            "reason": "Human immunopeptidomics is best annotated.",
        },
        "options": [
            {"id": "human_prefer", "label": "Prefer human, keep others"},
            {"id": "human_only", "label": "Human only"},
        ],
        "allow_free_text": True,
    }
    explicit_patch = {"species": ["human"], "species_policy": "prefer"}
    monkeypatch.setattr(
        web_app,
        "_run_discovery_patch_verifier_agents_sdk",
        lambda *_args, **kwargs: {
            "verified": True,
            "verdict": "accept",
            "patch": dict(kwargs["proposed_patch"]),
            "evidence": [
                {"field": field, "source": "1"}
                for field in kwargs["proposed_patch"]
            ],
            "rationale": "The active option grounds both target fields.",
        },
    )

    result, _llm = _run_turn(
        monkeypatch,
        {
            "_agent_runtime": "openai_agents",
            "action": "update_strategy",
            "assistant_message": "Human is now preferred without excluding other species.",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {"patch": explicit_patch},
                }
            ],
        },
        user_message="1",
        phase="grilling",
        pending_decision=pending,
        intent_snapshot={"species": ["human"], "species_policy": "open"},
        decision_memory=[],
        resolved_fields=[],
        gap_report={
            "required_missing": ["run_horizon"],
            "optional_missing": [],
            "ready_for_confirm": False,
        },
    )

    assert result["action"] == "update_strategy"
    assert result["extra_fields"] == explicit_patch
    assert result["resolved_decision"]["selected_values"] == explicit_patch


def test_grounded_free_text_open_answer_is_a_resolution_delta(monkeypatch):
    pending = {
        "focus": "species scope",
        "target_fields": ["species", "species_policy"],
        "question": "Which species scope should be used?",
        "recommendation": {
            "id": "human_prefer",
            "label": "Prefer human",
            "reason": "Human data is best annotated.",
        },
        "options": [
            {"id": "human_prefer", "label": "Prefer human"},
            {"id": "all_species", "label": "All species"},
        ],
        "allow_free_text": True,
    }
    monkeypatch.setattr(
        web_app,
        "_run_discovery_patch_verifier_agents_sdk",
        lambda *_args, **_kwargs: pytest.fail(
            "A grounded no-op commitment must not need semantic re-interpretation"
        ),
    )
    result, _llm = _run_turn(
        monkeypatch,
        {
            "_agent_runtime": "openai_agents",
            "action": "update_strategy",
            "assistant_message": "All species remain open.",
            "turn_interpretation": {
                "commitments": [
                    {"field": "species", "value": [], "source": "anything works"},
                    {
                        "field": "species_policy",
                        "value": "open",
                        "source": "anything works",
                    },
                ]
            },
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {
                        "patch": {"species": [], "species_policy": "open"}
                    },
                }
            ],
            # A weak model may try to ask the same question again. The server
            # must recognize the explicit resolution before returning it.
            "next_decision": pending,
        },
        user_message="anything works",
        phase="grilling",
        pending_decision=pending,
        intent_snapshot={"species": [], "species_policy": "open"},
        decision_memory=[],
        resolved_fields=[],
        gap_report={
            "required_missing": ["run_horizon"],
            "optional_missing": [],
            "ready_for_confirm": False,
        },
    )

    assert result["action"] == "update_strategy"
    assert result["extra_fields"] == {"species": [], "species_policy": "open"}
    assert "next_decision" not in result
    assert "resolved_decision" not in result


def test_predeclared_numeric_option_self_repairs_invalid_later_model_patch(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv(
        "AGENT_DIALOGUE_SESSION_DB",
        str(tmp_path / "failed_numeric_option.sqlite"),
    )
    pending = _agent_generated_task_decision()
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "update_strategy",
            "assistant_message": "Applied the selected option.",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {"patch": {"task_type": "not_a_real_task"}},
                }
            ],
            "next_decision": pending,
        },
        user_message="1",
        phase="grilling",
        session_id="failed-numeric-option",
        pending_decision=pending,
        intent_snapshot={},
        decision_memory=[],
        gap_report={
            "required_missing": ["task"],
            "optional_missing": [],
            "ready_for_confirm": False,
        },
    )

    assert result["tool_calls"] == [
        {
            "name": "update_strategy",
            "arguments": {"patch": {"task_type": "browse_only"}},
        }
    ]
    assert result["resolved_decision"]["selected_option_id"] == "browse"
    assert result["decision_memory"][0]["selected_option_id"] == "browse"
    # After applying the predeclared option, remaining critical gaps (e.g.
    # objective) may still be grilled via a server-synthesized next_decision.
    next_decision = result.get("next_decision")
    if next_decision is not None:
        assert next_decision["question"]
        assert 2 <= len(next_decision["options"]) <= 8
        assert next_decision["recommendation"].get("reason")
    _history, memory = web_app._load_discovery_dialogue_session(
        "failed-numeric-option",
        [],
    )
    assert memory[0]["selected_option_id"] == "browse"


def test_predeclared_numeric_option_discards_unrelated_later_model_patch(monkeypatch):
    pending = _agent_generated_task_decision()
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "update_strategy",
            "assistant_message": "Updated coverage, not the task choice.",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {"patch": {"coverage_mode": "curated"}},
                }
            ],
            "next_decision": pending,
        },
        user_message="1",
        phase="grilling",
        pending_decision=pending,
        intent_snapshot={},
        decision_memory=[],
        gap_report={
            "required_missing": ["task"],
            "optional_missing": [],
            "ready_for_confirm": False,
        },
    )

    assert result["tool_calls"] == [
        {
            "name": "update_strategy",
            "arguments": {"patch": {"task_type": "browse_only"}},
        }
    ]
    assert result["decision_memory"][0]["selected_option_id"] == "browse"
    # Same as self-repair path: option write may leave other critical gaps open.
    next_decision = result.get("next_decision")
    if next_decision is not None:
        assert next_decision["question"]
        assert 2 <= len(next_decision["options"]) <= 8
    assert result["option_resolution"]["discarded_model_fields"] == ["coverage_mode"]


def test_selected_enum_repairs_only_an_explicit_malformed_tool_event(monkeypatch):
    pending = _agent_generated_task_decision()
    pending["focus"] = "objective_and_task"
    pending["recommendation"]["id"] = "browse_only"
    pending["options"][0]["id"] = "browse_only"
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "update_strategy",
            "assistant_message": "Applied the selected option.",
            "turn_interpretation": {
                "commitments": [
                    {
                        "field": "task_type",
                        "value": {"value": "browse_only", "label": "Browse first"},
                        "source": "1",
                    }
                ]
            },
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {
                        "patch": {
                            "task_type": {
                                "value": "browse_only",
                                "label": "Browse first",
                            }
                        }
                    },
                }
            ],
        },
        user_message="1",
        phase="grilling",
        pending_decision=pending,
        intent_snapshot={},
        gap_report={
            "required_missing": ["task"],
            "optional_missing": [],
            "ready_for_confirm": False,
        },
    )

    assert result["action"] == "update_strategy"
    assert result["extra_fields"] == {"task_type": "browse_only"}
    assert result.get("contract_errors") is None


@pytest.mark.parametrize(
    ("reply", "expected_id"),
    [
        ("1", "browse"),
        ("browse", "browse"),
        ("Browse first", "browse"),
        ("RT prediction", "rt"),
    ],
)
def test_pending_selection_resolves_only_against_dynamic_option_context(
    reply: str,
    expected_id: str,
):
    selected = web_app._resolve_discovery_pending_selection(
        reply,
        _agent_generated_task_decision(),
    )

    assert selected is not None
    assert selected["option"]["id"] == expected_id
    assert selected["explicit_acceptance"] is True


@pytest.mark.parametrize("reply", ["3", "15", "something else"])
def test_pending_selection_does_not_invent_an_option(reply: str):
    assert (
        web_app._resolve_discovery_pending_selection(
            reply,
            _agent_generated_task_decision(),
        )
        is None
    )


def test_decision_identity_survives_focus_paraphrasing():
    original = {
        **_agent_generated_task_decision(),
        "target_fields": ["task_type"],
    }
    paraphrased = {**original, "focus": "objective_and_task"}

    assert web_app._same_discovery_decision(original, paraphrased) is True


def test_decision_memory_keeps_same_option_ids_for_distinct_fallback_focuses():
    memory = web_app._normalise_discovery_decision_memory(
        [
            {
                "focus": "first_unmapped_tradeoff",
                "target_fields": [],
                "option_ids": ["recommended", "open"],
                "selected_option_id": "recommended",
            },
            {
                "focus": "second_unmapped_tradeoff",
                "target_fields": [],
                "option_ids": ["recommended", "open"],
                "selected_option_id": "open",
            },
        ]
    )

    assert [item["focus"] for item in memory] == [
        "first_unmapped_tradeoff",
        "second_unmapped_tradeoff",
    ]


def test_same_option_ids_for_different_target_fields_do_not_collide(monkeypatch):
    pending = {
        "focus": "labeling_strategy",
        "target_fields": ["labeling_strategy"],
        "question": "How should labeling be handled?",
        "recommendation": {
            "id": "recommended",
            "label": "Use the recommendation",
            "reason": "It is the safest labeling default.",
        },
        "options": [
            {"id": "recommended", "label": "Use the recommendation"},
            {"id": "open", "label": "Keep it open"},
        ],
        "allow_free_text": True,
    }
    next_decision = {
        **pending,
        "focus": "acquisition_mode",
        "target_fields": ["acquisition_mode"],
        "question": "How should acquisition be handled?",
        "recommendation": {
            "id": "recommended",
            "label": "Use the recommendation",
            "reason": "It is the safest acquisition default.",
        },
    }
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "update_strategy",
            "assistant_message": "The labeling choice is recorded.",
            "turn_interpretation": {
                "commitments": [
                    {
                        "field": "labeling_strategy",
                        "value": "any",
                        "source": "1",
                    }
                ]
            },
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {"patch": {"labeling_strategy": "any"}},
                }
            ],
            "next_decision": next_decision,
        },
        user_message="1",
        phase="grilling",
        pending_decision=pending,
        intent_snapshot={},
        decision_memory=[],
        gap_report={
            "required_missing": ["acquisition"],
            "optional_missing": [],
            "ready_for_confirm": False,
        },
    )

    assert result["resolved_decision"]["target_fields"] == ["labeling_strategy"]
    assert result["next_decision"]["target_fields"] == ["acquisition_mode"]


def _coverage_decision(*, revisit_existing: bool = False) -> dict[str, Any]:
    return {
        "focus": "coverage_mode",
        "target_fields": ["coverage_mode", "target_project_count"],
        "question": "How broad should this search be?",
        "recommendation": {
            "id": "curated",
            "label": "Curated",
            "reason": "A focused first pass is easier to review.",
        },
        "options": [
            {"id": "curated", "label": "Curated", "reason": "About 20"},
            {"id": "balanced", "label": "Balanced", "reason": "More breadth"},
            {"id": "exhaustive", "label": "Exhaustive", "reason": "Maximum recall"},
        ],
        "revisit_existing": revisit_existing,
        "allow_free_text": True,
    }


def test_resolved_decision_memory_blocks_a_later_question_loop(monkeypatch):
    decision = _coverage_decision()
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "clarify",
            "assistant_message": "Let's choose coverage again.",
            "tool_calls": [],
            "next_decision": decision,
        },
        user_message="继续",
        phase="grilling",
        decision_memory=[
            {
                "focus": "coverage_mode",
                "target_fields": ["coverage_mode", "target_project_count"],
                "option_ids": ["curated", "balanced", "exhaustive"],
                "selected_option_id": "curated",
                "selected_option_label": "Curated",
            }
        ],
        intent_snapshot={
            "objective": "Browse a focused proteomics landscape",
            "task_type": "browse_only",
            "run_horizon": "candidates_only",
            "coverage_mode": "curated",
            "target_project_count": 20,
        },
        gap_report={
            "required_missing": [],
            "optional_missing": ["labeling"],
            "ready_for_confirm": True,
        },
    )

    assert "next_decision" not in result
    assert result["action"] == "ready_to_confirm"
    assert "不会重复" in result["assistant_message"]


def test_redundant_next_question_does_not_erase_update_acknowledgement(monkeypatch):
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "update_strategy",
            "assistant_message": "已把物种改为斑马鱼。",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {"patch": {"species": ["zebrafish"]}},
                }
            ],
            "next_decision": {
                "focus": "task_type",
                "target_fields": ["task_type"],
                "question": "Which downstream task?",
                "recommendation": {
                    "id": "browse_only",
                    "label": "Browse",
                    "reason": "The card already records browsing.",
                },
                "options": [
                    {"id": "browse_only", "label": "Browse", "reason": "Explore"},
                    {"id": "denovo", "label": "De novo", "reason": "Model"},
                ],
                "allow_free_text": True,
            },
        },
        user_message="把物种改为斑马鱼，其它不变。",
        intent_snapshot={"task_type": "browse_only", "species": ["rat"]},
    )

    assert result["action"] == "update_strategy"
    assert result["extra_fields"] == {"species": ["zebrafish"]}
    assert "next_decision" not in result
    assert result["assistant_message"].startswith("已把物种改为斑马鱼。")
    assert "不会重复询问" in result["assistant_message"]


def test_changed_or_cleared_fields_reopen_stale_decision_memory(
    monkeypatch,
):
    decision = _coverage_decision()
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "clarify",
            "assistant_message": "Let's choose the now-open coverage decision.",
            "tool_calls": [],
            "next_decision": decision,
        },
        user_message="继续",
        phase="grilling",
        decision_memory=[
            {
                "focus": "coverage_mode",
                "target_fields": ["coverage_mode", "target_project_count"],
                "option_ids": ["curated", "balanced", "exhaustive"],
                "selected_option_id": "curated",
                "selected_values": {
                    "coverage_mode": "curated",
                    "target_project_count": 20,
                },
            }
        ],
        intent_snapshot={"coverage_mode": None, "target_project_count": None},
        gap_report={
            "required_missing": [],
            "optional_missing": ["coverage"],
            "ready_for_confirm": False,
        },
    )

    assert result["next_decision"]["focus"] == "coverage_mode"
    assert result["decision_memory"] == []


def test_replaced_fields_remove_old_selected_values_from_prompt_memory():
    memory = web_app._normalise_discovery_decision_memory(
        [
            {
                "focus": "coverage_mode",
                "target_fields": ["coverage_mode", "target_project_count"],
                "option_ids": ["curated", "balanced", "exhaustive"],
                "selected_option_id": "curated",
                "selected_values": {
                    "coverage_mode": "curated",
                    "target_project_count": 20,
                },
            }
        ]
    )

    assert web_app._filter_discovery_decision_memory_for_snapshot(
        memory,
        intent_snapshot={"coverage_mode": "balanced", "target_project_count": 50},
        resolved_fields=set(),
    ) == []


def test_explicit_revisit_can_reopen_an_existing_strategy_field(monkeypatch):
    decision = _coverage_decision(revisit_existing=True)
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "clarify",
            "assistant_message": "可以，我们重新比较覆盖范围。",
            "tool_calls": [],
            "next_decision": decision,
        },
        user_message="我想重新考虑覆盖范围",
        phase="grilling",
        decision_memory=[
            {
                "focus": "coverage_mode",
                "target_fields": ["coverage_mode", "target_project_count"],
                "option_ids": ["curated", "balanced", "exhaustive"],
                "selected_option_id": "curated",
            }
        ],
        intent_snapshot={"coverage_mode": "curated", "target_project_count": 20},
        gap_report={
            "required_missing": [],
            "optional_missing": [],
            "ready_for_confirm": True,
        },
    )

    assert result["next_decision"]["focus"] == "coverage_mode"
    assert result["next_decision"]["revisit_existing"] is True


def test_dynamic_decision_keeps_all_material_options():
    raw = {
        "focus": "labeling_strategy",
        "target_fields": ["labeling_strategy"],
        "question": "Which labeling family fits this study?",
        "recommendation": {
            "id": "label_free",
            "label": "Label-free",
            "reason": "It is the least restrictive exploratory default.",
        },
        "options": [
            {"id": "label_free", "label": "Label-free"},
            {"id": "tmt", "label": "TMT"},
            {"id": "itraq", "label": "iTRAQ"},
            {"id": "silac", "label": "SILAC"},
            {"id": "dimethyl", "label": "Dimethyl"},
            {"id": "any", "label": "Keep open"},
        ],
        "option_mode": "expanded",
    }

    decision = web_app._normalise_discovery_next_decision(raw)

    assert decision is not None
    assert [option["id"] for option in decision["options"]] == [
        "label_free",
        "tmt",
        "itraq",
        "silac",
        "dimethyl",
        "any",
    ]
    assert decision["target_fields"] == ["labeling_strategy"]


def test_expanded_enum_decision_fills_material_labeling_alternatives(monkeypatch):
    result, llm = _run_turn(
        monkeypatch,
        {
            "action": "clarify",
            "assistant_message": (
                "Besides label-free, relevant alternatives include TMT, iTRAQ, "
                "SILAC, and dimethyl labeling."
            ),
            "tool_calls": [],
            "next_decision": {
                "focus": "labeling_strategy",
                "target_fields": ["labeling_strategy"],
                "question": "Which labeling strategy should we compare?",
                "recommendation": {
                    "id": "label_free",
                    "label": "Label-free",
                    "reason": "It is the least restrictive exploratory default.",
                },
                "options": [
                    {"id": "label_free", "label": "Label-free"},
                    {"id": "any", "label": "Keep open"},
                ],
                "option_mode": "expanded",
                "revisit_existing": False,
            },
        },
        user_message="What other labeling methods are there? Please compare them.",
    )

    assert [option["id"] for option in result["next_decision"]["options"]] == [
        "label_free",
        "tmt",
        "itraq",
        "silac",
        "dimethyl",
        "any",
    ]
    assert result["next_decision"]["option_mode"] == "expanded"
    assert '"option_mode": "focused|expanded"' in llm.calls[0]["user_prompt"]


def test_labeling_discussion_expands_even_when_model_omits_option_mode(monkeypatch):
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "clarify",
            "assistant_message": "We can compare label-free, TMT, and SILAC.",
            "tool_calls": [],
            "next_decision": {
                "focus": "labeling_strategy",
                "target_fields": ["labeling_strategy"],
                "question": "Which labeling family should we use?",
                "recommendation": {
                    "id": "label_free",
                    "label": "Label-free",
                    "reason": "It keeps an exploratory search broad.",
                },
                "options": [
                    {"id": "label_free", "label": "Label-free"},
                    {"id": "any", "label": "Keep open"},
                ],
            },
        },
        user_message="What other labeling methods are available?",
    )

    assert [option["id"] for option in result["next_decision"]["options"]] == [
        "label_free",
        "tmt",
        "itraq",
        "silac",
        "dimethyl",
        "any",
    ]
    assert result["next_decision"]["option_mode"] == "expanded"


def test_focused_enum_decision_does_not_expand_from_an_empty_catalog_label():
    decision = web_app._normalise_discovery_next_decision(
        {
            "focus": "task_type",
            "target_fields": ["task_type"],
            "question": "What is the downstream task?",
            "recommendation": {
                "id": "browse_only",
                "label": "Browse first",
                "reason": "It is a flexible exploratory start.",
            },
            "options": [
                {"id": "browse_only", "label": "Browse first"},
                {"id": "denovo", "label": "De novo"},
                {"id": "psm_scoring", "label": "PSM scoring"},
                {"id": "other", "label": "Other"},
            ],
            "option_mode": "focused",
        }
    )

    expanded = web_app._expand_discovery_enum_decision_options(
        decision,
        "Browse, de novo, or PSM scoring are useful task-specific directions.",
    )

    assert expanded is not None
    assert expanded["option_mode"] == "focused"
    assert [option["id"] for option in expanded["options"]] == [
        "browse_only",
        "denovo",
        "psm_scoring",
        "other",
    ]


def test_sdk_boundary_drops_recommended_defaults_not_accepted_by_user():
    accepted, dropped = web_app._filter_discovery_unaccepted_recommendations(
        {
            "special_themes": ["immunopeptidomics"],
            "task_type": "browse_only",
            "species": ["human"],
            "species_policy": "prefer",
            "coverage_mode": "curated",
            "target_project_count": 20,
        },
        user_message="\u514d\u75ab\u80bd\u5427",
        selected_decision=None,
    )

    assert accepted == {"special_themes": ["immunopeptidomics"]}
    assert set(dropped) == {
        "task_type",
        "species",
        "species_policy",
        "coverage_mode",
        "target_project_count",
    }


def test_sdk_boundary_keeps_multi_field_values_stated_by_user():
    accepted, dropped = web_app._filter_discovery_unaccepted_recommendations(
        {
            "species": ["human"],
            "acquisition_mode": "dda",
            "labeling_strategy": "tmt",
            "target_project_count": 20,
            "quota_flexibility": "recommended",
        },
        user_message="Human DDA with TMT, about 20 projects",
        selected_decision=None,
    )

    assert dropped == []
    assert accepted == {
        "species": ["human"],
        "acquisition_mode": "dda",
        "labeling_strategy": "tmt",
        "target_project_count": 20,
        "quota_flexibility": "recommended",
    }


def test_sdk_session_persists_dialogue_and_resolved_decisions(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv(
        "AGENT_DIALOGUE_SESSION_DB",
        str(tmp_path / "dialogue_sessions.sqlite"),
    )
    resolved = {
        "focus": "coverage_mode",
        "target_fields": ["coverage_mode"],
        "option_ids": ["curated", "balanced", "exhaustive"],
        "selected_option_id": "curated",
        "selected_option_label": "Curated",
    }

    web_app._store_discovery_dialogue_session_turn(
        "session-test",
        user_message="1",
        assistant_message="已选择精选。",
        action="update_strategy",
        patch={"coverage_mode": "curated"},
        next_decision=None,
        resolved_decision=resolved,
    )
    history, decision_memory = web_app._load_discovery_dialogue_session(
        "session-test",
        [],
    )

    assert [item["role"] for item in history] == ["user", "assistant"]
    assert history[0]["content"] == "1"
    assert decision_memory[0]["selected_option_id"] == "curated"


def test_d1_agents_sdk_runs_real_strategy_tool_and_persistent_session(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv(
        "AGENT_DIALOGUE_SESSION_DB",
        str(tmp_path / "dialogue_agent.sqlite"),
    )
    final_payload = {
        "action": "update_strategy",
        "assistant_message": "TMT has been recorded as the labeling strategy.",
        "turn_interpretation": {
            "commitments": [
                {
                    "field": "labeling_strategy",
                    "value": "tmt",
                    "source": "Use TMT",
                }
            ],
            "consultations": [],
            "clause_audit": [
                {
                    "clause_id": "C1",
                    "classification": "commitment",
                    "decisions": [
                        {"field": "labeling_strategy", "value": "tmt"}
                    ],
                }
            ],
        },
        # This textual projection is deliberately wrong. The SDK-executed
        # function call below must be the only mutation authority.
        "tool_calls": [
            {
                "name": "update_strategy",
                "arguments": {"patch": {"labeling_strategy": "label_free"}},
            }
        ],
    }
    model = _DialogueScriptedModel(
        [
            (
                "update_strategy",
                {
                    "patch": {"labeling_strategy": "tmt"},
                    "response_json": json.dumps(final_payload),
                },
            ),
        ]
    )
    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)

    raw = web_app._run_discovery_dialogue_agents_sdk(
        client,
        system_prompt="You are a dialogue agent.",
        dialogue_history=[],
        state_prompt="Return JSON.",
        user_message="Use TMT",
        session_id="sdk-d1-test",
        model=model,
    )

    assert model.calls == 1
    assert raw["_agent_runtime"] == "openai_agents"
    # The SDK runner must not persist pre-validation tool output.  The web
    # boundary stores only the final, normalized turn after semantic review.
    assert raw["_sdk_session_managed"] is False
    assert raw["tool_calls"] == [
        {
            "name": "update_strategy",
            "arguments": {"patch": {"labeling_strategy": "tmt"}},
        }
    ]

    history, _memory = web_app._load_discovery_dialogue_session(
        "sdk-d1-test",
        [],
    )
    assert history == []


def test_d1_agents_sdk_recovers_provider_plain_text_as_non_mutating():
    plain_reply = "免疫肽是一个研究主题。你更关注哪种下游用途？"
    model = _DialogueScriptedModel([("final", plain_reply)])
    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)

    raw = web_app._run_discovery_dialogue_agents_sdk(
        client,
        system_prompt="You are a dialogue agent.",
        dialogue_history=[],
        state_prompt="Finish with one function tool.",
        user_message="免疫肽数据",
        session_id="plain-text-provider-test",
        model=model,
    )

    assert raw["action"] == "advise"
    assert raw["assistant_message"] == plain_reply
    assert raw["tool_calls"] == []
    assert raw["_provider_compatibility_recovery"] == {
        "mode": "plain_text_as_non_mutating",
        "chars": len(plain_reply),
        "advisor_calls": 0,
    }


def test_dialogue_json_action_compatibility_preserves_roles_and_typed_envelope():
    response = {
        "action": "update_strategy",
        "assistant_message": "已记录免疫肽主题。",
        "turn_interpretation": {
            "commitments": [
                {
                    "field": "special_themes",
                    "value": ["immunopeptidomics"],
                    "source": "免疫肽数据",
                }
            ]
        },
        "tool_calls": [
            {
                "name": "update_strategy",
                "arguments": {
                    "patch": {"special_themes": ["immunopeptidomics"]}
                },
            }
        ],
    }
    llm = _RoleAwareTurnLLM([response])

    raw = web_app._run_discovery_dialogue_json_compatibility(
        llm,
        system_prompt="manager system",
        dialogue_history=[
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好，请说目标。"},
        ],
        state_prompt="current state contract",
    )

    assert raw == response
    messages = llm.message_calls[0]
    assert [item["role"] for item in messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert "PROVIDER JSON ACTION COMPATIBILITY" in messages[-1]["content"]
    assert "typed event" in messages[-1]["content"]


def test_dialogue_manager_can_consult_read_only_scientific_agent_as_tool():
    advisor_output = {
        "analysis": "Scale is the highest unresolved execution decision.",
        "critical_decisions": [
            {
                "id": "search_scale",
                "priority": 92,
                "target_fields": [
                    "coverage_mode",
                    "target_project_count",
                    "quota_flexibility",
                ],
                "question": "How many usable projects should the search target?",
                "recommendation": "Start with about 20 reviewed projects.",
                "reason": "This balances training diversity and review cost.",
            }
        ],
        "repository_evidence_to_fetch": ["project-level raw and identification files"],
        "scientific_risks": ["cross-project label heterogeneity"],
    }
    final_payload = {
        "action": "clarify",
        "assistant_message": "搜索规模是当前影响最大的未决项。",
        "next_decision": {
            "focus": "search_scale",
            "target_fields": [
                "coverage_mode",
                "target_project_count",
                "quota_flexibility",
            ],
            "question": "这轮希望目标约多少个可用项目？",
            "recommendation": {
                "id": "focused_20",
                "label": "约 20 个",
                "reason": "兼顾训练多样性和逐项目审查成本。",
            },
            "options": [
                {
                    "id": "focused_20",
                    "label": "约 20 个",
                    "strategy_patch": {
                        "coverage_mode": "curated",
                        "target_project_count": 20,
                        "quota_flexibility": "recommended",
                    },
                },
                {
                    "id": "balanced_50",
                    "label": "约 50 个",
                    "strategy_patch": {
                        "coverage_mode": "balanced",
                        "target_project_count": 50,
                        "quota_flexibility": "recommended",
                    },
                },
            ],
        },
    }
    model = _DialogueScriptedModel(
        [
            (
                "consult_scientific_advisor",
                {
                    "question": "Prioritize the next decision for this de novo training set.",
                    "decision_goal": "identify the highest-impact unresolved user choice",
                },
            ),
            ("final", json.dumps(advisor_output)),
            ("respond", {"response_json": json.dumps(final_payload)}),
        ]
    )
    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)

    raw = web_app._run_discovery_dialogue_agents_sdk(
        client,
        system_prompt="You are a dialogue manager.",
        dialogue_history=[],
        state_prompt="Ask the highest-impact next decision.",
        user_message="我想做免疫肽 de novo 训练集",
        session_id="advisor-as-tool-test",
        advisor_context={
            "intent_snapshot": {"task_type": "denovo"},
            "critical_decision_agenda": [{"id": "search_scale", "priority": 92}],
        },
        model=model,
    )

    assert raw["action"] == "clarify"
    assert raw["tool_calls"] == []
    assert raw["_advisor_calls"][0]["critical_decisions"][0]["id"] == "search_scale"
    assert model.calls == 3


def test_d1_agents_sdk_ignores_textual_tool_call_without_function_execution(tmp_path):
    model = _DialogueScriptedModel(
        [
            (
                "final",
                json.dumps(
                    {
                        "action": "update_strategy",
                        "assistant_message": "Pretend update",
                        "tool_calls": [
                            {
                                "name": "update_strategy",
                                "arguments": {
                                    "patch": {"target_project_count": 999}
                                },
                            }
                        ],
                    }
                ),
            )
        ]
    )
    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)

    raw = web_app._run_discovery_dialogue_agents_sdk(
        client,
        system_prompt="You are a dialogue agent.",
        dialogue_history=[],
        state_prompt="Return JSON.",
        user_message="What can you do?",
        session_id="",
        model=model,
    )

    assert raw["tool_calls"] == []


def test_sdk_managed_turn_persists_resolved_decision_without_client_memory(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv(
        "AGENT_DIALOGUE_SESSION_DB",
        str(tmp_path / "sdk_semantic_memory.sqlite"),
    )
    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)
    monkeypatch.setattr(
        web_app,
        "_discovery_llm_client",
        lambda *_args, **_kwargs: client,
    )

    def fake_sdk_turn(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "action": "update_strategy",
            "assistant_message": "Curated coverage recorded.",
            "turn_interpretation": {
                "commitments": [
                    {"field": "coverage_mode", "value": "curated", "source": "1"}
                ],
                "consultations": [],
                "clause_audit": [
                    {
                        "clause_id": "C1",
                        "classification": "commitment",
                        "decisions": [
                            {"field": "coverage_mode", "value": "curated"}
                        ],
                    }
                ],
            },
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {"patch": {"coverage_mode": "curated"}},
                }
            ],
            "_agent_runtime": "openai_agents",
            "_sdk_session_managed": True,
        }

    monkeypatch.setattr(
        web_app,
        "_run_discovery_dialogue_agents_sdk",
        fake_sdk_turn,
    )
    pending = _coverage_decision()
    result = asyncio.run(
        web_app.discovery_grill_turn(
            {
                "user_message": "1",
                "phase": "grilling",
                "session_id": "semantic-memory-session",
                "pending_decision": pending,
                "intent_snapshot": {},
                "decision_memory": [],
                "resolved_fields": [],
                "gap_report": {
                    "required_missing": ["task"],
                    "optional_missing": [],
                    "ready_for_confirm": False,
                },
            }
        )
    )

    assert result["resolved_decision"]["selected_option_id"] == "curated"
    _history, memory = web_app._load_discovery_dialogue_session(
        "semantic-memory-session",
        [],
    )
    assert memory[0]["selected_option_id"] == "curated"


def test_grounded_commitment_audit_drops_incidental_tool_fields():
    raw = {
        "action": "update_strategy",
        "turn_interpretation": {
            "commitments": [
                {"field": "species", "value": ["mouse"], "source": "Use mouse"}
            ]
        },
        "tool_calls": [
            {
                "name": "update_strategy",
                "arguments": {
                    "patch": {
                        "species": ["mouse"],
                        "max_candidate_projects": 64,
                    }
                },
            }
        ],
    }

    patch, errors = web_app._discovery_turn_patch(
        raw,
        user_message="Use mouse; keep the count unchanged, but why was 64 suggested?",
        intent_snapshot={"max_candidate_projects": 80},
    )

    assert errors == []
    assert patch == {"species": ["mouse"]}


def test_grounded_commitment_audit_completes_omitted_tool_fields():
    raw = {
        "action": "update_strategy",
        "turn_interpretation": {
            "commitments": [
                {"field": "species", "value": ["mouse"], "source": "Use mouse"},
                {"field": "labeling_strategy", "value": "any", "source": "leave labeling open"},
                {
                    "field": "run_horizon",
                    "value": "candidates_reviewed",
                    "source": "review the candidates afterward",
                },
            ]
        },
        "tool_calls": [
            {
                "name": "update_strategy",
                "arguments": {"patch": {"species": ["mouse"]}},
            }
        ],
    }

    patch, errors = web_app._discovery_turn_patch(
        raw,
        user_message="Use mouse, leave labeling open, and review the candidates afterward.",
        intent_snapshot={},
    )

    assert errors == []
    assert patch == {
        "species": ["mouse"],
        "labeling_strategy": "any",
        "run_horizon": "candidates_reviewed",
    }


def test_empty_optional_commitment_audit_does_not_veto_typed_sdk_patch():
    raw = {
        "action": "update_strategy",
        "turn_interpretation": {
            "commitments": [],
            "consultations": [],
            "clause_audit": [],
        },
        "tool_calls": [
            {
                "name": "update_strategy",
                "arguments": {
                    "patch": {
                        "instrument_preference": "newer",
                        "run_horizon": "candidates_reviewed",
                    }
                },
            }
        ],
    }

    patch, errors = web_app._discovery_turn_patch(
        raw,
        user_message="Prefer the newest feasible instrument generation and review the result set afterward.",
        intent_snapshot={},
    )

    assert errors == []
    assert patch == {
        "instrument_preference": "newer",
        "run_horizon": "candidates_reviewed",
    }


def test_one_malformed_optional_commitment_does_not_veto_valid_tool_or_siblings():
    raw = {
        "action": "update_strategy",
        "turn_interpretation": {
            "commitments": [
                {"field": "species", "value": ["fish"], "source": "Use fish"},
                {"field": "broken_without_value", "source": "and DIA"},
            ]
        },
        "tool_calls": [
            {
                "name": "update_strategy",
                "arguments": {
                    "patch": {
                        "species": ["fish"],
                        "acquisition_mode": "dia",
                    }
                },
            }
        ],
    }

    patch, errors = web_app._discovery_turn_patch(
        raw,
        user_message="Use fish and DIA.",
        intent_snapshot={},
    )

    assert errors == []
    assert patch == {"species": ["fish"]}


def test_typed_tool_value_wins_harmless_commitment_paraphrase_without_losing_siblings():
    raw = {
        "action": "update_strategy",
        "turn_interpretation": {
            "commitments": [
                {
                    "field": "objective",
                    "value": "Explore fish immunopeptidomics",
                    "source": "fish immunopeptidomics",
                },
                {
                    "field": "instrument_preference",
                    "value": "newer",
                    "source": "prefer the newest feasible instruments",
                },
            ]
        },
        "tool_calls": [
            {
                "name": "update_strategy",
                "arguments": {
                    "patch": {
                        "objective": "Curate fish immunopeptidomics datasets",
                        "instrument_preference": "newer",
                    }
                },
            }
        ],
    }

    patch, errors = web_app._discovery_turn_patch(
        raw,
        user_message=(
            "Please curate fish immunopeptidomics datasets and prefer the newest feasible instruments."
        ),
        intent_snapshot={},
    )

    assert errors == []
    assert patch == {
        "objective": "Curate fish immunopeptidomics datasets",
        "instrument_preference": "newer",
    }


def test_semantic_critic_cannot_add_or_change_manager_patch_fields():
    verification = {
        "verdict": "repair",
        "patch": {
            "species": ["fish"],
            "special_themes": ["immunopeptidomics"],
            "instrument_preference": "newer",
            "task_type": "browse_only",
            # This is an unaccepted recommendation and deliberately has no
            # exact evidence span in the latest message.
            "coverage_mode": "curated",
        },
        "evidence": [
            {"field": "species", "source": "fish"},
            {"field": "special_themes", "source": "immunopeptidomics"},
            {"field": "instrument_preference", "source": "newest feasible instruments"},
            {"field": "task_type", "source": "Use fish immunopeptidomics"},
            {"field": "coverage_mode", "source": "recommended curated mode"},
        ],
    }

    patch = web_app._ground_discovery_patch_verification(
        verification,
        user_message=(
            "Use fish immunopeptidomics and prefer the newest feasible instruments."
        ),
        intent_snapshot={},
        proposed_patch={
            "species": ["fish", "fish synonym"],
            "instrument_preference": "newer",
        },
    )

    assert patch == {"instrument_preference": "newer"}

    recovered = web_app._ground_discovery_patch_verification(
        {
            "verdict": "repair",
            "patch": {
                "task_type": "browse_only",
                "species": ["Danio rerio"],
                "acquisition_mode": "dia",
                "target_project_count": 12,
            },
            "evidence": [
                {"field": "task_type", "source": "浏览探索"},
                {"field": "species", "source": "斑马鱼"},
                {"field": "acquisition_mode", "source": "DIA"},
                {"field": "target_project_count", "source": "12个项目"},
            ],
        },
        user_message="先做浏览探索，只要斑马鱼，DIA，目标12个项目。",
        intent_snapshot={},
        proposed_patch={},
        allow_commitment_recovery=True,
    )
    assert recovered == {}


def test_semantic_verifier_accepts_indexed_evidence_for_structured_constraints():
    constraint = {
        "id": "min_bio_replicates",
        "label": "At least 10 biological replicates per project",
        "dimension": "biological_replicates",
        "operator": "gte",
        "value": 10,
        "strength": "hard",
        "scope": "project",
        "evidence_required": True,
        "source": "user",
    }
    patch = web_app._ground_discovery_patch_verification(
        {
            "verdict": "repair",
            "patch": {"scientific_constraints": [constraint]},
            "evidence": [
                {
                    "field": "scientific_constraints[0]",
                    "source": "at least 10 biological replicates per project",
                }
            ],
        },
        user_message="Use at least 10 biological replicates per project.",
        intent_snapshot={},
        proposed_patch={"scientific_constraints": [constraint]},
        allow_commitment_recovery=True,
    )

    assert patch["scientific_constraints"][0]["id"] == "min_bio_replicates"


def test_semantic_verifier_missing_tool_result_is_unavailable_not_reject():
    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)
    model = _DialogueScriptedModel(
        [("final", json.dumps({"verdict": "reject", "patch": {}}))]
    )

    result = web_app._run_discovery_patch_verifier_agents_sdk(
        client,
        user_message="Use mouse DIA.",
        intent_snapshot={},
        proposed_patch={"species": ["mouse"], "acquisition_mode": "dia"},
        timeout_seconds=10,
        model=model,
    )

    assert result["verified"] is False
    assert result["verdict"] == "unavailable"
    assert result["patch"] == {}


def test_read_only_verifier_json_fallback_classifies_clauses_without_write_authority():
    llm = _TurnLLM(
        [
            {
                "verdict": "repair",
                "candidate_findings": [
                    {
                        "field": "special_themes",
                        # Provider compatibility: one finding may serialize an
                        # array-valued field as a scalar item.
                        "value": "immunopeptidomics",
                        "source": "免疫肽数据",
                    }
                ],
                "rationale": "The short topic utterance is a commitment.",
            }
        ]
    )

    result = web_app._run_discovery_patch_verifier_json_fallback(
        llm,
        user_message="免疫肽数据",
        intent_snapshot={},
        proposed_patch={},
        selected_decision=None,
    )

    assert result["verdict"] == "repair"
    assert result["patch"] == {
        "special_themes": ["immunopeptidomics"]
    }
    assert result["evidence"] == [
        {"field": "special_themes", "source": "免疫肽数据"}
    ]
    assert result["findings_contract"] == "candidate_findings_v1"
    assert "read-only semantic critic" in llm.calls[0]["system_prompt"]
    assert "Classify every punctuation-delimited clause" in llm.calls[0][
        "system_prompt"
    ]
    assert "Latest user message: 免疫肽数据" in llm.calls[0]["user_prompt"]
    assert "candidate_findings" in llm.calls[0]["system_prompt"]


def test_semantic_verifier_partial_grounding_applies_verified_subset():
    """Evidence for a subset of proposed fields must authorize that subset.

    Policy (strategy-paste fix): multi-field updates must not wipe the whole
    patch when the independent verifier grounds only some fields.
    """
    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)
    model = _DialogueScriptedModel(
        [
            (
                "verify_strategy_patch",
                {
                    "verification": {
                        "verdict": "accept",
                        "patch": {
                            "species": ["mouse"],
                            "acquisition_mode": "dia",
                        },
                        "evidence": [
                            {"field": "species", "source": "mouse"},
                        ],
                        "rationale": "Only species is evidenced in the latest message.",
                    },
                },
            )
        ]
    )

    result = web_app._run_discovery_patch_verifier_agents_sdk(
        client,
        user_message="Use mouse DIA.",
        intent_snapshot={},
        proposed_patch={"species": ["mouse"], "acquisition_mode": "dia"},
        timeout_seconds=10,
        model=model,
    )

    assert result["verified"] is True
    assert result["verdict"] == "repair"
    assert result["patch"] == {"species": ["mouse"]}
    assert result["missing_fields"] == ["acquisition_mode"]
    assert result["partial_grounding"] is True


def test_semantic_verifier_ignores_unmentioned_null_schema_placeholders():
    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)
    model = _DialogueScriptedModel(
        [
            (
                "verify_strategy_patch",
                {
                    "verification": {
                        "verdict": "repair",
                        "patch": {
                            "species": ["Rattus norvegicus"],
                            "acquisition_mode": "dia",
                            # Some OpenAI-compatible providers materialize all
                            # omitted optional tool fields as JSON null.
                            "objective": None,
                            "task_type": None,
                            "coverage_mode": None,
                            "repository": None,
                        },
                        "evidence": [
                            {
                                "field": "species",
                                "source": "Rattus norvegicus",
                            },
                            {"field": "acquisition_mode", "source": "DIA"},
                        ],
                        "rationale": "The two proposed fields are grounded.",
                    }
                },
            )
        ]
    )

    result = web_app._run_discovery_patch_verifier_agents_sdk(
        client,
        user_message="Use Rattus norvegicus and DIA.",
        intent_snapshot={},
        proposed_patch={
            "species": ["Rattus norvegicus"],
            "acquisition_mode": "dia",
        },
        timeout_seconds=10,
        model=model,
    )

    assert result["verified"] is True
    assert result["verdict"] == "accept"
    assert result["patch"] == {
        "species": ["rat"],
        "acquisition_mode": "dia",
    }
    assert result.get("missing_fields") in (None, [])


def test_commitment_recovery_benign_reject_confirms_no_commitment():
    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)
    model = _DialogueScriptedModel(
        [
            (
                "verify_strategy_patch",
                {
                    "verification": {
                        "verdict": "reject",
                        "patch": {},
                        "evidence": [],
                        "rationale": "The latest message is consultation only.",
                    },
                },
            )
        ]
    )

    result = web_app._run_discovery_patch_verifier_agents_sdk(
        client,
        user_message="Please compare DIA and DDA without changing the strategy.",
        intent_snapshot={},
        proposed_patch={},
        timeout_seconds=10,
        model=model,
        allow_commitment_recovery=True,
    )

    assert result["verified"] is True
    assert result["verdict"] == "accept"
    assert result["patch"] == {}
    assert result["no_commitment_confirmed"] is True


def test_commitment_omission_audit_separates_goal_clause_from_advice_clause():
    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)
    source = "我想做免疫肽 de novo 训练集"
    model = _DialogueScriptedModel(
        [
            (
                "verify_strategy_patch",
                {
                    "verification": {
                        "verdict": "repair",
                        "patch": {
                            "objective": "构建免疫肽 de novo 训练集",
                            "task_type": "denovo",
                            "special_themes": ["immunopeptidomics"],
                        },
                        "evidence": [
                            {"field": "objective", "source": source},
                            {"field": "task_type", "source": source},
                            {"field": "special_themes", "source": source},
                        ],
                        "rationale": (
                            "The first clause is a commitment; the second clause only asks "
                            "for next-step advice."
                        ),
                    }
                },
            )
        ]
    )

    result = web_app._run_discovery_patch_verifier_agents_sdk(
        client,
        user_message=f"{source}，你先帮我分析下一步最关键的决定。",
        intent_snapshot={},
        proposed_patch={},
        timeout_seconds=10,
        model=model,
        allow_commitment_recovery=True,
    )

    assert result["verdict"] == "repair"
    assert result["patch"] == {}
    assert result["critic_suggested_fields"] == [
        "objective",
        "special_themes",
        "task_type",
    ]
    _args, request_kwargs = model.requests[0]
    system_instructions = request_kwargs["system_instructions"]
    tools = request_kwargs["tools"]
    assert "OMISSION-AUDIT PRECEDENCE" in system_instructions
    assert "cannot cancel or reclassify the preceding commitment" in system_instructions
    assert "Read-only omission audit" in tools[0].description
    assert "verdict=repair is mandatory" in tools[0].description


def test_semantic_verifier_audit_matches_the_effective_grounded_delta():
    proposed = {
        "species": ["non-human primate"],
        "acquisition_mode": "unknown",
        "target_project_count": 18,
    }
    result = web_app._normalise_discovery_patch_verification_audit(
        {
            "verdict": "repair",
            "evidence": [
                {"field": "species", "source": "非人灵长类"},
                {"field": "acquisition_mode", "source": "采集方式开放"},
                {"field": "target_project_count", "source": "18"},
                # This describes retained state rather than a field in the
                # effective delta and must not be presented as patch evidence.
                {"field": "scientific_constraints", "source": "要求保留"},
            ],
            "rationale": "I repaired fields that were actually unchanged.",
        },
        user_message="物种改成非人灵长类，采集方式开放，目标18个；重复要求保留。",
        proposed_patch=proposed,
        grounded_patch=proposed,
    )

    assert result["verdict"] == "accept"
    assert {item["field"] for item in result["evidence"]} == {
        "species",
        "acquisition_mode",
        "target_project_count",
    }
    assert result["model_rationale"] == (
        "I repaired fields that were actually unchanged."
    )
    assert "confirmed" in result["rationale"].lower()


def test_semantic_verifier_omits_empty_enum_placeholders_before_validation():
    context = SimpleNamespace(verification=None)
    ctx = SimpleNamespace(context=context)
    verification = web_app._DiscoveryPatchVerificationInput(
        verdict="repair",
        patch=web_app._DiscoveryStrategyPatchToolInput(
            species=["non-human primate"],
            target_project_count=18,
            coverage_mode="",
            time_budget="",
        ),
        evidence=[
            web_app._DiscoveryPatchEvidenceInput(
                field="species",
                source="non-human primate",
            )
        ],
        rationale="Remove unrequested empty defaults.",
    )

    asyncio.run(web_app._sdk_discovery_verify_strategy_patch(ctx, verification))

    assert context.verification["verdict"] == "repair"
    assert context.verification["errors"] == []
    assert context.verification["patch"] == {
        "species": ["non-human primate"],
        "target_project_count": 18,
    }


def test_read_only_critic_cannot_recover_commitment_missed_by_manager(
    monkeypatch,
):
    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)
    monkeypatch.setattr(
        web_app,
        "_discovery_llm_client",
        lambda *_args, **_kwargs: client,
    )
    monkeypatch.setattr(
        web_app,
        "_complete_discovery_dialogue_json",
        lambda *_args, **_kwargs: {
            "action": "chat",
            "assistant_message": "I am not sure what you want.",
            "tool_calls": [],
            "_agent_runtime": "openai_agents",
            "_sdk_session_managed": False,
        },
    )
    verifier_calls: list[dict[str, Any]] = []

    def recover(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        verifier_calls.append(kwargs)
        return {
            "verified": True,
            "verdict": "repair",
            "patch": {
                "task_type": "browse_only",
                "species": ["Danio rerio"],
                "acquisition_mode": "dia",
                "target_project_count": 12,
            },
            "rationale": "Recovered four exact commitments.",
            "tool_authority": "update_strategy",
        }

    monkeypatch.setattr(web_app, "_run_discovery_patch_verifier_agents_sdk", recover)
    result = asyncio.run(
        web_app.discovery_grill_turn(
            {
                "user_message": "先做浏览探索，只要斑马鱼，DIA，目标12个项目。",
                "phase": "grilling",
                "intent_snapshot": {},
                "gap_report": {
                    "required_missing": ["task"],
                    "optional_missing": [],
                    "ready_for_confirm": False,
                },
            }
        )
    )

    assert len(verifier_calls) == 1
    assert verifier_calls[0]["proposed_patch"] == {}
    assert verifier_calls[0]["use_update_strategy_tool"] is False
    assert result["action"] == "chat"
    assert result["tool_calls"] == []
    assert result["extra_fields"] == {}
    assert result["semantic_verification"]["verdict"] == "repair"
    assert result["semantic_verification"]["patch"] == {}


def test_read_only_critic_feedback_triggers_one_same_manager_retry(monkeypatch):
    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=30)
    monkeypatch.setattr(
        web_app,
        "_discovery_llm_client",
        lambda *_args, **_kwargs: client,
    )
    manager_calls: list[dict[str, Any]] = []

    def manager(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        manager_calls.append(kwargs)
        if len(manager_calls) == 1:
            return {
                "action": "advise",
                "assistant_message": "Let's discuss the task.",
                "tool_calls": [],
                "_provider_compatibility_recovery": {
                    "mode": "plain_text_as_non_mutating",
                    "chars": 24,
                    "advisor_calls": 0,
                },
                "_agent_runtime": "openai_agents",
            }
        return {
            "action": "update_strategy",
            "assistant_message": "I re-read the explicit commitments.",
            "turn_interpretation": {
                "commitments": [
                    {"field": "task_type", "value": "denovo", "source": "de novo"},
                    {
                        "field": "objective",
                        "value": "Build an immunopeptide de novo training set",
                        "source": "免疫肽 de novo 训练集",
                    },
                ]
            },
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {
                        "patch": {
                            "task_type": "denovo",
                            "objective": "Build an immunopeptide de novo training set",
                        }
                    },
                }
            ],
            "_agent_runtime": "openai_agents",
        }

    monkeypatch.setattr(web_app, "_complete_discovery_dialogue_json", manager)
    critic_calls: list[dict[str, Any]] = []

    def critic(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        critic_calls.append(kwargs)
        if len(critic_calls) == 1:
            return {
                "verified": False,
                "verdict": "repair",
                "patch": {},
                "critic_suggested_fields": ["objective", "task_type"],
                "evidence": [
                    {"field": "objective", "source": "免疫肽 de novo 训练集"},
                    {"field": "task_type", "source": "de novo"},
                ],
                "tool_authority": "verify_strategy_patch",
            }
        return {
            "verified": True,
            "verdict": "accept",
            "patch": dict(kwargs["proposed_patch"]),
            "evidence": [
                {"field": "objective", "source": "免疫肽 de novo 训练集"},
                {"field": "task_type", "source": "de novo"},
            ],
            "tool_authority": "verify_strategy_patch",
        }

    monkeypatch.setattr(web_app, "_run_discovery_patch_verifier_agents_sdk", critic)

    result = asyncio.run(
        web_app.discovery_grill_turn(
            {
                "user_message": "我想做免疫肽 de novo 训练集，请分析下一步。",
                "phase": "grilling",
                "request_timeout_seconds": 30,
                "intent_snapshot": {},
                "gap_report": {
                    "required_missing": ["task", "objective", "horizon", "coverage"],
                    "optional_missing": [],
                    "ready_for_confirm": False,
                },
            }
        )
    )

    assert len(manager_calls) == 2
    assert "read_only_critic_feedback_from_previous_attempt" in manager_calls[1]["user_prompt"]
    assert result["action"] == "update_strategy"
    assert result["extra_fields"]["task_type"] == "denovo"
    assert result["manager_repair"]["writer"] == "dialogue_manager"
    assert result["manager_repair"]["critic_authority"] == "read_only"
    assert result["manager_repair"]["trigger"]["provider_compatibility_recovery"] == {
        "mode": "plain_text_as_non_mutating",
        "chars": 24,
        "advisor_calls": 0,
    }


def test_critic_evidence_fields_trigger_manager_retry_without_suggested_fields(
    monkeypatch,
):
    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=30)
    monkeypatch.setattr(
        web_app,
        "_discovery_llm_client",
        lambda *_args, **_kwargs: client,
    )
    manager_calls: list[dict[str, Any]] = []

    def manager(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        manager_calls.append(kwargs)
        if len(manager_calls) == 1:
            return {
                "action": "clarify",
                "assistant_message": "你对 HLA 类别有偏好吗？",
                "tool_calls": [],
                "_agent_runtime": "openai_agents",
            }
        return {
            "action": "update_strategy",
            "assistant_message": "已写入你明确给出的完整策略。",
            "turn_interpretation": {
                "commitments": [
                    {
                        "field": "objective",
                        "value": "免疫肽组学数据发现",
                        "source": "科学目标：免疫肽组学数据发现",
                    },
                    {
                        "field": "special_themes",
                        "value": ["immunopeptidomics"],
                        "source": "研究主题：immunopeptidomics",
                    },
                ],
            },
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {
                        "patch": {
                            "objective": "免疫肽组学数据发现",
                            "special_themes": ["immunopeptidomics"],
                        }
                    },
                }
            ],
            "_agent_runtime": "openai_agents",
        }

    monkeypatch.setattr(web_app, "_complete_discovery_dialogue_json", manager)
    monkeypatch.setattr(
        web_app,
        "_run_discovery_patch_verifier_agents_sdk",
        lambda *_args, **_kwargs: {
            "verified": False,
            "verdict": "repair",
            "model_verdict": "repair",
            "patch": {},
            "missing_fields": ["objective", "special_themes"],
            "evidence": [
                {
                    "field": "objective",
                    "source": "科学目标：免疫肽组学数据发现",
                },
                {
                    "field": "special_themes",
                    "source": "研究主题：immunopeptidomics",
                },
            ],
            "tool_authority": "verify_strategy_patch",
        },
    )

    result = asyncio.run(
        web_app.discovery_grill_turn(
            {
                "user_message": (
                    "科学目标：免疫肽组学数据发现"
                    "研究主题：immunopeptidomics"
                ),
                "phase": "grilling",
                "request_timeout_seconds": 30,
                "intent_snapshot": {},
                "gap_report": {
                    "required_missing": ["objective"],
                    "optional_missing": [],
                    "ready_for_confirm": False,
                },
            }
        )
    )

    assert len(manager_calls) == 2
    assert result["action"] == "update_strategy"
    assert result["extra_fields"] == {
        "objective": "免疫肽组学数据发现",
        "special_themes": ["immunopeptidomics"],
    }
    assert result["manager_repair"]["trigger"]["kind"] == "omitted_commitment"
    assert result["manager_repair"]["trigger"]["suggested_fields"] == [
        "objective",
        "special_themes",
    ]


def test_short_topic_plain_text_recovery_is_audited_and_repaired(monkeypatch):
    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=30)
    monkeypatch.setattr(
        web_app,
        "_discovery_llm_client",
        lambda *_args, **_kwargs: client,
    )
    manager_calls: list[dict[str, Any]] = []

    def manager(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        manager_calls.append(kwargs)
        if len(manager_calls) == 1:
            return {
                "action": "advise",
                "assistant_message": "你想用这些数据做什么？",
                "tool_calls": [],
                "_provider_compatibility_recovery": {
                    "mode": "plain_text_as_non_mutating",
                    "chars": 12,
                    "advisor_calls": 0,
                },
                "_agent_runtime": "openai_agents",
            }
        return {
            "action": "update_strategy",
            "assistant_message": "已记录免疫肽研究主题。",
            "turn_interpretation": {
                "commitments": [
                    {
                        "field": "special_themes",
                        "value": ["immunopeptidomics"],
                        "source": "免疫肽数据",
                    }
                ]
            },
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {
                        "patch": {"special_themes": ["immunopeptidomics"]}
                    },
                }
            ],
            "_agent_runtime": "openai_agents",
        }

    monkeypatch.setattr(web_app, "_complete_discovery_dialogue_json", manager)
    verifier_calls: list[dict[str, Any]] = []

    def critic(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        verifier_calls.append(kwargs)
        return {
            "verified": False,
            "verdict": "repair",
            "patch": {},
            "critic_suggested_fields": ["special_themes"],
            "evidence": [
                {"field": "special_themes", "source": "免疫肽数据"}
            ],
            "tool_authority": "verify_strategy_patch",
        }

    monkeypatch.setattr(web_app, "_run_discovery_patch_verifier_agents_sdk", critic)

    result = asyncio.run(
        web_app.discovery_grill_turn(
            {
                "user_message": "免疫肽数据",
                "phase": "grilling",
                "request_timeout_seconds": 30,
                "intent_snapshot": {},
                "gap_report": {
                    "required_missing": ["task", "objective", "horizon", "coverage"],
                    "optional_missing": [],
                    "ready_for_confirm": False,
                },
            }
        )
    )

    assert len(manager_calls) == 2
    assert len(verifier_calls) == 1
    assert result["action"] == "update_strategy"
    assert result["extra_fields"] == {
        "special_themes": ["immunopeptidomics"]
    }
    assert result["manager_repair"]["trigger"]["provider_compatibility_recovery"][
        "mode"
    ] == "plain_text_as_non_mutating"


def test_invalid_compound_strategy_patch_gets_one_manager_repair(monkeypatch):
    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=30)
    monkeypatch.setattr(
        web_app,
        "_discovery_llm_client",
        lambda *_args, **_kwargs: client,
    )
    manager_calls: list[dict[str, Any]] = []

    def manager(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        manager_calls.append(kwargs)
        coverage_mode = "infinite" if len(manager_calls) == 1 else "exhaustive"
        return {
            "action": "update_strategy",
            "assistant_message": "已读取完整策略。",
            "turn_interpretation": {
                "commitments": [
                    {
                        "field": "objective",
                        "value": "免疫肽组学数据发现",
                        "source": "科学目标：免疫肽组学数据发现",
                    },
                    {
                        "field": "coverage_mode",
                        "value": coverage_mode,
                        "source": "规模：越多越好",
                    },
                    {
                        "field": "quota_flexibility",
                        "value": "open_ended",
                        "source": "规模：越多越好，开放上限（exhaustive + open_ended）",
                    },
                    {
                        "field": "special_themes",
                        "value": ["immunopeptidomics"],
                        "source": "研究主题：immunopeptidomics",
                    },
                ],
            },
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {
                        "patch": {
                            "objective": "免疫肽组学数据发现",
                            "special_themes": ["immunopeptidomics"],
                            "coverage_mode": coverage_mode,
                            "quota_flexibility": "open_ended",
                        }
                    },
                }
            ],
            "_agent_runtime": "openai_agents",
        }

    monkeypatch.setattr(web_app, "_complete_discovery_dialogue_json", manager)

    result = asyncio.run(
        web_app.discovery_grill_turn(
            {
                "user_message": (
                    "科学目标：免疫肽组学数据发现\n"
                    "研究主题：immunopeptidomics\n"
                    "规模：越多越好，开放上限（exhaustive + open_ended）"
                ),
                "phase": "grilling",
                "request_timeout_seconds": 30,
                "intent_snapshot": {},
                "gap_report": {
                    "required_missing": ["objective", "coverage"],
                    "optional_missing": [],
                    "ready_for_confirm": False,
                },
            }
        )
    )

    assert len(manager_calls) == 2
    assert "invalid_strategy_patch" in manager_calls[1]["user_prompt"]
    assert result["action"] == "update_strategy"
    assert result["extra_fields"] == {
        "objective": "免疫肽组学数据发现",
        "special_themes": ["immunopeptidomics"],
        "coverage_mode": "exhaustive",
        "quota_flexibility": "open_ended",
    }
    assert result["manager_repair"]["trigger"]["kind"] == "invalid_strategy_patch"


def test_semantic_verifier_retries_once_to_repair_a_partial_primary_omission(
    monkeypatch,
):
    """Unavailable first attempt must retry once; critic cannot invent fields."""

    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=30)
    monkeypatch.setattr(
        web_app,
        "_discovery_llm_client",
        lambda *_args, **_kwargs: client,
    )
    # Force critic path (whitelist compound would otherwise skip verifier).
    monkeypatch.setattr(
        web_app,
        "_discovery_low_risk_single_field_verifier_skip",
        lambda *_args, **_kwargs: False,
    )
    # Multi-field patch keeps the second verifier on the critical path; a lone
    # low-risk field (e.g. species) now intentionally skips the critic.
    monkeypatch.setattr(
        web_app,
        "_complete_discovery_dialogue_json",
        lambda *_args, **_kwargs: {
            "action": "update_strategy",
            "assistant_message": "已更新斑马鱼范围。",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {
                        "patch": {
                            "species": ["Danio rerio"],
                            "task_type": "denovo",
                        }
                    },
                }
            ],
            "_agent_runtime": "openai_agents",
            "_sdk_session_managed": False,
        },
    )
    verifier_calls: list[dict[str, Any]] = []

    def verify(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        verifier_calls.append(kwargs)
        if len(verifier_calls) == 1:
            return {
                "verified": False,
                "verdict": "unavailable",
                "patch": {},
                "rationale": "No valid tool result.",
            }
        return {
            "verified": True,
            "verdict": "repair",
            "patch": {
                "species": ["Danio rerio"],
                "task_type": "denovo",
                "special_themes": ["immunopeptidomics"],
            },
            "rationale": "Recovered the omitted study theme.",
            "tool_authority": "verify_strategy_patch",
        }

    monkeypatch.setattr(web_app, "_run_discovery_patch_verifier_agents_sdk", verify)
    result = asyncio.run(
        web_app.discovery_grill_turn(
            {
                "user_message": "只要斑马鱼免疫肽；找到后再复核。",
                "phase": "grilling",
                "request_timeout_seconds": 30,
                "intent_snapshot": {},
                "gap_report": {
                    "required_missing": ["task"],
                    "optional_missing": [],
                    "ready_for_confirm": False,
                },
            }
        )
    )

    assert len(verifier_calls) == 2
    assert verifier_calls[0]["allow_commitment_recovery"] is False
    assert verifier_calls[0]["use_update_strategy_tool"] is False
    assert verifier_calls[1]["allow_commitment_recovery"] is False
    assert verifier_calls[1]["use_update_strategy_tool"] is False
    assert result["extra_fields"] == {
        "species": ["Danio rerio"],
        "task_type": "denovo",
    }
    assert result["tool_calls"] == [
        {
            "name": "update_strategy",
            "arguments": {
                "patch": {
                    "species": ["Danio rerio"],
                    "task_type": "denovo",
                }
            },
        }
    ]
    assert "special_themes" not in result["extra_fields"]
    assert result["semantic_verification"]["attempts"] == 2
    assert result["semantic_verification"]["previous_attempts"][0]["verdict"] == (
        "unavailable"
    )



def test_independent_agent_confirms_long_consultation_should_not_write_card(
    monkeypatch,
):
    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)
    monkeypatch.setattr(
        web_app,
        "_discovery_llm_client",
        lambda *_args, **_kwargs: client,
    )
    monkeypatch.setattr(
        web_app,
        "_complete_discovery_dialogue_json",
        lambda *_args, **_kwargs: {
            "action": "advise",
            "assistant_message": "Here are the scientific trade-offs.",
            "tool_calls": [],
            "_agent_runtime": "openai_agents",
            "_sdk_session_managed": False,
        },
    )
    monkeypatch.setattr(
        web_app,
        "_run_discovery_patch_verifier_agents_sdk",
        lambda *_args, **_kwargs: {
            "verified": False,
            "verdict": "reject",
            "patch": {},
            "rationale": "The user requested discussion only.",
            "tool_authority": "update_strategy",
        },
    )

    result = asyncio.run(
        web_app.discovery_grill_turn(
            {
                "user_message": "请比较DIA和DDA的利弊，先讨论，不要修改策略。",
                "phase": "grilling",
                "intent_snapshot": {},
                "gap_report": {
                    "required_missing": ["task"],
                    "optional_missing": [],
                    "ready_for_confirm": False,
                },
            }
        )
    )

    assert result["action"] == "advise"
    assert result["tool_calls"] == []
    assert result["extra_fields"] == {}
    assert result["semantic_verification"]["verdict"] == "accept"
    assert result["semantic_verification"]["no_commitment_confirmed"] is True


def test_semantic_verifier_reject_blocks_strategy_write_and_persists_final_memory(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv(
        "AGENT_DIALOGUE_SESSION_DB",
        str(tmp_path / "semantic_reject.sqlite"),
    )
    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)
    monkeypatch.setattr(
        web_app,
        "_discovery_llm_client",
        lambda *_args, **_kwargs: client,
    )
    # Force critic path (multi-field whitelist compound would otherwise skip).
    monkeypatch.setattr(
        web_app,
        "_discovery_low_risk_single_field_verifier_skip",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        web_app,
        "_complete_discovery_dialogue_json",
        lambda *_args, **_kwargs: {
            "action": "update_strategy",
            "assistant_message": "I will update the strategy.",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {
                        "patch": {
                            "species": ["mouse"],
                            "acquisition_mode": "dia",
                        }
                    },
                }
            ],
            "_agent_runtime": "openai_agents",
            "_sdk_session_managed": False,
        },
    )
    monkeypatch.setattr(
        web_app,
        "_run_discovery_patch_verifier_agents_sdk",
        lambda *_args, **_kwargs: {
            "verified": False,
            "verdict": "reject",
            "patch": {},
            "rationale": "The proposed patch is not grounded in the latest message.",
        },
    )

    result = asyncio.run(
        web_app.discovery_grill_turn(
            {
                "user_message": "Can you compare mouse DIA with other options?",
                "session_id": "semantic-reject-session",
                "phase": "grilling",
                "intent_snapshot": {},
                "gap_report": {
                    "required_missing": ["task"],
                    "optional_missing": [],
                    "ready_for_confirm": False,
                },
            }
        )
    )

    assert result["tool_calls"] == []
    assert result["extra_fields"] == {}
    assert result["action"] in {"advise", "clarify"}
    assert result["semantic_verification"]["verdict"] == "reject"
    assert any("semantic verification" in error for error in result["contract_errors"])

    history, _memory = web_app._load_discovery_dialogue_session(
        "semantic-reject-session",
        [],
    )
    assert len(history) == 2
    assert '"strategy_patch":{}' in history[-1]["content"]
    assert '"species":["mouse"]' not in history[-1]["content"]



def test_bare_digit_applies_predeclared_labeling_soft_option(monkeypatch):
    pending = {
        "focus": "labeling_strategy",
        "target_fields": ["labeling_strategy", "labeling_hard"],
        "question": "标记方式？",
        "recommendation": {
            "id": "prefer_lf",
            "label": "优先 label-free（软性偏好）",
            "reason": "最直接",
        },
        "options": [
            {
                "id": "prefer_lf",
                "label": "优先 label-free（软性偏好）",
                "reason": "软",
                "strategy_patch": {
                    "labeling_strategy": "label_free",
                    "labeling_hard": False,
                },
            },
            {
                "id": "hard_lf",
                "label": "只要 label-free",
                "reason": "硬",
                "strategy_patch": {
                    "labeling_strategy": "label_free",
                    "labeling_hard": True,
                },
            },
            {
                "id": "any_lab",
                "label": "不限标记方式",
                "reason": "开放",
                "strategy_patch": {
                    "labeling_strategy": "unknown",
                    "labeling_hard": False,
                },
            },
        ],
        "option_mode": "focused",
        "revisit_existing": False,
        "allow_free_text": True,
    }
    # Model wrongly returns advise; server must still apply option 1 contract
    # and must NOT run semantic verifier that would reject bare "1".
    def boom_verifier(*_a, **_k):
        raise AssertionError("semantic verifier must not run for bare option index")

    monkeypatch.setattr(
        web_app,
        "_run_discovery_patch_verifier_agents_sdk",
        boom_verifier,
    )
    result, _llm = _run_turn(
        monkeypatch,
        {
            "_agent_runtime": "openai_agents",
            "action": "advise",
            "assistant_message": "好的。",
            "tool_calls": [],
        },
        user_message="1",
        phase="grilling",
        pending_decision=pending,
        intent_snapshot={"labeling_strategy": "unknown", "labeling_hard": False},
        gap_report={"required_missing": [], "optional_missing": ["labeling"], "ready_for_confirm": False},
    )
    assert result["action"] == "update_strategy"
    assert result["extra_fields"]["labeling_strategy"] == "label_free"
    assert result["extra_fields"]["labeling_hard"] is False
    assert result.get("semantic_verification", {}).get("verdict") in {
        None,
        "skipped",
        "not_required",
        "accepted",
        "pass",
    } or result.get("semantic_verification") in (None, {})
    # Must not be rejected
    assert (result.get("semantic_verification") or {}).get("verdict") != "rejected"


def test_synthesize_labeling_soft_when_option_missing_strategy_patch():
    selected = {
        "focus": "labeling",
        "target_fields": ["labeling_strategy"],
        "explicit_acceptance": True,
        "option": {
            "id": "prefer_lf",
            "label": "优先 label-free（软性偏好）",
            "reason": "软性",
        },
    }
    patch = web_app._discovery_selected_option_strategy_patch(selected)
    assert patch.get("labeling_strategy") == "label_free"
    assert patch.get("labeling_hard") is False


def test_low_risk_single_field_skip_helper_whitelists_objective_and_species():
    assert web_app._discovery_low_risk_single_field_verifier_skip(
        {"objective": "免疫肽数据"},
        tool_interpretation_difference=False,
    )
    assert web_app._discovery_low_risk_single_field_verifier_skip(
        {"species": ["mouse"]},
        tool_interpretation_difference=False,
    )
    # Compound dumps of low-risk first-class fields may also skip (agent-style multi-commit).
    assert web_app._discovery_low_risk_single_field_verifier_skip(
        {
            "objective": "人源免疫肽 RT",
            "species": ["human"],
            "task_type": "rt_prediction",
            "acquisition_mode": "dda",
            "run_horizon": "candidates_reviewed",
            "quota_flexibility": "open_ended",
        },
        tool_interpretation_difference=False,
    )
    assert web_app._discovery_low_risk_single_field_verifier_skip(
        {"special_themes": ["immunopeptidomics"]},
        tool_interpretation_difference=False,
    )
    # Empty / non-whitelist / over compound max / recovery still force verifier.
    assert not web_app._discovery_low_risk_single_field_verifier_skip(
        {},
        tool_interpretation_difference=False,
    )
    assert not web_app._discovery_low_risk_single_field_verifier_skip(
        {"ptm_types": ["phospho"]},
        tool_interpretation_difference=False,
    )
    # Soft keys + one non-whitelist field must not skip (soft_reject path needs critic).
    assert not web_app._discovery_low_risk_single_field_verifier_skip(
        {
            "objective": "免疫肽数据",
            "special_themes": ["immunopeptidomics"],
            "ptm_types": ["phospho"],
        },
        tool_interpretation_difference=False,
    )
    over_max = {
        field: True
        for field in sorted(web_app._DISCOVERY_LOW_RISK_SINGLE_FIELD_VERIFIER_SKIP)[
            : web_app._DISCOVERY_LOW_RISK_COMPOUND_MAX_FIELDS + 1
        ]
    }
    assert len(over_max) == web_app._DISCOVERY_LOW_RISK_COMPOUND_MAX_FIELDS + 1
    assert not web_app._discovery_low_risk_single_field_verifier_skip(
        over_max,
        tool_interpretation_difference=False,
    )
    # Single-field interpretation gaps still force the verifier path.
    assert not web_app._discovery_low_risk_single_field_verifier_skip(
        {"species": ["mouse"]},
        tool_interpretation_difference=True,
    )
    # Multi-field pure-whitelist compounds may skip even with interpretation
    # differences so soft-reject cannot blank hard whitelist fields.
    assert web_app._discovery_low_risk_single_field_verifier_skip(
        {
            "objective": "人源免疫肽 RT",
            "species": ["human"],
            "task_type": "rt_prediction",
            "acquisition_mode": "dda",
            "run_horizon": "candidates_reviewed",
            "quota_flexibility": "open_ended",
        },
        tool_interpretation_difference=True,
    )
    assert not web_app._discovery_low_risk_single_field_verifier_skip(
        {"scientific_constraints": [{"value": True}]},
        tool_interpretation_difference=False,
    )
    assert not web_app._discovery_low_risk_single_field_verifier_skip(
        {"objective": "免疫肽数据"},
        tool_interpretation_difference=False,
        provider_compatibility_recovery={
            "mode": "json_action_contract_after_plain_text"
        },
    )


def test_soft_reject_v2_global_reject_does_not_keep_species():
    """Boss condition: global critic reject keeps soft set only (no species/DDA)."""

    tool_patch = {
        "objective": "免疫肽数据",
        "special_themes": ["immunopeptidomics"],
        "species": ["mouse"],
        "acquisition_mode": "dia",
        "run_horizon": "candidates_reviewed",
        "ptm_types": ["phospho"],
    }
    kept = web_app._discovery_soft_reject_kept_patch(
        tool_patch,
        semantic_verification={
            "verdict": "reject",
            "rationale": "Global reject without field granularity.",
        },
    )
    assert kept == {
        "objective": "免疫肽数据",
        "special_themes": ["immunopeptidomics"],
    }
    assert "species" not in kept
    assert "acquisition_mode" not in kept
    assert "run_horizon" not in kept
    dropped = web_app._discovery_soft_reject_dropped_fields(tool_patch, kept)
    assert "species" in dropped
    assert "acquisition_mode" in dropped
    assert "ptm_types" in dropped
    assert "objective" not in dropped
    msg = web_app._format_discovery_soft_reject_message(
        kept, dropped_fields=dropped
    )
    assert "已写入" in msg
    assert "未写入" in msg
    assert "策略完全未更新" not in msg


def test_soft_reject_v2_field_level_keeps_unnamed_hard_keys():
    """Field-level critic veto drops only named keys; other whitelist hard keys stay."""

    tool_patch = {
        "objective": "人源免疫肽",
        "species": ["human"],
        "acquisition_mode": "dda",
        "run_horizon": "candidates_reviewed",
        "special_themes": ["immunopeptidomics"],
        "ptm_types": ["phospho"],
    }
    kept = web_app._discovery_soft_reject_kept_patch(
        tool_patch,
        semantic_verification={
            "verdict": "reject",
            "missing_fields": ["species", "ptm_types"],
            "rationale": "species and ptm ungrounded; acquisition ok.",
        },
    )
    assert kept.get("objective") == "人源免疫肽"
    assert kept.get("special_themes") == ["immunopeptidomics"]
    assert kept.get("acquisition_mode") == "dda"
    assert kept.get("run_horizon") == "candidates_reviewed"
    assert "species" not in kept
    assert "ptm_types" not in kept
    dropped = web_app._discovery_soft_reject_dropped_fields(tool_patch, kept)
    assert set(dropped) == {"ptm_types", "species"}


def test_soft_reject_v2_end_to_end_emits_dropped_fields(monkeypatch):
    """Grill turn soft-reject path exposes soft_reject_dropped_fields + 已写入 copy."""

    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)
    monkeypatch.setattr(
        web_app,
        "_discovery_llm_client",
        lambda *_args, **_kwargs: client,
    )
    monkeypatch.setattr(
        web_app,
        "_complete_discovery_dialogue_json",
        lambda *_args, **_kwargs: {
            "action": "update_strategy",
            "assistant_message": "I will update the strategy.",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {
                        "patch": {
                            "objective": "免疫肽数据",
                            "special_themes": ["immunopeptidomics"],
                            "species": ["mouse"],
                            "acquisition_mode": "dia",
                            "ptm_types": ["phospho"],
                        }
                    },
                }
            ],
            "_agent_runtime": "openai_agents",
            "_sdk_session_managed": False,
        },
    )
    monkeypatch.setattr(
        web_app,
        "_run_discovery_patch_verifier_agents_sdk",
        lambda *_args, **_kwargs: {
            "verified": False,
            "verdict": "reject",
            "patch": {},
            "rationale": "Hard fields are not grounded in the latest message.",
        },
    )

    result = asyncio.run(
        web_app.discovery_grill_turn(
            {
                "user_message": "免疫肽主题，顺带提了 mouse DIA phospho。",
                "phase": "grilling",
                "intent_snapshot": {},
                "gap_report": {
                    "required_missing": ["task"],
                    "optional_missing": [],
                    "ready_for_confirm": False,
                },
            }
        )
    )

    assert result["action"] == "update_strategy"
    assert result["extra_fields"] == {
        "objective": "免疫肽数据",
        "special_themes": ["immunopeptidomics"],
    }
    sv = result["semantic_verification"]
    assert sv["verdict"] == "reject"
    assert sv["soft_reject_kept_fields"] == ["objective", "special_themes"]
    assert "species" in sv["soft_reject_dropped_fields"]
    assert "acquisition_mode" in sv["soft_reject_dropped_fields"]
    assert "ptm_types" in sv["soft_reject_dropped_fields"]
    assert "已写入" in result["assistant_message"]
    assert "未写入" in result["assistant_message"]


def test_soft_reject_kept_patch_retains_only_soft_keys():
    """Global reject (no field list) keeps soft set only — not species/DDA."""

    kept = web_app._discovery_soft_reject_kept_patch(
        {
            "objective": "免疫肽数据",
            "special_themes": ["immunopeptidomics"],
            "species": ["mouse"],
            "acquisition_mode": "dia",
        }
    )
    assert kept == {
        "objective": "免疫肽数据",
        "special_themes": ["immunopeptidomics"],
    }
    assert web_app._discovery_soft_reject_kept_patch(
        {"species": ["mouse"], "acquisition_mode": "dia"}
    ) == {}


def test_soft_reject_v2_global_reject_does_not_keep_species():
    tool = {
        "objective": "免疫肽数据",
        "species": ["mouse"],
        "acquisition_mode": "dda",
        "run_horizon": "candidates_reviewed",
        "notes": "user notes",
    }
    # No field-level critic signal → soft set only.
    kept = web_app._discovery_soft_reject_kept_patch(
        tool,
        semantic_verification={"verdict": "reject", "rationale": "global reject"},
    )
    assert kept == {"objective": "免疫肽数据", "notes": "user notes"}
    assert "species" not in kept
    assert "acquisition_mode" not in kept
    assert "run_horizon" not in kept
    dropped = web_app._discovery_soft_reject_dropped_fields(tool, kept)
    assert dropped == ["acquisition_mode", "run_horizon", "species"]


def test_soft_reject_v2_field_level_keeps_unnamed_low_risk_hard_keys():
    tool = {
        "objective": "免疫肽数据",
        "species": ["human"],
        "acquisition_mode": "dda",
        "run_horizon": "candidates_reviewed",
        "ptm_types": ["phospho"],
    }
    kept = web_app._discovery_soft_reject_kept_patch(
        tool,
        semantic_verification={
            "verdict": "reject",
            "missing_fields": ["ptm_types"],
            "rationale": "ptm not grounded",
        },
    )
    # Field-level: keep low-risk whitelist keys not named by critic.
    assert kept.get("objective") == "免疫肽数据"
    assert kept.get("species") == ["human"]
    assert kept.get("acquisition_mode") == "dda"
    assert kept.get("run_horizon") == "candidates_reviewed"
    assert "ptm_types" not in kept
    dropped = web_app._discovery_soft_reject_dropped_fields(tool, kept)
    assert dropped == ["ptm_types"]


def test_soft_reject_v2_field_level_drops_only_named_veto():
    tool = {
        "objective": "免疫肽数据",
        "species": ["human"],
        "acquisition_mode": "dda",
    }
    kept = web_app._discovery_soft_reject_kept_patch(
        tool,
        semantic_verification={
            "verdict": "reject",
            "rejected_fields": ["species"],
        },
    )
    assert kept == {
        "objective": "免疫肽数据",
        "acquisition_mode": "dda",
    }
    assert "species" not in kept


def test_low_risk_single_field_objective_skips_semantic_verifier(monkeypatch):
    """Short topic single-field SDK patch must not pay for a second verifier."""

    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)
    monkeypatch.setattr(
        web_app,
        "_discovery_llm_client",
        lambda *_args, **_kwargs: client,
    )
    monkeypatch.setattr(
        web_app,
        "_complete_discovery_dialogue_json",
        lambda *_args, **_kwargs: {
            "action": "update_strategy",
            "assistant_message": "已记录主题。",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {
                        "patch": {"objective": "免疫肽数据"}
                    },
                }
            ],
            "_agent_runtime": "openai_agents",
            "_sdk_session_managed": False,
        },
    )
    verifier_calls: list[dict[str, Any]] = []

    def verify(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        verifier_calls.append(kwargs)
        return {
            "verified": False,
            "verdict": "reject",
            "patch": {},
            "rationale": "should not run on low-risk single-field path",
        }

    monkeypatch.setattr(web_app, "_run_discovery_patch_verifier_agents_sdk", verify)

    result = asyncio.run(
        web_app.discovery_grill_turn(
            {
                "user_message": "免疫肽数据",
                "phase": "grilling",
                "intent_snapshot": {},
                "gap_report": {
                    "required_missing": ["task"],
                    "optional_missing": [],
                    "ready_for_confirm": False,
                },
            }
        )
    )

    assert verifier_calls == []
    assert result["action"] == "update_strategy"
    assert result["extra_fields"] == {"objective": "免疫肽数据"}
    assert result["tool_calls"] == [
        {
            "name": "update_strategy",
            "arguments": {"patch": {"objective": "免疫肽数据"}},
        }
    ]
    assert result.get("semantic_verification") is None
    assert not any(
        "semantic verification" in error
        for error in result.get("contract_errors") or []
    )


def test_low_risk_compound_multi_field_patch_skips_semantic_verifier(monkeypatch):
    """Whitelist-only compound update_strategy must skip the second verifier."""

    compound_patch = {
        "objective": "人源免疫肽 RT",
        "species": ["human"],
        "species_policy": "prefer",
        "task_type": "rt_prediction",
        "acquisition_mode": "dda",
        "run_horizon": "candidates_reviewed",
        "quota_flexibility": "open_ended",
        "special_themes": ["immunopeptidomics"],
    }
    assert web_app._discovery_low_risk_single_field_verifier_skip(
        compound_patch,
        tool_interpretation_difference=False,
    )

    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)
    monkeypatch.setattr(
        web_app,
        "_discovery_llm_client",
        lambda *_args, **_kwargs: client,
    )
    monkeypatch.setattr(
        web_app,
        "_complete_discovery_dialogue_json",
        lambda *_args, **_kwargs: {
            "action": "update_strategy",
            "assistant_message": "已按你的多条要求更新策略。",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {"patch": compound_patch},
                }
            ],
            "_agent_runtime": "openai_agents",
            "_sdk_session_managed": False,
        },
    )
    verifier_calls: list[dict[str, Any]] = []

    def verify(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        verifier_calls.append(kwargs)
        return {
            "verified": False,
            "verdict": "reject",
            "patch": {},
            "rationale": "should not run on low-risk compound path",
        }

    monkeypatch.setattr(web_app, "_run_discovery_patch_verifier_agents_sdk", verify)

    result = asyncio.run(
        web_app.discovery_grill_turn(
            {
                "user_message": (
                    "人源免疫肽，RT 预测，越多越好，DDA，审查候选"
                ),
                "phase": "grilling",
                "intent_snapshot": {},
                "gap_report": {
                    "required_missing": ["task"],
                    "optional_missing": [],
                    "ready_for_confirm": False,
                },
            }
        )
    )

    assert verifier_calls == []
    assert result["action"] == "update_strategy"
    # May equal the Manager dump or a deterministic compound-hint superset
    # (species_coverage / mixed_acquisition_policy / coverage_mode, etc.).
    extra = result["extra_fields"]
    for key, value in compound_patch.items():
        if key == "run_horizon":
            continue
        assert key in extra, key
        assert extra[key] == value
    assert result["tool_calls"]
    assert result["tool_calls"][0]["name"] == "update_strategy"
    assert result.get("semantic_verification") is None
    assert not any(
        "semantic verification" in error
        for error in result.get("contract_errors") or []
    )


def test_compound_commitment_hints_extracts_packed_sentence_without_inventing_rt():
    packed = web_app._discovery_compound_commitment_hints(
        "人源免疫肽，RT 预测，越多越好，DDA，审查候选"
    )
    assert packed["species"] == ["human"]
    assert packed["species_policy"] == "prefer"
    assert packed["species_coverage"] == "prefer_listed"
    assert packed["task_type"] == "rt_prediction"
    assert packed["acquisition_mode"] == "dda"
    assert packed["mixed_acquisition_policy"] == "reject_mixed"
    assert packed["quota_flexibility"] == "open_ended"
    assert packed["coverage_mode"] == "exhaustive"
    assert packed["run_horizon"] == "candidates_reviewed"
    assert packed["special_themes"] == ["immunopeptidomics"]

    all_data = web_app._discovery_compound_commitment_hints(
        "目标：检索PRIDE数据库中的所有人类免疫肽组学数据"
    )
    assert all_data["species"] == ["human"]
    assert all_data["coverage_mode"] == "exhaustive"
    assert all_data["quota_flexibility"] == "open_ended"
    assert all_data["target_project_count"] is None

    # Theme-only chat must not invent RT or acquisition/horizon.
    immuno_only = web_app._discovery_compound_commitment_hints("免疫肽数据")
    assert "task_type" not in immuno_only
    assert "acquisition_mode" not in immuno_only
    assert "run_horizon" not in immuno_only
    assert immuno_only.get("special_themes") == ["immunopeptidomics"]

    recap_text = (
        "科学目标：免疫肽组学数据发现"
        "研究主题：immunopeptidomics"
        "物种：仅限人（Homo sapiens），硬约束"
        "采集模式：仅 DDA，硬约束"
        "下游任务：纯浏览探索（browse_only）\n"
        "交付终点：候选加审查（candidates_reviewed）\n"
        "规模：越多越好，开放上限（exhaustive + open_ended）\n"
        "标记方式：不限（保持开放）"
    )
    recap = web_app._discovery_compound_commitment_hints(recap_text)
    assert recap["objective"] == "免疫肽组学数据发现"
    assert recap["special_themes"] == ["immunopeptidomics"]
    assert recap["species"] == ["human"]
    assert recap["species_policy"] == "include_only"
    assert recap["acquisition_mode"] == "dda"
    assert recap["task_type"] == "browse_only"
    assert recap["run_horizon"] == "candidates_reviewed"
    assert recap["coverage_mode"] == "exhaustive"
    assert recap["quota_flexibility"] == "open_ended"
    assert recap["labeling_strategy"] == "any"
    assert recap["labeling_hard"] is False

    manager_underwrite = {
        "objective": "免疫肽组学数据发现",
        "task_type": "browse_only",
        "run_horizon": "candidates_reviewed",
        "species": ["human"],
        "acquisition_mode": "dda",
        "special_themes": ["immunopeptidomics"],
    }
    filled_recap = web_app._merge_discovery_compound_commitment_hints(
        manager_underwrite,
        recap_text,
    )
    assert filled_recap["species_policy"] == "include_only"
    assert filled_recap["labeling_strategy"] == "any"
    assert filled_recap["labeling_hard"] is False
    assert filled_recap["quota_flexibility"] == "open_ended"
    assert filled_recap["coverage_mode"] == "exhaustive"
    assert len(filled_recap) == 13
    assert web_app._discovery_low_risk_single_field_verifier_skip(
        filled_recap,
        tool_interpretation_difference=True,
    )


def test_merge_compound_hints_fills_soft_only_manager_dump_and_keeps_skip_true():
    """Local flake: Manager soft-only dump + packed user message → ≥6 fields, skip."""

    soft_only = {
        "objective": "人源免疫肽组 RT 预测数据集构建",
        "task_type": "rt_prediction",
        "special_themes": ["immunopeptidomics"],
    }
    user_message = "人源免疫肽，RT 预测，越多越好，DDA，审查候选"
    filled = web_app._merge_discovery_compound_commitment_hints(
        soft_only,
        user_message,
    )
    assert len(filled) >= 6
    assert filled["species"] == ["human"]
    assert filled["acquisition_mode"] == "dda"
    assert filled["run_horizon"] == "candidates_reviewed"
    assert filled["quota_flexibility"] == "open_ended"
    assert filled["coverage_mode"] == "exhaustive"
    assert "species_coverage" in filled
    assert web_app._discovery_low_risk_single_field_verifier_skip(
        filled,
        tool_interpretation_difference=True,
    )
    # Non-whitelist Manager keys must still force critic (not stripped).
    mixed = {
        **soft_only,
        "ptm_types": ["phospho"],
    }
    filled_mixed = web_app._merge_discovery_compound_commitment_hints(
        mixed,
        user_message,
    )
    assert filled_mixed.get("ptm_types") == ["phospho"]
    assert not web_app._discovery_low_risk_single_field_verifier_skip(
        filled_mixed,
        tool_interpretation_difference=False,
    )


def test_soft_only_compound_manager_underwrite_skips_verifier_with_hint_fill(
    monkeypatch,
):
    """Soft-only SDK patch on packed compound sentence must write ≥6 via hints."""

    soft_only = {
        "objective": "人源免疫肽组 RT 预测数据集构建",
        "task_type": "rt_prediction",
        "special_themes": ["immunopeptidomics"],
    }
    user_message = "人源免疫肽，RT 预测，越多越好，DDA，审查候选"

    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)
    monkeypatch.setattr(
        web_app,
        "_discovery_llm_client",
        lambda *_args, **_kwargs: client,
    )
    monkeypatch.setattr(
        web_app,
        "_complete_discovery_dialogue_json",
        lambda *_args, **_kwargs: {
            "action": "update_strategy",
            "assistant_message": "已记录主题相关信息。",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {"patch": soft_only},
                }
            ],
            "_agent_runtime": "openai_agents",
            "_sdk_session_managed": False,
        },
    )
    verifier_calls: list[dict[str, Any]] = []

    def verify(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        verifier_calls.append(kwargs)
        return {
            "verified": False,
            "verdict": "reject",
            "patch": {},
            "rationale": "should not run after compound hint fill",
        }

    monkeypatch.setattr(web_app, "_run_discovery_patch_verifier_agents_sdk", verify)

    result = asyncio.run(
        web_app.discovery_grill_turn(
            {
                "user_message": user_message,
                "phase": "grilling",
                "intent_snapshot": {},
                "gap_report": {
                    "required_missing": ["task"],
                    "optional_missing": [],
                    "ready_for_confirm": False,
                },
            }
        )
    )

    assert verifier_calls == []
    assert result["action"] == "update_strategy"
    extra = result["extra_fields"]
    assert len(extra) >= 6
    assert extra["species"] == ["human"]
    assert extra["acquisition_mode"] == "dda"
    assert "run_horizon" not in extra
    assert extra["task_type"] == "rt_prediction"
    assert extra["quota_flexibility"] == "open_ended"
    assert result.get("semantic_verification") is None
    assert not any(
        "semantic verification" in error
        for error in result.get("contract_errors") or []
    )



def test_non_whitelist_field_forces_semantic_verifier_even_with_soft_keys(
    monkeypatch,
):
    """ptm_types (non-whitelist) must force critic even when soft keys are present."""

    mixed_patch = {
        "objective": "免疫肽数据",
        "special_themes": ["immunopeptidomics"],
        "ptm_types": ["phospho"],
    }
    assert not web_app._discovery_low_risk_single_field_verifier_skip(
        mixed_patch,
        tool_interpretation_difference=False,
    )

    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)
    monkeypatch.setattr(
        web_app,
        "_discovery_llm_client",
        lambda *_args, **_kwargs: client,
    )
    monkeypatch.setattr(
        web_app,
        "_complete_discovery_dialogue_json",
        lambda *_args, **_kwargs: {
            "action": "update_strategy",
            "assistant_message": "I will update the strategy.",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {"patch": mixed_patch},
                }
            ],
            "_agent_runtime": "openai_agents",
            "_sdk_session_managed": False,
        },
    )
    verifier_calls: list[dict[str, Any]] = []

    def verify(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        verifier_calls.append(kwargs)
        return {
            "verified": False,
            "verdict": "reject",
            "patch": {},
            "rationale": "Hard non-whitelist fields are not grounded.",
        }

    monkeypatch.setattr(web_app, "_run_discovery_patch_verifier_agents_sdk", verify)

    result = asyncio.run(
        web_app.discovery_grill_turn(
            {
                "user_message": "免疫肽数据，要看 phospho",
                "phase": "grilling",
                "intent_snapshot": {},
                "gap_report": {
                    "required_missing": ["task"],
                    "optional_missing": [],
                    "ready_for_confirm": False,
                },
            }
        )
    )

    assert len(verifier_calls) == 1
    assert verifier_calls[0]["proposed_patch"] == mixed_patch
    # Soft reject retains soft keys only.
    assert result["action"] == "update_strategy"
    assert result["extra_fields"] == {
        "objective": "免疫肽数据",
        "special_themes": ["immunopeptidomics"],
    }
    assert "ptm_types" not in result["extra_fields"]
    sv = result["semantic_verification"]
    assert sv["verdict"] == "reject"
    assert sv["verified"] is False
    assert sv["soft_reject_kept_fields"] == ["objective", "special_themes"]
    assert "ptm_types" in sv["soft_reject_dropped_fields"]


def test_soft_reject_keeps_objective_from_explicit_tool_patch(monkeypatch):
    """Verifier reject must retain soft keys instead of wiping the whole card."""

    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)
    monkeypatch.setattr(
        web_app,
        "_discovery_llm_client",
        lambda *_args, **_kwargs: client,
    )
    monkeypatch.setattr(
        web_app,
        "_complete_discovery_dialogue_json",
        lambda *_args, **_kwargs: {
            "action": "update_strategy",
            "assistant_message": "I will update the strategy.",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {
                        "patch": {
                            "objective": "免疫肽数据",
                            "special_themes": ["immunopeptidomics"],
                            "species": ["mouse"],
                            "acquisition_mode": "dia",
                            # non-whitelist forces critic even with soft keys present
                            "ptm_types": ["phospho"],
                        }
                    },
                }
            ],
            "_agent_runtime": "openai_agents",
            "_sdk_session_managed": False,
        },
    )
    monkeypatch.setattr(
        web_app,
        "_run_discovery_patch_verifier_agents_sdk",
        lambda *_args, **_kwargs: {
            "verified": False,
            "verdict": "reject",
            "patch": {},
            "rationale": "Hard fields are not grounded in the latest message.",
        },
    )

    result = asyncio.run(
        web_app.discovery_grill_turn(
            {
                "user_message": (
                    "Use mouse DIA for immunopeptidomics, objective is "
                    "免疫肽数据."
                ),
                "phase": "grilling",
                "intent_snapshot": {},
                "gap_report": {
                    "required_missing": ["task"],
                    "optional_missing": [],
                    "ready_for_confirm": False,
                },
            }
        )
    )

    assert result["action"] == "update_strategy"
    assert result["extra_fields"] == {
        "objective": "免疫肽数据",
        "special_themes": ["immunopeptidomics"],
    }
    assert "species" not in result["extra_fields"]
    assert "acquisition_mode" not in result["extra_fields"]
    assert result["tool_calls"] == [
        {
            "name": "update_strategy",
            "arguments": {
                "patch": {
                    "objective": "免疫肽数据",
                    "special_themes": ["immunopeptidomics"],
                }
            },
        }
    ]
    sv = result["semantic_verification"]
    assert sv["verdict"] == "reject"
    assert sv["verified"] is False
    assert sv["soft_reject_kept_fields"] == ["objective", "special_themes"]
    assert set(sv["soft_reject_dropped_fields"]) == {
        "acquisition_mode",
        "ptm_types",
        "species",
    }
    assert sv["patch"] == {
        "objective": "免疫肽数据",
        "special_themes": ["immunopeptidomics"],
    }
    assert ("已写入" in result["assistant_message"] or "可核验" in result["assistant_message"])
    assert "未写入" in result["assistant_message"]
    assert not any(
        "semantic verification" in error
        for error in result.get("contract_errors") or []
    )


def test_soft_reject_without_soft_keys_still_blocks_hard_fields(monkeypatch):
    """Hard-only rejected patches keep fail-closed wipe (no soft subset)."""

    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)
    monkeypatch.setattr(
        web_app,
        "_discovery_llm_client",
        lambda *_args, **_kwargs: client,
    )
    monkeypatch.setattr(
        web_app,
        "_complete_discovery_dialogue_json",
        lambda *_args, **_kwargs: {
            "action": "update_strategy",
            "assistant_message": "I will update the strategy.",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {
                        "patch": {
                            "species": ["mouse"],
                            "acquisition_mode": "dia",
                            "ptm_types": ["phospho"],
                        }
                    },
                }
            ],
            "_agent_runtime": "openai_agents",
            "_sdk_session_managed": False,
        },
    )
    monkeypatch.setattr(
        web_app,
        "_run_discovery_patch_verifier_agents_sdk",
        lambda *_args, **_kwargs: {
            "verified": False,
            "verdict": "reject",
            "patch": {},
            "rationale": "Not grounded.",
        },
    )

    result = asyncio.run(
        web_app.discovery_grill_turn(
            {
                "user_message": "Can you compare mouse DIA with other options?",
                "phase": "grilling",
                "intent_snapshot": {},
                "gap_report": {
                    "required_missing": ["task"],
                    "optional_missing": [],
                    "ready_for_confirm": False,
                },
            }
        )
    )

    assert result["tool_calls"] == []
    assert result["extra_fields"] == {}
    assert result["action"] in {"advise", "clarify"}
    assert result["semantic_verification"]["verdict"] == "reject"
    assert result["semantic_verification"].get("soft_reject_kept_fields") is None
    assert any(
        "semantic verification" in error for error in result["contract_errors"]
    )


def test_soft_reject_v2_global_reject_drops_species_in_turn(monkeypatch):
    """Global critic reject must not soft-keep species (NI-2 fixture)."""

    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)
    monkeypatch.setattr(
        web_app,
        "_discovery_llm_client",
        lambda *_args, **_kwargs: client,
    )
    monkeypatch.setattr(
        web_app,
        "_complete_discovery_dialogue_json",
        lambda *_args, **_kwargs: {
            "action": "update_strategy",
            "assistant_message": "I will update the strategy.",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {
                        "patch": {
                            "objective": "免疫肽数据",
                            "species": ["mouse"],
                            "acquisition_mode": "dia",
                            "ptm_types": ["phospho"],
                        }
                    },
                }
            ],
            "_agent_runtime": "openai_agents",
            "_sdk_session_managed": False,
        },
    )
    monkeypatch.setattr(
        web_app,
        "_run_discovery_patch_verifier_agents_sdk",
        lambda *_args, **_kwargs: {
            "verified": False,
            "verdict": "reject",
            "patch": {},
            "rationale": "Global reject without field list.",
        },
    )

    result = asyncio.run(
        web_app.discovery_grill_turn(
            {
                "user_message": "mouse DIA immunopeptidomics",
                "phase": "grilling",
                "intent_snapshot": {},
                "gap_report": {
                    "required_missing": ["task"],
                    "optional_missing": [],
                    "ready_for_confirm": False,
                },
            }
        )
    )

    assert result["action"] == "update_strategy"
    assert result["extra_fields"] == {"objective": "免疫肽数据"}
    assert "species" not in result["extra_fields"]
    sv = result["semantic_verification"]
    assert sv["soft_reject_kept_fields"] == ["objective"]
    assert "species" in sv["soft_reject_dropped_fields"]
    assert "acquisition_mode" in sv["soft_reject_dropped_fields"]
    assert "已写入可核验部分" in result["assistant_message"]
    assert "未写入" in result["assistant_message"]


def test_soft_reject_v2_field_level_keeps_species_when_only_ptm_vetoed(monkeypatch):
    """Field-level critic veto drops only named keys; low-risk hard keys stay."""

    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)
    monkeypatch.setattr(
        web_app,
        "_discovery_llm_client",
        lambda *_args, **_kwargs: client,
    )
    monkeypatch.setattr(
        web_app,
        "_complete_discovery_dialogue_json",
        lambda *_args, **_kwargs: {
            "action": "update_strategy",
            "assistant_message": "I will update the strategy.",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {
                        "patch": {
                            "objective": "免疫肽数据",
                            "species": ["human"],
                            "acquisition_mode": "dda",
                            "ptm_types": ["phospho"],
                        }
                    },
                }
            ],
            "_agent_runtime": "openai_agents",
            "_sdk_session_managed": False,
        },
    )
    monkeypatch.setattr(
        web_app,
        "_run_discovery_patch_verifier_agents_sdk",
        lambda *_args, **_kwargs: {
            "verified": False,
            "verdict": "reject",
            "patch": {},
            "missing_fields": ["ptm_types"],
            "rationale": "ptm_types not grounded in latest message.",
        },
    )

    result = asyncio.run(
        web_app.discovery_grill_turn(
            {
                "user_message": "人源免疫肽 DDA，顺带看 phospho",
                "phase": "grilling",
                "intent_snapshot": {},
                "gap_report": {
                    "required_missing": ["task"],
                    "optional_missing": [],
                    "ready_for_confirm": False,
                },
            }
        )
    )

    assert result["action"] == "update_strategy"
    extra = result["extra_fields"]
    assert extra["objective"] == "免疫肽数据"
    assert extra["species"] == ["human"]
    assert extra["acquisition_mode"] == "dda"
    assert "ptm_types" not in extra
    sv = result["semantic_verification"]
    assert set(sv["soft_reject_kept_fields"]) == {
        "acquisition_mode",
        "objective",
        "species",
    }
    assert sv["soft_reject_dropped_fields"] == ["ptm_types"]
    assert "已写入可核验部分" in result["assistant_message"]
    assert "未写入" in result["assistant_message"]


def test_thin_warrant_multi_clause_chinese_whitelist_skips_sv(monkeypatch):
    """Multi-clause Chinese + pure whitelist patch must not force SV (NI-2)."""

    patch = {
        "objective": "人源免疫肽",
        "species": ["human"],
        "task_type": "rt_prediction",
    }
    assert web_app._discovery_low_risk_single_field_verifier_skip(
        patch,
        tool_interpretation_difference=False,
    )

    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)
    monkeypatch.setattr(
        web_app,
        "_discovery_llm_client",
        lambda *_args, **_kwargs: client,
    )
    monkeypatch.setattr(
        web_app,
        "_complete_discovery_dialogue_json",
        lambda *_args, **_kwargs: {
            "action": "update_strategy",
            "assistant_message": "已记录。",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {"patch": patch},
                }
            ],
            "_agent_runtime": "openai_agents",
            "_sdk_session_managed": False,
        },
    )
    verifier_calls: list[dict] = []

    def verify(*_args, **kwargs):
        verifier_calls.append(kwargs)
        return {
            "verified": False,
            "verdict": "reject",
            "patch": {},
            "rationale": "must not run for multi-clause whitelist",
        }

    monkeypatch.setattr(web_app, "_run_discovery_patch_verifier_agents_sdk", verify)

    result = asyncio.run(
        web_app.discovery_grill_turn(
            {
                # Chinese commas create multiple clauses; thin warrant ignores that.
                "user_message": "人源免疫肽，要做 RT 预测。",
                "phase": "grilling",
                "intent_snapshot": {},
                "gap_report": {
                    "required_missing": ["task"],
                    "optional_missing": [],
                    "ready_for_confirm": False,
                },
            }
        )
    )

    assert verifier_calls == []
    assert result["action"] == "update_strategy"
    for key, value in patch.items():
        assert result["extra_fields"][key] == value
    assert result.get("semantic_verification") is None


def test_semantic_verifier_runs_for_final_reconciled_multi_field_patch(monkeypatch):
    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)
    monkeypatch.setattr(
        web_app,
        "_discovery_llm_client",
        lambda *_args, **_kwargs: client,
    )
    # Force critic path (whitelist compound would otherwise skip verifier).
    monkeypatch.setattr(
        web_app,
        "_discovery_low_risk_single_field_verifier_skip",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        web_app,
        "_complete_discovery_dialogue_json",
        lambda *_args, **_kwargs: {
            "action": "update_strategy",
            "assistant_message": "Recorded both choices.",
            "turn_interpretation": {
                "commitments": [
                    {"field": "species", "value": ["mouse"], "source": "mouse"},
                ],
                "clause_audit": [
                    {
                        "clause_id": "C1",
                        "classification": "commitment",
                        "decisions": [
                            {"field": "species", "value": ["mouse"]},
                            {"field": "acquisition_mode", "value": "dia"},
                        ],
                    }
                ],
            },
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {"patch": {"species": ["mouse"]}},
                }
            ],
            "_agent_runtime": "openai_agents",
            "_sdk_session_managed": False,
        },
    )
    verifier_calls: list[dict[str, Any]] = []

    def verify(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        verifier_calls.append(kwargs)
        return {
            "verified": True,
            "verdict": "accept",
            "patch": {
                "species": ["mouse"],
                "acquisition_mode": "dia",
            },
            "rationale": "Both commitments are grounded.",
        }

    monkeypatch.setattr(web_app, "_run_discovery_patch_verifier_agents_sdk", verify)

    result = asyncio.run(
        web_app.discovery_grill_turn(
            {
                "user_message": "Use mouse and DIA.",
                "phase": "grilling",
                "intent_snapshot": {},
                "gap_report": {
                    "required_missing": ["task"],
                    "optional_missing": [],
                    "ready_for_confirm": False,
                },
            }
        )
    )

    assert len(verifier_calls) == 1
    assert verifier_calls[0]["proposed_patch"] == {
        "species": ["mouse"],
        "acquisition_mode": "dia",
    }
    assert result["extra_fields"] == {
        "species": ["mouse"],
        "acquisition_mode": "dia",
    }


def test_single_sentence_primary_sdk_delta_difference_triggers_completeness_review(
    monkeypatch,
):
    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)
    monkeypatch.setattr(
        web_app,
        "_discovery_llm_client",
        lambda *_args, **_kwargs: client,
    )
    # Force critic: multi-field pure-whitelist skips by design after compound fix.
    monkeypatch.setattr(
        web_app,
        "_discovery_low_risk_single_field_verifier_skip",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        web_app,
        "_complete_discovery_dialogue_json",
        lambda *_args, **_kwargs: {
            "action": "update_strategy",
            "assistant_message": "Recorded mouse DIA.",
            # This compatibility audit accidentally omitted acquisition_mode.
            "turn_interpretation": {
                "commitments": [
                    {"field": "species", "value": ["mouse"], "source": "mouse"},
                ],
            },
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {
                        "patch": {
                            "species": ["mouse"],
                            "acquisition_mode": "dia",
                        }
                    },
                }
            ],
            "_agent_runtime": "openai_agents",
            "_sdk_session_managed": False,
        },
    )
    verifier_calls: list[dict[str, Any]] = []

    def verify(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        verifier_calls.append(kwargs)
        return {
            "verified": True,
            "verdict": "accept",
            "patch": {"species": ["mouse"], "acquisition_mode": "dia"},
            "rationale": "The complete SDK delta is grounded.",
        }

    monkeypatch.setattr(web_app, "_run_discovery_patch_verifier_agents_sdk", verify)

    result = asyncio.run(
        web_app.discovery_grill_turn(
            {
                "user_message": "Use mouse DIA.",
                "phase": "grilling",
                "intent_snapshot": {},
                "gap_report": {
                    "required_missing": ["task"],
                    "optional_missing": [],
                    "ready_for_confirm": False,
                },
            }
        )
    )

    assert len(verifier_calls) == 1
    assert verifier_calls[0]["proposed_patch"] == {
        "species": ["mouse"],
        "acquisition_mode": "dia",
    }
    assert result["extra_fields"] == {
        "species": ["mouse"],
        "acquisition_mode": "dia",
    }


def test_primary_sdk_null_schema_placeholders_are_not_treated_as_clear_commands(
    monkeypatch,
):
    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)
    monkeypatch.setattr(
        web_app,
        "_discovery_llm_client",
        lambda *_args, **_kwargs: client,
    )
    # Force critic: multi-field pure-whitelist skips by design after compound fix.
    monkeypatch.setattr(
        web_app,
        "_discovery_low_risk_single_field_verifier_skip",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        web_app,
        "_complete_discovery_dialogue_json",
        lambda *_args, **_kwargs: {
            "action": "update_strategy",
            "assistant_message": "Recorded rat DIA.",
            "turn_interpretation": {
                "commitments": [
                    {"field": "species", "value": ["rat"], "source": "rat"},
                    {
                        "field": "acquisition_mode",
                        "value": "dia",
                        "source": "DIA",
                    },
                ],
            },
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {
                        "patch": {
                            "species": ["rat"],
                            "acquisition_mode": "dia",
                            "objective": None,
                            "task_type": None,
                            "coverage_mode": None,
                            "repository": None,
                        }
                    },
                }
            ],
            "_agent_runtime": "openai_agents",
            "_sdk_session_managed": False,
        },
    )
    verifier_calls: list[dict[str, Any]] = []

    def verify(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        verifier_calls.append(kwargs)
        return {
            "verified": True,
            "verdict": "accept",
            "patch": {"species": ["rat"], "acquisition_mode": "dia"},
            "rationale": "The complete committed delta is grounded.",
        }

    monkeypatch.setattr(web_app, "_run_discovery_patch_verifier_agents_sdk", verify)

    result = asyncio.run(
        web_app.discovery_grill_turn(
            {
                "user_message": "Use rat DIA.",
                "phase": "grilling",
                "intent_snapshot": {},
                "gap_report": {
                    "required_missing": ["task"],
                    "optional_missing": [],
                    "ready_for_confirm": False,
                },
            }
        )
    )

    assert verifier_calls[0]["proposed_patch"] == {
        "species": ["rat"],
        "acquisition_mode": "dia",
    }
    assert result["extra_fields"] == {
        "species": ["rat"],
        "acquisition_mode": "dia",
    }


def test_partial_completeness_verifier_applies_grounded_subset(monkeypatch):
    """Grill turn must apply the grounded subset, not reject-all + empty patch.

    When the manager proposes multi-field update_strategy and the verifier
    omits 1-2 fields, remaining valid fields still write to the card.
    """
    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)
    monkeypatch.setattr(
        web_app,
        "_discovery_llm_client",
        lambda *_args, **_kwargs: client,
    )
    # Force critic path (whitelist compound would otherwise skip verifier).
    monkeypatch.setattr(
        web_app,
        "_discovery_low_risk_single_field_verifier_skip",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        web_app,
        "_complete_discovery_dialogue_json",
        lambda *_args, **_kwargs: {
            "action": "update_strategy",
            "assistant_message": "Recorded mouse DIA.",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {
                        "patch": {
                            "species": ["mouse"],
                            "acquisition_mode": "dia",
                        }
                    },
                }
            ],
            "_agent_runtime": "openai_agents",
            "_sdk_session_managed": False,
        },
    )
    monkeypatch.setattr(
        web_app,
        "_run_discovery_patch_verifier_agents_sdk",
        lambda *_args, **_kwargs: {
            "verified": True,
            "verdict": "repair",
            "patch": {"species": ["mouse"]},
            "missing_fields": ["acquisition_mode"],
            "partial_grounding": True,
            "rationale": "Only one field was grounded.",
        },
    )

    result = asyncio.run(
        web_app.discovery_grill_turn(
            {
                "user_message": "Use mouse DIA.",
                "phase": "grilling",
                "intent_snapshot": {},
                "gap_report": {
                    "required_missing": ["task"],
                    "optional_missing": [],
                    "ready_for_confirm": False,
                },
            }
        )
    )

    assert result["action"] == "update_strategy"
    assert result["extra_fields"] == {"species": ["mouse"]}
    assert result["tool_calls"] == [
        {
            "name": "update_strategy",
            "arguments": {"patch": {"species": ["mouse"]}},
        }
    ]
    assert result["semantic_verification"]["verified"] is True
    assert result["semantic_verification"]["partial_grounding"] is True
    assert result["semantic_verification"]["missing_fields"] == [
        "acquisition_mode"
    ]
    assert result["semantic_verification"]["patch"] == {"species": ["mouse"]}




def test_multi_field_strategy_paste_partial_verifier_still_applies_remaining_patch(
    monkeypatch,
):
    """When the critic runs, partial_grounding still applies the remaining patch.

    Pure whitelist compound dumps skip the second verifier (see
    test_low_risk_compound_multi_field_patch_skips_semantic_verifier). This test
    forces the critic path so a multi-field paste that omits 1–2 fields still
    applies the grounded subset instead of wiping the whole card.
    """
    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)
    monkeypatch.setattr(
        web_app,
        "_discovery_llm_client",
        lambda *_args, **_kwargs: client,
    )
    # Force critic even if the patch is otherwise low-risk compound whitelist.
    monkeypatch.setattr(
        web_app,
        "_discovery_low_risk_single_field_verifier_skip",
        lambda *_args, **_kwargs: False,
    )
    multi_field_patch = {
        "task_type": "denovo",
        "species": ["human"],
        "species_policy": "prefer",
        "acquisition_mode": "dda",
        "target_project_count": 20,
        "quota_flexibility": "recommended",
    }
    grounded_subset = {
        "task_type": "denovo",
        "species": ["human"],
        "species_policy": "prefer",
        "target_project_count": 20,
        "quota_flexibility": "recommended",
    }
    monkeypatch.setattr(
        web_app,
        "_complete_discovery_dialogue_json",
        lambda *_args, **_kwargs: {
            "action": "update_strategy",
            "assistant_message": "已根据粘贴内容更新策略。",
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {"patch": multi_field_patch},
                }
            ],
            "_agent_runtime": "openai_agents",
            "_sdk_session_managed": False,
        },
    )
    monkeypatch.setattr(
        web_app,
        "_run_discovery_patch_verifier_agents_sdk",
        lambda *_args, **_kwargs: {
            "verified": True,
            "verdict": "repair",
            "patch": grounded_subset,
            "missing_fields": ["acquisition_mode"],
            "partial_grounding": True,
            "rationale": (
                "Grounded task/species/quota-style fields; acquisition_mode "
                "was not independently evidenced."
            ),
        },
    )

    result = asyncio.run(
        web_app.discovery_grill_turn(
            {
                "user_message": (
                    "免疫肽/HLA 配体 · 人源 · 下游偏de novo · "
                    "DDA下游任务de novo物种优先 human规模精选 · "
                    "约 20 个项目采集方式DDA"
                ),
                "phase": "grilling",
                "intent_snapshot": {},
                "gap_report": {
                    "required_missing": ["task"],
                    "optional_missing": [],
                    "ready_for_confirm": False,
                },
            }
        )
    )

    assert result["action"] == "update_strategy"
    assert result["extra_fields"] == grounded_subset
    assert "acquisition_mode" not in result["extra_fields"]
    assert result["tool_calls"] == [
        {
            "name": "update_strategy",
            "arguments": {"patch": grounded_subset},
        }
    ]
    sv = result["semantic_verification"]
    assert sv["verified"] is True
    assert sv["partial_grounding"] is True
    assert sv["missing_fields"] == ["acquisition_mode"]
    assert sv["patch"] == grounded_subset
    # Must not surface the old reject-all contract wipe.
    assert not any(
        "incomplete strategy patch" in error
        for error in result.get("contract_errors") or []
    )

def test_verifier_can_remove_primary_tool_fields_not_classified_as_commitments(
    monkeypatch,
):
    client = OpenAICompatibleDiscoveryLLM(api_key="test", timeout=10)
    monkeypatch.setattr(
        web_app,
        "_discovery_llm_client",
        lambda *_args, **_kwargs: client,
    )
    # Force critic path so uncommitted notes can be stripped by the verifier.
    monkeypatch.setattr(
        web_app,
        "_discovery_low_risk_single_field_verifier_skip",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        web_app,
        "_complete_discovery_dialogue_json",
        lambda *_args, **_kwargs: {
            "action": "update_strategy",
            "assistant_message": "Recorded mouse DIA.",
            "turn_interpretation": {
                "commitments": [
                    {"field": "species", "value": ["mouse"], "source": "mouse"},
                    {
                        "field": "acquisition_mode",
                        "value": "dia",
                        "source": "DIA",
                    },
                    {
                        "field": "notes",
                        "value": "Model-authored convenience summary",
                        "source": "Use mouse DIA",
                    },
                ]
            },
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {
                        "patch": {
                            "species": ["mouse"],
                            "acquisition_mode": "dia",
                            "notes": "Model-authored convenience summary",
                        }
                    },
                }
            ],
            "_agent_runtime": "openai_agents",
            "_sdk_session_managed": False,
        },
    )

    def verify(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["required_fields"] == {
            "species",
            "acquisition_mode",
            "notes",
        }
        return {
            "verified": True,
            "verdict": "repair",
            "patch": {"species": ["mouse"], "acquisition_mode": "dia"},
            "rationale": "Removed a field not grounded as a user commitment.",
        }

    monkeypatch.setattr(web_app, "_run_discovery_patch_verifier_agents_sdk", verify)

    result = asyncio.run(
        web_app.discovery_grill_turn(
            {
                "user_message": "Use mouse DIA.",
                "phase": "grilling",
                "intent_snapshot": {},
                "gap_report": {
                    "required_missing": ["task"],
                    "optional_missing": [],
                    "ready_for_confirm": False,
                },
            }
        )
    )

    assert result["action"] == "update_strategy"
    assert result["extra_fields"] == {
        "species": ["mouse"],
        "acquisition_mode": "dia",
    }
    assert result["semantic_verification"]["removed_uncommitted_fields"] == [
        "notes"
    ]


def test_clause_audit_decisions_are_a_redundant_generic_completeness_channel():
    raw = {
        "action": "update_strategy",
        "turn_interpretation": {
            "commitments": [
                {"field": "species", "value": ["mouse"], "source": "Use mouse"}
            ],
            "clause_audit": [
                {
                    "clause_id": "C1",
                    "classification": "commitment",
                    "decisions": [{"field": "species", "value": ["mouse"]}],
                },
                {
                    "clause_id": "C2",
                    "classification": "commitment",
                    "decisions": [
                        {
                            "field": "run_horizon",
                            "value": "candidates_reviewed",
                        }
                    ],
                },
            ],
        },
        "tool_calls": [
            {
                "name": "update_strategy",
                "arguments": {"patch": {"species": ["mouse"]}},
            }
        ],
    }

    patch, errors = web_app._discovery_turn_patch(
        raw,
        user_message="Use mouse, and review candidates afterward.",
        intent_snapshot={},
    )

    assert errors == []
    assert patch == {
        "species": ["mouse"],
        "run_horizon": "candidates_reviewed",
    }


def test_reconciled_patch_replaces_inconsistent_model_prose(monkeypatch):
    # Keep commitment-filtered patch (species only); low-risk compound skip would
    # otherwise re-apply the full tool dump including uncommitted theme clears.
    monkeypatch.setattr(
        web_app,
        "_discovery_low_risk_single_field_verifier_skip",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        web_app,
        "_merge_discovery_compound_commitment_hints",
        lambda patch, *_args, **_kwargs: dict(patch or {}),
    )
    result, _llm = _run_turn(
        monkeypatch,
        {
            "action": "update_strategy",
            "assistant_message": "Species changed and the old theme was secretly removed.",
            "turn_interpretation": {
                "commitments": [
                    {"field": "species", "value": ["mouse"], "source": "Use mouse"},
                    {
                        "field": "special_themes",
                        "value": [],
                        "source": "old theme was incompatible",
                    },
                ]
            },
            "tool_calls": [
                {
                    "name": "update_strategy",
                    "arguments": {
                        "patch": {"species": ["mouse"], "special_themes": []}
                    },
                }
            ],
        },
        user_message="Use mouse; keep every other setting unchanged.",
        intent_snapshot={"special_themes": ["immunopeptide"]},
    )

    assert result["extra_fields"] == {"species": ["mouse"]}
    assert "secretly removed" not in result["assistant_message"]
    assert "物种=mouse" in result["assistant_message"]


def test_commitment_audit_requires_latest_turn_evidence_and_matching_values():
    ungrounded = {
        "action": "update_strategy",
        "turn_interpretation": {
            "commitments": [
                {"field": "species", "value": ["rat"], "source": "earlier rat idea"}
            ]
        },
        "tool_calls": [
            {
                "name": "update_strategy",
                "arguments": {"patch": {"species": ["rat"]}},
            }
        ],
    }
    patch, errors = web_app._discovery_turn_patch(
        ungrounded,
        user_message="Keep the current species.",
        intent_snapshot={"species": ["human"]},
    )
    assert patch == {}
    assert errors

    conflicting = {
        "action": "update_strategy",
        "turn_interpretation": {
            "commitments": [
                {"field": "species", "value": ["mouse"], "source": "Use mouse"}
            ]
        },
        "tool_calls": [
            {
                "name": "update_strategy",
                "arguments": {"patch": {"species": ["rat"]}},
            }
        ],
    }
    patch, errors = web_app._discovery_turn_patch(
        conflicting,
        user_message="Use mouse.",
        intent_snapshot={},
    )
    assert patch == {}
    assert errors


def test_grill_turn_uses_one_bounded_model_attempt(monkeypatch):
    result, llm = _run_turn(
        monkeypatch,
        TimeoutError("temporary timeout"),
        user_message="继续",
        request_timeout_seconds=25,
    )

    assert result["status"] == "failed"
    assert result["action"] == "advise"
    assert result["tool_calls"] == []
    assert result["extra_fields"] == {}
    assert "策略保持不变" in result["assistant_message"]
    assert len(llm.calls) == 1
    assert llm.timeout == 25


def test_grill_turn_request_budget_uses_profile_timeout_with_server_cap():
    class Client:
        timeout = 180

    client = Client()
    assert web_app._bind_discovery_turn_request_budget(client, {}) == 180
    assert client.timeout == 180

    capped = Client()
    capped.timeout = 600
    assert web_app._bind_discovery_turn_request_budget(capped, {}) == 300
    assert capped.timeout == 300


@pytest.mark.parametrize("grill_confirmed", [None, False, 1, "true"])
def test_discovery_job_start_requires_explicit_boolean_confirmation(
    monkeypatch,
    tmp_path,
    grill_confirmed: Any,
):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    started: list[str] = []
    monkeypatch.setattr(web_app, "_start_discovery_job_thread", started.append)
    before = set(web_app._discovery_jobs)
    body: dict[str, Any] = {"prompt": "Find human data"}
    if grill_confirmed is not None:
        body["grill_confirmed"] = grill_confirmed

    try:
        result = asyncio.run(web_app.start_discovery_job(body))

        assert result["status"] == "rejected"
        assert result["code"] == "grill_confirmation_required"
        assert "grill_confirmed must be true" in result["error"]
        assert started == []
        assert set(web_app._discovery_jobs) == before
    finally:
        with web_app._discovery_jobs_lock:
            for job_id in set(web_app._discovery_jobs) - before:
                web_app._discovery_jobs.pop(job_id, None)


@pytest.mark.parametrize("grill_confirmed", [None, False, 1, "true"])
def test_legacy_direct_discovery_route_cannot_bypass_confirmation(
    monkeypatch,
    grill_confirmed: Any,
):
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        web_app,
        "_run_web_discovery",
        lambda body, **_kwargs: calls.append(body) or {"status": "completed"},
    )
    body: dict[str, Any] = {"prompt": "Find human data"}
    if grill_confirmed is not None:
        body["grill_confirmed"] = grill_confirmed

    result = asyncio.run(web_app.create_discovery(body))

    assert result["status"] == "rejected"
    assert result["code"] == "grill_confirmation_required"
    assert calls == []


def test_core_discovery_runner_enforces_confirmation_even_for_internal_callers():
    with pytest.raises(ValueError, match="grill_confirmed must be true"):
        web_app._run_web_discovery({"prompt": "Find human data"})


def test_discovery_job_preserves_quality_blocked_terminal_state(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr(
        web_app,
        "_run_web_discovery",
        lambda _body, **_kwargs: {
            "status": "blocked",
            "project_count": 24,
            "file_count": 1477,
            "summary": {
                "candidate_projects": 24,
                "selected_projects": 0,
                "delivery_eligible_projects": 0,
            },
            "agent": {
                "status": "blocked",
                "stop_reason": "selection_quality_gate_not_completed",
            },
        },
    )
    monkeypatch.setattr(web_app, "_archive_discovery_job_artifacts", lambda _job_id: None)
    job_id = "discovery_job_quality_blocked"
    with web_app._discovery_jobs_lock:
        web_app._discovery_jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "created_at": web_app._now_app_iso(),
            "started_at": None,
            "finished_at": None,
            "cancel_requested": False,
            "logs": [],
            "body": {"grill_confirmed": True},
            "record": None,
            "error": None,
        }
    try:
        web_app._run_discovery_job(job_id)
        final = asyncio.run(web_app.get_discovery_job(job_id))

        assert final["status"] == "blocked"
        assert final["record"]["summary"]["selected_projects"] == 0
        assert any("quality gate" in item["message"].lower() for item in final["logs"])
    finally:
        with web_app._discovery_jobs_lock:
            web_app._discovery_jobs.pop(job_id, None)


def test_discovery_job_start_accepts_explicit_confirmation(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    started: list[str] = []
    monkeypatch.setattr(web_app, "_start_discovery_job_thread", started.append)
    before = set(web_app._discovery_jobs)

    try:
        result = asyncio.run(
            web_app.start_discovery_job(
                {"prompt": "Find human data", "grill_confirmed": True}
            )
        )

        assert result["status"] == "queued"
        assert started == [result["job_id"]]
    finally:
        with web_app._discovery_jobs_lock:
            for job_id in set(web_app._discovery_jobs) - before:
                web_app._discovery_jobs.pop(job_id, None)


def test_discovery_job_rejects_confirmed_exhaustive_intent_with_legacy_bounded_payload(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    started: list[str] = []
    monkeypatch.setattr(web_app, "_start_discovery_job_thread", started.append)
    before = set(web_app._discovery_jobs)

    try:
        result = asyncio.run(
            web_app.start_discovery_job(
                {
                    "prompt": "目标：检索PRIDE数据库中的所有人类免疫肽组学数据",
                    "goal": "immunopeptidomics",
                    "scale_mode": "curated",
                    "max_projects": 20,
                    "max_candidate_projects": 80,
                    "continuous_discovery": False,
                    "quota_flexibility": "recommended",
                    "quantity_scope": "unspecified",
                    "grill_confirmed": True,
                }
            )
        )

        assert result["status"] == "rejected"
        assert result["code"] == "exhaustive_intent_downgraded"
        assert "重新确认" in result["error"]
        assert started == []
        assert set(web_app._discovery_jobs) == before
    finally:
        with web_app._discovery_jobs_lock:
            for job_id in set(web_app._discovery_jobs) - before:
                web_app._discovery_jobs.pop(job_id, None)


def test_confirmation_fingerprint_is_bound_to_exact_execution_payload(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    started: list[str] = []
    monkeypatch.setattr(web_app, "_start_discovery_job_thread", started.append)
    before = set(web_app._discovery_jobs)
    confirmed = {
        "prompt": "Find human DIA data",
        "species": ["human"],
        "acquisition_mode": "dia",
        "max_projects": 20,
        "grill_confirmed": True,
    }
    fingerprint = web_app._discovery_execution_fingerprint(confirmed)

    try:
        stale_payload = {
            **confirmed,
            "species": ["mouse"],
            "strategy_fingerprint": fingerprint,
        }
        rejected = asyncio.run(web_app.start_discovery_job(stale_payload))

        assert rejected["status"] == "rejected"
        assert rejected["code"] == "strategy_confirmation_mismatch"
        assert started == []
        assert set(web_app._discovery_jobs) == before

        exact_payload = {**confirmed, "strategy_fingerprint": fingerprint}
        accepted = asyncio.run(web_app.start_discovery_job(exact_payload))
        assert accepted["status"] == "queued"
        assert started == [accepted["job_id"]]
    finally:
        with web_app._discovery_jobs_lock:
            for job_id in set(web_app._discovery_jobs) - before:
                web_app._discovery_jobs.pop(job_id, None)


def test_confirmation_fingerprint_survives_server_internal_execution_id():
    canonical = '{"grill_confirmed":true,"prompt":"Find human immunopeptidomics"}'
    body = {
        "grill_confirmed": True,
        "prompt": "Find human immunopeptidomics",
        "strategy_fingerprint_payload": canonical,
        "strategy_fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }

    assert web_app._discovery_confirmation_rejection(body) is None
    assert web_app._discovery_confirmation_rejection(
        {
            **body,
            "_execution_discovery_id": "agents_job_discovery_job_123",
            "_resume_existing_discovery_run": True,
        }
    ) is None


def test_arbitrary_confirmation_fingerprint_is_rejected():
    rejection = web_app._discovery_confirmation_rejection(
        {
            "prompt": "Find human data",
            "grill_confirmed": True,
            "strategy_fingerprint": "a" * 64,
        }
    )

    assert rejection is not None
    assert rejection["code"] == "strategy_confirmation_mismatch"


def test_execution_fingerprint_matches_frontend_contract_fixture():
    assert web_app._discovery_execution_fingerprint(
        {
            "acquisition_mode": "unknown",
            "grill_confirmed": True,
            "idempotency_key": "request-one",
            "mixed_acquisition_policy": "review_mixed",
            "strategy_fingerprint": "old-proof",
        }
    ) == "6efcfee288ca15ae5331a3e8aaa811b490f54ce461a11771ca785986ee5c21f1"


def test_browser_canonical_fingerprint_accepts_valid_small_numbers():
    canonical = '{"grill_confirmed":true,"legacy_floor_ratio":0.000001}'
    body = {
        "grill_confirmed": True,
        "legacy_floor_ratio": 0.000001,
        "strategy_fingerprint_payload": canonical,
        "strategy_fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }

    assert web_app._discovery_confirmation_rejection(body) is None

    rejection = web_app._discovery_confirmation_rejection(
        {**body, "legacy_floor_ratio": 0.000002}
    )
    assert rejection is not None
    assert rejection["code"] == "strategy_confirmation_mismatch"


def test_browser_canonical_fingerprint_comparison_is_type_strict():
    canonical = (
        '{"grill_confirmed":true,"scientific_constraints":'
        '[{"value":true}]}'
    )
    rejection = web_app._discovery_confirmation_rejection(
        {
            "grill_confirmed": True,
            "scientific_constraints": [{"value": 1}],
            "strategy_fingerprint_payload": canonical,
            "strategy_fingerprint": hashlib.sha256(
                canonical.encode("utf-8")
            ).hexdigest(),
        }
    )

    assert rejection is not None
    assert rejection["code"] == "strategy_confirmation_mismatch"


def test_legacy_direct_discovery_route_accepts_explicit_confirmation(monkeypatch):
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        web_app,
        "_run_web_discovery",
        lambda body, **_kwargs: calls.append(body) or {"status": "completed"},
    )
    body = {"prompt": "Find human data", "grill_confirmed": True}

    result = asyncio.run(web_app.create_discovery(body))

    assert result == {"status": "completed"}
    assert calls == [body]
