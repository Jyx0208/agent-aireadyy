from __future__ import annotations

import os
from pathlib import Path


def docker_host_mount_path(path: Path) -> Path:
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
        return Path(host_root_raw).expanduser().resolve() / relative
    return resolved
