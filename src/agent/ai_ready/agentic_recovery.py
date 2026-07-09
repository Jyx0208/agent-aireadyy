from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import Field

from agent.ai_ready.mini_e2e import MiniE2EResult, validate_agent_run_ai_ready_mini
from agent.discovery.agentic import default_discovery_llm_client
from agent.models import JsonModel
from agent.utils import write_json


RecoveryActionType = Literal[
    "generate_peaklist_and_retry",
    "partial_export_from_existing_results",
    "recommend_memory_retry",
    "recommend_lower_resources",
    "recommend_smaller_candidate",
    "recommend_spectrum_matching_retry",
    "stop_with_review_gate",
    "no_action",
]

SAFE_EXECUTABLE_ACTIONS = {"generate_peaklist_and_retry"}


class RecoveryLLMClient(Protocol):
    def complete_json(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        """Return a JSON object from an LLM completion."""


class AgenticRecoveryObservation(JsonModel):
    mini_e2e_dir: str
    status: str
    ai_ready_outcome: str | None = None
    usable_partial_outputs: bool = False
    upstream_workflow_outcome: str | None = None
    upstream_usable_partial_outputs: bool = False
    agent_run_dir: str | None = None
    task_types: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    primary_issue: str | None = None
    recommended_next_step: str | None = None
    recovery_status: str | None = None
    recovery_actions: list[dict[str, Any]] = Field(default_factory=list)
    task_results: list[dict[str, Any]] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict)


class AgenticRecoveryActionPlan(JsonModel):
    action_type: RecoveryActionType
    reason: str = ""
    safe_to_execute: bool = False
    auto_executable: bool = False
    expected_effect: str = ""
    blockers: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)


class AgenticRecoveryTraceStep(JsonModel):
    step: str
    thought: str
    action: str | None = None
    observation: str | None = None
    outputs: dict[str, Any] = Field(default_factory=dict)


class AgenticRecoveryResult(JsonModel):
    status: str
    mode: str
    mini_e2e_dir: str
    output_dir: str
    observation: AgenticRecoveryObservation
    planned_actions: list[AgenticRecoveryActionPlan] = Field(default_factory=list)
    executed_actions: list[dict[str, Any]] = Field(default_factory=list)
    retry_summary_path: str | None = None
    final_recommendation: str
    trace: list[AgenticRecoveryTraceStep] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict)


def default_agentic_recovery_llm_client() -> RecoveryLLMClient | None:
    return default_discovery_llm_client()


def run_agentic_recovery(
    *,
    mini_e2e_dir: str | Path,
    output_dir: str | Path | None = None,
    allow_safe_actions: bool = True,
    llm_client: RecoveryLLMClient | None = None,
) -> AgenticRecoveryResult:
    mini_e2e_dir = Path(mini_e2e_dir)
    if not mini_e2e_dir.exists():
        raise ValueError(f"Mini E2E directory does not exist: {mini_e2e_dir}")
    output_dir = Path(output_dir) if output_dir is not None else mini_e2e_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    observation = _load_observation(mini_e2e_dir)
    trace = [
        AgenticRecoveryTraceStep(
            step="observe",
            thought="Read mini E2E and recovery artifacts before planning recovery.",
            observation=observation.model_dump_json(),
        )
    ]
    deterministic_actions = _deterministic_plan(observation)
    llm_actions, llm_note = _llm_plan(llm_client, observation, deterministic_actions) if llm_client else ([], "")
    planned_actions = _validated_actions(llm_actions or deterministic_actions, observation)
    mode = "llm_react" if llm_actions else "deterministic_react"
    trace.append(
        AgenticRecoveryTraceStep(
            step="plan",
            thought=llm_note or "Planned recovery from deterministic blocker rules.",
            action="plan_recovery_actions",
            outputs={"planned_actions": [item.model_dump(mode="json") for item in planned_actions]},
        )
    )

    executed_actions: list[dict[str, Any]] = []
    retry_summary_path: str | None = None
    if allow_safe_actions:
        for action in planned_actions:
            if not action.safe_to_execute or action.action_type not in SAFE_EXECUTABLE_ACTIONS:
                continue
            retry = _execute_safe_action(action, observation, output_dir)
            executed_actions.append(_executed_action_record(retry))
            retry_summary_path = retry.get("retry_summary_path") or retry_summary_path
            trace.append(
                AgenticRecoveryTraceStep(
                    step="act",
                    thought=f"Executed allowlisted recovery action `{action.action_type}`.",
                    action=action.action_type,
                    observation=retry.get("observation"),
                    outputs=retry,
                )
            )
            break
    else:
        trace.append(
            AgenticRecoveryTraceStep(
                step="act",
                thought="Safe action execution disabled by caller.",
                action="skip_execution",
            )
        )

    final_recommendation = _final_recommendation(planned_actions, executed_actions)
    status = _result_status(planned_actions, executed_actions)
    result = AgenticRecoveryResult(
        status=status,
        mode=mode,
        mini_e2e_dir=str(mini_e2e_dir),
        output_dir=str(output_dir),
        observation=observation,
        planned_actions=planned_actions,
        executed_actions=executed_actions,
        retry_summary_path=retry_summary_path,
        final_recommendation=final_recommendation,
        trace=trace,
    )
    files = _write_outputs(result, output_dir)
    result.files = files
    write_json(output_dir / "agentic_recovery_summary.json", result.model_dump(mode="json"))
    return result


def _load_observation(mini_e2e_dir: Path) -> AgenticRecoveryObservation:
    summary_path = mini_e2e_dir / "mini_e2e_summary.json"
    if not summary_path.exists():
        raise ValueError(f"mini_e2e_summary.json not found: {summary_path}")
    summary = _read_json(summary_path)
    recovery = _read_json(mini_e2e_dir / "agent_recovery_report.json")
    task_results = summary.get("task_results") if isinstance(summary.get("task_results"), list) else []
    task_types = [str(item.get("task_type")) for item in task_results if isinstance(item, dict) and item.get("task_type")]
    files = {
        "mini_e2e_summary_json": str(summary_path),
        "mini_e2e_report_md": str(mini_e2e_dir / "mini_e2e_report.md"),
    }
    if (mini_e2e_dir / "agent_recovery_report.json").exists():
        files["agent_recovery_report_json"] = str(mini_e2e_dir / "agent_recovery_report.json")
    if (mini_e2e_dir / "agent_recovery_report.md").exists():
        files["agent_recovery_report_md"] = str(mini_e2e_dir / "agent_recovery_report.md")
    return AgenticRecoveryObservation(
        mini_e2e_dir=str(mini_e2e_dir),
        status=str(summary.get("status") or "unknown"),
        ai_ready_outcome=str(summary.get("ai_ready_outcome") or "") or None,
        usable_partial_outputs=bool(summary.get("usable_partial_outputs")),
        upstream_workflow_outcome=str(summary.get("upstream_workflow_outcome") or recovery.get("workflow_outcome") or "") or None,
        upstream_usable_partial_outputs=bool(
            summary.get("upstream_usable_partial_outputs") or recovery.get("usable_partial_outputs")
        ),
        agent_run_dir=str(summary.get("agent_run_dir") or "") or None,
        task_types=task_types,
        blockers=_safe_string_list(summary.get("blockers")),
        warnings=_safe_string_list(summary.get("warnings")),
        primary_issue=str(recovery.get("primary_issue") or summary.get("primary_issue") or "") or None,
        recommended_next_step=str(recovery.get("recommended_next_step") or summary.get("recommended_next_step") or "") or None,
        recovery_status=str(summary.get("recovery_status") or recovery.get("status") or "") or None,
        recovery_actions=summary.get("recovery_actions") if isinstance(summary.get("recovery_actions"), list) else [],
        task_results=[item for item in task_results if isinstance(item, dict)],
        files=files,
    )


def _deterministic_plan(observation: AgenticRecoveryObservation) -> list[AgenticRecoveryActionPlan]:
    blockers = {item.casefold() for item in observation.blockers}
    primary = (observation.primary_issue or "").casefold()
    task_blockers = {
        str(blocker).casefold()
        for task in observation.task_results
        for blocker in (task.get("blockers") or [])
        if isinstance(task, dict)
    }
    all_blockers = blockers | task_blockers
    warnings = {item.casefold() for item in observation.warnings}
    issue_text = " ".join(sorted(all_blockers | warnings | {primary}))
    if "missing_peaklist" in primary or "needs_peaklist" in all_blockers or "missing_peaklist" in all_blockers:
        return [
            AgenticRecoveryActionPlan(
                action_type="generate_peaklist_and_retry",
                reason="A spectrum-level task is blocked by a missing peaklist.",
                safe_to_execute=bool(observation.agent_run_dir),
                auto_executable=bool(observation.agent_run_dir),
                expected_effect="Generate MGF from existing MSDT/rawspectrum parquet, then rerun mini E2E.",
                blockers=[] if observation.agent_run_dir else ["agent_run_dir_missing"],
            )
        ]
    if "resource_oom" in primary or any("oom" in item for item in all_blockers):
        return [
            AgenticRecoveryActionPlan(
                action_type="recommend_memory_retry",
                reason="Run failed due to memory/resource pressure.",
                expected_effect=(
                    "Recommend a bounded full retry with more JVM memory and lower threads only after explicit user approval."
                ),
                parameters=_oom_retry_parameters(),
            )
        ]
    if any(marker in issue_text for marker in ["download_slow", "download_failed", "download_slow_or_failed", "oversized", "input_too_large"]):
        return [
            AgenticRecoveryActionPlan(
                action_type="recommend_smaller_candidate",
                reason="The current candidate is too slow, too large, or failed during acquisition.",
                expected_effect="Skip this candidate for benchmark and choose a smaller cached mzML/mzXML file.",
                parameters={"auto_execute": False, "preferred_file_types": ["mzML", "mzXML"], "hard_size_limit_mb": 500},
            )
        ]
    if any(marker in issue_text for marker in ["spectrum_mismatch", "spectrum_not_matched", "scan_not_matched"]):
        return [
            AgenticRecoveryActionPlan(
                action_type="recommend_spectrum_matching_retry",
                reason="Search result spectrum ids do not match the available peaklist titles/scans.",
                expected_effect="Try conservative spectrum-id matching strategies before marking spectrum-level tasks blocked.",
                parameters={
                    "auto_execute": False,
                    "matching_strategies": ["title_exact", "scan_number", "native_id", "basename_scan"],
                    "requires_label_revalidation": True,
                },
            )
        ]
    if "msdt_feature_missing" in primary or "msdt_feature_missing" in all_blockers:
        return [
            AgenticRecoveryActionPlan(
                action_type="partial_export_from_existing_results",
                reason=(
                    "FragPipe produced reusable search outputs, but MSDT conversion needs pin features "
                    "that are absent from the selected workflow output."
                ),
                expected_effect=(
                    "Use existing PSM/peptide/PIN/rawspectrum outputs for task-specific AI-ready export; "
                    "only retry clean MSDT with an MSBooster-compatible workflow/config."
                ),
                parameters={
                    "requires_full_rerun": False,
                    "training_quality": "weak",
                    "clean_full_retry_requires": "msbooster_compatible_workflow_or_converter_fix",
                },
            )
        ]
    if (
        "low_psm_msbooster" in primary
        or "zero_psm" in primary
        or "partial_outputs_available" in primary
        or observation.usable_partial_outputs
        or observation.upstream_usable_partial_outputs
        or observation.ai_ready_outcome == "completed_from_usable_partial_outputs"
        or any("low_psm" in item or "zero_psm" in item or "msbooster" in item for item in all_blockers)
    ):
        return [
            AgenticRecoveryActionPlan(
                action_type="partial_export_from_existing_results",
                reason=(
                    "Full workflow produced usable intermediate outputs, but the final workflow state is not cleanly completed."
                ),
                expected_effect=(
                    "Use existing search outputs only for conservative partial AI-ready export; otherwise choose a cleaner candidate."
                ),
                parameters={"requires_full_rerun": False, "training_quality": "weak"},
            )
        ]
    if "review_gate_blocked" in primary:
        return [
            AgenticRecoveryActionPlan(
                action_type="stop_with_review_gate",
                reason="Review gate blocked this run; biological or metadata uncertainty cannot be bypassed automatically.",
                expected_effect="Require file-level evidence or choose another candidate.",
            )
        ]
    return [
        AgenticRecoveryActionPlan(
            action_type="no_action",
            reason="No allowlisted recovery action applies.",
            expected_effect="Keep current report and review manually if needed.",
        )
    ]


def _llm_plan(
    llm_client: RecoveryLLMClient,
    observation: AgenticRecoveryObservation,
    deterministic_actions: list[AgenticRecoveryActionPlan],
) -> tuple[list[AgenticRecoveryActionPlan], str]:
    try:
        payload = llm_client.complete_json(
            system_prompt=_recovery_system_prompt(),
            user_prompt=_recovery_user_prompt(observation, deterministic_actions),
        )
    except Exception:
        return [], ""
    raw_actions = payload.get("actions") if isinstance(payload.get("actions"), list) else []
    actions: list[AgenticRecoveryActionPlan] = []
    for item in raw_actions:
        if not isinstance(item, dict):
            continue
        action_type = str(item.get("action_type") or "").strip()
        if action_type not in _allowed_action_values():
            actions.append(
                AgenticRecoveryActionPlan(
                    action_type="no_action",
                    reason=f"Rejected unsupported LLM action: {action_type}",
                    blockers=["unsupported_llm_action"],
                )
            )
            continue
        actions.append(
            AgenticRecoveryActionPlan(
                action_type=action_type,  # type: ignore[arg-type]
                reason=str(item.get("reason") or ""),
                expected_effect=str(item.get("expected_effect") or ""),
                parameters=item.get("parameters") if isinstance(item.get("parameters"), dict) else {},
            )
        )
    thought = str(payload.get("thought") or payload.get("final_recommendation") or "")
    return actions, thought


def _validated_actions(
    actions: list[AgenticRecoveryActionPlan],
    observation: AgenticRecoveryObservation,
) -> list[AgenticRecoveryActionPlan]:
    deterministic = _deterministic_plan(observation)
    deterministic_by_type = {item.action_type: item for item in deterministic}
    validated: list[AgenticRecoveryActionPlan] = []
    for action in actions:
        if action.action_type not in _allowed_action_values():
            continue
        if action.action_type in SAFE_EXECUTABLE_ACTIONS:
            allowed = deterministic_by_type.get(action.action_type)
            if allowed is None:
                validated.append(
                    action.model_copy(
                        update={
                            "safe_to_execute": False,
                            "auto_executable": False,
                            "blockers": _dedupe([*action.blockers, "action_not_supported_by_current_observation"]),
                        }
                    )
                )
            else:
                validated.append(
                    action.model_copy(
                        update={
                            "safe_to_execute": allowed.safe_to_execute,
                            "auto_executable": allowed.auto_executable,
                            "blockers": _dedupe([*action.blockers, *allowed.blockers]),
                            "expected_effect": action.expected_effect or allowed.expected_effect,
                            "parameters": {**allowed.parameters, **action.parameters},
                        }
                    )
                )
        else:
            validated.append(action.model_copy(update={"safe_to_execute": False, "auto_executable": False}))
    return validated or deterministic


def _execute_safe_action(
    action: AgenticRecoveryActionPlan,
    observation: AgenticRecoveryObservation,
    output_dir: Path,
) -> dict[str, Any]:
    if action.action_type != "generate_peaklist_and_retry":
        return {"action": action.model_dump(mode="json"), "status": "skipped"}
    if not observation.agent_run_dir:
        return {
            "action": action.model_dump(mode="json"),
            "status": "blocked",
            "blockers": ["agent_run_dir_missing"],
        }
    retry_dir = output_dir / "agentic_recovery_retry"
    retry_result: MiniE2EResult = validate_agent_run_ai_ready_mini(
        agent_run_dir=observation.agent_run_dir,
        task_types=observation.task_types or None,
        output_dir=retry_dir,
        auto_recover=True,
    )
    return {
        "action": action.model_dump(mode="json"),
        "status": retry_result.status,
        "retry_summary_path": retry_result.summary_path,
        "observation": retry_result.model_dump_json(),
        "files": {
            "retry_mini_e2e_summary_json": retry_result.summary_path,
            "retry_mini_e2e_report_md": retry_result.report_path,
            "retry_recovery_report_json": retry_result.recovery_report_json,
            "retry_recovery_report_md": retry_result.recovery_report_md,
        },
    }


def _executed_action_record(retry: dict[str, Any]) -> dict[str, Any]:
    action = retry.get("action") if isinstance(retry.get("action"), dict) else {}
    return {
        **action,
        "status": retry.get("status"),
        "retry_summary_path": retry.get("retry_summary_path"),
        "files": retry.get("files") or {},
    }


def _write_outputs(result: AgenticRecoveryResult, output_dir: Path) -> dict[str, str]:
    plan_path = output_dir / "agentic_recovery_plan.json"
    trace_path = output_dir / "agentic_recovery_trace.json"
    summary_path = output_dir / "agentic_recovery_summary.json"
    report_path = output_dir / "agentic_recovery_report.md"
    write_json(
        plan_path,
        {
            "mode": result.mode,
            "observation": result.observation.model_dump(mode="json"),
            "planned_actions": [item.model_dump(mode="json") for item in result.planned_actions],
        },
    )
    write_json(trace_path, [item.model_dump(mode="json") for item in result.trace])
    report_path.write_text(_markdown_report(result), encoding="utf-8")
    return {
        "agentic_recovery_plan_json": str(plan_path),
        "agentic_recovery_trace_json": str(trace_path),
        "agentic_recovery_summary_json": str(summary_path),
        "agentic_recovery_report_md": str(report_path),
    }


def _markdown_report(result: AgenticRecoveryResult) -> str:
    lines = [
        "# Agentic Recovery Report",
        "",
        f"- Status: `{result.status}`",
        f"- Mode: `{result.mode}`",
        f"- Mini E2E dir: `{result.mini_e2e_dir}`",
        f"- Primary issue: `{result.observation.primary_issue or 'none'}`",
        f"- Final recommendation: {result.final_recommendation}",
        "",
        "## Planned Actions",
        "",
    ]
    for action in result.planned_actions:
        lines.extend(
            [
                f"### `{action.action_type}`",
                "",
                f"- Safe to execute: `{action.safe_to_execute}`",
                f"- Auto executable: `{action.auto_executable}`",
                f"- Reason: {action.reason}",
                f"- Expected effect: {action.expected_effect}",
                f"- Blockers: {', '.join(action.blockers) if action.blockers else 'None'}",
                f"- Parameters: `{json.dumps(action.parameters, ensure_ascii=False, sort_keys=True)}`",
                "",
            ]
        )
    lines.extend(["## Executed Actions", ""])
    if not result.executed_actions:
        lines.append("- None")
    for action in result.executed_actions:
        lines.append(f"- `{json.dumps(action, ensure_ascii=False, sort_keys=True)}`")
    lines.extend(["", "## Trace", ""])
    for step in result.trace:
        lines.append(f"- `{step.step}`: {step.thought}")
    return "\n".join(lines) + "\n"


def _recovery_system_prompt() -> str:
    return (
        "You are a cautious ReAct-style proteomics recovery planner. Return only JSON. "
        "You may propose actions, but deterministic validators will decide what is safe to execute. "
        "Never bypass review gates, change species/FASTA/workflow, or rerun full workflow."
    )


def _recovery_user_prompt(
    observation: AgenticRecoveryObservation,
    deterministic_actions: list[AgenticRecoveryActionPlan],
) -> str:
    return (
        "Observe this mini E2E recovery state and propose the next recovery action.\n\n"
        f"Observation JSON:\n{observation.model_dump_json()}\n\n"
        f"Deterministic safe baseline actions:\n{json.dumps([item.model_dump(mode='json') for item in deterministic_actions], ensure_ascii=False)}\n\n"
        "Allowed action_type values:\n"
        f"{json.dumps(sorted(_allowed_action_values()), ensure_ascii=False)}\n\n"
        "Return JSON with keys:\n"
        "- thought: short reasoning\n"
        "- actions: list of {action_type, reason, expected_effect, parameters}\n"
        "- final_recommendation: short user-facing recommendation\n"
    )


def _result_status(planned_actions: list[AgenticRecoveryActionPlan], executed_actions: list[dict[str, Any]]) -> str:
    if executed_actions:
        return "executed"
    if any(action.action_type != "no_action" for action in planned_actions):
        return "planned"
    return "no_action"


def _final_recommendation(
    planned_actions: list[AgenticRecoveryActionPlan],
    executed_actions: list[dict[str, Any]],
) -> str:
    if executed_actions:
        latest = executed_actions[-1]
        return f"Executed safe action; retry status is {latest.get('status', 'unknown')}."
    first = planned_actions[0] if planned_actions else None
    if first is None or first.action_type == "no_action":
        return "No allowlisted recovery action applies; keep current report for manual review."
    if first.action_type == "recommend_lower_resources":
        return "Recommend lower threads/RAM or a smaller candidate; do not automatically rerun full workflow."
    if first.action_type == "recommend_memory_retry":
        ram = first.parameters.get("suggested_fragpipe_ram_gb") or "bounded"
        threads = first.parameters.get("suggested_threads") or "lower"
        return (
            f"Recommend explicit full retry with AGENT_FRAGPIPE_RAM_GB={ram} and threads={threads}; "
            "do not rerun automatically."
        )
    if first.action_type == "partial_export_from_existing_results":
        return "Use existing search results for conservative partial AI-ready export, then choose a cleaner candidate if label quality is too weak."
    if first.action_type == "recommend_smaller_candidate":
        return "Recommend choosing a cleaner/smaller candidate or using partial export if search results exist."
    if first.action_type == "recommend_spectrum_matching_retry":
        return "Recommend reviewing spectrum id/title/scan matching before rerunning spectrum-level exporters."
    if first.action_type == "stop_with_review_gate":
        return "Stop at review gate; require stronger file-level evidence or choose another candidate."
    return first.expected_effect or first.reason


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _safe_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _allowed_action_values() -> set[str]:
    return {
        "generate_peaklist_and_retry",
        "partial_export_from_existing_results",
        "recommend_memory_retry",
        "recommend_lower_resources",
        "recommend_smaller_candidate",
        "recommend_spectrum_matching_retry",
        "stop_with_review_gate",
        "no_action",
    }


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _oom_retry_parameters() -> dict[str, Any]:
    current_ram = _int_env("AGENT_FRAGPIPE_RAM_GB", default=8)
    max_ram = _int_env("AGENT_RECOVERY_MAX_FRAGPIPE_RAM_GB", default=16)
    suggested_ram = min(max(current_ram + 4, 8), max_ram)
    current_threads = _int_env("AGENT_THREAD_NUM", default=8)
    suggested_threads = max(1, min(4, current_threads // 2 or 1))
    return {
        "current_fragpipe_ram_gb": current_ram,
        "max_fragpipe_ram_gb": max_ram,
        "suggested_fragpipe_ram_gb": suggested_ram,
        "suggested_threads": suggested_threads,
        "requires_explicit_rerun": True,
        "auto_execute": False,
        "safety_note": "Only retry if host/container memory budget can safely cover this setting.",
    }


def _int_env(name: str, *, default: int) -> int:
    try:
        value = int(str(os.environ.get(name) or "").strip())
    except ValueError:
        return default
    return value if value > 0 else default
