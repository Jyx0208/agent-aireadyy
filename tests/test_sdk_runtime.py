from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from agents import Agent, ModelSettings, RunConfig, Runner, function_tool
from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

from agent.control_plane.sdk_runtime import (
    ApprovalDecision,
    PublicRunHooks,
    configure_local_trace,
    create_role_session,
    resume_with_approval_decisions,
    role_session_id,
    serialize_run_state,
)


class ScriptedModel(Model):
    def __init__(self, actions: list[tuple[str, dict[str, Any] | str]]) -> None:
        self.actions = actions
        self.calls = 0

    async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
        action, payload = self.actions[self.calls]
        self.calls += 1
        if action == "final":
            output = [
                ResponseOutputMessage(
                    id=f"message_{self.calls}",
                    content=[ResponseOutputText(annotations=[], text=str(payload), type="output_text")],
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


def test_role_sessions_are_isolated_and_persistent(tmp_path: Path) -> None:
    db_path = tmp_path / "agent_sessions.sqlite"

    async def exercise() -> None:
        manager = create_role_session(db_path, project_id="project 1", role="manager")
        discovery = create_role_session(db_path, project_id="project 1", role="discovery")
        await manager.add_items([{"role": "user", "content": "manager context"}])
        await discovery.add_items([{"role": "user", "content": "discovery context"}])

        reopened = create_role_session(db_path, project_id="project 1", role="manager")
        manager_items = await reopened.get_items()
        discovery_items = await discovery.get_items()
        assert manager_items[-1]["content"] == "manager context"
        assert discovery_items[-1]["content"] == "discovery context"
        assert role_session_id("project 1", "manager") == "project_1:manager"

    asyncio.run(exercise())


def test_manager_calls_specialist_with_sessions_hooks_and_local_trace(tmp_path: Path) -> None:
    evidence_calls: list[str] = []
    public_events: list[tuple[str, dict[str, Any]]] = []
    session_db = tmp_path / "agent_sessions.sqlite"
    trace_path = tmp_path / "sdk_trace.jsonl"
    manager_session = create_role_session(session_db, project_id="project_contract", role="manager")
    specialist_session = create_role_session(
        session_db,
        project_id="project_contract",
        role="discovery",
    )
    hooks = PublicRunHooks(lambda event_type, payload: public_events.append((event_type, payload)))

    def read_evidence(candidate_id: str) -> str:
        """Read deterministic candidate evidence."""
        evidence_calls.append(candidate_id)
        return json.dumps({"candidate_id": candidate_id, "valid": True})

    specialist = Agent(
        name="Discovery Specialist",
        instructions="Inspect the requested candidate using read_evidence.",
        model=ScriptedModel(
            [
                ("read_evidence", {"candidate_id": "PXD_CONTRACT"}),
                ("final", "Candidate evidence is valid."),
            ]
        ),
        tools=[function_tool(read_evidence)],
        model_settings=ModelSettings(parallel_tool_calls=False),
    )
    manager = Agent(
        name="Project Manager",
        instructions="Ask the specialist to inspect the candidate, then summarize.",
        model=ScriptedModel(
            [
                ("inspect_dataset", {"input": "Inspect PXD_CONTRACT"}),
                ("final", "The specialist validated the candidate."),
            ]
        ),
        tools=[
            specialist.as_tool(
                tool_name="inspect_dataset",
                tool_description="Inspect one candidate dataset.",
                session=specialist_session,
                hooks=hooks,
            )
        ],
        model_settings=ModelSettings(parallel_tool_calls=False),
    )
    configure_local_trace("sdk_contract", trace_path)
    run_config = RunConfig(
        workflow_name="sdk_contract",
        group_id="project_contract",
        trace_metadata={
            "run_id": "sdk_contract",
            "workflow": "contract_test",
            "api_key": "must-not-be-stored",
        },
        tracing_disabled=False,
        trace_include_sensitive_data=False,
    )

    async def exercise() -> tuple[Any, list[Any], list[Any]]:
        result = await Runner.run(
            manager,
            "Inspect the candidate.",
            session=manager_session,
            hooks=hooks,
            run_config=run_config,
            max_turns=5,
        )
        return result, await manager_session.get_items(), await specialist_session.get_items()

    result, manager_items, specialist_items = asyncio.run(exercise())

    assert result.final_output == "The specialist validated the candidate."
    assert evidence_calls == ["PXD_CONTRACT"]
    assert manager_items
    assert specialist_items
    event_types = [event_type for event_type, _payload in public_events]
    assert "sdk_agent_started" in event_types
    assert "sdk_tool_started" in event_types
    assert "sdk_tool_completed" in event_types
    assert "sdk_agent_completed" in event_types
    assert trace_path.exists()
    trace_text = trace_path.read_text(encoding="utf-8")
    assert "trace_started" in trace_text
    assert "span_completed" in trace_text
    assert "must-not-be-stored" not in trace_text
    assert "[REDACTED]" in trace_text


def test_approval_run_state_survives_serialization_and_resume(tmp_path: Path) -> None:
    executions: list[str] = []
    session_db = tmp_path / "agent_sessions.sqlite"
    context = {"project_id": "approval_project"}

    def publish_release(release_id: str) -> str:
        """Publish one release after approval."""
        executions.append(release_id)
        return f"published:{release_id}"

    approval_tool = function_tool(publish_release, needs_approval=True)
    initial_agent = Agent(
        name="Approval Manager",
        instructions="Publish the requested release.",
        model=ScriptedModel([("publish_release", {"release_id": "release_v1"})]),
        tools=[approval_tool],
    )
    session = create_role_session(session_db, project_id="approval_project", role="manager")
    first_result = asyncio.run(
        Runner.run(
            initial_agent,
            "Publish release_v1.",
            context=context,
            session=session,
            max_turns=3,
            run_config=RunConfig(tracing_disabled=True),
        )
    )

    assert len(first_result.interruptions) == 1
    assert executions == []
    state_json = serialize_run_state(first_result.to_state())

    resumed_agent = Agent(
        name="Approval Manager",
        instructions="Publish the requested release.",
        model=ScriptedModel([("final", "Release publication completed.")]),
        tools=[approval_tool],
    )
    reopened_session = create_role_session(
        session_db,
        project_id="approval_project",
        role="manager",
    )
    resumed = asyncio.run(
        resume_with_approval_decisions(
            initial_agent=resumed_agent,
            state_json=state_json,
            context=context,
            decisions=[ApprovalDecision(outcome="approve")],
            session=reopened_session,
            run_config=RunConfig(tracing_disabled=True),
            max_turns=3,
        )
    )

    assert resumed.final_output == "Release publication completed."
    assert executions == ["release_v1"]


def test_resume_requires_one_decision_per_interruption(tmp_path: Path) -> None:
    def controlled_action(value: str) -> str:
        """Run a controlled action."""
        return value

    tool = function_tool(controlled_action, needs_approval=True)
    agent = Agent(
        name="Approval Manager",
        instructions="Run the controlled action.",
        model=ScriptedModel([("controlled_action", {"value": "x"})]),
        tools=[tool],
    )
    result = asyncio.run(
        Runner.run(
            agent,
            "Run it.",
            context={},
            max_turns=2,
            run_config=RunConfig(tracing_disabled=True),
        )
    )
    state_json = serialize_run_state(result.to_state())

    with pytest.raises(ValueError, match="approval_decision_count_mismatch"):
        asyncio.run(
            resume_with_approval_decisions(
                initial_agent=agent,
                state_json=state_json,
                context={},
                decisions=[],
                run_config=RunConfig(tracing_disabled=True),
            )
        )


def test_public_run_hooks_raise_when_cancel_requested() -> None:
    from types import SimpleNamespace

    events: list[tuple[str, dict[str, Any]]] = []
    hooks = PublicRunHooks(
        lambda event_type, payload: events.append((event_type, payload)),
        should_cancel=lambda: True,
    )

    async def exercise() -> None:
        with pytest.raises(InterruptedError, match="cancelled"):
            await hooks.on_llm_start(
                context=None,
                agent=SimpleNamespace(name="agent"),
                system_prompt="x",
                input_items=[],
            )

    asyncio.run(exercise())
    assert events == []
