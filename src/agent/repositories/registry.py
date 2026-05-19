from __future__ import annotations

from typing import Iterable

from agent.models import ProjectResolution, RepositoryName
from agent.pride.client import PrideClient
from agent.repositories.base import RepositoryAdapter
from agent.repositories.iprox_adapter import IproxAdapter
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
            IproxAdapter(),
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

    def resolve_project(self, repository: str, raw_input: str) -> ProjectResolution:
        if repository != "auto":
            return self.get(repository).resolve_project(raw_input)

        results: list[ProjectResolution] = []
        errors: list[str] = []
        for adapter in self.adapters:
            try:
                resolution = adapter.resolve_project(raw_input)
            except Exception as exc:
                errors.append(f"{adapter.name}: {type(exc).__name__}: {exc}")
                continue
            if resolution.primary_project is not None:
                results.append(resolution)

        if not results:
            reason = "No project resolved by any repository adapter."
            if errors:
                reason += " Adapter errors: " + "; ".join(errors)
            return ProjectResolution.empty().model_copy(update={"resolution_reason": reason})

        ordered = sorted(results, key=self._resolution_rank, reverse=True)
        selected = ordered[0]
        selected_primary = selected.primary_project
        alternatives = self._combined_alternatives(ordered)
        candidate_summary = "; ".join(
            f"{item.primary_project.repository}:{item.primary_project.project_accession}={item.resolution_confidence:.2f}"
            for item in ordered
            if item.primary_project is not None
        )
        tied = len(ordered) > 1 and self._resolution_rank(ordered[0]) == self._resolution_rank(ordered[1])
        reason = (
            f"Auto selected {selected_primary.repository}:{selected_primary.project_accession} "
            f"by highest resolution confidence ({selected.resolution_confidence:.2f}). "
            f"Candidates: {candidate_summary}"
        )
        if selected.resolution_reason:
            reason += f"; selected adapter reason: {selected.resolution_reason}"
        if errors:
            reason += "; Adapter errors: " + "; ".join(errors)
        if tied:
            reason += "; multiple repository matches have the same confidence"
        return selected.model_copy(
            update={
                "alternative_projects": alternatives,
                "resolution_reason": reason,
                "needs_review": selected.needs_review or tied,
            }
        )

    @staticmethod
    def _resolution_rank(resolution: ProjectResolution) -> tuple[float, int, float, int]:
        primary = resolution.primary_project
        if primary is None:
            return (-1.0, -1, -1.0, 0)
        return (
            resolution.resolution_confidence,
            primary.match_score,
            primary.metadata_consistency,
            0 if resolution.needs_review else 1,
        )

    @staticmethod
    def _combined_alternatives(ordered: list[ProjectResolution]):
        alternatives = []
        seen: set[tuple[str, str]] = set()
        for index, resolution in enumerate(ordered):
            candidates = list(resolution.alternative_projects)
            if index > 0 and resolution.primary_project is not None:
                candidates.insert(0, resolution.primary_project)
            for candidate in candidates:
                key = (candidate.repository, candidate.project_accession)
                if key in seen:
                    continue
                seen.add(key)
                alternatives.append(candidate)
        return alternatives

    @staticmethod
    def supported_names() -> tuple[RepositoryName, ...]:
        return ("pride", "massive", "iprox")
