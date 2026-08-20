from __future__ import annotations

import threading

from huey import SqliteHuey

from agent.operations.config import OperationsSettings
from agent.operations.runtime import get_operations_repository


_settings = OperationsSettings.from_environment()
_settings.ensure_directories()

huey = SqliteHuey(
    "pride-agent-operations",
    filename=str(_settings.queue_path),
    results=True,
    store_none=False,
    immediate=False,
    utc=True,
)


@huey.task(retries=2, retry_delay=30, context=True)
def execute_discovery_job(job_id: str, task=None) -> dict[str, str]:
    """Execute a persisted discovery job from a dedicated Huey worker.

    Importing the web adapter inside the task avoids creating a second copy of
    the scientific discovery implementation. The adapter itself reloads the
    persisted job when a worker process has no in-memory copy.
    """

    from agent.web import app as web_app

    stop_heartbeat = threading.Event()

    def heartbeat() -> None:
        repository = get_operations_repository()
        while not stop_heartbeat.is_set():
            try:
                snapshot = repository.get_job(job_id)
                if snapshot is None or snapshot["status"] in {
                    "completed",
                    "blocked",
                    "failed",
                    "cancelled",
                }:
                    return
                repository.heartbeat(job_id)
            except Exception:
                # The job execution remains authoritative; the next event also
                # refreshes heartbeat, and a transient heartbeat write must not
                # duplicate the queued task.
                pass
            stop_heartbeat.wait(15)

    heartbeat_thread = threading.Thread(
        target=heartbeat,
        name=f"operations-heartbeat-{job_id}",
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        web_app._run_discovery_job(job_id)
        return {
            "job_id": job_id,
            "task_id": str(getattr(task, "id", "") or ""),
        }
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=2)


def enqueue_discovery_job(job_id: str) -> str:
    result = execute_discovery_job(job_id)
    return str(getattr(result, "id", "") or "")


@huey.task(retries=2, retry_delay=30, context=True)
def execute_dataset_job(job_id: str, task=None) -> dict[str, str]:
    from agent.dataset_construction.operations import execute_dataset_construction_job

    stop_heartbeat = threading.Event()

    def heartbeat() -> None:
        repository = get_operations_repository()
        while not stop_heartbeat.is_set():
            try:
                snapshot = repository.get_job(job_id)
                if snapshot is None or snapshot["status"] in {
                    "completed", "blocked", "failed", "cancelled",
                }:
                    return
                repository.heartbeat(job_id)
            except Exception:
                pass
            stop_heartbeat.wait(15)

    heartbeat_thread = threading.Thread(
        target=heartbeat,
        name=f"dataset-heartbeat-{job_id}",
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        execute_dataset_construction_job(job_id)
        return {"job_id": job_id, "task_id": str(getattr(task, "id", "") or "")}
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=2)


def enqueue_operations_job(job_id: str) -> str:
    repository = get_operations_repository()
    snapshot = repository.get_job(job_id)
    if snapshot is None:
        raise KeyError(job_id)
    if snapshot["job_type"] == "discovery":
        return enqueue_discovery_job(job_id)
    if snapshot["job_type"] == "dataset_construction":
        result = execute_dataset_job(job_id)
        return str(getattr(result, "id", "") or "")
    raise ValueError(f"unsupported_job_type:{snapshot['job_type']}")


def revoke_discovery_job(task_id: str) -> bool:
    if not task_id:
        return False
    huey.revoke_by_id(task_id)
    return True
