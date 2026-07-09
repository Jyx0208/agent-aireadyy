from __future__ import annotations

import os
import re
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath


def docker_host_mount_path(path: Path) -> PurePath:
    """Translate an in-container path to the host path seen by Docker daemon."""
    resolved = path.resolve()
    mappings = (
        ("AGENT_CONTAINER_RUNS_DIR", "AGENT_HOST_RUNS_DIR"),
        ("AGENT_CONTAINER_APP_DIR", "AGENT_HOST_APP_DIR"),
    )
    for container_key, host_key in mappings:
        container_root_raw = os.getenv(container_key)
        host_root_raw = os.getenv(host_key)
        if not container_root_raw or not host_root_raw:
            continue
        container_root = Path(container_root_raw).expanduser().resolve()
        try:
            relative = resolved.relative_to(container_root)
        except ValueError:
            continue
        return _join_host_path(host_root_raw, relative)
    return resolved


def _join_host_path(host_root_raw: str, relative: Path) -> PurePath:
    """Join a Docker-daemon host root without corrupting Windows paths inside Linux containers."""
    host_root = host_root_raw.strip()
    relative_parts = relative.parts
    if re.match(r"^[A-Za-z]:[\\/]", host_root):
        return PureWindowsPath(host_root, *relative_parts)
    if host_root.startswith("/"):
        return PurePosixPath(host_root, *relative_parts)
    return Path(host_root).expanduser().resolve().joinpath(*relative_parts)
