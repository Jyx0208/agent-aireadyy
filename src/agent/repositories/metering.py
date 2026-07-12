from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

RepositoryRequestCallback = Callable[[str, str], None]
_request_callback: ContextVar[RepositoryRequestCallback | None] = ContextVar(
    "repository_request_callback", default=None
)


@contextmanager
def meter_repository_requests(callback: RepositoryRequestCallback) -> Iterator[None]:
    token = _request_callback.set(callback)
    try:
        yield
    finally:
        _request_callback.reset(token)


def record_repository_request(repository: str, operation: str) -> None:
    callback = _request_callback.get()
    if callback is not None:
        callback(repository, operation)
