from __future__ import annotations

from pathlib import Path

from huey import SqliteHuey
from sqlalchemy import inspect
from sse_starlette.sse import EventSourceResponse

from agent.operations.config import OperationsSettings
from agent.operations.repository import OperationsRepository


def test_operations_dependency_smoke(tmp_path: Path) -> None:
    settings = OperationsSettings(
        database_path=tmp_path / "operations.sqlite",
        queue_path=tmp_path / "queue.sqlite",
        artifact_root=tmp_path / "artifacts",
        worker_count=4,
    )
    repository = OperationsRepository(settings)
    try:
        tables = set(inspect(repository.database.engine).get_table_names())
        assert {
            "alembic_version",
            "jobs",
            "job_terms",
            "project_reviews",
            "file_records",
            "job_events",
            "batches",
            "batch_files",
            "history_entries",
            "dataset_releases",
            "dataset_split_protocols",
            "dataset_audit_findings",
            "dataset_split_allocations",
        }.issubset(tables)
    finally:
        repository.close()

    huey = SqliteHuey(
        "operations-smoke",
        filename=str(settings.queue_path),
        immediate=True,
        results=True,
    )

    @huey.task()
    def identity(value: str) -> str:
        return value

    assert identity("durable").get(blocking=True) == "durable"

    async def one_event():
        yield {"event": "ready", "id": "1", "data": "{}"}

    response = EventSourceResponse(one_event())
    assert response.media_type == "text/event-stream"
