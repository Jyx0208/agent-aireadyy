from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Callable, Literal

from agent.models import JsonModel

try:
    from agents import RunHooks
    from agents.tracing import TracingProcessor
except ImportError:  # pragma: no cover - optional agents-sdk extra
    RunHooks = object  # type: ignore[assignment,misc]
    TracingProcessor = object  # type: ignore[assignment,misc]


PublicEventSink = Callable[[str, dict[str, Any]], None]
ApprovalOutcome = Literal["approve", "reject"]


class ApprovalDecision(JsonModel):
    outcome: ApprovalOutcome
    rejection_message: str | None = None


class PublicRunHooks(RunHooks):  # type: ignore[misc]
    """Translate SDK lifecycle callbacks into compact, non-sensitive project events."""

    def __init__(
        self,
        sink: PublicEventSink,
        should_cancel: Callable[[], bool] | None = None,
    ) -> None:
        self._sink = sink
        self._should_cancel = should_cancel

    async def on_agent_start(self, context: Any, agent: Any) -> None:
        self._raise_if_cancelled()
        self._emit("sdk_agent_started", agent=_agent_name(agent), usage=_usage(context))

    async def on_agent_end(self, context: Any, agent: Any, output: Any) -> None:
        self._emit(
            "sdk_agent_completed",
            agent=_agent_name(agent),
            output_type=type(output).__name__,
            usage=_usage(context),
        )

    async def on_llm_start(
        self,
        context: Any,
        agent: Any,
        system_prompt: str | None,
        input_items: list[Any],
    ) -> None:
        self._raise_if_cancelled()
        self._emit(
            "sdk_llm_started",
            agent=_agent_name(agent),
            input_item_count=len(input_items),
            has_system_prompt=bool(system_prompt),
        )

    async def on_llm_end(self, context: Any, agent: Any, response: Any) -> None:
        self._emit(
            "sdk_llm_completed",
            agent=_agent_name(agent),
            output_item_count=len(getattr(response, "output", []) or []),
            usage=_usage(context),
        )

    async def on_tool_start(self, context: Any, agent: Any, tool: Any) -> None:
        self._raise_if_cancelled()
        self._emit(
            "sdk_tool_started",
            agent=_agent_name(agent),
            tool=_tool_name(tool),
        )

    async def on_tool_end(self, context: Any, agent: Any, tool: Any, result: object) -> None:
        self._emit(
            "sdk_tool_completed",
            agent=_agent_name(agent),
            tool=_tool_name(tool),
            result_type=type(result).__name__,
        )

    async def on_handoff(self, context: Any, from_agent: Any, to_agent: Any) -> None:
        self._emit(
            "sdk_handoff",
            from_agent=_agent_name(from_agent),
            to_agent=_agent_name(to_agent),
        )

    def _raise_if_cancelled(self) -> None:
        if self._should_cancel is not None and self._should_cancel():
            raise InterruptedError("Discovery cancelled.")

    def _emit(self, event_type: str, **payload: Any) -> None:
        self._sink(event_type, payload)


class LocalTraceProcessor(TracingProcessor):  # type: ignore[misc]
    """Route redacted SDK trace topology to per-run JSONL files."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._run_paths: dict[str, Path] = {}
        self._trace_paths: dict[str, Path] = {}

    def register_run(self, run_id: str, path: str | Path) -> None:
        with self._lock:
            self._run_paths[run_id] = Path(path)

    def on_trace_start(self, trace: Any) -> None:
        metadata = _safe_mapping(getattr(trace, "metadata", None))
        run_id = str(metadata.get("run_id") or "")
        trace_id = str(getattr(trace, "trace_id", "") or "")
        with self._lock:
            path = self._run_paths.get(run_id)
            if path is None:
                return
            self._trace_paths[trace_id] = path
        self._append(
            path,
            {
                "event": "trace_started",
                "trace_id": trace_id,
                "workflow_name": str(getattr(trace, "name", "") or ""),
                "group_id": str(getattr(trace, "group_id", "") or ""),
                "metadata": metadata,
            },
        )

    def on_trace_end(self, trace: Any) -> None:
        trace_id = str(getattr(trace, "trace_id", "") or "")
        with self._lock:
            path = self._trace_paths.pop(trace_id, None)
        if path is not None:
            self._append(path, {"event": "trace_completed", "trace_id": trace_id})

    def on_span_start(self, span: Any) -> None:
        self._write_span("span_started", span)

    def on_span_end(self, span: Any) -> None:
        self._write_span("span_completed", span)

    def shutdown(self) -> None:
        return None

    def force_flush(self) -> None:
        return None

    def _write_span(self, event: str, span: Any) -> None:
        trace_id = str(getattr(span, "trace_id", "") or "")
        with self._lock:
            path = self._trace_paths.get(trace_id)
        if path is None:
            return
        span_data = getattr(span, "span_data", None)
        self._append(
            path,
            {
                "event": event,
                "trace_id": trace_id,
                "span_id": str(getattr(span, "span_id", "") or ""),
                "parent_id": str(getattr(span, "parent_id", "") or ""),
                "span_type": str(getattr(span_data, "type", "") or type(span_data).__name__),
                "name": str(getattr(span_data, "name", "") or ""),
                "has_error": bool(getattr(span, "error", None)),
            },
        )

    def _append(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self._lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


_TRACE_LOCK = threading.RLock()
_LOCAL_TRACE_PROCESSOR: LocalTraceProcessor | None = None


def configure_local_trace(run_id: str, path: str | Path) -> LocalTraceProcessor:
    """Install one process-wide local processor and route this run to a JSONL file."""

    global _LOCAL_TRACE_PROCESSOR
    with _TRACE_LOCK:
        if _LOCAL_TRACE_PROCESSOR is None:
            try:
                from agents.tracing import set_trace_processors
            except ImportError as exc:  # pragma: no cover - optional agents-sdk extra
                raise RuntimeError("OpenAI Agents SDK is required for local tracing") from exc
            _LOCAL_TRACE_PROCESSOR = LocalTraceProcessor()
            set_trace_processors([_LOCAL_TRACE_PROCESSOR])
        _LOCAL_TRACE_PROCESSOR.register_run(run_id, path)
        return _LOCAL_TRACE_PROCESSOR


def role_session_id(project_id: str, role: str) -> str:
    project = _safe_identifier(project_id)
    agent_role = _safe_identifier(role)
    return f"{project}:{agent_role}"


def create_role_session(
    db_path: str | Path,
    *,
    project_id: str,
    role: str,
    encryption_key: str | None = None,
) -> Any:
    try:
        from agents import SQLiteSession
    except ImportError as exc:  # pragma: no cover - optional agents-sdk extra
        raise RuntimeError("OpenAI Agents SDK is required for agent sessions") from exc
    session_id = role_session_id(project_id, role)
    session = SQLiteSession(session_id, db_path=Path(db_path))
    if not encryption_key:
        return session
    try:
        from agents.extensions.memory import EncryptedSession
    except ImportError as exc:  # pragma: no cover - optional encrypt extra
        raise RuntimeError("EncryptedSession requires the OpenAI Agents SDK encrypt extra") from exc
    return EncryptedSession(session_id, session, encryption_key=encryption_key)


def serialize_run_state(state: Any) -> str:
    return state.to_string(include_tracing_api_key=False)


async def restore_run_state(
    initial_agent: Any,
    state_json: str,
    *,
    context: Any,
) -> Any:
    try:
        from agents import RunState
    except ImportError as exc:  # pragma: no cover - optional agents-sdk extra
        raise RuntimeError("OpenAI Agents SDK is required to restore run state") from exc
    return await RunState.from_string(
        initial_agent,
        state_json,
        context_override=context,
    )


async def resume_with_approval_decisions(
    *,
    initial_agent: Any,
    state_json: str,
    context: Any,
    decisions: list[ApprovalDecision],
    session: Any = None,
    hooks: Any = None,
    run_config: Any = None,
    max_turns: int | None = 10,
) -> Any:
    try:
        from agents import Runner
    except ImportError as exc:  # pragma: no cover - optional agents-sdk extra
        raise RuntimeError("OpenAI Agents SDK is required to resume a run") from exc
    state = await restore_run_state(initial_agent, state_json, context=context)
    interruptions = list(state.get_interruptions())
    if not interruptions:
        raise ValueError("saved_run_has_no_pending_approvals")
    if len(decisions) != len(interruptions):
        raise ValueError("approval_decision_count_mismatch")
    for interruption, decision in zip(interruptions, decisions, strict=True):
        if decision.outcome == "approve":
            state.approve(interruption, always_approve=False)
        else:
            state.reject(
                interruption,
                always_reject=False,
                rejection_message=decision.rejection_message,
            )
    return await Runner.run(
        initial_agent,
        state,
        max_turns=max_turns,
        hooks=hooks,
        run_config=run_config,
        session=session,
    )


def _agent_name(agent: Any) -> str:
    return str(getattr(agent, "name", "") or type(agent).__name__)


def _tool_name(tool: Any) -> str:
    return str(getattr(tool, "name", "") or type(tool).__name__)


def _usage(context: Any) -> dict[str, int]:
    usage = getattr(context, "usage", None)
    return {
        "requests": int(getattr(usage, "requests", 0) or 0),
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }


def _safe_identifier(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value).strip())
    normalized = normalized.strip("._-")
    if not normalized:
        raise ValueError("session_identifier_must_not_be_empty")
    return normalized[:120]


def _safe_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _redact(str(key), item) for key, item in value.items()}


def _redact(key: str, value: Any) -> Any:
    normalized = key.casefold()
    if any(marker in normalized for marker in ("key", "token", "secret", "password", "authorization")):
        return "[REDACTED]"
    if isinstance(value, dict):
        return _safe_mapping(value)
    if isinstance(value, list):
        return [_redact(key, item) for item in value[:50]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value if not isinstance(value, str) else value[:500]
    return type(value).__name__
