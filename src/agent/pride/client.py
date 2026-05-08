from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from time import monotonic, sleep
from typing import Any

import httpx


class PrideClient:
    def __init__(self, base_url: str = "https://www.ebi.ac.uk/pride/ws/archive/v2", timeout: float = 60.0):
        self._client = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout, read=max(timeout, 120.0)),
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PrideClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def search_projects(self, keyword: str, page_size: int = 100) -> list[dict[str, Any]]:
        response = self._client.get("/search/projects", params={"keyword": keyword, "pageSize": page_size})
        response.raise_for_status()
        return response.json()

    def get_project(self, accession: str) -> dict[str, Any]:
        response = self._client.get(f"/projects/{accession}")
        response.raise_for_status()
        return response.json()

    def list_project_files(self, accession: str, keyword: str | None = None, page_size: int = 1000) -> list[dict[str, Any]]:
        # The PRIDE Archive v2 API caps file-list pages at 100 entries even when a
        # larger pageSize is requested, so we page explicitly until the last batch.
        effective_page_size = max(1, min(page_size, 100))
        params: dict[str, Any] = {"pageSize": effective_page_size}
        if keyword:
            params["keyword"] = keyword

        files: list[dict[str, Any]] = []
        page = 0
        while True:
            page_params = dict(params)
            page_params["page"] = page
            response = self._client.get(f"/projects/{accession}/files", params=page_params)
            response.raise_for_status()
            batch = response.json()
            if not batch:
                break
            files.extend(batch)
            if len(batch) < effective_page_size:
                break
            page += 1
        return files

    @staticmethod
    def _normalize_download_url(url: str | None) -> str | None:
        if not url:
            return None
        if url.startswith("ftp://ftp.pride.ebi.ac.uk/"):
            return url.replace("ftp://ftp.pride.ebi.ac.uk/", "https://ftp.pride.ebi.ac.uk/")
        return url

    def download_text(self, url: str) -> str:
        url = self._normalize_download_url(url) or url
        response = self._client.get(url)
        response.raise_for_status()
        return response.text

    def download_binary(self, url: str) -> bytes:
        url = self._normalize_download_url(url) or url
        response = self._client.get(url)
        response.raise_for_status()
        return response.content

    def download_to_path(self, url: str, target_path: str | Path, report=None, retries: int = 3) -> Path:
        url = self._normalize_download_url(url) or url
        target_path = Path(target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = target_path.with_name(f"{target_path.name}.part")

        if report is not None:
            report(f"正在下载：{url}")

        attempts = max(1, retries)
        last_error: Exception | None = None
        total = 0
        downloaded = 0
        started = monotonic()
        for attempt in range(1, attempts + 1):
            if temp_path.exists():
                temp_path.unlink()
            downloaded = 0
            started = monotonic()
            try:
                with self._client.stream("GET", url) as response:
                    response.raise_for_status()
                    total = int(response.headers.get("Content-Length", "0") or "0")
                    with temp_path.open("wb") as handle:
                        for chunk in response.iter_bytes():
                            if not chunk:
                                continue
                            handle.write(chunk)
                            downloaded += len(chunk)
                            if report is not None:
                                elapsed = max(monotonic() - started, 0.001)
                                speed_bps = downloaded / elapsed
                                eta_seconds = ((total - downloaded) / speed_bps) if total > 0 and speed_bps > 0 else None
                                report(
                                    {
                                        "kind": "download_progress",
                                        "label": target_path.name,
                                        "downloaded": downloaded,
                                        "total": total,
                                        "speed_bps": speed_bps,
                                        "eta_seconds": eta_seconds,
                                        "complete": False,
                                    }
                                )
                temp_path.replace(target_path)
                last_error = None
                break
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                last_error = exc
                if temp_path.exists():
                    temp_path.unlink()
                if attempt >= attempts:
                    break
                if report is not None:
                    report(f"下载失败（第 {attempt}/{attempts} 次），正在重试：{exc}")
                sleep(min(2 ** (attempt - 1), 8))

        if last_error is not None:
            raise last_error

        if report is not None:
            elapsed = max(monotonic() - started, 0.001)
            speed_bps = downloaded / elapsed if downloaded > 0 else 0.0
            report(
                {
                    "kind": "download_progress",
                    "label": target_path.name,
                    "downloaded": downloaded,
                    "total": total,
                    "speed_bps": speed_bps,
                    "eta_seconds": 0,
                    "complete": True,
                }
            )
            if total > 0:
                report(f"下载完成：{target_path}（{downloaded}/{total} 字节）")
            else:
                report(f"下载完成：{target_path}")
        return target_path

    @staticmethod
    def first_download_url(file_record: dict[str, Any]) -> str | None:
        locations: Iterable[dict[str, Any]] = file_record.get("publicFileLocations", []) or []
        normalized_candidates: list[str] = []
        fallback_candidates: list[str] = []
        for location in locations:
            value = location.get("value")
            if value:
                normalized = PrideClient._normalize_download_url(str(value))
                if normalized and normalized.startswith(("https://", "http://", "ftp://")):
                    normalized_candidates.append(normalized)
                elif normalized:
                    fallback_candidates.append(normalized)
        if normalized_candidates:
            return normalized_candidates[0]
        if fallback_candidates:
            return fallback_candidates[0]
        return None
