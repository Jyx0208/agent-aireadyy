from __future__ import annotations

from pathlib import Path
from typing import Callable

from agent.models import FileAsset
from agent.utils import emit


def download_file_asset(client, asset: FileAsset, report: Callable[[str], None] | None = None) -> Path:
    if not asset.download_url:
        raise ValueError("Cannot download a file asset without a download URL.")
    if not asset.local_path:
        raise ValueError("Cannot download a file asset without a local target path.")

    emit(report, f"Downloading asset {asset.matched_project_file or asset.original_file_name} -> {asset.local_path}")
    if hasattr(client, "download_to_path"):
        return client.download_to_path(asset.download_url, asset.local_path, report=report)

    asset.local_path.parent.mkdir(parents=True, exist_ok=True)
    payload = client.download_binary(asset.download_url)
    asset.local_path.write_bytes(payload)
    emit(report, f"Download complete: {asset.local_path}")
    return asset.local_path
