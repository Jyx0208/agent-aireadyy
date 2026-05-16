from __future__ import annotations

from typing import Iterable

from agent.models import RepositoryName
from agent.pride.client import PrideClient
from agent.repositories.base import RepositoryAdapter
from agent.repositories.massive_adapter import MassiveAdapter
from agent.repositories.pride_adapter import PrideAdapter


class RepositoryRegistry:
    def __init__(
        self,
        adapters: Iterable[RepositoryAdapter] | None = None,
        pride_client: PrideClient | None = None,
    ) -> None:
        self.adapters: list[RepositoryAdapter] = list(adapters) if adapters is not None else [
            PrideAdapter(pride_client),
            MassiveAdapter(),
        ]
        seen: set[str] = set()
        for adapter in self.adapters:
            if adapter.name in seen:
                raise ValueError(f"Duplicate repository adapter: {adapter.name}")
            seen.add(adapter.name)

    def get(self, repository: str) -> RepositoryAdapter:
        for adapter in self.adapters:
            if adapter.name == repository:
                return adapter
        available = ", ".join(adapter.name for adapter in self.adapters)
        raise ValueError(f"Unknown repository '{repository}'. Available: {available}")

    def adapters_for(self, repository: str = "auto") -> list[RepositoryAdapter]:
        if repository == "auto":
            return list(self.adapters)
        return [self.get(repository)]

    def choose(self, repository: str, value: str) -> RepositoryAdapter:
        if repository != "auto":
            return self.get(repository)
        for adapter in self.adapters:
            if adapter.can_handle_accession(value):
                return adapter
        return self.get("pride")

    @staticmethod
    def supported_names() -> tuple[RepositoryName, ...]:
        return ("pride", "massive")
