from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from agent.models import ToolchainReport


def detect_toolchain(
    fragpipe_root: str | Path | None = None,
    msdt_converter_root: str | Path | None = None,
) -> ToolchainReport:
    docker_cli = shutil.which("docker")
    git_cli = shutil.which("git")
    java_cli = shutil.which("java")
    msconvert_cli = shutil.which("msconvert")

    docker_daemon_available = False
    docker_client_version = None
    docker_server_version = None
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
        git_available=bool(git_cli),
        java_available=bool(java_cli),
        msconvert_available=bool(msconvert_cli),
        fragpipe_root=str(Path(fragpipe_root)) if fragpipe_root else None,
        msdt_converter_root=str(Path(msdt_converter_root)) if msdt_converter_root else None,
        notes=notes,
    )
