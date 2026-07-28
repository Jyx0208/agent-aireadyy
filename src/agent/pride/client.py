from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

try:
    from agent.assets.download_contract import publish_part_file
except Exception:  # pragma: no cover
    publish_part_file = None  # type: ignore
from time import monotonic, sleep
from typing import Any

import httpx

from agent.repositories.metering import record_repository_request


class PrideInvalidResponseError(ValueError):
    """PRIDE returned HTTP success with a payload that violates its contract."""


def _invalid_response(
    operation: str,
    *,
    expected: str,
    payload: Any,
) -> PrideInvalidResponseError:
    preview = repr(payload)
    if len(preview) > 240:
        preview = f"{preview[:237]}..."
    return PrideInvalidResponseError(
        f"{operation} received an invalid PRIDE response: expected {expected}; "
        f"got {type(payload).__name__} {preview}"
    )


def _require_record(
    payload: Any,
    *,
    operation: str,
    record_kind: str,
    identity_field: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or not str(
        payload.get(identity_field) or ""
    ).strip():
        raise _invalid_response(
            operation,
            expected=f"a {record_kind} object with a non-empty {identity_field}",
            payload=payload,
        )
    return payload


def _require_record_list(
    payload: Any,
    *,
    operation: str,
    record_kind: str,
    identity_field: str,
) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise _invalid_response(
            operation,
            expected=f"a list of {record_kind} objects",
            payload=payload,
        )
    return [
        _require_record(
            record,
            operation=f"{operation} item {index}",
            record_kind=record_kind,
            identity_field=identity_field,
        )
        for index, record in enumerate(payload)
    ]


def search_projects_paginated(
    client: Any,
    keyword: str,
    *,
    page_size: int,
    max_pages: int,
    max_results: int,
    start_page: int = 0,
    on_page: Callable[[int, int, int], None] | None = None,
) -> list[dict[str, Any]]:
    """Use the paged client contract while preserving injected legacy clients.

    Prefer ``max_pages``/``max_results`` when the client supports them. Fall back
    to explicit ``page=`` loops for simple mocks, then single-call pageSize-only.
    """
    modern_kwargs: dict[str, Any] = {
        "page_size": page_size,
        "max_pages": max_pages,
        "max_results": max_results,
        "start_page": start_page,
        "on_page": on_page,
    }
    while True:
        try:
            projects = client.search_projects(keyword, **modern_kwargs)
            if "on_page" not in modern_kwargs and on_page is not None and projects:
                on_page(max(0, int(start_page)) + 1, len(projects), len(projects))
            return projects
        except TypeError as exc:
            message = str(exc)
            if "unexpected keyword argument" not in message:
                raise
            if "on_page" in message and "on_page" in modern_kwargs:
                modern_kwargs.pop("on_page")
                continue
            if (
                "start_page" in message
                and "start_page" in modern_kwargs
                and int(start_page) <= 0
            ):
                modern_kwargs.pop("start_page")
                continue
            if not any(name in message for name in ("start_page", "max_pages", "max_results")):
                raise
            break

    # Legacy clients that accept page= but not max_pages/max_results.
    projects: list[dict[str, Any]] = []
    page_limit = max(1, int(max_pages))
    effective_page_size = max(1, int(page_size))
    first_page = max(0, int(start_page))
    for page in range(first_page, first_page + page_limit):
        try:
            batch = client.search_projects(keyword, page_size=effective_page_size, page=page)
        except TypeError as exc:
            message = str(exc)
            if "unexpected keyword argument" not in message or "page" not in message:
                raise
            if first_page > 0:
                raise RuntimeError(
                    "repository client does not support resumable page cursors"
                ) from exc
            batch = client.search_projects(keyword, page_size=effective_page_size)
            projects.extend(batch or [])
            if on_page is not None and batch:
                on_page(1, len(batch), len(projects))
            break
        if not batch:
            break
        projects.extend(batch)
        if on_page is not None:
            on_page(page + 1, len(batch), len(projects))
        if max_results is not None and len(projects) >= int(max_results):
            return projects[: max(0, int(max_results))]
        if len(batch) < effective_page_size:
            break
    if max_results is not None:
        return projects[: max(0, int(max_results))]
    return projects


class PrideClient:
    def __init__(
        self,
        base_url: str = "https://www.ebi.ac.uk/pride/ws/archive/v2",
        timeout: float = 60.0,
        read_timeout: float | None = None,
        retries: int = 3,
    ):
        self._retries = max(1, retries)
        self._client = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout, read=max(timeout, 120.0) if read_timeout is None else read_timeout),
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PrideClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _get(self, path: str, *, operation: str, params: dict[str, Any] | None = None) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(1, self._retries + 1):
            try:
                record_repository_request("pride", operation)
                response = self._client.get(path, params=params)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code not in {429, 500, 502, 503, 504}:
                    raise
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                last_error = exc
            if attempt < self._retries:
                sleep(min(2 ** (attempt - 1), 4))
        if last_error is not None:
            raise last_error
        raise RuntimeError("PRIDE request failed without a response.")

    def search_projects(
        self,
        keyword: str,
        page_size: int = 100,
        *,
        max_pages: int | None = None,
        max_results: int | None = None,
        start_page: int = 0,
        on_page: Callable[[int, int, int], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Search PRIDE projects, paging until exhausted or a safety bound is hit.

        The Archive v2 search endpoint is page-based. Passing only ``pageSize``
        returns the first page; callers that want broad coverage must page.
        """
        effective_page_size = max(1, min(int(page_size or 100), 100))
        page_limit = 1 if max_pages is None else max(1, int(max_pages))
        projects: list[dict[str, Any]] = []
        page = max(0, int(start_page))
        pages_requested = 0
        while True:
            if pages_requested >= page_limit:
                break
            response = self._get(
                "/search/projects",
                operation="search_projects",
                params={
                    "keyword": keyword,
                    "pageSize": effective_page_size,
                    "page": page,
                },
            )
            batch = _require_record_list(
                response.json(),
                operation="search_projects",
                record_kind="project",
                identity_field="accession",
            )
            if not batch:
                break
            projects.extend(batch)
            if on_page is not None:
                on_page(page + 1, len(batch), len(projects))
            if max_results is not None and len(projects) >= max_results:
                return projects[: max(0, int(max_results))]
            if len(batch) < effective_page_size:
                break
            page += 1
            pages_requested += 1
        return projects

    def get_project(self, accession: str) -> dict[str, Any]:
        response = self._get(f"/projects/{accession}", operation="get_project")
        return _require_record(
            response.json(),
            operation="get_project",
            record_kind="project",
            identity_field="accession",
        )

    def list_project_files(
        self,
        accession: str,
        keyword: str | None = None,
        page_size: int = 1000,
        max_files: int | None = None,
    ) -> list[dict[str, Any]]:
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
            response = self._get(
                f"/projects/{accession}/files",
                operation="list_project_files",
                params=page_params,
            )
            batch = _require_record_list(
                response.json(),
                operation="list_project_files",
                record_kind="file",
                identity_field="fileName",
            )
            if not batch:
                break
            files.extend(batch)
            if max_files is not None and len(files) >= max_files:
                return files[:max(0, max_files)]
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
        if url.startswith("ftp://"):
            return "https://" + url[len("ftp://") :]
        return url

    def download_text(self, url: str) -> str:
        url = self._normalize_download_url(url) or url
        return self._get(url, operation="download_text").text

    def download_binary(self, url: str) -> bytes:
        url = self._normalize_download_url(url) or url
        return self._get(url, operation="download_binary").content

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
                        handle.flush()
                        import os as _os
                        _os.fsync(handle.fileno())
                # Atomic publish: .part -> final (WP-D download contract).
                if publish_part_file is not None:
                    publish_part_file(
                        temp_path,
                        target_path,
                        expected_size_bytes=total or None,
                    )
                else:
                    import os as _os
                    _os.replace(temp_path, target_path)
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
