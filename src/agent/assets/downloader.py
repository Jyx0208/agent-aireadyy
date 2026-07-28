from __future__ import annotations

import os
import shutil
import urllib.request
from pathlib import Path
from typing import Callable

from agent.assets.download_contract import (
    DownloadContractError,
    DownloadReceipt,
    part_path_for,
    publish_part_file,
    unlink_quiet,
    verify_existing_file,
    write_bytes_atomic,
)
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
    unlink_quiet(path)


def _asset_checksum(asset: FileAsset) -> str | None:
    value = getattr(asset, "checksum", None)
    text = str(value or "").strip()
    return text or None


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


def _reuse_or_remove(
    path: Path,
    expected_size_bytes: int | None,
    label: str,
    report: Callable[[str], None] | None,
    *,
    expected_checksum: str | None = None,
) -> bool:
    """Reuse only when size+checksum contract verifies; otherwise delete and redownload."""
    if not _has_non_empty_file(path):
        return False
    receipt = verify_existing_file(
        path,
        expected_size_bytes=expected_size_bytes,
        expected_checksum=expected_checksum,
    )
    if receipt.published:
        emit(report, f"复用{label}：{path}")
        return True
    emit(
        report,
        f"{label}未通过下载合同校验，将重新下载：{path} "
        f"({receipt.error_code or 'invalid'}; size={receipt.actual_size_bytes}/{expected_size_bytes})",
    )
    path.unlink()
    return False


def _download_without_client(
    download_url: str,
    target_path: Path,
    report: Callable[[str], None] | None = None,
    *,
    expected_size_bytes: int | None = None,
    expected_checksum: str | None = None,
) -> DownloadReceipt:
    """Download via urllib into .part then publish atomically."""
    part_path = part_path_for(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    unlink_quiet(part_path)
    if download_url.startswith("ftp://"):
        urllib.request.urlretrieve(download_url, part_path)
    else:
        with urllib.request.urlopen(download_url) as response, part_path.open("wb") as handle:
            shutil.copyfileobj(response, handle)
            handle.flush()
            os.fsync(handle.fileno())
    receipt = publish_part_file(
        part_path,
        target_path,
        expected_size_bytes=expected_size_bytes,
        expected_checksum=expected_checksum,
        source_urls=[download_url],
        report=report,
    )
    if not receipt.published:
        raise DownloadContractError(
            receipt.error_code or "publish_failed",
            receipt.error_message or f"download contract failed for {target_path}",
            receipt,
        )
    return receipt


def _candidate_download_urls(asset: FileAsset) -> list[str]:
    urls: list[str] = []
    for url in [asset.download_url, *list(asset.download_urls or [])]:
        text = str(url or "").strip()
        if text and text not in urls:
            urls.append(text)
    return urls


def _client_download_to_path(
    client,
    download_url: str,
    download_target: Path,
    report: Callable[[str], None] | None,
    *,
    expected_size_bytes: int | None,
    expected_checksum: str | None,
) -> DownloadReceipt:
    """Invoke client download, ensuring final path is published under the contract.

    Prefer clients that write final path via their own atomic helper. After any client
    write we re-verify; if the client left a `.part` only, publish it.
    """
    part_path = part_path_for(download_target)
    # Clear stale artifacts so a failed client cannot leave a corrupt final.
    if download_target.exists() and not _matches_expected_size(download_target, expected_size_bytes):
        # Leave good finals alone; only remove when we are about to overwrite.
        pass

    if hasattr(client, "download_to_path"):
        client.download_to_path(download_url, download_target, report=report)
    elif hasattr(client, "download_binary") and not download_url.startswith("ftp://"):
        payload = client.download_binary(download_url)
        receipt = write_bytes_atomic(
            download_target,
            payload,
            expected_size_bytes=expected_size_bytes if expected_size_bytes is not None else len(payload),
            expected_checksum=expected_checksum,
            source_urls=[download_url],
            report=report,
        )
        if not receipt.published:
            raise DownloadContractError(
                receipt.error_code or "publish_failed",
                receipt.error_message or f"download contract failed for {download_target}",
                receipt,
            )
        return receipt
    else:
        return _download_without_client(
            download_url,
            download_target,
            report=report,
            expected_size_bytes=expected_size_bytes,
            expected_checksum=expected_checksum,
        )

    # Client wrote something. Prefer verifying final; if only .part exists, publish it.
    if download_target.exists() and download_target.is_file() and download_target.stat().st_size > 0:
        receipt = verify_existing_file(
            download_target,
            expected_size_bytes=expected_size_bytes,
            expected_checksum=expected_checksum,
        )
        if not receipt.published:
            _unlink_file(download_target)
            unlink_quiet(part_path)
            raise DownloadContractError(
                receipt.error_code or "verify_failed",
                receipt.error_message or f"post-download verification failed for {download_target}",
                receipt,
            )
        receipt.source_urls = [download_url]
        receipt.reused = False
        return receipt

    if part_path.exists() and part_path.is_file():
        receipt = publish_part_file(
            part_path,
            download_target,
            expected_size_bytes=expected_size_bytes,
            expected_checksum=expected_checksum,
            source_urls=[download_url],
            report=report,
        )
        if not receipt.published:
            raise DownloadContractError(
                receipt.error_code or "publish_failed",
                receipt.error_message or f"download contract failed for {download_target}",
                receipt,
            )
        return receipt

    raise DownloadContractError(
        "download_missing_output",
        f"client download produced neither final nor part path for {download_target}",
    )


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

    expected_checksum = _asset_checksum(asset)

    if _reuse_or_remove(
        asset.local_path,
        asset.expected_size_bytes,
        "已下载的数据文件",
        report,
        expected_checksum=expected_checksum,
    ):
        return asset.local_path

    cache_path = _cache_path_for(asset)
    if cache_path and _reuse_or_remove(
        cache_path,
        asset.expected_size_bytes,
        "项目缓存中的数据文件",
        report,
        expected_checksum=expected_checksum,
    ):
        return _materialize_cached_file(cache_path, asset.local_path, report=report)

    download_target = cache_path or asset.local_path
    download_target.parent.mkdir(parents=True, exist_ok=True)
    emit(report, f"正在下载数据文件 {asset.matched_project_file or asset.original_file_name} -> {download_target}")
    last_error: Exception | None = None
    for index, download_url in enumerate(download_urls, start=1):
        try:
            if len(download_urls) > 1:
                emit(report, f"尝试下载源 {index}/{len(download_urls)}：{download_url}")
            _client_download_to_path(
                client,
                download_url,
                download_target,
                report,
                expected_size_bytes=asset.expected_size_bytes,
                expected_checksum=expected_checksum,
            )
            last_error = None
            break
        except Exception as exc:  # noqa: BLE001 - try alternate URLs
            last_error = exc
            _unlink_file(download_target)
            unlink_quiet(part_path_for(download_target))
            if index >= len(download_urls):
                raise
            emit(report, f"下载源失败，尝试下一个备选源：{exc}")

    if last_error is not None:
        raise last_error

    if not download_target.exists():
        raise IOError(f"下载未生成目标文件：{download_target}")

    if cache_path:
        return _materialize_cached_file(cache_path, asset.local_path, report=report)
    return asset.local_path
