from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from agent.models import ToolchainReport


PWIZ_DOCKER_IMAGE = "chambm/pwiz-skyline-i-agree-to-the-vendor-licenses:latest"
MSDT_DOCKER_IMAGE = "guomics2017/msdt-converter:v1.3"


def _docker_image_available(image: str) -> bool:
    result = subprocess.run(
        ["docker", "image", "inspect", image],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def detect_toolchain(
    fragpipe_root: str | Path | None = None,
    msdt_converter_root: str | Path | None = None,
) -> ToolchainReport:
    docker_cli = shutil.which("docker")
    git_cli = shutil.which("git")
    java_cli = shutil.which("java")
    configured_msconvert = os.getenv(
        "AGENT_MSCONVERT_EXECUTABLE",
        "",
    ).strip()
    msconvert_cli = (
        configured_msconvert
        if configured_msconvert and Path(configured_msconvert).is_file()
        else shutil.which("msconvert")
    )

    docker_daemon_available = False
    docker_client_version = None
    docker_server_version = None
    docker_pwiz_image_available = False
    docker_msdt_image_available = False
    notes: list[str] = []

    if docker_cli:
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Client.Version}}|{{.Server.Version}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        raw = (result.stdout or "").strip()
        if "|" in raw:
            client, server = raw.split("|", 1)
            docker_client_version = client or None
            docker_server_version = server or None
            docker_daemon_available = bool(server)
        if not docker_daemon_available:
            notes.append("Docker CLI is installed but the Docker daemon is not reachable.")
        else:
            docker_pwiz_image_available = _docker_image_available(
                PWIZ_DOCKER_IMAGE
            )
            docker_msdt_image_available = _docker_image_available(
                MSDT_DOCKER_IMAGE
            )
            if not docker_pwiz_image_available:
                notes.append(
                    f"Required ProteoWizard Docker image is not installed: "
                    f"{PWIZ_DOCKER_IMAGE}"
                )
            if not docker_msdt_image_available:
                notes.append(
                    f"Required MSDT workflow Docker image is not installed: "
                    f"{MSDT_DOCKER_IMAGE}"
                )
    else:
        notes.append("Docker CLI is not installed.")

    if not java_cli:
        notes.append("Java runtime is not installed or not on PATH.")
    if not git_cli:
        notes.append("Git is not installed or not on PATH.")
    if not msconvert_cli:
        notes.append("msconvert is not installed or not on PATH.")

    return ToolchainReport(
        docker_cli_available=bool(docker_cli),
        docker_daemon_available=docker_daemon_available,
        docker_client_version=docker_client_version,
        docker_server_version=docker_server_version,
        docker_pwiz_image_available=docker_pwiz_image_available,
        docker_msdt_image_available=docker_msdt_image_available,
        git_available=bool(git_cli),
        java_available=bool(java_cli),
        msconvert_available=bool(msconvert_cli),
        fragpipe_root=str(Path(fragpipe_root)) if fragpipe_root else None,
        msdt_converter_root=str(Path(msdt_converter_root)) if msdt_converter_root else None,
        notes=notes,
    )
