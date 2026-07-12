from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.repositories.base import RepositoryAdapter
    from agent.repositories.registry import RepositoryRegistry

__all__ = ["RepositoryAdapter", "RepositoryRegistry"]


def __getattr__(name: str) -> Any:
    if name == "RepositoryAdapter":
        from agent.repositories.base import RepositoryAdapter

        return RepositoryAdapter
    if name == "RepositoryRegistry":
        from agent.repositories.registry import RepositoryRegistry

        return RepositoryRegistry
    raise AttributeError(name)
