"""Safe filesystem lifecycle helpers for persisted web workflow runs."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def managed_child(root: Path, child: str | Path) -> Path:
    """Resolve one managed child while rejecting traversal and the root itself."""

    root = root.resolve(strict=False)
    raw = Path(child)
    candidate = raw.resolve(strict=False) if raw.is_absolute() else (root / raw).resolve(strict=False)
    if candidate == root or not _within(candidate, root):
        raise ValueError("Managed path is outside the allowed root.")
    return candidate


def path_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for candidate in path.rglob("*"):
        try:
            if candidate.is_file() and not candidate.is_symlink():
                total += candidate.stat().st_size
        except OSError:
            continue
    return total


def delete_managed_tree(root: Path, target: str | Path) -> dict[str, Any]:
    """Delete exactly one managed tree and return an auditable receipt."""

    candidate = managed_child(root, target)
    estimated = path_size_bytes(candidate)
    if not candidate.exists():
        return {
            "status": "completed",
            "estimated_bytes": 0,
            "released_bytes": 0,
            "removed_paths": [],
            "errors": [],
        }
    if candidate.is_symlink():
        raise ValueError("Managed run directory may not be a symbolic link.")
    shutil.rmtree(candidate)
    return {
        "status": "completed",
        "estimated_bytes": estimated,
        "released_bytes": estimated if not candidate.exists() else 0,
        "removed_paths": [str(candidate)],
        "errors": [],
    }


def clean_item_source_assets(item_dir: Path) -> dict[str, Any]:
    """Remove only reproducible task-local source assets from a batch item."""

    item_root = item_dir.resolve(strict=False)
    started = []
    errors: list[str] = []
    estimated = 0
    released = 0
    for relative in (Path("assets") / "downloads", Path("assets") / "prepared"):
        candidate = managed_child(item_root, relative)
        if not candidate.exists():
            continue
        if candidate.is_symlink():
            errors.append(f"Refused symbolic link: {relative.as_posix()}")
            continue
        size = path_size_bytes(candidate)
        estimated += size
        try:
            shutil.rmtree(candidate)
        except OSError as exc:
            errors.append(f"{relative.as_posix()}: {exc}")
            continue
        started.append(relative.as_posix())
        if not candidate.exists():
            released += size
    if errors and released:
        status = "partial"
    elif errors:
        status = "failed"
    else:
        status = "completed"
    return {
        "status": status,
        "estimated_bytes": estimated,
        "released_bytes": released,
        "removed_paths": started,
        "errors": errors,
    }
