from __future__ import annotations

import os
import shutil
import urllib.request
from pathlib import Path
from typing import Callable

from agent.models import FileAsset
from agent.utils import emit


def _default_cache_root(repository: str = "pride") -> Path:
    if repository == "pride":
        configured = os.environ.get("AGENT_PRIDE_CACHE_DIR")
        if configured:
            return Path(configured)
    configured = os.environ.get("AGENT_REPOSITORY_CACHE_DIR")
    if configured:
        return Path(configured) / repository
    return Path.cwd() / ".agent_cache" / repository


def _safe_cache_segment(value: str) -> str:
    cleaned = "".join(character if character.isalnum() or character in {"-", "_", "."} else "_" for character in value)
    return cleaned.strip("._") or "unknown"


def _cache_path_for(asset: FileAsset) -> Path | None:
    if not asset.project_accession or not asset.local_path:
        return None
    repository = _safe_cache_segment(getattr(asset, "repository", "pride") or "pride")
    project = _safe_cache_segment(asset.project_accession)
    file_name = _safe_cache_segment(asset.local_path.name)
    return _default_cache_root(repository) / project / file_name


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
        emit(report, f"删除疑似损坏的项目缓存：{cache_path}")
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
    repository_label = cache_path.parent.parent.name.upper()
    try:
        os.link(cache_path, local_path)
        emit(report, f"已硬链接缓存的 {repository_label} 文件：{cache_path} -> {local_path}")
    except OSError:
        shutil.copy2(cache_path, local_path)
        emit(report, f"已复制缓存的 {repository_label} 文件：{cache_path} -> {local_path}")
    return local_path


def _reuse_or_remove(path: Path, expected_size_bytes: int | None, label: str, report: Callable[[str], None] | None) -> bool:
    if not _has_non_empty_file(path):
        return False
    if _matches_expected_size(path, expected_size_bytes):
        emit(report, f"复用{label}：{path}")
        return True
    emit(
        report,
        f"{label}大小与数据库元数据不一致，将重新下载：{path} "
        f"({path.stat().st_size}/{expected_size_bytes} bytes)",
    )
    path.unlink()
    return False


def _download_without_client(download_url: str, target_path: Path, report: Callable[[str], None] | None = None) -> None:
    if download_url.startswith("ftp://"):
        urllib.request.urlretrieve(download_url, target_path)
        emit(report, f"下载完成：{target_path}")
        return
    with urllib.request.urlopen(download_url) as response, target_path.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    emit(report, f"下载完成：{target_path}")


def _candidate_download_urls(asset: FileAsset) -> list[str]:
    urls: list[str] = []
    for url in [asset.download_url, *list(asset.download_urls or [])]:
        text = str(url or "").strip()
        if text and text not in urls:
            urls.append(text)
    return urls


def download_file_asset(client, asset: FileAsset, report: Callable[[str], None] | None = None) -> Path:
    download_urls = _candidate_download_urls(asset)
    if not download_urls:
        raise ValueError("无法下载文件资产：缺少下载 URL。")
    if not asset.local_path:
        raise ValueError("无法下载文件资产：缺少本地目标路径。")
    if asset.transfer_method == "aspera":
        raise ValueError(
            "File requires Aspera transfer. Use the repository adapter to generate an ascp command "
            f"for {asset.matched_project_file or asset.original_file_name}."
        )

    if _reuse_or_remove(asset.local_path, asset.expected_size_bytes, "已下载的数据文件", report):
        return asset.local_path

    cache_path = _cache_path_for(asset)
    if cache_path and _reuse_or_remove(cache_path, asset.expected_size_bytes, "项目缓存中的数据文件", report):
        return _materialize_cached_file(cache_path, asset.local_path, report=report)

    download_target = cache_path or asset.local_path
    download_target.parent.mkdir(parents=True, exist_ok=True)
    emit(report, f"正在下载数据文件 {asset.matched_project_file or asset.original_file_name} -> {download_target}")
    for index, download_url in enumerate(download_urls, start=1):
        try:
            if len(download_urls) > 1:
                emit(report, f"尝试下载源 {index}/{len(download_urls)}：{download_url}")
            if hasattr(client, "download_to_path"):
                client.download_to_path(download_url, download_target, report=report)
            elif hasattr(client, "download_binary") and not download_url.startswith("ftp://"):
                payload = client.download_binary(download_url)
                download_target.write_bytes(payload)
                emit(report, f"下载完成：{download_target}")
            else:
                _download_without_client(download_url, download_target, report=report)
            break
        except Exception as exc:
            _unlink_file(download_target)
            if index >= len(download_urls):
                raise
            emit(report, f"下载源失败，尝试下一个备选源：{exc}")

    if not _matches_expected_size(download_target, asset.expected_size_bytes):
        actual = download_target.stat().st_size if download_target.exists() else 0
        _unlink_file(download_target)
        raise IOError(
            f"下载的文件大小与数据库元数据不匹配：{download_target} "
            f"({actual}/{asset.expected_size_bytes} bytes)"
        )

    if cache_path:
        return _materialize_cached_file(cache_path, asset.local_path, report=report)
    return asset.local_path
