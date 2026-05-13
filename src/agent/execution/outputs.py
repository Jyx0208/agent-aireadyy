from __future__ import annotations

import re
from pathlib import Path

from agent.models import DdaExecutionPlan


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


def missing_required_execution_outputs(plan: DdaExecutionPlan) -> list[str]:
    missing: list[str] = []
    for label, path in required_execution_outputs(plan):
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
