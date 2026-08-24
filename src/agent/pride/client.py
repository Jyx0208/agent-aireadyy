from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from hashlib import sha256
from inspect import Parameter, signature
from pathlib import Path

try:
    from agent.assets.download_contract import publish_part_file
except Exception:  # pragma: no cover
    publish_part_file = None  # type: ignore
from time import monotonic, sleep
from typing import Any, Literal

import httpx

from agent.repositories.metering import record_repository_request


class PrideInvalidResponseError(ValueError):
    """PRIDE returned HTTP success with a payload that violates its contract."""


PaginationMode = Literal["exhaustive", "budgeted"]


@dataclass(frozen=True, slots=True)
class PridePaginationState:
    repository: str
    operation: str
    mode: PaginationMode
    query_id: str
    query_text: str
    project_accession: str | None
    keyword: str | None
    page_size: int
    next_page: int | None
    next_page_offset: int
    pages_completed: int
    records_seen: int
    unique_records_seen: int
    last_page_count: int
    exhausted: bool
    truncated: bool
    stop_reason: str
    page_progress_exact: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_prefixed_dict(self, prefix: str) -> dict[str, Any]:
        """Expose one stable flat schema to legacy summary payloads."""
        return {f"{prefix}_{key}": value for key, value in self.to_dict().items()}

    @classmethod
    def unavailable(
        cls,
        *,
        operation: str,
        mode: PaginationMode,
        query_text: str = "",
        project_accession: str | None = None,
        keyword: str | None = None,
        page_size: int = 100,
        stop_reason: str,
    ) -> "PridePaginationState":
        """Build the same public schema for not-started and failed operations."""
        return _pagination_state(
            operation=operation,
            mode=mode,
            query_text=query_text,
            project_accession=project_accession,
            keyword=keyword,
            page_size=max(1, min(int(page_size or 100), 100)),
            next_page=None,
            next_page_offset=0,
            pages_completed=0,
            records_seen=0,
            unique_records_seen=0,
            last_page_count=0,
            exhausted=False,
            truncated=True,
            stop_reason=stop_reason,
            page_progress_exact=False,
        )


@dataclass(frozen=True, slots=True)
class PridePaginatedResult:
    records: list[dict[str, Any]]
    state: PridePaginationState


def _pagination_query_id(
    operation: str,
    *,
    query_text: str,
    project_accession: str | None,
    keyword: str | None,
) -> str:
    payload = (
        f"pride\0{operation}\0{query_text}\0"
        f"{project_accession or ''}\0{keyword or ''}"
    ).encode("utf-8")
    return sha256(payload).hexdigest()[:20]


def _pagination_state(
    *,
    operation: str,
    mode: PaginationMode,
    query_text: str,
    project_accession: str | None = None,
    keyword: str | None = None,
    page_size: int,
    next_page: int | None,
    next_page_offset: int,
    pages_completed: int,
    records_seen: int,
    unique_records_seen: int,
    last_page_count: int,
    exhausted: bool,
    truncated: bool,
    stop_reason: str,
    page_progress_exact: bool = True,
) -> PridePaginationState:
    return PridePaginationState(
        repository="pride",
        operation=operation,
        mode=mode,
        query_id=_pagination_query_id(
            operation,
            query_text=query_text,
            project_accession=project_accession,
            keyword=keyword,
        ),
        query_text=query_text,
        project_accession=project_accession,
        keyword=keyword,
        page_size=page_size,
        next_page=next_page,
        next_page_offset=next_page_offset,
        pages_completed=pages_completed,
        records_seen=records_seen,
        unique_records_seen=unique_records_seen,
        last_page_count=last_page_count,
        exhausted=exhausted,
        truncated=truncated,
        stop_reason=stop_reason,
        page_progress_exact=page_progress_exact,
    )


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


def _rejected_keyword_argument(
    message: str,
    candidates: Iterable[str],
) -> str | None:
    """Return the exact rejected keyword named by a Python ``TypeError``."""
    if "unexpected keyword argument" not in message:
        return None
    return next(
        (
            name
            for name in candidates
            if f"'{name}'" in message or f'"{name}"' in message
        ),
        None,
    )


def _call_legacy_page(
    method: Callable[..., Any],
    positional_args: tuple[Any, ...],
    keyword_args: dict[str, Any],
    *,
    page: int,
    operation: str,
    record_kind: str,
    identity_field: str,
) -> tuple[list[dict[str, Any]], bool]:
    """Call an injected client while discovering whether it honors ``page``."""
    kwargs = {**keyword_args, "page": page}
    cursor_supported = _has_cursor_capability(method, "page")
    while True:
        try:
            payload = method(*positional_args, **kwargs)
            return (
                _require_record_list(
                    list(payload or []),
                    operation=operation,
                    record_kind=record_kind,
                    identity_field=identity_field,
                ),
                cursor_supported,
            )
        except TypeError as exc:
            message = str(exc)
            rejected = _rejected_keyword_argument(message, kwargs)
            if rejected is None:
                raise
            kwargs.pop(rejected)
            if rejected == "page":
                cursor_supported = False


def _call_legacy_search_page(
    client: Any,
    keyword: str,
    *,
    page: int,
    page_size: int,
) -> tuple[list[dict[str, Any]], bool]:
    """Adapt both historical ``page`` and modern ``start_page`` clients."""
    cursor_supported = _has_cursor_capability(client.search_projects, "page")
    page_kwargs: dict[str, Any] = {"page_size": page_size, "page": page}
    while True:
        try:
            payload = client.search_projects(keyword, **page_kwargs)
            break
        except TypeError as page_error:
            rejected = _rejected_keyword_argument(str(page_error), page_kwargs)
            if rejected is None:
                raise
            page_kwargs.pop(rejected)
            if rejected == "page":
                cursor_supported = False
                break
    if "page" not in page_kwargs:
        start_kwargs: dict[str, Any] = {
            "page_size": page_size,
            "max_pages": 1,
            "max_results": page_size,
            "start_page": page,
        }
        cursor_supported = _has_cursor_capability(
            client.search_projects,
            "start_page",
        )
        while True:
            try:
                payload = client.search_projects(keyword, **start_kwargs)
                break
            except TypeError as start_error:
                rejected = _rejected_keyword_argument(
                    str(start_error),
                    start_kwargs,
                )
                if rejected is None:
                    raise
                start_kwargs.pop(rejected)
                if rejected == "start_page":
                    cursor_supported = False
    return (
        _require_record_list(
            list(payload or []),
            operation="search_projects",
            record_kind="project",
            identity_field="accession",
        ),
        cursor_supported,
    )


def _declares_keyword_argument(method: Callable[..., Any], name: str) -> bool:
    try:
        parameters = signature(method).parameters
    except (TypeError, ValueError):
        return False
    return name in parameters


def _has_cursor_capability(method: Callable[..., Any], name: str) -> bool:
    owner = getattr(method, "__self__", None)
    return _declares_keyword_argument(method, name) or bool(
        getattr(owner, "supports_page_cursor", False)
    )


def _has_page_progress_capability(method: Callable[..., Any]) -> bool:
    owner = getattr(method, "__self__", None)
    return _declares_keyword_argument(method, "on_page") or bool(
        getattr(owner, "supports_page_progress", False)
    )


def _paginate_legacy_search_budget(
    client: Any,
    keyword: str,
    *,
    page_size: int,
    max_pages: int | None,
    max_results: int | None,
    start_page: int,
    start_page_offset: int,
    on_page: Callable[[int, int, int], None] | None,
) -> PridePaginatedResult:
    """Preserve modern injected-client batching while adding shared state."""
    page_offset = max(0, int(start_page_offset))
    if page_offset >= page_size:
        raise ValueError("start_page_offset must be smaller than page_size")
    page_limit = (
        int(max_pages)
        if max_pages is not None
        else max(1, (page_offset + int(max_results or 1) + page_size - 1) // page_size)
    )
    page_capacity = page_size * page_limit
    requested_capacity = (
        page_offset + int(max_results)
        if max_results is not None
        else page_capacity
    )
    raw_limit = min(page_capacity, requested_capacity)
    last_page_count: int | None = None
    raw_cumulative = 0
    delivered_cumulative = 0
    callback_pages = 0
    native_page_progress = _has_page_progress_capability(client.search_projects)

    def capture_page(page: int, page_count: int, cumulative: int) -> None:
        nonlocal last_page_count, raw_cumulative, delivered_cumulative, callback_pages
        last_page_count = page_count
        raw_cumulative = cumulative
        callback_pages += 1
        visible_cumulative = max(0, cumulative - page_offset)
        if max_results is not None:
            visible_cumulative = min(visible_cumulative, int(max_results))
        delivered_page_count = max(0, visible_cumulative - delivered_cumulative)
        delivered_cumulative = visible_cumulative
        if on_page is not None and delivered_page_count:
            on_page(page, delivered_page_count, delivered_cumulative)

    raw_records = _require_record_list(
        search_projects_paginated(
            client,
            keyword,
            page_size=page_size,
            max_pages=page_limit,
            max_results=raw_limit,
            start_page=start_page,
            on_page=capture_page if native_page_progress else None,
        ),
        operation="search_projects",
        record_kind="project",
        identity_field="accession",
    )
    if len(raw_records) < page_offset:
        raise RuntimeError("saved page offset exceeds the returned repository page")
    visible_records = raw_records[page_offset:]
    records = (
        visible_records
        if max_results is None
        else visible_records[: int(max_results)]
    )
    callback_proves_terminal = (
        native_page_progress
        and last_page_count is not None
        and last_page_count < page_size
        and raw_cumulative == len(raw_records)
    )
    exhausted = len(raw_records) < raw_limit or callback_proves_terminal
    derived_pages_completed = max(
        1,
        (len(raw_records) + page_size - 1) // page_size,
    )
    pages_completed = callback_pages if native_page_progress else derived_pages_completed
    if not native_page_progress and on_page is not None:
        synthetic_cumulative = 0
        remaining = None if max_results is None else int(max_results)
        for page_index in range(derived_pages_completed):
            page_records = raw_records[
                page_index * page_size : (page_index + 1) * page_size
            ]
            if page_index == 0 and page_offset:
                page_records = page_records[page_offset:]
            if remaining is not None:
                page_records = page_records[: max(0, remaining)]
                remaining -= len(page_records)
            if page_records:
                synthetic_cumulative += len(page_records)
                on_page(
                    max(0, int(start_page)) + page_index + 1,
                    len(page_records),
                    synthetic_cumulative,
                )
    if exhausted:
        next_page = None
        next_page_offset = 0
        stop_reason = "empty_page" if not raw_records else "short_page"
    else:
        raw_consumed = page_offset + len(records)
        next_page = max(0, int(start_page)) + raw_consumed // page_size
        next_page_offset = raw_consumed % page_size
        result_bound_is_first = (
            max_results is not None and requested_capacity <= page_capacity
        )
        stop_reason = "max_results" if result_bound_is_first else "max_pages"
    return PridePaginatedResult(
        records=records,
        state=_pagination_state(
            operation="search_projects",
            mode="budgeted",
            query_text=keyword,
            keyword=keyword,
            page_size=page_size,
            next_page=next_page,
            next_page_offset=next_page_offset,
            pages_completed=pages_completed,
            records_seen=len(visible_records),
            unique_records_seen=len(
                {str(record.get("accession") or "") for record in visible_records}
            ),
            last_page_count=(
                last_page_count
                if native_page_progress and last_page_count is not None
                else len(raw_records) % page_size
                or (page_size if raw_records else 0)
            ),
            exhausted=exhausted,
            truncated=not exhausted,
            stop_reason=stop_reason,
            page_progress_exact=native_page_progress,
        ),
    )


def _paginate_pages(
    fetch_page: Callable[[int], tuple[list[dict[str, Any]], bool]],
    *,
    operation: str,
    mode: PaginationMode,
    query_text: str,
    project_accession: str | None,
    keyword: str | None,
    identity_field: str,
    page_size: int,
    start_page: int,
    start_page_offset: int,
    max_pages: int | None,
    max_records: int | None,
    limit_stop_reason: str,
    on_page: Callable[[int, int, int], None] | None,
) -> PridePaginatedResult:
    """Apply one resumable stop-state machine to repository page fetchers."""
    records: list[dict[str, Any]] = []
    unique_identities: set[str] = set()
    records_seen = 0
    page = max(0, int(start_page))
    page_offset = max(0, int(start_page_offset))
    pages_requested = 0
    previous_full_page_fingerprint: tuple[str, ...] | None = None
    repeated_full_pages = 0
    while True:
        batch, cursor_supported = fetch_page(page)
        pages_requested += 1
        raw_page_count = len(batch)
        if not cursor_supported and (page > 0 or page_offset > 0):
            raise RuntimeError(
                "repository client cannot resume exhaustive pagination; "
                "use a stateful client"
            )
        if page_offset > raw_page_count:
            raise RuntimeError("saved page offset exceeds the returned repository page")
        if raw_page_count == page_size:
            page_fingerprint = tuple(
                str(record.get(identity_field) or "") for record in batch
            )
            if page_fingerprint == previous_full_page_fingerprint:
                repeated_full_pages += 1
            else:
                previous_full_page_fingerprint = page_fingerprint
                repeated_full_pages = 1
            if repeated_full_pages >= 3:
                raise RuntimeError(
                    "repository pagination returned the same full page repeatedly; "
                    "the page cursor did not advance"
                )
        else:
            previous_full_page_fingerprint = None
            repeated_full_pages = 0
        visible_batch = batch[page_offset:]
        records_seen += len(visible_batch)
        unique_identities.update(
            str(record.get(identity_field) or "") for record in visible_batch
        )
        remaining = (
            None if max_records is None else max(0, int(max_records) - len(records))
        )
        delivered_batch = (
            visible_batch if remaining is None else visible_batch[:remaining]
        )
        records.extend(delivered_batch)
        raw_consumed = page_offset + len(delivered_batch)
        if on_page is not None and delivered_batch:
            on_page(page + 1, len(delivered_batch), len(records))
        record_limit_reached = max_records is not None and len(records) >= int(max_records)
        hidden_in_page = raw_consumed < raw_page_count
        terminal_page = raw_page_count < page_size
        if record_limit_reached and (hidden_in_page or not terminal_page):
            return PridePaginatedResult(
                records=records,
                state=_pagination_state(
                    operation=operation,
                    mode=mode,
                    query_text=query_text,
                    project_accession=project_accession,
                    keyword=keyword,
                    page_size=page_size,
                    next_page=page if hidden_in_page else page + 1,
                    next_page_offset=raw_consumed if hidden_in_page else 0,
                    pages_completed=pages_requested,
                    records_seen=records_seen,
                    unique_records_seen=len(unique_identities),
                    last_page_count=raw_page_count,
                    exhausted=False,
                    truncated=True,
                    stop_reason=limit_stop_reason,
                ),
            )
        if raw_page_count < page_size:
            return PridePaginatedResult(
                records=records,
                state=_pagination_state(
                    operation=operation,
                    mode=mode,
                    query_text=query_text,
                    project_accession=project_accession,
                    keyword=keyword,
                    page_size=page_size,
                    next_page=None,
                    next_page_offset=0,
                    pages_completed=pages_requested,
                    records_seen=records_seen,
                    unique_records_seen=len(unique_identities),
                    last_page_count=raw_page_count,
                    exhausted=True,
                    truncated=False,
                    stop_reason="empty_page" if raw_page_count == 0 else "short_page",
                ),
            )
        if max_pages is not None and pages_requested >= int(max_pages):
            return PridePaginatedResult(
                records=records,
                state=_pagination_state(
                    operation=operation,
                    mode=mode,
                    query_text=query_text,
                    project_accession=project_accession,
                    keyword=keyword,
                    page_size=page_size,
                    next_page=page + 1,
                    next_page_offset=0,
                    pages_completed=pages_requested,
                    records_seen=records_seen,
                    unique_records_seen=len(unique_identities),
                    last_page_count=raw_page_count,
                    exhausted=False,
                    truncated=True,
                    stop_reason="max_pages",
                ),
            )
        if not cursor_supported:
            raise RuntimeError(
                "repository client returned a full page but cannot request the next page"
            )
        page += 1
        page_offset = 0


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
            modern_projects = client.search_projects(keyword, **modern_kwargs)
            if "on_page" not in modern_kwargs and on_page is not None and modern_projects:
                on_page(
                    max(0, int(start_page)) + 1,
                    len(modern_projects),
                    len(modern_projects),
                )
            return modern_projects
        except TypeError as exc:
            message = str(exc)
            rejected = _rejected_keyword_argument(message, modern_kwargs)
            if rejected is None:
                raise
            if rejected == "on_page":
                modern_kwargs.pop("on_page")
                continue
            if (
                rejected == "start_page"
                and int(start_page) <= 0
            ):
                modern_kwargs.pop("start_page")
                continue
            if rejected not in ("start_page", "max_pages", "max_results"):
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
            if _rejected_keyword_argument(message, ("page",)) != "page":
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


def search_projects_paginated_with_state(
    client: Any,
    keyword: str,
    *,
    mode: PaginationMode,
    page_size: int,
    max_pages: int | None = None,
    max_results: int | None = None,
    start_page: int = 0,
    start_page_offset: int = 0,
    on_page: Callable[[int, int, int], None] | None = None,
) -> PridePaginatedResult:
    """Return pagination state for real and injected PRIDE clients."""
    stateful_search = getattr(client, "search_projects_with_state", None)
    if callable(stateful_search):
        return stateful_search(
            keyword,
            mode=mode,
            page_size=page_size,
            max_pages=max_pages,
            max_results=max_results,
            start_page=start_page,
            start_page_offset=start_page_offset,
            on_page=on_page,
        )

    effective_page_size = max(1, min(int(page_size or 100), 100))
    if mode == "exhaustive":
        if max_pages is not None or max_results is not None:
            raise ValueError("exhaustive pagination cannot have page or result limits")
    elif mode == "budgeted":
        if max_pages is None and max_results is None:
            raise ValueError("budgeted pagination requires max_pages or max_results")
    else:
        raise ValueError(f"unsupported PRIDE pagination mode: {mode}")
    if max_pages is not None and int(max_pages) < 1:
        raise ValueError("max_pages must be at least 1")
    if max_results is not None and int(max_results) < 1:
        raise ValueError("max_results must be at least 1")
    if mode == "budgeted":
        return _paginate_legacy_search_budget(
            client,
            keyword,
            page_size=effective_page_size,
            max_pages=max_pages,
            max_results=max_results,
            start_page=start_page,
            start_page_offset=start_page_offset,
            on_page=on_page,
        )
    return _paginate_pages(
        lambda page: _call_legacy_search_page(
            client,
            keyword,
            page=page,
            page_size=effective_page_size,
        ),
        operation="search_projects",
        mode="exhaustive",
        query_text=keyword,
        project_accession=None,
        keyword=keyword,
        identity_field="accession",
        page_size=effective_page_size,
        start_page=start_page,
        start_page_offset=start_page_offset,
        max_pages=None,
        max_records=None,
        limit_stop_reason="max_results",
        on_page=on_page,
    )


def list_project_files_paginated_with_state(
    client: Any,
    accession: str,
    *,
    mode: PaginationMode,
    keyword: str | None = None,
    page_size: int = 1000,
    max_files: int | None = None,
    max_pages: int | None = None,
    start_page: int = 0,
    start_page_offset: int = 0,
    on_page: Callable[[int, int, int], None] | None = None,
) -> PridePaginatedResult:
    """Return file-inventory completeness for real and injected clients."""
    stateful_list = getattr(client, "list_project_files_with_state", None)
    if callable(stateful_list):
        return stateful_list(
            accession,
            mode=mode,
            keyword=keyword,
            page_size=page_size,
            max_files=max_files,
            max_pages=max_pages,
            start_page=start_page,
            start_page_offset=start_page_offset,
            on_page=on_page,
        )

    effective_page_size = max(1, min(int(page_size or 100), 100))
    if mode == "exhaustive":
        if max_files is not None or max_pages is not None:
            raise ValueError("exhaustive pagination cannot have file or page limits")
    elif mode == "budgeted":
        if max_files is None and max_pages is None:
            raise ValueError("budgeted pagination requires max_files or max_pages")
    else:
        raise ValueError(f"unsupported PRIDE pagination mode: {mode}")
    if max_files is not None and int(max_files) < 1:
        raise ValueError("max_files must be at least 1")
    if max_pages is not None and int(max_pages) < 1:
        raise ValueError("max_pages must be at least 1")
    return _paginate_pages(
        lambda page: _call_legacy_page(
            client.list_project_files,
            (accession,),
            {
                "keyword": keyword,
                "page_size": effective_page_size,
                "max_files": None,
            },
            page=page,
            operation="list_project_files",
            record_kind="file",
            identity_field="fileName",
        ),
        operation="list_project_files",
        mode=mode,
        query_text=keyword or "",
        project_accession=accession,
        keyword=keyword,
        identity_field="fileName",
        page_size=effective_page_size,
        start_page=start_page,
        start_page_offset=start_page_offset,
        max_pages=max_pages,
        max_records=max_files,
        limit_stop_reason="max_files",
        on_page=on_page,
    )


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
        start_page_offset: int = 0,
        on_page: Callable[[int, int, int], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Search PRIDE projects, paging until exhausted or a safety bound is hit.

        The Archive v2 search endpoint is page-based. With no explicit limits,
        this method reads through the terminal short or empty page.
        """
        mode: PaginationMode = (
            "exhaustive"
            if max_pages is None and max_results is None
            else "budgeted"
        )
        return self.search_projects_with_state(
            keyword,
            mode=mode,
            page_size=page_size,
            max_pages=max_pages,
            max_results=max_results,
            start_page=start_page,
            start_page_offset=start_page_offset,
            on_page=on_page,
        ).records

    def search_projects_with_state(
        self,
        keyword: str,
        *,
        mode: PaginationMode,
        page_size: int = 100,
        max_pages: int | None = None,
        max_results: int | None = None,
        start_page: int = 0,
        start_page_offset: int = 0,
        on_page: Callable[[int, int, int], None] | None = None,
    ) -> PridePaginatedResult:
        """Search project pages and return a proof of why pagination stopped."""
        if mode == "exhaustive":
            if max_pages is not None or max_results is not None:
                raise ValueError("exhaustive pagination cannot have page or result limits")
        elif mode == "budgeted":
            if max_pages is None and max_results is None:
                raise ValueError("budgeted pagination requires max_pages or max_results")
        else:
            raise ValueError(f"unsupported PRIDE pagination mode: {mode}")
        if max_pages is not None and int(max_pages) < 1:
            raise ValueError("max_pages must be at least 1")
        if max_results is not None and int(max_results) < 1:
            raise ValueError("max_results must be at least 1")

        effective_page_size = max(1, min(int(page_size or 100), 100))

        def fetch_page(page: int) -> tuple[list[dict[str, Any]], bool]:
            response = self._get(
                "/search/projects",
                operation="search_projects",
                params={
                    "keyword": keyword,
                    "pageSize": effective_page_size,
                    "page": page,
                },
            )
            return (
                _require_record_list(
                    response.json(),
                    operation="search_projects",
                    record_kind="project",
                    identity_field="accession",
                ),
                True,
            )

        return _paginate_pages(
            fetch_page,
            operation="search_projects",
            mode=mode,
            query_text=keyword,
            project_accession=None,
            keyword=keyword,
            identity_field="accession",
            page_size=effective_page_size,
            start_page=start_page,
            start_page_offset=start_page_offset,
            max_pages=max_pages,
            max_records=max_results,
            limit_stop_reason="max_results",
            on_page=on_page,
        )

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
        mode: PaginationMode = "exhaustive" if max_files is None else "budgeted"
        return self.list_project_files_with_state(
            accession,
            mode=mode,
            keyword=keyword,
            page_size=page_size,
            max_files=max_files,
        ).records

    def list_project_files_with_state(
        self,
        accession: str,
        *,
        mode: PaginationMode,
        keyword: str | None = None,
        page_size: int = 1000,
        max_files: int | None = None,
        max_pages: int | None = None,
        start_page: int = 0,
        start_page_offset: int = 0,
        on_page: Callable[[int, int, int], None] | None = None,
    ) -> PridePaginatedResult:
        """List project files and retain a resumable completeness proof."""
        if mode == "exhaustive":
            if max_files is not None or max_pages is not None:
                raise ValueError("exhaustive pagination cannot have file or page limits")
        elif mode == "budgeted":
            if max_files is None and max_pages is None:
                raise ValueError("budgeted pagination requires max_files or max_pages")
        else:
            raise ValueError(f"unsupported PRIDE pagination mode: {mode}")
        if max_files is not None and int(max_files) < 1:
            raise ValueError("max_files must be at least 1")
        if max_pages is not None and int(max_pages) < 1:
            raise ValueError("max_pages must be at least 1")

        # PRIDE Archive v2 caps file-list pages at 100 records.
        effective_page_size = max(1, min(page_size, 100))
        params: dict[str, Any] = {"pageSize": effective_page_size}
        if keyword:
            params["keyword"] = keyword

        def fetch_page(page: int) -> tuple[list[dict[str, Any]], bool]:
            page_params = dict(params)
            page_params["page"] = page
            response = self._get(
                f"/projects/{accession}/files",
                operation="list_project_files",
                params=page_params,
            )
            return (
                _require_record_list(
                    response.json(),
                    operation="list_project_files",
                    record_kind="file",
                    identity_field="fileName",
                ),
                True,
            )

        return _paginate_pages(
            fetch_page,
            operation="list_project_files",
            mode=mode,
            query_text=keyword or "",
            project_accession=accession,
            keyword=keyword,
            identity_field="fileName",
            page_size=effective_page_size,
            start_page=start_page,
            start_page_offset=start_page_offset,
            max_pages=max_pages,
            max_records=max_files,
            limit_stop_reason="max_files",
            on_page=on_page,
        )

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
