from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable, Mapping

from agent.runtime.toolchain import detect_toolchain


RUN_MODES = {"parameters", "prepare", "full"}
RESOURCE_POLICIES = {"conservative", "balanced", "fast"}
VENDOR_RAW_SUFFIXES = (
    ".raw",
    ".raw.zip",
    ".raw.gz",
    ".wiff",
    ".wiff.scan",
    ".d",
    ".d.zip",
    ".d.tar.gz",
    ".d.tgz",
)


def normalize_run_mode(value: Any, *, default: str = "full") -> str:
    mode = str(value or "").strip().lower().replace("-", "_")
    if mode in {"parameters", "parameter", "parameter_only", "params", "plan", "planning"}:
        return "parameters"
    if mode in {"prepare", "prepare_input", "prepare_package", "input_package", "package"}:
        return "prepare"
    if mode in {"full", "workflow", "full_workflow", "run", "run_full"}:
        return "full"
    return default if default in RUN_MODES else "full"


def normalize_resource_policy(value: Any) -> str:
    policy = str(value or "").strip().lower().replace("-", "_")
    return policy if policy in RESOURCE_POLICIES else "balanced"


def _contains_vendor_raw_input(inputs: list[str]) -> bool:
    for value in inputs:
        path = str(value or "").split("?", 1)[0].split("#", 1)[0].lower()
        if path.endswith(VENDOR_RAW_SUFFIXES):
            return True
    return False


def run_preflight(
    *,
    inputs: list[str],
    run_mode: str,
    repository: str,
    output_root: str | Path,
    resource_policy: str = "balanced",
    toolchain_detector: Callable[[], Any] = detect_toolchain,
    disk_usage: Callable[[Path], Any] = shutil.disk_usage,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    mode = normalize_run_mode(run_mode)
    policy = normalize_resource_policy(resource_policy)
    output_root = Path(output_root)
    checks: list[dict[str, str]] = []
    blocking: list[str] = []
    warnings: list[str] = []

    toolchain = toolchain_detector()
    contains_vendor_raw = _contains_vendor_raw_input(inputs)
    if mode == "parameters":
        checks.append({"name": "toolchain", "status": "ok", "message": "Parameter mode does not require Docker or msconvert."})
    else:
        if not bool(getattr(toolchain, "docker_daemon_available", False)):
            issue = "Docker daemon is required for prepare/full one-click runs."
            blocking.append(issue)
            checks.append({"name": "docker", "status": "blocked", "message": issue})
        else:
            checks.append({"name": "docker", "status": "ok", "message": "Docker daemon is reachable."})
        if (
            mode == "full"
            and bool(getattr(toolchain, "docker_daemon_available", False))
            and not bool(
                getattr(toolchain, "docker_msdt_image_available", False)
            )
        ):
            issue = (
                "Required MSDT workflow Docker image is not installed: "
                "guomics2017/msdt-converter:v1.3."
            )
            blocking.append(issue)
            checks.append(
                {"name": "msdt_image", "status": "blocked", "message": issue}
            )
        if not bool(getattr(toolchain, "msconvert_available", False)) and not bool(getattr(toolchain, "docker_daemon_available", False)):
            issue = "msconvert or Docker ProteoWizard fallback is required for vendor RAW conversion."
            blocking.append(issue)
            checks.append({"name": "msconvert", "status": "blocked", "message": issue})
        elif bool(getattr(toolchain, "msconvert_available", False)):
            checks.append({"name": "msconvert", "status": "ok", "message": "Local msconvert is available."})
        elif (
            contains_vendor_raw
            and not bool(
                getattr(toolchain, "docker_pwiz_image_available", False)
            )
        ):
            issue = (
                "Vendor RAW input requires local msconvert or the installed "
                "ProteoWizard Docker image; the Docker daemon is reachable "
                "but the ProteoWizard Docker image is missing."
            )
            blocking.append(issue)
            checks.append(
                {"name": "pwiz_image", "status": "blocked", "message": issue}
            )
        else:
            checks.append({"name": "msconvert", "status": "warning", "message": "Local msconvert is missing; Docker ProteoWizard fallback will be used."})

    disk_required = _required_disk_bytes(mode, policy)
    try:
        output_root.mkdir(parents=True, exist_ok=True)
        usage = disk_usage(output_root)
        free = int(getattr(usage, "free"))
        if free < disk_required:
            issue = f"Available disk space is below the {policy} policy minimum for {mode} mode."
            blocking.append(issue)
            checks.append({"name": "disk", "status": "blocked", "message": issue})
        else:
            checks.append({"name": "disk", "status": "ok", "message": f"Free disk space is sufficient for {mode} mode."})
    except OSError as exc:
        issue = f"Output directory is not writable: {exc}"
        blocking.append(issue)
        checks.append({"name": "output", "status": "blocked", "message": issue})

    for note in list(getattr(toolchain, "notes", []) or []):
        if note:
            warnings.append(str(note))

    status = "blocked" if blocking else "warning" if warnings else "ok"
    return {
        "status": status,
        "run_mode": mode,
        "resource_policy": policy,
        "repository": repository,
        "input_count": len(inputs),
        "checks": checks,
        "blocking_issues": blocking,
        "warnings": warnings,
        "required_disk_bytes": disk_required,
    }


def _required_disk_bytes(mode: str, policy: str) -> int:
    gib = 1024**3
    if mode == "parameters":
        return 128 * 1024**2
    matrix = {
        "prepare": {"fast": 5 * gib, "balanced": 10 * gib, "conservative": 20 * gib},
        "full": {"fast": 10 * gib, "balanced": 25 * gib, "conservative": 50 * gib},
    }
    return matrix.get(mode, matrix["full"])[policy]
