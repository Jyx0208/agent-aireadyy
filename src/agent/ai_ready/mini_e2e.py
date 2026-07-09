from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field

from agent.ai_ready.agent_run_bridge import AgentRunAiReadyBuildResult, build_ai_ready_from_agent_run
from agent.ai_ready.agent_run_peaklist import generate_agent_run_peaklist
from agent.ai_ready.validation import validate_ai_ready_build
from agent.agent_core.recovery_report import analyze_agent_recovery
from agent.discovery.task_readiness import normalize_task_type
from agent.models import JsonModel
from agent.utils import write_json


DEFAULT_MINI_E2E_TASKS = ["rt_prediction", "denovo"]


class MiniE2ETaskResult(JsonModel):
    task_type: str
    status: str
    rows_out: int = 0
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    validation_status: str | None = None
    files: dict[str, str] = Field(default_factory=dict)


class MiniE2ERecoveryAction(JsonModel):
    action_type: str
    status: str
    trigger: str
    output_dir: str | None = None
    files: dict[str, str] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MiniE2EResult(JsonModel):
    status: str
    ai_ready_outcome: str | None = None
    usable_partial_outputs: bool = False
    mode: str
    agent_run_dir: str | None = None
    input_value: str | None = None
    output_dir: str
    generic_ai_ready_available: bool = False
    located_artifacts: int = 0
    task_results: list[MiniE2ETaskResult] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    recovery_actions: list[MiniE2ERecoveryAction] = Field(default_factory=list)
    recovery_status: str | None = None
    primary_issue: str | None = None
    recommended_next_step: str | None = None
    recovery_report_json: str | None = None
    recovery_report_md: str | None = None
    upstream_recovery_status: str | None = None
    upstream_workflow_outcome: str | None = None
    upstream_usable_partial_outputs: bool = False
    upstream_primary_issue: str | None = None
    upstream_recommended_next_step: str | None = None
    upstream_recovery_report_json: str | None = None
    upstream_recovery_report_md: str | None = None
    summary_path: str
    report_path: str


def validate_agent_run_ai_ready_mini(
    *,
    agent_run_dir: str | Path,
    task_types: list[str] | None,
    output_dir: str | Path,
    project_accession: str | None = None,
    source_file: str | None = None,
    q_value_threshold: float = 0.01,
    probability_threshold: float = 0.9,
    require_confidence: bool = False,
    search_engine: str | None = None,
    max_input_file_mb: int = 2048,
    allow_large_input: bool = False,
    peaklists: list[str | Path] | None = None,
    auto_recover: bool = True,
) -> MiniE2EResult:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    tasks = _normalize_tasks(task_types)
    bridge = build_ai_ready_from_agent_run(
        agent_run_dir=agent_run_dir,
        task_types=tasks,
        output_dir=output_dir / "agent_run_build",
        project_accession=project_accession,
        source_file=source_file,
        q_value_threshold=q_value_threshold,
        probability_threshold=probability_threshold,
        require_confidence=require_confidence,
        search_engine=search_engine,
        max_input_file_mb=max_input_file_mb,
        allow_large_input=allow_large_input,
        peaklists=peaklists,
    )
    recovery_actions: list[MiniE2ERecoveryAction] = []
    if auto_recover:
        action, recovered_bridge = _try_recover_missing_peaklist(
            agent_run_dir=agent_run_dir,
            output_dir=output_dir,
            tasks=tasks,
            bridge=bridge,
            explicit_peaklists=peaklists or [],
            project_accession=project_accession,
            source_file=source_file,
            q_value_threshold=q_value_threshold,
            probability_threshold=probability_threshold,
            require_confidence=require_confidence,
            search_engine=search_engine,
            max_input_file_mb=max_input_file_mb,
            allow_large_input=allow_large_input,
        )
        if action is not None:
            recovery_actions.append(action)
        if recovered_bridge is not None:
            bridge = recovered_bridge
    task_results = [_mini_task_result(task, bridge) for task in bridge.task_results]
    blockers = _dedupe([blocker for task in task_results for blocker in task.blockers])
    warnings = _dedupe([warning for task in task_results for warning in task.warnings])
    status = _overall_status(task_results, blockers)
    summary_path = output_dir / "mini_e2e_summary.json"
    report_path = output_dir / "mini_e2e_report.md"
    _remove_stale_report_inputs(summary_path, report_path)
    result = MiniE2EResult(
        status=status,
        mode="existing_agent_run",
        agent_run_dir=str(agent_run_dir),
        output_dir=str(output_dir),
        generic_ai_ready_available=bool(bridge.locator_summary.get("generic_ai_ready_available")),
        located_artifacts=int(bridge.locator_summary.get("located_artifacts") or 0),
        task_results=task_results,
        blockers=blockers,
        warnings=warnings,
        recovery_actions=recovery_actions,
        summary_path=str(summary_path),
        report_path=str(report_path),
    )
    _attach_recovery_report(result, output_dir)
    _attach_upstream_recovery_report(result, Path(agent_run_dir), output_dir)
    _finalize_ai_ready_outcome(result)
    write_json(summary_path, result.model_dump(mode="json"))
    report_path.write_text(_markdown_report(result, bridge), encoding="utf-8")
    return result


def mini_e2e_parameters_only_placeholder(
    *,
    input_value: str,
    output_dir: str | Path,
    mode: str,
) -> MiniE2EResult:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    normalized_mode = str(mode or "parameters").strip().lower()
    blockers = ["input_value_mini_run_not_implemented"]
    warnings = [
        "v1 mini E2E is read-only for existing agent run directories.",
        f"Requested mode was {normalized_mode}; no original agent workflow was executed.",
    ]
    summary_path = output_dir / "mini_e2e_summary.json"
    report_path = output_dir / "mini_e2e_report.md"
    _remove_stale_report_inputs(summary_path, report_path)
    result = MiniE2EResult(
        status="blocked",
        mode=normalized_mode,
        input_value=input_value,
        output_dir=str(output_dir),
        blockers=blockers,
        warnings=warnings,
        summary_path=str(summary_path),
        report_path=str(report_path),
    )
    _finalize_ai_ready_outcome(result)
    write_json(summary_path, result.model_dump(mode="json"))
    _attach_recovery_report(result, output_dir)
    _finalize_ai_ready_outcome(result)
    write_json(summary_path, result.model_dump(mode="json"))
    report_path.write_text(_markdown_report(result, None), encoding="utf-8")
    return result


def _mini_task_result(task, bridge: AgentRunAiReadyBuildResult) -> MiniE2ETaskResult:
    validation_status: str | None = None
    files = dict(task.files)
    try:
        paths = validate_ai_ready_build(task.output_dir, task.task_type)
        files.update({key: str(path) for key, path in paths.items()})
        payload = _read_json(paths["ai_ready_validation_report_json"])
        validation_status = str(payload.get("status") or "")
    except Exception as exc:
        task.warnings.append(f"validation_failed:{exc}")
    return MiniE2ETaskResult(
        task_type=task.task_type,
        status=task.status,
        rows_out=task.rows_out,
        blockers=task.blockers,
        warnings=task.warnings,
        validation_status=validation_status,
        files=files,
    )


def _normalize_tasks(task_types: list[str] | None) -> list[str]:
    values = task_types or DEFAULT_MINI_E2E_TASKS
    result: list[str] = []
    for value in values:
        task_type = normalize_task_type(value)
        if task_type is None:
            raise ValueError(f"Unsupported task type: {value}")
        if task_type not in result:
            result.append(task_type)
    return result


def _overall_status(tasks: list[MiniE2ETaskResult], blockers: list[str]) -> str:
    if any(task.status == "completed" and task.rows_out > 0 for task in tasks):
        return "completed"
    if blockers:
        return "blocked"
    return "needs_review"


def _markdown_report(result: MiniE2EResult, bridge: AgentRunAiReadyBuildResult | None) -> str:
    lines = [
        "# Mini E2E AI-ready Validation Report",
        "",
        f"- Status: `{result.status}`",
        f"- AI-ready outcome: `{result.ai_ready_outcome or 'unknown'}`",
        f"- Usable partial outputs: `{result.usable_partial_outputs}`",
        f"- Mode: `{result.mode}`",
        f"- Agent run dir: `{result.agent_run_dir or ''}`",
        f"- Input value: `{result.input_value or ''}`",
        f"- Located artifacts: {result.located_artifacts}",
        f"- Generic AI-ready available: {result.generic_ai_ready_available}",
        "",
        "## Upstream Run Recovery",
        "",
        f"- Recovery status: `{result.upstream_recovery_status or 'not_run'}`",
        f"- Workflow outcome: `{result.upstream_workflow_outcome or 'unknown'}`",
        f"- Usable partial outputs: `{result.upstream_usable_partial_outputs}`",
        f"- Primary issue: `{result.upstream_primary_issue or 'none'}`",
        f"- Recommended next step: {result.upstream_recommended_next_step or 'None'}",
        f"- Recovery report JSON: `{result.upstream_recovery_report_json or ''}`",
        f"- Recovery report Markdown: `{result.upstream_recovery_report_md or ''}`",
        "",
        "## Task Results",
        "",
    ]
    if not result.task_results:
        lines.append("- No task exports were executed.")
    for task in result.task_results:
        lines.extend(
            [
                f"### {task.task_type}",
                "",
                f"- Status: `{task.status}`",
                f"- Validation: `{task.validation_status or 'not_run'}`",
                f"- Rows out: {task.rows_out}",
                f"- Blockers: {', '.join(task.blockers) if task.blockers else 'None'}",
                f"- Warnings: {', '.join(task.warnings) if task.warnings else 'None'}",
                "",
            ]
        )
    lines.extend(["## Overall Blockers", ""])
    if not result.blockers:
        lines.append("- None")
    for blocker in result.blockers:
        lines.append(f"- `{blocker}`")
    lines.extend(["", "## Overall Warnings", ""])
    if not result.warnings:
        lines.append("- None")
    for warning in result.warnings:
        lines.append(f"- `{warning}`")
    lines.extend(
        [
            "",
            "## Recovery Actions",
            "",
        ]
    )
    if not result.recovery_actions:
        lines.append("- None")
    for action in result.recovery_actions:
        lines.extend(
            [
                f"### {action.action_type}",
                "",
                f"- Status: `{action.status}`",
                f"- Trigger: `{action.trigger}`",
                f"- Output dir: `{action.output_dir or ''}`",
                f"- Blockers: {', '.join(action.blockers) if action.blockers else 'None'}",
                f"- Warnings: {', '.join(action.warnings) if action.warnings else 'None'}",
                "",
            ]
        )
    lines.extend(
        [
            "",
            "## Recovery",
            "",
            f"- Recovery status: `{result.recovery_status or 'not_run'}`",
            f"- Primary issue: `{result.primary_issue or 'none'}`",
            f"- Recommended next step: {result.recommended_next_step or 'None'}",
            f"- Recovery report JSON: `{result.recovery_report_json or ''}`",
            f"- Recovery report Markdown: `{result.recovery_report_md or ''}`",
        ]
    )
    if bridge is not None:
        lines.extend(["", "## Bridge Summary", ""])
        lines.append(f"- Bridge status: `{bridge.status}`")
        lines.append(f"- Search result files: {bridge.locator_summary.get('search_result_count', 0)}")
        lines.append(f"- Peaklists: {bridge.locator_summary.get('peaklist_count', 0)}")
    return "\n".join(lines) + "\n"


def _try_recover_missing_peaklist(
    *,
    agent_run_dir: str | Path,
    output_dir: Path,
    tasks: list[str],
    bridge: AgentRunAiReadyBuildResult,
    explicit_peaklists: list[str | Path],
    project_accession: str | None,
    source_file: str | None,
    q_value_threshold: float,
    probability_threshold: float,
    require_confidence: bool,
    search_engine: str | None,
    max_input_file_mb: int,
    allow_large_input: bool,
) -> tuple[MiniE2ERecoveryAction | None, AgentRunAiReadyBuildResult | None]:
    if explicit_peaklists or not _has_missing_peaklist(bridge):
        return None, None
    action_dir = output_dir / "recovery_generate_peaklist"
    try:
        peaklist = generate_agent_run_peaklist(
            agent_run_dir=agent_run_dir,
            output_dir=action_dir,
            source="auto",
            max_output_mb=max_input_file_mb,
        )
    except Exception as exc:
        return (
            MiniE2ERecoveryAction(
                action_type="generate_peaklist_and_retry",
                status="blocked",
                trigger="task_peaklist_absent",
                output_dir=str(action_dir),
                blockers=[f"peaklist_generation_failed:{exc}"],
            ),
            None,
        )
    files = {
        "peaklist_report_json": peaklist.json_path,
        "peaklist_report_md": peaklist.report_path,
    }
    if peaklist.peaklist_path:
        files["peaklist_mgf"] = peaklist.peaklist_path
    if peaklist.status != "completed" or not peaklist.peaklist_path:
        return (
            MiniE2ERecoveryAction(
                action_type="generate_peaklist_and_retry",
                status="blocked",
                trigger="task_peaklist_absent",
                output_dir=str(action_dir),
                files=files,
                blockers=peaklist.blockers,
                warnings=peaklist.warnings,
            ),
            None,
        )
    recovered_bridge = build_ai_ready_from_agent_run(
        agent_run_dir=agent_run_dir,
        task_types=tasks,
        output_dir=output_dir / "agent_run_build",
        project_accession=project_accession,
        source_file=source_file,
        q_value_threshold=q_value_threshold,
        probability_threshold=probability_threshold,
        require_confidence=require_confidence,
        search_engine=search_engine,
        max_input_file_mb=max_input_file_mb,
        allow_large_input=allow_large_input,
        peaklists=[peaklist.peaklist_path],
    )
    return (
        MiniE2ERecoveryAction(
            action_type="generate_peaklist_and_retry",
            status="completed",
            trigger="task_peaklist_absent",
            output_dir=str(action_dir),
            files=files,
            warnings=peaklist.warnings,
        ),
        recovered_bridge,
    )


def _has_missing_peaklist(bridge: AgentRunAiReadyBuildResult) -> bool:
    return any("needs_peaklist" in task.blockers for task in bridge.task_results)


def _read_json(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {}
    payload = path.read_text(encoding="utf-8")
    try:
        value = __import__("json").loads(payload)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


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


def _attach_recovery_report(result: MiniE2EResult, output_dir: Path) -> None:
    try:
        paths = analyze_agent_recovery(output_dir)
        report = _read_json(paths["agent_recovery_report_json"])
    except Exception as exc:
        result.warnings = _dedupe([*result.warnings, f"recovery_report_failed:{exc}"])
        return
    result.recovery_status = str(report.get("status") or "")
    result.usable_partial_outputs = bool(report.get("usable_partial_outputs") or result.usable_partial_outputs)
    primary_issue = report.get("primary_issue")
    result.primary_issue = str(primary_issue) if primary_issue else None
    next_step = report.get("recommended_next_step")
    result.recommended_next_step = str(next_step) if next_step else None
    result.recovery_report_json = str(paths["agent_recovery_report_json"])
    result.recovery_report_md = str(paths["agent_recovery_report_md"])


def _remove_stale_report_inputs(*paths: Path) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _attach_upstream_recovery_report(result: MiniE2EResult, agent_run_dir: Path, output_dir: Path) -> None:
    if not agent_run_dir.exists():
        return
    try:
        paths = analyze_agent_recovery(agent_run_dir, output_dir=output_dir / "upstream_recovery")
        report = _read_json(paths["agent_recovery_report_json"])
    except Exception as exc:
        result.warnings = _dedupe([*result.warnings, f"upstream_recovery_report_failed:{exc}"])
        return
    result.upstream_recovery_status = str(report.get("status") or "")
    workflow_outcome = report.get("workflow_outcome")
    result.upstream_workflow_outcome = str(workflow_outcome) if workflow_outcome else None
    result.upstream_usable_partial_outputs = bool(report.get("usable_partial_outputs"))
    if result.upstream_usable_partial_outputs:
        result.usable_partial_outputs = True
    primary_issue = report.get("primary_issue")
    result.upstream_primary_issue = str(primary_issue) if primary_issue else None
    next_step = report.get("recommended_next_step")
    result.upstream_recommended_next_step = str(next_step) if next_step else None
    result.upstream_recovery_report_json = str(paths["agent_recovery_report_json"])
    result.upstream_recovery_report_md = str(paths["agent_recovery_report_md"])


def _finalize_ai_ready_outcome(result: MiniE2EResult) -> None:
    if result.status == "completed" and result.upstream_usable_partial_outputs:
        result.ai_ready_outcome = "completed_from_usable_partial_outputs"
        result.usable_partial_outputs = True
        return
    if result.status == "completed":
        result.ai_ready_outcome = "completed_from_clean_or_existing_outputs"
        return
    if result.status == "blocked" and result.generic_ai_ready_available:
        result.ai_ready_outcome = "generic_ai_ready_available_task_labels_missing"
        return
    if result.status == "blocked":
        result.ai_ready_outcome = "blocked"
        return
    result.ai_ready_outcome = result.status or "unknown"
