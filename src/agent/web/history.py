from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

from agent.input.normalizer import safe_output_stem


_RUN_TIME_FIELDS = ("started_at", "created_at", "finished_at", "updated_at")
_UPDATE_TIME_FIELDS = ("updated_at", "finished_at", "started_at", "created_at")
_HISTORY_ID_FIELDS = ("history_id", "run_id", "output_dir", "result_id", "name")


def _basename(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/").rstrip("/")
    if not raw:
        return ""
    return raw.rsplit("/", 1)[-1]


def _first_text(record: Mapping[str, Any], fields: Iterable[str]) -> str:
    for field in fields:
        value = record.get(field)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def history_project_key(record: Mapping[str, Any]) -> str:
    for field in ("project_key", "output_dir", "result_id", "name"):
        value = _basename(record.get(field))
        if value:
            return safe_output_stem(value)
    input_value = _first_text(record, ("input_value",))
    if input_value:
        return safe_output_stem(input_value)
    task_id = _first_text(record, ("task_id",))
    return safe_output_stem(task_id) if task_id else ""


def history_record_key(record: Mapping[str, Any]) -> str:
    for field in _HISTORY_ID_FIELDS:
        value = _basename(record.get(field))
        if value:
            return safe_output_stem(value)
    task_id = _first_text(record, ("task_id",))
    if task_id:
        return safe_output_stem(task_id)
    project_key = _first_text(record, ("project_key",))
    if project_key:
        return safe_output_stem(project_key)
    input_value = _first_text(record, ("input_value",))
    return safe_output_stem(input_value) if input_value else ""


def history_time_value(record: Mapping[str, Any]) -> str:
    return _first_text(record, _RUN_TIME_FIELDS)


def _timestamp(record: Mapping[str, Any], fields: Iterable[str]) -> float:
    for field in fields:
        value = record.get(field)
        if not value:
            continue
        raw = str(value).strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(raw).timestamp()
        except ValueError:
            continue
    return 0.0


def history_timestamp(record: Mapping[str, Any]) -> float:
    return _timestamp(record, _RUN_TIME_FIELDS)


def _update_timestamp(record: Mapping[str, Any]) -> float:
    return _timestamp(record, _UPDATE_TIME_FIELDS)


def _task_ids(record: Mapping[str, Any]) -> list[str]:
    task_ids: list[str] = []
    raw_ids = record.get("task_ids")
    if isinstance(raw_ids, list):
        for value in raw_ids:
            text = str(value or "").strip()
            if text and text not in task_ids:
                task_ids.append(text)
    task_id = str(record.get("task_id") or "").strip()
    if task_id and task_id not in task_ids:
        task_ids.append(task_id)
    return task_ids


def _merge_task_ids(left: Mapping[str, Any], right: Mapping[str, Any]) -> list[str]:
    merged: list[str] = []
    for task_id in [*_task_ids(left), *_task_ids(right)]:
        if task_id not in merged:
            merged.append(task_id)
    return merged


def _has_task_identity(record: Mapping[str, Any]) -> bool:
    return bool(_task_ids(record))


def with_history_identity(record: Mapping[str, Any]) -> dict[str, Any]:
    item = dict(record)
    project_key = history_project_key(item)
    if project_key:
        item["project_key"] = project_key
    history_id = history_record_key(item)
    if history_id:
        item["history_id"] = history_id
    history_time = history_time_value(item)
    if history_time:
        item["history_time"] = history_time
    task_ids = _task_ids(item)
    if task_ids:
        item["task_ids"] = task_ids
    return item


def merge_project_history_records(records: Iterable[Mapping[str, Any]], *, limit: int | None = None) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: dict[str, int] = {}
    for index, record in enumerate(records):
        item = with_history_identity(record)
        history_id = str(item.get("history_id") or "")
        if not history_id:
            continue
        existing = merged.get(history_id)
        if existing is None:
            merged[history_id] = item
            order[history_id] = index
            continue
        task_ids = _merge_task_ids(existing, item)
        existing_ts = _update_timestamp(existing)
        item_ts = _update_timestamp(item)
        if item_ts > existing_ts or (item_ts == existing_ts and index >= order[history_id]):
            if _has_task_identity(existing) and not _has_task_identity(item):
                existing["task_ids"] = task_ids
                continue
            item["task_ids"] = task_ids
            merged[history_id] = item
            order[history_id] = index
        else:
            existing["task_ids"] = task_ids

    items = list(merged.values())
    items.sort(key=lambda item: (history_timestamp(item), str(item.get("history_time") or ""), str(item.get("project_key") or "")))
    if limit is not None and limit > 0:
        items = items[-limit:]
    return items
