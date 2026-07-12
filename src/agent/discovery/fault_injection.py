from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, Protocol

from pydantic import Field

from agent.models import JsonModel


FaultOperation = Literal["search_projects", "get_project", "list_project_files"]
FaultOutcome = Literal["pass", "empty", "timeout", "rate_limit", "duplicate", "incomplete"]


class RepositoryFaultStep(JsonModel):
    operation: FaultOperation
    outcome: FaultOutcome
    match_text: str = ""
    note: str = ""


class RepositoryFaultEvent(JsonModel):
    operation: FaultOperation
    target: str
    outcome: FaultOutcome
    step_index: int | None = None


class PrideLikeClient(Protocol):
    def search_projects(self, keyword: str, page_size: int = 100) -> list[dict[str, Any]]: ...

    def get_project(self, accession: str) -> dict[str, Any]: ...

    def list_project_files(
        self,
        accession: str,
        keyword: str | None = None,
        page_size: int = 1000,
        max_files: int | None = None,
    ) -> list[dict[str, Any]]: ...


class FaultInjectingPrideClient:
    def __init__(
        self,
        delegate: PrideLikeClient,
        steps: Sequence[RepositoryFaultStep | Mapping[str, Any]],
    ) -> None:
        self.delegate = delegate
        self.steps = [RepositoryFaultStep.model_validate(step) for step in steps]
        self.events: list[RepositoryFaultEvent] = []
        self._consumed: set[int] = set()

    def search_projects(self, keyword: str, page_size: int = 100) -> list[dict[str, Any]]:
        outcome, index = self._next("search_projects", keyword)
        self._record("search_projects", keyword, outcome, index)
        if outcome == "empty":
            return []
        self._raise_if_error(outcome)
        rows = self.delegate.search_projects(keyword, page_size=page_size)
        if outcome == "duplicate" and rows:
            return [*rows, dict(rows[0])]
        if outcome == "incomplete":
            return [_strip_metadata(row) for row in rows]
        return rows

    def get_project(self, accession: str) -> dict[str, Any]:
        outcome, index = self._next("get_project", accession)
        self._record("get_project", accession, outcome, index)
        if outcome == "empty":
            return {}
        self._raise_if_error(outcome)
        row = self.delegate.get_project(accession)
        return _strip_metadata(row) if outcome == "incomplete" else row

    def list_project_files(
        self,
        accession: str,
        keyword: str | None = None,
        page_size: int = 1000,
        max_files: int | None = None,
    ) -> list[dict[str, Any]]:
        target = f"{accession}:{keyword or ''}"
        outcome, index = self._next("list_project_files", target)
        self._record("list_project_files", target, outcome, index)
        if outcome == "empty":
            return []
        self._raise_if_error(outcome)
        rows = self.delegate.list_project_files(
            accession,
            keyword=keyword,
            page_size=page_size,
            max_files=max_files,
        )
        if outcome == "duplicate" and rows:
            return [*rows, dict(rows[0])]
        if outcome == "incomplete":
            return [{"fileName": row.get("fileName") or row.get("name")} for row in rows]
        return rows

    def close(self) -> None:
        close = getattr(self.delegate, "close", None)
        if callable(close):
            close()

    def _next(self, operation: FaultOperation, target: str) -> tuple[FaultOutcome, int | None]:
        for index, step in enumerate(self.steps):
            if index in self._consumed or step.operation != operation:
                continue
            if step.match_text and step.match_text.casefold() not in target.casefold():
                continue
            self._consumed.add(index)
            return step.outcome, index
        return "pass", None

    def _record(
        self,
        operation: FaultOperation,
        target: str,
        outcome: FaultOutcome,
        index: int | None,
    ) -> None:
        self.events.append(
            RepositoryFaultEvent(
                operation=operation,
                target=target,
                outcome=outcome,
                step_index=index,
            )
        )

    @staticmethod
    def _raise_if_error(outcome: FaultOutcome) -> None:
        if outcome == "timeout":
            raise TimeoutError("injected repository timeout")
        if outcome == "rate_limit":
            raise RuntimeError("injected repository rate limit (429)")


def _strip_metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    keep = {"accession", "projectAccession", "title", "fileName", "name"}
    return {key: value for key, value in row.items() if key in keep}
