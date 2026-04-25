from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Callable

from agent.models import FileAsset
from agent.utils import emit


def _default_cache_root() -> Path:
    configured = os.environ.get("AGENT_PRIDE_CACHE_DIR")
    if configured:
        return Path(configured)
    return Path.cwd() / ".agent_cache" / "pride"


def _safe_cache_segment(value: str) -> str:
    cleaned = "".join(character if character.isalnum() or character in {"-", "_", "."} else "_" for character in value)
    return cleaned.strip("._") or "unknown"


def _cache_path_for(asset: FileAsset) -> Path | None:
    if not asset.project_accession or not asset.local_path:
        return None
    project = _safe_cache_segment(asset.project_accession)
    file_name = _safe_cache_segment(asset.local_path.name)
    return _default_cache_root() / project / file_name


def _has_non_empty_file(path: Path) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size > 0


def _matches_expected_size(path: Path, expected_size_bytes: int | None) -> bool:
    if expected_size_bytes in (None, 0):
        return True
    return path.exists() and path.is_file() and path.stat().st_size == expected_size_bytes


def _unlink_file(path: Path | None) -> None:
    if path is not None and path.exists() and path.is_file():
        path.unlink()


def invalidate_file_asset_cache(asset: FileAsset, report: Callable[[str], None] | None = None) -> None:
    if asset.local_path is not None and asset.local_path.exists():
        emit(report, f"删除疑似损坏的本地文件：{asset.local_path}")
        _unlink_file(asset.local_path)
    cache_path = _cache_path_for(asset)
    if cache_path is not None and cache_path.exists():
        emit(report, f"删除疑似损坏的 PRIDE 缓存：{cache_path}")
        _unlink_file(cache_path)


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except FileNotFoundError:
        return left.absolute() == right.absolute()


def _materialize_cached_file(cache_path: Path, local_path: Path, report: Callable[[str], None] | None = None) -> Path:
    if _same_path(cache_path, local_path):
        return cache_path
    local_path.parent.mkdir(parents=True, exist_ok=True)
    if local_path.exists():
        local_path.unlink()
    try:
        os.link(cache_path, local_path)
        emit(report, f"已硬链接缓存的 PRIDE 文件：{cache_path} -> {local_path}")
    except OSError:
        shutil.copy2(cache_path, local_path)
        emit(report, f"已复制缓存的 PRIDE 文件：{cache_path} -> {local_path}")
    return local_path


def _reuse_or_remove(path: Path, expected_size_bytes: int | None, label: str, report: Callable[[str], None] | None) -> bool:
    if not _has_non_empty_file(path):
        return False
    if _matches_expected_size(path, expected_size_bytes):
        emit(report, f"复用{label}：{path}")
        return True
    emit(
        report,
        f"{label}大小与 PRIDE 元数据不一致，将重新下载：{path} "
        f"({path.stat().st_size}/{expected_size_bytes} bytes)",
    )
    path.unlink()
    return False


def download_file_asset(client, asset: FileAsset, report: Callable[[str], None] | None = None) -> Path:
    if not asset.download_url:
        raise ValueError("Cannot download a file asset without a download URL.")
    if not asset.local_path:
        raise ValueError("Cannot download a file asset without a local target path.")

    if _reuse_or_remove(asset.local_path, asset.expected_size_bytes, "已下载的数据文件", report):
        return asset.local_path

    cache_path = _cache_path_for(asset)
    if cache_path and _reuse_or_remove(cache_path, asset.expected_size_bytes, "项目缓存中的 PRIDE 文件", report):
        return _materialize_cached_file(cache_path, asset.local_path, report=report)

    download_target = cache_path or asset.local_path
    download_target.parent.mkdir(parents=True, exist_ok=True)
    emit(report, f"正在下载数据文件 {asset.matched_project_file or asset.original_file_name} -> {download_target}")
    if hasattr(client, "download_to_path"):
        client.download_to_path(asset.download_url, download_target, report=report)
    else:
        payload = client.download_binary(asset.download_url)
        download_target.write_bytes(payload)
        emit(report, f"下载完成：{download_target}")

    if not _matches_expected_size(download_target, asset.expected_size_bytes):
        actual = download_target.stat().st_size if download_target.exists() else 0
        _unlink_file(download_target)
        raise IOError(
            f"Downloaded file size does not match PRIDE metadata: {download_target} "
            f"({actual}/{asset.expected_size_bytes} bytes)"
        )

    if cache_path:
        return _materialize_cached_file(cache_path, asset.local_path, report=report)
    return asset.local_path
