from __future__ import annotations

import threading

from agent.operations.repository import OperationsRepository


_repository: OperationsRepository | None = None
_repository_lock = threading.Lock()


def get_operations_repository() -> OperationsRepository:
    global _repository
    if _repository is not None:
        return _repository
    with _repository_lock:
        if _repository is None:
            _repository = OperationsRepository()
    return _repository


def close_operations_repository() -> None:
    global _repository
    with _repository_lock:
        if _repository is not None:
            _repository.close()
            _repository = None


def reset_operations_repository_for_tests() -> None:
    close_operations_repository()
