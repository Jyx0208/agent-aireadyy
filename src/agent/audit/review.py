from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from agent.models import ReviewItem, TaskStateSnapshot


def build_review_item(
    task_id: str,
    source_file: str,
    project_accession: str | None,
    stage: str,
    reasons: list[str],
) -> ReviewItem:
    return ReviewItem(
        task_id=task_id,
        source_file=source_file,
        project_accession=project_accession,
        stage=stage,
        reasons=reasons,
        created_at=datetime.now(UTC),
    )


def append_review_item(path: str | Path, item: ReviewItem) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    queue: list[dict] = []
    if path.exists():
        queue = json.loads(path.read_text(encoding="utf-8"))
    queue.append(item.model_dump(mode="json"))
    path.write_text(json.dumps(queue, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def build_task_state_snapshot(
    task_id: str,
    status: str,
    stage: str,
    source_file: str,
    project_accession: str | None = None,
    notes: list[str] | None = None,
) -> TaskStateSnapshot:
    return TaskStateSnapshot(
        task_id=task_id,
        status=status,  # type: ignore[arg-type]
        stage=stage,
        source_file=source_file,
        project_accession=project_accession,
        updated_at=datetime.now(UTC),
        notes=notes or [],
    )


def write_task_state(path: str | Path, snapshot: TaskStateSnapshot) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
    return path
