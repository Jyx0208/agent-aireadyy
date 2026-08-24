from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

from agent.operations.queue import enqueue_operations_job, revoke_discovery_job
from agent.operations.runtime import get_operations_repository
from agent.dataset_construction.models import DatasetConstructionJobSpec
from agent.dataset_construction.operations import (
    submit_dataset_construction_job as submit_dataset_job,
)


router = APIRouter(prefix="/api/ops", tags=["operations"])


@router.post("/dataset-construction/jobs", status_code=202)
async def submit_dataset_construction_job(
    request: DatasetConstructionJobSpec,
) -> dict[str, Any]:
    try:
        return submit_dataset_job(request, enqueue=enqueue_operations_job)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        if str(exc) == "idempotency_key_conflict":
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise


def _job_or_404(job_id: str) -> dict[str, Any]:
    job = get_operations_repository().get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="operations_job_not_found")
    return job


@router.get("/health")
async def operations_health() -> dict[str, Any]:
    repository = get_operations_repository()
    summary = repository.history_summary()
    return {
        "ok": True,
        "database": "ready",
        "queue": "sqlite-huey",
        "history_total": summary["total"],
    }


@router.get("/jobs/{job_id}")
async def operations_job_snapshot(job_id: str) -> dict[str, Any]:
    return _job_or_404(job_id)


@router.get("/jobs/{job_id}/artifacts/{artifact_key}")
async def operations_job_artifact(job_id: str, artifact_key: str) -> FileResponse:
    job = _job_or_404(job_id)
    if job["job_type"] != "dataset_construction" or job["status"] != "completed":
        raise HTTPException(status_code=409, detail="dataset_release_not_completed")
    result = job.get("result") or {}
    allowed = {
        key: value
        for key, value in result.items()
        if key.endswith(("_json", "_parquet", "_sha256")) and isinstance(value, str)
    }
    raw_path = allowed.get(artifact_key)
    if not raw_path:
        raise HTTPException(status_code=404, detail="dataset_artifact_not_found")
    path = Path(raw_path).resolve()
    release_manifest = Path(str(result.get("release_manifest_json") or "")).resolve()
    release_root = release_manifest.parent
    if not path.is_relative_to(release_root) or not path.is_file():
        raise HTTPException(status_code=404, detail="dataset_artifact_not_found")
    return FileResponse(path, filename=path.name)


@router.get("/jobs/{job_id}/events/page")
async def operations_job_events_page(
    job_id: str,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict[str, Any]:
    job = _job_or_404(job_id)
    events = get_operations_repository().events_after(
        job_id,
        after=after,
        limit=limit,
    )
    return {
        "items": events,
        "after": after,
        "last_event_sequence": job["last_event_sequence"],
        "has_more": bool(events)
        and int(events[-1]["sequence"]) < int(job["last_event_sequence"]),
    }


@router.get("/jobs/{job_id}/events")
async def operations_job_events(
    request: Request,
    job_id: str,
    after: Annotated[int, Query(ge=0)] = 0,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> EventSourceResponse:
    _job_or_404(job_id)
    try:
        header_sequence = int(last_event_id or "0")
    except ValueError:
        header_sequence = 0
    cursor = max(after, header_sequence)

    async def stream():
        nonlocal cursor
        repository = get_operations_repository()
        yield {
            "event": "connected",
            "id": str(cursor),
            "data": json.dumps(
                {
                    "job_id": job_id,
                    "after": cursor,
                    "snapshot": repository.get_job(job_id),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }
        idle_ticks = 0
        while True:
            if await request.is_disconnected():
                return
            events = repository.events_after(job_id, after=cursor, limit=100)
            if events:
                idle_ticks = 0
                for event in events:
                    cursor = int(event["sequence"])
                    yield {
                        "event": "job-event",
                        "id": str(cursor),
                        "data": json.dumps(
                            event,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    }
                snapshot = repository.get_job(job_id)
                if snapshot is None:
                    return
                yield {
                    "event": "snapshot",
                    "id": str(cursor),
                    "data": json.dumps(
                        snapshot,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }
                if snapshot["status"] in {
                    "completed",
                    "failed",
                    "blocked",
                    "cancelled",
                } and cursor >= int(snapshot["last_event_sequence"]):
                    yield {
                        "event": "complete",
                        "id": str(cursor),
                        "data": json.dumps(
                            snapshot,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    }
                    return
            else:
                idle_ticks += 1
                if idle_ticks % 20 == 0:
                    snapshot = repository.get_job(job_id)
                    if snapshot is None:
                        return
                    yield {
                        "event": "heartbeat",
                        "id": str(cursor),
                        "data": json.dumps(
                            {
                                "job_id": job_id,
                                "status": snapshot["status"],
                                "phase": snapshot["phase"],
                                "heartbeat_at": snapshot["heartbeat_at"],
                                "updated_at": snapshot["updated_at"],
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    }
            await asyncio.sleep(0.5)

    return EventSourceResponse(
        stream(),
        ping=15,
        send_timeout=30,
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/jobs/{job_id}/terms")
async def operations_job_terms(job_id: str) -> dict[str, Any]:
    _job_or_404(job_id)
    items = get_operations_repository().list_terms(job_id)
    return {"items": items, "total": len(items)}


@router.get("/jobs/{job_id}/reviews")
async def operations_job_reviews(
    job_id: str,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    status: str = "",
    decision: str = "",
    query: str = "",
    sort: str = "position",
    direction: Literal["asc", "desc"] = "asc",
) -> dict[str, Any]:
    _job_or_404(job_id)
    return get_operations_repository().list_reviews(
        job_id,
        page=page,
        page_size=page_size,
        status=status,
        decision=decision,
        query=query,
        sort=sort,
        direction=direction,
    ).as_dict()


@router.get("/jobs/{job_id}/workers")
async def operations_job_workers(job_id: str) -> dict[str, Any]:
    job = _job_or_404(job_id)
    running = get_operations_repository().list_reviews(
        job_id,
        page=1,
        page_size=100,
        status="running",
        sort="position",
        direction="asc",
    )
    slots: dict[int, dict[str, Any]] = {}
    for review in running.items:
        slot = int(review.get("worker_slot") or 0)
        if slot <= 0:
            continue
        slots[slot] = {
            "slot": slot,
            "status": "busy",
            "project_accession": review["accession"],
            "step": review["current_step"],
            "started_at": review["started_at"],
        }
    configured = int(job["progress"]["worker_count"] or 0)
    return {
        "items": [
            slots.get(
                slot,
                {
                    "slot": slot,
                    "status": "idle",
                    "project_accession": None,
                    "step": "waiting",
                    "started_at": None,
                },
            )
            for slot in range(1, configured + 1)
        ],
        "total": configured,
        "busy": len(slots),
    }


@router.get("/jobs/{job_id}/files")
async def operations_job_files(
    job_id: str,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    eligible: bool | None = None,
    project_accession: str = "",
    query: str = "",
    sort: str = "project_accession",
    direction: Literal["asc", "desc"] = "asc",
    cursor: Annotated[int | None, Query(ge=0)] = None,
    review_status: str = "",
    decision: str = "",
    reason_status: str = "",
) -> dict[str, Any]:
    _job_or_404(job_id)
    return get_operations_repository().list_files(
        job_id,
        page=page,
        page_size=page_size,
        eligible=eligible,
        project_accession=project_accession,
        query=query,
        sort=sort,
        direction=direction,
        cursor=cursor,
        review_status=review_status,
        decision=decision,
        reason_status=reason_status,
    ).as_dict()


@router.get("/jobs/{job_id}/files/{file_id}")
async def operations_job_file(job_id: str, file_id: str) -> dict[str, Any]:
    _job_or_404(job_id)
    item = get_operations_repository().get_file(job_id, file_id)
    if item is None:
        raise HTTPException(status_code=404, detail="File not found")
    return item


@router.get("/jobs/{job_id}/batches")
async def operations_job_batches(job_id: str) -> dict[str, Any]:
    _job_or_404(job_id)
    items = get_operations_repository().list_batches(job_id)
    return {"items": items, "total": len(items)}


@router.post("/jobs/{job_id}/cancel")
async def operations_cancel_job(job_id: str) -> dict[str, Any]:
    job = _job_or_404(job_id)
    updated = get_operations_repository().request_cancel(job_id)
    if job["status"] == "queued":
        revoke_discovery_job(str(job.get("queue_task_id") or ""))
    return updated


@router.post("/jobs/{job_id}/resume")
async def operations_resume_job(job_id: str) -> dict[str, Any]:
    job = _job_or_404(job_id)
    if not job["resumable"] and job["status"] not in {
        "interrupted",
        "failed",
        "blocked",
        "cancelled",
    }:
        return job
    repository = get_operations_repository()
    updated = repository.transition_job(
        job_id,
        "queued",
        phase="queued",
        reason="用户请求从持久断点继续任务。",
        event_type="job_resume_requested",
        actor="user",
        resumable=False,
    )
    queue_task_id = enqueue_operations_job(job_id)
    repository.append_event(
        job_id,
        event_type="job_enqueued",
        actor="queue",
        phase="queued",
        message="恢复任务已进入持久队列。",
        payload={"queue_task_id": queue_task_id},
    )
    return updated


@router.get("/history")
async def operations_history(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    status_group: str = "",
    kind: str = "",
    query: str = "",
    archived: bool = False,
    trash: bool = False,
    sort: str = "updated_at",
    direction: Literal["asc", "desc"] = "desc",
) -> dict[str, Any]:
    repository = get_operations_repository()
    result = repository.list_history(
        page=page,
        page_size=page_size,
        status_group_filter=status_group,
        kind=kind,
        query=query,
        archived=archived,
        trash=trash,
        sort=sort,
        direction=direction,
    ).as_dict()
    result["summary"] = repository.history_summary()
    return result


@router.post("/history/{history_id}/archive")
async def operations_archive_history(
    history_id: str,
    archived: bool = True,
) -> dict[str, Any]:
    try:
        return get_operations_repository().archive_history(history_id, archived)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="history_entry_not_found") from exc


@router.post("/history/{history_id}/deleted")
async def operations_mark_history_deleted(
    history_id: str,
    released_bytes: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    try:
        return get_operations_repository().mark_history_deleted(
            history_id,
            released_bytes=released_bytes,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="history_entry_not_found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
