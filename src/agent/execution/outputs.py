from __future__ import annotations

import re
from glob import glob
from pathlib import Path

from agent.models import DdaExecutionPlan, JsonModel


class ExecutionFailureEvent(JsonModel):
    category: str
    reason: str
    evidence_kind: str = "log_marker"
    path: Path | None = None
    marker: str | None = None


def required_execution_outputs(plan: DdaExecutionPlan) -> list[tuple[str, Path]]:
    outputs: list[tuple[str, Path]] = []
    if plan.raw_data_type != "mgf":
        outputs.append(("raw spectrum", plan.rawspectrum_output_path))
    if plan.raw_data_type == "mzml":
        outputs.append(("FragPipe PIN", plan.expected_pin_path))
    msdt_output = plan.output_paths.get("fp_msdt")
    if msdt_output is not None:
        outputs.append(("MSDT parquet", msdt_output))
    return outputs


def _fragpipe_pin_exists(plan: DdaExecutionPlan) -> bool:
    if plan.expected_pin_path.exists() and plan.expected_pin_path.is_file():
        return True
    return any(Path(path).is_file() for path in glob(plan.expected_pin_glob))


def missing_required_execution_outputs(plan: DdaExecutionPlan) -> list[str]:
    missing: list[str] = []
    for label, path in required_execution_outputs(plan):
        if label == "FragPipe PIN" and _fragpipe_pin_exists(plan):
            continue
        if not path.exists() or not path.is_file():
            missing.append(f"{label}: {path}")
    if plan.output_paths.get("fp_msdt") is None:
        missing.append("MSDT parquet: <not configured>")
    return missing


def execution_failure_reasons(
    plan: DdaExecutionPlan,
    returncode: int,
    stdout: str = "",
    stderr: str = "",
) -> list[str]:
    reasons: list[str] = []
    if returncode != 0:
        reasons.append(f"Docker exited with code {returncode}.")
    reasons.extend(f"Missing required output: {item}" for item in missing_required_execution_outputs(plan))

    combined = f"{stdout}\n{stderr}"
    if "Insufficient memory!" in combined:
        reasons.append("MSDT-Converter reported insufficient memory.")
    if re.search(r"finished,\s*exit code:\s*[1-9]\d*", combined, re.IGNORECASE):
        reasons.append("MSDT-Converter internal process exited non-zero.")
    for marker in (
        "Process returned non-zero exit code",
        "generate msdt fail",
        "miss mzml_fp_pin_path",
    ):
        if marker in combined:
            reasons.append(f"MSDT-Converter log marker: {marker}")

    deduped: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            deduped.append(reason)
    return deduped


def execution_failure_events(
    plan: DdaExecutionPlan,
    returncode: int,
    stdout: str = "",
    stderr: str = "",
) -> list[ExecutionFailureEvent]:
    events: list[ExecutionFailureEvent] = []
    if returncode != 0:
        events.append(
            ExecutionFailureEvent(
                category="process_failed",
                reason=f"Docker exited with code {returncode}.",
                evidence_kind="process_exit",
                marker=str(returncode),
            )
        )

    for label, path in required_execution_outputs(plan):
        if label == "FragPipe PIN" and _fragpipe_pin_exists(plan):
            continue
        if not path.exists() or not path.is_file():
            if label == "FragPipe PIN":
                category = "missing_pin"
            elif label == "MSDT parquet":
                category = "missing_msdt_output"
            else:
                category = "missing_rawspectrum_output"
            events.append(
                ExecutionFailureEvent(
                    category=category,
                    reason=f"Missing required output: {label}: {path}",
                    evidence_kind="missing_output",
                    path=path,
                )
            )
    if plan.output_paths.get("fp_msdt") is None:
        events.append(
            ExecutionFailureEvent(
                category="missing_msdt_output",
                reason="Missing required output: MSDT parquet: <not configured>",
                evidence_kind="missing_output",
            )
        )

    combined = f"{stdout}\n{stderr}"
    if "Insufficient memory!" in combined:
        events.append(
            ExecutionFailureEvent(
                category="insufficient_memory",
                reason="MSDT-Converter reported insufficient memory.",
                evidence_kind="log_marker",
                marker="Insufficient memory!",
            )
        )
    if re.search(r"finished,\s*exit code:\s*[1-9]\d*", combined, re.IGNORECASE):
        events.append(
            ExecutionFailureEvent(
                category="process_failed",
                reason="MSDT-Converter internal process exited non-zero.",
                evidence_kind="log_marker",
                marker="finished, exit code",
            )
        )
    for marker in (
        "Process returned non-zero exit code",
        "generate msdt fail",
        "miss mzml_fp_pin_path",
    ):
        if marker in combined:
            category = "missing_pin" if marker == "miss mzml_fp_pin_path" else "process_failed"
            events.append(
                ExecutionFailureEvent(
                    category=category,
                    reason=f"MSDT-Converter log marker: {marker}",
                    evidence_kind="log_marker",
                    marker=marker,
                )
            )

    deduped: list[ExecutionFailureEvent] = []
    seen: set[tuple[str, str, str | None, str | None]] = set()
    for event in events:
        key = (event.category, event.reason, str(event.path) if event.path else None, event.marker)
        if key not in seen:
            seen.add(key)
            deduped.append(event)
    return deduped
