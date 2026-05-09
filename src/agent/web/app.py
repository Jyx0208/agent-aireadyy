from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from agent.input.normalizer import safe_output_stem
from agent.progress import render_download_progress


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _start_result_cleanup_worker()
    yield


app = FastAPI(title="PRIDE AI-ready Agent", version="0.3.1", lifespan=lifespan)

_tasks: dict[str, dict[str, Any]] = {}
_tasks_lock = threading.Lock()
_runs_dir = Path("runs")
_runs_dir.mkdir(exist_ok=True)
_templates_dir = Path(__file__).parent / "templates"
_ACTIVE_STATUSES = {"queued", "running"}
_TERMINAL_STATUSES = {"completed", "failed", "blocked"}
_cleanup_thread_started = False
_PUBLIC_HISTORY_FILE = "task_history.json"
_HISTORY_INDEX_FILE = "project_history.json"
_DOWNLOAD_CACHE_DIR = ".download_cache"
_DOWNLOAD_ZIP_NAME = "results-compressed.zip"
_DOWNLOAD_RESULT_DIRS = {"ai_ready", "msdt", "rawspectrum", "logs"}
_DOWNLOAD_ROOT_SUFFIXES = {".json", ".txt", ".log", ".tsv", ".csv"}
_MAX_PERSISTED_LOGS = 2000

# 默认配置（不从 .env 加载，由用户在页面填写）
_DEFAULT_CONFIG = {
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-v4-flash",
    "timeout": "1200",
}
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\[(?:\d{1,3};?)*m")
_APP_TZ = ZoneInfo(os.getenv("TZ", "Asia/Shanghai"))


def _now() -> datetime:
    return datetime.now(_APP_TZ)


def _now_iso() -> str:
    return _now().isoformat()


def _now_time() -> str:
    return _now().strftime("%H:%M:%S")


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _clean_submitter(value: Any) -> str:
    submitter = _clean_text(value)
    submitter = re.sub(r"[\x00-\x1f\x7f]+", " ", submitter).strip()
    if not submitter:
        return "未填写"
    return submitter[:80]


def _strip_ansi(value: Any) -> str:
    return _ANSI_RE.sub("", str(value)).replace("\r", "")


def _redact_secrets(value: Any) -> str:
    text = _strip_ansi(value)
    text = re.sub(r"(?i)(api[_ -]?key\s*[:=]\s*)\S+", r"\1[redacted]", text)
    text = re.sub(r"sk-[A-Za-z0-9_\-]{6,}", "[redacted-api-key]", text)
    return text


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)
    return value


def _sanitize_log_entry(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    allowed = ("type", "ts", "level", "message", "key", "replace", "step", "status", "summary")
    sanitized: dict[str, Any] = {}
    for key in allowed:
        if key not in entry:
            continue
        value = entry[key]
        if key == "message":
            value = _redact_secrets(value).strip()
        sanitized[key] = _json_safe(value)
    if not sanitized:
        return None
    return sanitized


def _public_logs_from_task(task: dict[str, Any]) -> list[dict[str, Any]]:
    logs = list(task.get("logs") or [])
    public_logs: list[dict[str, Any]] = []
    for entry in logs[-_MAX_PERSISTED_LOGS:]:
        sanitized = _sanitize_log_entry(entry)
        if sanitized:
            public_logs.append(sanitized)
    return public_logs


def _parse_history_timestamp(value: Any) -> float | None:
    if not value:
        return None
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_APP_TZ)
    return dt.timestamp()


def _history_retention_start(history: dict[str, Any], fallback_mtime: float = 0.0) -> float:
    for key in ("finished_at", "started_at", "created_at", "updated_at"):
        parsed = _parse_history_timestamp(history.get(key))
        if parsed is not None:
            return parsed
    return fallback_mtime


def _positive_float(value: str, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _max_concurrent_tasks() -> int:
    raw = os.getenv("AGENT_MAX_CONCURRENT_TASKS", "1")
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def _result_retention_seconds() -> int:
    raw = os.getenv("AGENT_RESULT_RETENTION_SECONDS", "1800")
    try:
        parsed = int(float(raw))
    except (TypeError, ValueError):
        return 1800
    return max(1, parsed)


def _max_result_projects() -> int:
    raw = os.getenv("AGENT_MAX_RESULT_PROJECTS", "4")
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return 4
    return max(1, parsed)


def _zip_compress_level() -> int:
    raw = os.getenv("AGENT_ZIP_COMPRESS_LEVEL", "6")
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return 6
    return min(9, max(1, parsed))


def _format_bytes(size: int | float) -> str:
    size = float(size)
    if size >= 1024 * 1024 * 1024:
        return f"{size / 1024 / 1024 / 1024:.1f} GB"
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{int(size)} B"


def _pride_cache_dir() -> Path:
    configured = os.getenv("AGENT_PRIDE_CACHE_DIR")
    if configured:
        return Path(configured)
    return Path.cwd() / ".agent_cache" / "pride"


def _path_file_stats(path: Path, excluded_names: set[str] | None = None, excluded_dir_names: set[str] | None = None) -> tuple[int, int, float]:
    excluded_names = excluded_names or set()
    excluded_dir_names = excluded_dir_names or set()
    file_count = 0
    size_bytes = 0
    latest_mtime = 0.0
    try:
        latest_mtime = path.stat().st_mtime
    except OSError:
        return 0, 0, 0.0
    for file in path.rglob("*"):
        try:
            relative = file.relative_to(path)
        except ValueError:
            continue
        if any(part in excluded_dir_names for part in relative.parts[:-1]):
            continue
        if file.is_file() and file.name in excluded_names:
            continue
        try:
            stat = file.stat()
        except OSError:
            continue
        latest_mtime = max(latest_mtime, stat.st_mtime)
        if file.is_file():
            file_count += 1
            size_bytes += stat.st_size
    return file_count, size_bytes, latest_mtime


def _active_output_dirs_locked() -> set[Path]:
    active_dirs: set[Path] = set()
    for task in _tasks.values():
        if task.get("status") not in _ACTIVE_STATUSES:
            continue
        output_dir = task.get("output_dir")
        if not output_dir:
            continue
        active_dirs.add(Path(output_dir).resolve())
    return active_dirs


def _safe_run_dir(result_id: str) -> Path | None:
    if not result_id or safe_output_stem(result_id) != result_id:
        return None
    root = _runs_dir.resolve()
    candidate = (_runs_dir / result_id).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _read_public_history(run_dir: Path) -> dict[str, Any]:
    history_path = run_dir / _PUBLIC_HISTORY_FILE
    if not history_path.exists():
        return {}
    try:
        data = json.loads(history_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _public_task_record_locked(task_id: str, task: dict[str, Any], *, include_logs: bool = False) -> dict[str, Any]:
    output_dir_raw = task.get("output_dir")
    output_dir = Path(output_dir_raw) if output_dir_raw else None
    can_download = bool(
        task.get("status") == "completed"
        and output_dir
        and output_dir.exists()
        and _is_download_zip_ready(output_dir)
    )
    logs = _public_logs_from_task(task)
    updated_at = task.get("updated_at") or task.get("finished_at") or task.get("started_at") or task.get("created_at")
    record = {
        "task_id": task_id,
        "input_value": task.get("input_value", ""),
        "submitter": task.get("submitter", "未填写"),
        "status": task.get("status", "unknown"),
        "created_at": task.get("created_at"),
        "started_at": task.get("started_at"),
        "finished_at": task.get("finished_at"),
        "updated_at": updated_at,
        "step": task.get("step", 0),
        "total_steps": task.get("total_steps", 5),
        "queue_position": _queue_position_locked(task_id),
        "output_dir": Path(output_dir).name if output_dir else "",
        "log_count": len(logs),
        "blocking_issues": list(task.get("blocking_issues") or []),
        "review_summary": task.get("review_summary"),
        "fasta_preference": "project" if task.get("prefer_project_fasta") else "llm",
        "can_download": can_download,
    }
    if include_logs:
        record["logs"] = logs
    return record


def _history_index_path() -> Path:
    return _runs_dir / _HISTORY_INDEX_FILE


def _read_history_index() -> list[dict[str, Any]]:
    path = _history_index_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _upsert_history_index(record: dict[str, Any]) -> None:
    key = record.get("task_id") or record.get("output_dir")
    if not key:
        return
    records = _read_history_index()
    replaced = False
    for index, existing in enumerate(records):
        existing_key = existing.get("task_id") or existing.get("output_dir")
        if existing_key == key:
            records[index] = record
            replaced = True
            break
    if not replaced:
        records.append(record)
    try:
        _runs_dir.mkdir(parents=True, exist_ok=True)
        _history_index_path().write_text(json.dumps(records[-200:], indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return


def _write_task_history(task_id: str) -> None:
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task is None:
            return
        output_dir_raw = task.get("output_dir")
        if not output_dir_raw:
            return
        record = _public_task_record_locked(task_id, task, include_logs=True)
    output_dir = Path(output_dir_raw)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / _PUBLIC_HISTORY_FILE).write_text(
            json.dumps(record, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        _upsert_history_index(record)
    except OSError:
        return


def _archive_run_history(run_dir: Path, history: dict[str, Any] | None = None) -> None:
    record = dict(history or _read_public_history(run_dir))
    if not record:
        record = {
            "task_id": run_dir.name,
            "input_value": run_dir.name,
            "status": "completed",
            "output_dir": run_dir.name,
        }
    record.setdefault("output_dir", run_dir.name)
    record["can_download"] = False
    _upsert_history_index(record)


def _find_history_record(task_id: str) -> dict[str, Any] | None:
    for record in reversed(_read_history_index()):
        if str(record.get("task_id") or "") == task_id or str(record.get("output_dir") or "") == task_id:
            return dict(record)
    if not _runs_dir.exists():
        return None
    for run_dir in _runs_dir.iterdir():
        if not run_dir.is_dir():
            continue
        record = _read_public_history(run_dir)
        if not record:
            continue
        if str(record.get("task_id") or "") == task_id or str(record.get("output_dir") or run_dir.name) == task_id:
            return dict(record)
    return None


def _public_logs_from_history(record: dict[str, Any]) -> list[dict[str, Any]]:
    raw_logs = record.get("logs")
    if not isinstance(raw_logs, list):
        return []
    logs: list[dict[str, Any]] = []
    for entry in raw_logs[-_MAX_PERSISTED_LOGS:]:
        sanitized = _sanitize_log_entry(entry)
        if sanitized:
            logs.append(sanitized)
    return logs


def _task_detail_from_history(task_id: str, record: dict[str, Any]) -> dict[str, Any]:
    output_dir_name = str(record.get("output_dir") or "")
    output_dir = _runs_dir / output_dir_name if output_dir_name else None
    can_download = bool(
        record.get("status") == "completed"
        and output_dir
        and output_dir.exists()
        and _is_download_zip_ready(output_dir)
    )
    logs = _public_logs_from_history(record)
    return {
        "task_id": str(record.get("task_id") or task_id),
        "input_value": record.get("input_value", ""),
        "submitter": record.get("submitter", "未填写"),
        "status": record.get("status", "unknown"),
        "created_at": record.get("created_at"),
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
        "updated_at": record.get("updated_at") or record.get("finished_at") or record.get("started_at") or record.get("created_at"),
        "step": record.get("step", 5 if record.get("status") == "completed" else 0),
        "total_steps": record.get("total_steps", 5),
        "log_count": len(logs),
        "logs": logs,
        "blocking_issues": list(record.get("blocking_issues") or []),
        "review_summary": record.get("review_summary"),
        "fasta_preference": record.get("fasta_preference", "llm"),
        "can_download": can_download,
        "archived": True,
        "queue_position": 0,
        "queue_length": 0,
        "queued_tasks": 0,
    }


def _list_public_results() -> list[dict[str, Any]]:
    if not _runs_dir.exists():
        return []
    retention = _result_retention_seconds()
    now = time.time()
    results: list[dict[str, Any]] = []
    with _tasks_lock:
        active_dirs = _active_output_dirs_locked()
    for run_dir in _runs_dir.iterdir():
        if not run_dir.is_dir():
            continue
        if run_dir.resolve() in active_dirs:
            continue
        file_count, size_bytes, latest_mtime = _path_file_stats(
            run_dir,
            excluded_names={_PUBLIC_HISTORY_FILE, _HISTORY_INDEX_FILE},
            excluded_dir_names={_DOWNLOAD_CACHE_DIR},
        )
        if file_count == 0 or latest_mtime <= 0:
            continue
        history = _read_public_history(run_dir)
        status = history.get("status", "completed")
        retention_start = _history_retention_start(history, latest_mtime)
        updated_at_ts = max(latest_mtime, retention_start)
        expires_at_ts = retention_start + retention
        results.append(
            {
                "result_id": run_dir.name,
                "task_id": history.get("task_id", ""),
                "name": run_dir.name,
                "input_value": history.get("input_value", run_dir.name),
                "submitter": history.get("submitter", "未填写"),
                "status": status,
                "path": str(run_dir),
                "file_count": file_count,
                "size_bytes": size_bytes,
                "updated_at": datetime.fromtimestamp(updated_at_ts, _APP_TZ).isoformat(),
                "expires_at": datetime.fromtimestamp(expires_at_ts, _APP_TZ).isoformat(),
                "expires_in_seconds": max(0, int(expires_at_ts - now)),
                "can_download": status == "completed" and _is_download_zip_ready(run_dir),
            }
        )
    results.sort(key=lambda item: item["updated_at"], reverse=True)
    return results


def _list_project_history_records() -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for record in _read_history_index():
        key = str(record.get("task_id") or record.get("output_dir") or "")
        if key:
            item = dict(record)
            item.pop("logs", None)
            output_dir_name = item.get("output_dir")
            output_dir = _runs_dir / str(output_dir_name) if output_dir_name else None
            file_count = 0
            size_bytes = 0
            if output_dir and output_dir.exists():
                file_count, size_bytes, latest_mtime = _path_file_stats(
                    output_dir,
                    excluded_names={_PUBLIC_HISTORY_FILE, _HISTORY_INDEX_FILE},
                    excluded_dir_names={_DOWNLOAD_CACHE_DIR},
                )
                item["updated_at"] = datetime.fromtimestamp(latest_mtime, _APP_TZ).isoformat() if latest_mtime else item.get("updated_at")
            item["file_count"] = file_count
            item["size_bytes"] = size_bytes
            item["can_download"] = bool(item.get("status") == "completed" and output_dir and output_dir.exists() and _is_download_zip_ready(output_dir))
            records[key] = item

    for result in _list_public_results():
        key = str(result.get("task_id") or result.get("result_id") or "")
        if key:
            records[key] = result

    with _tasks_lock:
        for task_id, task in _tasks.items():
            if task.get("status") in _ACTIVE_STATUSES:
                continue
            records[task_id] = _public_task_record_locked(task_id, task)

    items = list(records.values())
    items.sort(key=lambda item: str(item.get("updated_at") or item.get("finished_at") or item.get("created_at") or ""), reverse=True)
    return items


def _cleanup_expired_results() -> list[str]:
    if not _runs_dir.exists():
        return []
    with _tasks_lock:
        active_dirs = _active_output_dirs_locked()
        has_active_tasks = any(task.get("status") in _ACTIVE_STATUSES for task in _tasks.values())
    now = time.time()
    retention = _result_retention_seconds()
    candidates: list[tuple[float, Path]] = []
    removed: list[str] = []
    for run_dir in _runs_dir.iterdir():
        if not run_dir.is_dir():
            continue
        resolved = run_dir.resolve()
        if resolved in active_dirs:
            continue
        history = _read_public_history(run_dir)
        status = history.get("status", "completed")
        file_count, _size_bytes, latest_mtime = _path_file_stats(
            run_dir,
            excluded_names={_PUBLIC_HISTORY_FILE, _HISTORY_INDEX_FILE},
            excluded_dir_names={_DOWNLOAD_CACHE_DIR},
        )
        retention_start = _history_retention_start(history, latest_mtime)
        if retention_start <= 0:
            continue
        if now - retention_start >= retention:
            _archive_run_history(run_dir, history)
            shutil.rmtree(run_dir, ignore_errors=True)
            removed.append(run_dir.name)
            continue
        if status != "completed" or file_count == 0 or not _has_downloadable_result_file(run_dir):
            continue
        candidates.append((retention_start, run_dir))
    max_projects = _max_result_projects()
    candidates.sort(key=lambda item: item[0], reverse=True)
    to_remove = sorted(candidates[max_projects:], key=lambda item: item[0])
    for _mtime, run_dir in to_remove:
        _archive_run_history(run_dir)
        shutil.rmtree(run_dir, ignore_errors=True)
        removed.append(run_dir.name)
    if not has_active_tasks:
        removed.extend(_cleanup_pride_cache(now, retention))
    return removed


def _cleanup_pride_cache(now: float, retention: int) -> list[str]:
    cache_root = _pride_cache_dir()
    if not cache_root.exists() or not cache_root.is_dir():
        return []
    removed: list[str] = []
    for file in cache_root.rglob("*"):
        if not file.is_file():
            continue
        try:
            stat = file.stat()
        except OSError:
            continue
        if now - stat.st_mtime < retention:
            continue
        try:
            relative = file.relative_to(cache_root)
        except ValueError:
            relative = file.name
        try:
            file.unlink()
            removed.append(f"pride-cache/{relative}")
        except OSError:
            continue
    for directory in sorted((path for path in cache_root.rglob("*") if path.is_dir()), key=lambda path: len(path.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass
    return removed


def _cleanup_loop() -> None:
    while True:
        try:
            _cleanup_expired_results()
        except Exception:
            pass
        time.sleep(120)


def _is_download_result_file(output_dir: Path, file: Path) -> bool:
    if not file.is_file() or file.name in {_PUBLIC_HISTORY_FILE, _HISTORY_INDEX_FILE}:
        return False
    try:
        relative = file.relative_to(output_dir)
    except ValueError:
        return False
    if not relative.parts:
        return False
    first = relative.parts[0]
    if first == _DOWNLOAD_CACHE_DIR:
        return False
    if first in _DOWNLOAD_RESULT_DIRS:
        return True
    if len(relative.parts) == 1 and file.suffix.lower() in _DOWNLOAD_ROOT_SUFFIXES:
        return True
    return False


def _has_downloadable_result_file(output_dir: Path) -> bool:
    return any(_is_download_result_file(output_dir, path) for path in output_dir.rglob("*"))


def _download_zip_path(output_dir: Path) -> Path:
    return output_dir / _DOWNLOAD_CACHE_DIR / _DOWNLOAD_ZIP_NAME


def _download_result_files(output_dir: Path) -> list[Path]:
    return sorted(path for path in output_dir.rglob("*") if _is_download_result_file(output_dir, path))


def _download_source_mtime(output_dir: Path, files: list[Path] | None = None) -> float:
    latest_mtime = 0.0
    for path in files if files is not None else _download_result_files(output_dir):
        try:
            latest_mtime = max(latest_mtime, path.stat().st_mtime)
        except OSError:
            continue
    return latest_mtime


def _is_download_zip_ready(output_dir: Path) -> bool:
    zip_path = _download_zip_path(output_dir)
    try:
        source_mtime = _download_source_mtime(output_dir)
        return source_mtime > 0 and zip_path.exists() and zip_path.is_file() and zip_path.stat().st_size > 0 and zip_path.stat().st_mtime >= source_mtime
    except OSError:
        return False


def _zip_output_dir(output_dir: Path, report: Callable[[str], None] | None = None) -> Path:
    files = _download_result_files(output_dir)
    source_mtime = _download_source_mtime(output_dir, files)
    zip_path = _download_zip_path(output_dir)
    try:
        if files and zip_path.exists() and zip_path.stat().st_mtime >= source_mtime:
            if report:
                report(f"结果 ZIP 已存在，复用缓存：{zip_path.name} ({_format_bytes(zip_path.stat().st_size)})")
            return zip_path
    except OSError:
        pass
    if not files:
        raise FileNotFoundError("没有可打包的结果文件。")

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = zip_path.parent / f".{uuid.uuid4().hex}.zip.tmp"
    total_size = 0
    for file in files:
        try:
            total_size += file.stat().st_size
        except OSError:
            continue
    if report:
        report(
            f"开始打包下载 ZIP：{len(files)} 个结果文件，源文件合计 {_format_bytes(total_size)}，"
            f"压缩等级 {_zip_compress_level()}"
        )
    packed_size = 0
    last_report = monotonic()
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=_zip_compress_level()) as zf:
            for index, file in enumerate(files, start=1):
                zf.write(file, file.relative_to(output_dir))
                try:
                    packed_size += file.stat().st_size
                except OSError:
                    pass
                now = monotonic()
                if report and (index == 1 or index == len(files) or now - last_report >= 1.0):
                    report(f"ZIP 打包进度：{index}/{len(files)}，已处理 {_format_bytes(packed_size)} / {_format_bytes(total_size)}")
                    last_report = now
        temp_path.replace(zip_path)
        if report:
            report(f"结果 ZIP 打包完成：{zip_path.name} ({_format_bytes(zip_path.stat().st_size)})")
    finally:
        temp_path.unlink(missing_ok=True)
    return zip_path


def _active_task_count_locked() -> int:
    return sum(1 for task in _tasks.values() if task.get("status") in _ACTIVE_STATUSES)


def _running_task_count_locked() -> int:
    return sum(1 for task in _tasks.values() if task.get("status") == "running")


def _queued_task_ids_locked() -> list[str]:
    return [task_id for task_id, task in _tasks.items() if task.get("status") == "queued"]


def _queue_position_locked(task_id: str) -> int:
    queued_ids = _queued_task_ids_locked()
    try:
        return queued_ids.index(task_id) + 1
    except ValueError:
        return 0


def _queue_state_locked(task_id: str | None = None) -> dict[str, int]:
    queued_tasks = len(_queued_task_ids_locked())
    state = {
        "active_tasks": _active_task_count_locked(),
        "running_tasks": _running_task_count_locked(),
        "queued_tasks": queued_tasks,
        "queue_length": queued_tasks,
        "max_concurrent_tasks": _max_concurrent_tasks(),
    }
    if task_id is not None:
        state["queue_position"] = _queue_position_locked(task_id)
    return state


def _try_start_queued_task_locked(task_id: str) -> bool:
    task = _tasks.get(task_id)
    if task is None or task.get("status") != "queued":
        return False
    running = _running_task_count_locked()
    limit = _max_concurrent_tasks()
    if running >= limit:
        return False
    available_slots = limit - running
    if task_id not in _queued_task_ids_locked()[:available_slots]:
        return False
    task["status"] = "running"
    task["started_at"] = _now_iso()
    task["logs"].append(
        {
            "type": "log",
            "ts": _now_time(),
            "level": "info",
            "message": "任务已从队列启动。",
        }
    )
    return True


def _try_start_queued_task(task_id: str) -> bool:
    with _tasks_lock:
        return _try_start_queued_task_locked(task_id)


def _start_pipeline_thread(task_id: str) -> None:
    worker = threading.Thread(
        target=_run_pipeline,
        args=(task_id,),
        name=f"agent-task-{task_id}",
        daemon=True,
    )
    worker.start()


def _start_ready_queued_tasks() -> list[str]:
    started: list[str] = []
    with _tasks_lock:
        while _running_task_count_locked() < _max_concurrent_tasks():
            queued_ids = _queued_task_ids_locked()
            if not queued_ids:
                break
            task_id = queued_ids[0]
            if not _try_start_queued_task_locked(task_id):
                break
            started.append(task_id)
    for task_id in started:
        _write_task_history(task_id)
        _start_pipeline_thread(task_id)
    return started


def _build_llm_config(llm_config: dict[str, Any]) -> tuple[dict[str, str] | None, str | None]:
    api_key = _clean_text(llm_config.get("api_key"))
    if not api_key:
        return None, "请先填写本次任务使用的 API Key"

    base_url = _clean_text(llm_config.get("base_url")) or os.getenv("AGENT_LLM_BASE_URL") or _DEFAULT_CONFIG["base_url"]
    model = _clean_text(llm_config.get("model")) or os.getenv("AGENT_LLM_MODEL") or _DEFAULT_CONFIG["model"]
    timeout = _clean_text(llm_config.get("timeout")) or os.getenv("AGENT_LLM_TIMEOUT") or _DEFAULT_CONFIG["timeout"]
    try:
        if float(timeout) <= 0:
            return None, "大模型超时时间必须大于 0"
    except ValueError:
        return None, "大模型超时时间必须是数字"

    return {"api_key": api_key, "base_url": base_url.rstrip("/"), "model": model, "timeout": timeout}, None


def _task_llm_reasoner(config: dict[str, str]):
    from agent.llm.reasoner import OpenAICompatibleReasoner

    return OpenAICompatibleReasoner(
        api_key=config["api_key"],
        base_url=config["base_url"],
        model=config["model"],
        timeout=_positive_float(config["timeout"], 300.0),
    )


def _display_value(value: Any) -> str:
    if value is None or value == "":
        return "无"
    if isinstance(value, dict):
        return ", ".join(f"{key}={_display_value(val)}" for key, val in value.items())
    if isinstance(value, list | tuple | set):
        return ", ".join(_display_value(item) for item in value)
    return str(value)


def _path_name(value: Any) -> str:
    try:
        return Path(value).name
    except TypeError:
        return _display_value(value)


def _append_review_item(
    items: list[dict[str, Any]],
    label: str,
    value: Any,
    *,
    source: str = "",
    confidence: float | None = None,
    conflict: bool = False,
) -> None:
    if value is None or value == "" or value == [] or value == {}:
        return
    item: dict[str, Any] = {
        "label": label,
        "value": _display_value(value),
        "source": source,
        "conflict": bool(conflict),
    }
    if confidence is not None:
        item["confidence"] = confidence
    items.append(item)


def _append_attribute_item(items: list[dict[str, Any]], label: str, attribute: Any) -> None:
    _append_review_item(
        items,
        label,
        getattr(attribute, "value", None),
        source=str(getattr(attribute, "source", "")),
        confidence=getattr(attribute, "confidence", None),
        conflict=bool(getattr(attribute, "conflict_flag", False)),
    )


def _choice_values_from_metadata(result: Any, key: str) -> list[str]:
    context = getattr(result, "context", None)
    metadata = getattr(context, "metadata", {}) or {}
    raw = getattr(metadata.get(key), "value", None) if hasattr(metadata, "get") else None
    values = raw if isinstance(raw, list | tuple | set) else [raw] if raw else []
    unique: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in unique:
            unique.append(text)
    return unique


def _choice_values_from_attribute(attribute: Any) -> list[str]:
    raw = getattr(attribute, "value", None)
    if isinstance(raw, list | tuple | set):
        candidates = [str(item) for item in raw]
    else:
        candidates = re.split(r"\s*(?:;|\||,)\s*", str(raw or ""))
    unique: list[str] = []
    for value in candidates:
        text = value.strip()
        if text and text.lower() != "unknown" and text not in unique:
            unique.append(text)
    return unique


def _review_options(result: Any, issues: list[str]) -> list[dict[str, Any]]:
    attributes = result.attributes
    options: list[dict[str, Any]] = []
    species_attr = getattr(attributes, "species", None)
    if bool(getattr(species_attr, "conflict_flag", False)) or any("多个物种" in issue for issue in issues):
        values = _choice_values_from_metadata(result, "organisms") or _choice_values_from_attribute(species_attr)
        if len(values) > 1:
            options.append({"field": "species", "label": "选择物种", "values": values})
    instrument_attr = getattr(attributes, "instrument_name", None)
    if bool(getattr(instrument_attr, "conflict_flag", False)) or any("多个仪器" in issue for issue in issues):
        values = _choice_values_from_metadata(result, "instruments") or _choice_values_from_attribute(instrument_attr)
        if len(values) > 1:
            options.append({"field": "instrument_name", "label": "选择仪器", "values": values})
    return options


def _build_review_summary(result: Any) -> dict[str, Any]:
    attributes = result.attributes
    plan = result.plan
    items: list[dict[str, Any]] = []

    _append_review_item(items, "workflow", _path_name(getattr(plan, "fragpipe_workflow_path", None)), source="plan")
    fasta = _path_name(getattr(plan, "fasta_path", None))
    fasta_mode = getattr(plan, "fasta_selection_mode", "")
    if fasta_mode:
        fasta = f"{fasta} ({fasta_mode})"
    _append_review_item(items, "FASTA", fasta, source="plan")
    _append_review_item(items, "FASTA URL", getattr(plan, "fasta_download_url", None), source="plan")
    project_files = getattr(getattr(result, "context", None), "project_files", []) or []
    project_fastas = [
        str(file_record.get("fileName", ""))
        for file_record in project_files
        if str(file_record.get("fileName", "")).lower().endswith((".fasta", ".fa", ".faa", ".fasta.gz", ".fa.gz", ".faa.gz"))
    ]
    if project_fastas and fasta_mode != "reproduced":
        preview = ", ".join(project_fastas[:3])
        if len(project_fastas) > 3:
            preview += f", +{len(project_fastas) - 3}"
        _append_review_item(
            items,
            "项目 FASTA 可选",
            f"已默认使用大模型/物种推荐的 UniProt FASTA；PRIDE 项目中也检测到 {preview}。如需复现原项目 FASTA，勾选创建区的项目 FASTA 优先后重新提交。",
            source="pride",
        )
    _append_review_item(items, "raw_data_type", getattr(plan, "raw_data_type", None), source="plan")
    _append_review_item(items, "thread_num", getattr(plan, "thread_num", None), source="plan")

    _append_attribute_item(items, "采集模式", getattr(attributes, "acquisition_mode", None))
    _append_attribute_item(items, "物种", getattr(attributes, "species", None))
    _append_attribute_item(items, "仪器", getattr(attributes, "instrument_name", None))
    _append_attribute_item(items, "酶", getattr(attributes, "enzyme", None))
    _append_attribute_item(items, "固定修饰", getattr(attributes, "fixed_mods", None))
    _append_attribute_item(items, "可变修饰", getattr(attributes, "variable_mods", None))

    hints_attr = getattr(attributes, "search_parameter_hints", None)
    hints = getattr(hints_attr, "value", {})
    hint_source = str(getattr(hints_attr, "source", ""))
    hint_confidence = getattr(hints_attr, "confidence", None)
    if isinstance(hints, dict):
        for key in (
            "missed_cleavages",
            "precursor_tol",
            "fragment_tol",
            "min_peaks",
            "max_variable_mods",
            "data_family",
            "recommended_workflow_name",
            "recommended_fasta_name",
            "recommended_fasta_url",
            "recommended_fasta_source",
        ):
            if key in hints:
                _append_review_item(items, key, hints[key], source=hint_source, confidence=hint_confidence)

    issues = list(getattr(plan, "blocking_issues", []) or [])
    return {
        "updated_at": _now_time(),
        "needs_review": bool(getattr(plan, "needs_review", False)),
        "issues": issues,
        "review_options": _review_options(result, issues),
        "items": items,
    }


def _set_review_summary(task_id: str, result: Any) -> None:
    task = _tasks.get(task_id)
    if task is None:
        return
    summary = _build_review_summary(result)
    task["review_summary"] = summary
    _emit(task_id, "review", summary=summary)


def _set_task_terminal_status(task_id: str, status: str) -> None:
    task = _tasks.get(task_id)
    if task is None:
        return
    task["status"] = status
    task["finished_at"] = _now_iso()
    _write_task_history(task_id)


def _llm_check_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        if status_code in {401, 403}:
            return f"API Key 无效或没有权限（HTTP {status_code}）"
        if status_code == 404:
            return "Base URL 或模型名称不可用（HTTP 404）"
        if status_code == 429:
            return "大模型 API 额度不足或触发限流（HTTP 429）"
        detail = exc.response.text[:200].strip()
        suffix = f"：{detail}" if detail else ""
        return f"大模型 API 检查失败（HTTP {status_code}）{suffix}"
    if isinstance(exc, httpx.TimeoutException):
        return "大模型 API 检查超时，请确认 Base URL、模型和网络可用"
    if isinstance(exc, httpx.RequestError):
        return f"无法连接大模型 API：{exc}"
    return f"大模型 API 检查失败：{exc}"


async def _check_llm_api(config: dict[str, str]) -> tuple[bool, str]:
    check_timeout = max(5.0, min(_positive_float(config["timeout"], 15.0), 15.0))
    payload = {
        "model": config["model"],
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "stream": False,
    }
    timeout = httpx.Timeout(connect=5.0, read=check_timeout, write=5.0, pool=5.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{config['base_url']}/chat/completions",
                headers={"Authorization": f"Bearer {config['api_key']}"},
                json=payload,
            )
            response.raise_for_status()
    except Exception as exc:
        return False, _llm_check_error(exc)
    return True, "API Key 可用"


async def _run_llm_check(config: dict[str, str]) -> tuple[bool, str]:
    result = _check_llm_api(config)
    if inspect.isawaitable(result):
        return await result
    return result


# ── 页面 ──────────────────────────────────────────────────────────
def _start_result_cleanup_worker() -> None:
    global _cleanup_thread_started
    if _cleanup_thread_started:
        return
    _cleanup_thread_started = True
    threading.Thread(target=_cleanup_loop, name="result-cleanup", daemon=True).start()


@app.get("/", response_class=HTMLResponse)
async def index():
    return (_templates_dir / "index.html").read_text(encoding="utf-8")


# ── 健康检查 ──────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    with _tasks_lock:
        queue_state = _queue_state_locked()
    return {
        "status": "ok",
        "llm_configured": False,
        "per_task_api_keys": True,
        "result_retention_seconds": _result_retention_seconds(),
        "max_result_projects": _max_result_projects(),
        **queue_state,
    }


# ── 获取当前配置（脱敏） ──────────────────────────────────────────
@app.get("/api/config")
async def get_config():
    with _tasks_lock:
        queue_state = _queue_state_locked()
    return {
        "api_key_masked": "",
        "api_key_set": False,
        "per_task_api_keys": True,
        "base_url": os.getenv("AGENT_LLM_BASE_URL") or _DEFAULT_CONFIG["base_url"],
        "model": os.getenv("AGENT_LLM_MODEL") or _DEFAULT_CONFIG["model"],
        "timeout": os.getenv("AGENT_LLM_TIMEOUT") or _DEFAULT_CONFIG["timeout"],
        "result_retention_seconds": _result_retention_seconds(),
        "max_result_projects": _max_result_projects(),
        **queue_state,
    }


@app.post("/api/llm/check")
async def check_llm(body: dict[str, Any]):
    llm_config = body.get("llm_config", body)
    if not isinstance(llm_config, dict):
        llm_config = {}
    config, error = _build_llm_config(llm_config)
    if error or config is None:
        return {"ok": False, "error": error}
    ok, message = await _run_llm_check(config)
    if not ok:
        return {"ok": False, "error": message}
    return {"ok": True, "message": message, "base_url": config["base_url"], "model": config["model"]}


@app.get("/api/results")
async def list_public_results():
    removed = _cleanup_expired_results()
    return {
        "retention_seconds": _result_retention_seconds(),
        "max_result_projects": _max_result_projects(),
        "removed": removed,
        "results": _list_public_results(),
    }


@app.get("/api/history")
async def list_project_history():
    removed = _cleanup_expired_results()
    with _tasks_lock:
        active_tasks = [
            _public_task_record_locked(task_id, task)
            for task_id, task in _tasks.items()
            if task.get("status") in _ACTIVE_STATUSES
        ]
    active_tasks.sort(key=lambda item: str(item.get("created_at") or ""))
    return {
        "retention_seconds": _result_retention_seconds(),
        "max_result_projects": _max_result_projects(),
        "removed": removed,
        "active_tasks": active_tasks,
        "results": _list_project_history_records(),
    }


@app.get("/api/results/{result_id}/download")
async def download_public_result(result_id: str):
    output_dir = _safe_run_dir(result_id)
    if output_dir is None or not output_dir.exists():
        return {"error": "结果目录不存在。"}
    history = _read_public_history(output_dir)
    if history.get("status", "completed") != "completed":
        return {"error": "任务未完成，不能下载结果。"}
    if not _has_downloadable_result_file(output_dir):
        return {"error": "结果目录没有可下载文件。"}

    if not _is_download_zip_ready(output_dir):
        return {"error": "结果 ZIP 尚未打包完成，请等待任务日志提示后再下载。"}
    zip_path = _download_zip_path(output_dir)
    return FileResponse(
        path=str(zip_path),
        filename=f"{result_id}_results.zip",
        media_type="application/zip",
    )


# ── 创建任务 ──────────────────────────────────────────────────────
@app.post("/api/tasks")
async def create_task(body: dict[str, Any]):
    try:
        return await _create_task_inner(body)
    except Exception as exc:
        return {"error": f"创建任务失败：{exc}"}


async def _create_task_inner(body: dict[str, Any]):
    input_value = _clean_text(body.get("input_value"))
    if not input_value:
        return {"error": "请输入 PRIDE 文件名"}
    submitter = _clean_submitter(body.get("submitter"))
    fasta_preference = _clean_text(body.get("fasta_preference")).lower()
    prefer_project_fasta = fasta_preference == "project" or body.get("prefer_project_fasta") is True

    # 应用用户填写的 LLM 配置
    llm_config = body.get("llm_config", {})
    if not isinstance(llm_config, dict):
        llm_config = {}
    config, config_error = _build_llm_config(llm_config)
    if config_error or config is None:
        return {"error": config_error}

    ok, message = await _run_llm_check(config)
    if not ok:
        return {"error": message}

    task_id = uuid.uuid4().hex[:12]
    output_dir = _runs_dir / safe_output_stem(input_value)

    with _tasks_lock:
        _tasks[task_id] = {
            "task_id": task_id,
            "input_value": input_value,
            "submitter": submitter,
            "output_dir": str(output_dir),
            "status": "queued",
            "created_at": _now_iso(),
            "logs": deque(maxlen=5000),
            "step": 0,
            "total_steps": 5,
            "blocking_issues": [],
            "prefer_project_fasta": prefer_project_fasta,
            "llm_config": dict(config),
        }
        queue_state = _queue_state_locked(task_id)
        _tasks[task_id]["logs"].append(
            {
                "type": "log",
                "ts": _now_time(),
                "level": "info",
                "message": f"任务已进入队列，当前位置 {queue_state['queue_position']}/{queue_state['queue_length']}。",
            }
        )
    _write_task_history(task_id)
    _start_ready_queued_tasks()
    with _tasks_lock:
        status = _tasks[task_id]["status"]
        queue_state = _queue_state_locked(task_id)
    return {"task_id": task_id, "submitter": submitter, "output_dir": str(output_dir), "status": status, **queue_state}


# ── WebSocket 实时日志 ────────────────────────────────────────────
@app.websocket("/ws/tasks/{task_id}")
async def task_ws(websocket: WebSocket, task_id: str):
    await websocket.accept()

    if task_id not in _tasks:
        await websocket.send_json({"type": "error", "message": f"任务 {task_id} 不存在"})
        await websocket.close()
        return

    task = _tasks[task_id]

    for log in task["logs"]:
        await websocket.send_json(log)

    if task["status"] in _TERMINAL_STATUSES:
        await websocket.send_json({"type": "done", "status": task["status"]})
        await websocket.close()
        return

    sent = len(task["logs"])
    try:
        while task["status"] == "queued":
            with _tasks_lock:
                queue_message = {"type": "queue", "status": "queued", **_queue_state_locked(task_id)}
            await websocket.send_json(queue_message)
            while sent < len(task["logs"]):
                await websocket.send_json(task["logs"][sent])
                sent += 1
            await asyncio.sleep(1.0)
        while task["status"] == "running":
            await asyncio.sleep(0.3)
            while sent < len(task["logs"]):
                await websocket.send_json(task["logs"][sent])
                sent += 1
        while sent < len(task["logs"]):
            await websocket.send_json(task["logs"][sent])
            sent += 1
        await websocket.send_json({"type": "done", "status": task["status"]})
    except WebSocketDisconnect:
        pass


# ── 日志工具 ──────────────────────────────────────────────────────
def _emit(task_id: str, msg_type: str, data: Any = None, **kwargs):
    task = _tasks.get(task_id)
    if task is None:
        return
    if "message" in kwargs:
        kwargs["message"] = _strip_ansi(kwargs["message"]).strip()
    entry = {"type": msg_type, "ts": _now_time(), **kwargs}
    if data is not None:
        entry["data"] = data
    task["logs"].append(entry)


def _log(task_id: str, level: str, message: str, **kwargs):
    if not _strip_ansi(message).strip():
        return
    _emit(task_id, "log", message=message, level=level, **kwargs)


def _step(task_id: str, step: int, label: str):
    task = _tasks.get(task_id)
    if task:
        task["step"] = step
    _emit(task_id, "step", message=label, step=step)


# ── Web Reporter ──────────────────────────────────────────────────
class WebReporter:
    def __init__(self, task_id: str):
        self.task_id = task_id
        self._progress_last_emit: dict[str, float] = {}

    def __call__(self, message):
        if isinstance(message, dict):
            kind = message.get("kind", "")
            if kind == "download_progress":
                label = _clean_text(message.get("label")) or "download"
                complete = bool(message.get("complete"))
                now = monotonic()
                last_emit = self._progress_last_emit.get(label)
                if not complete and last_emit is not None and now - last_emit < 0.5:
                    return
                self._progress_last_emit[label] = now
                msg = render_download_progress(message, width=16)
                if complete:
                    msg = f"下载完成 {msg}"
                _log(self.task_id, "info", msg, key=f"download:{label}", replace=True)
            elif kind == "activity_start":
                _log(self.task_id, "info", message.get("label", "处理中..."))
            elif kind == "activity_stop":
                if message.get("message"):
                    _log(self.task_id, "info", message["message"])
            else:
                _log(self.task_id, "info", json.dumps(message, ensure_ascii=False))
        else:
            text = str(message)
            level = "info"
            if "LLM" in text or "大模型" in text or "streaming" in text.lower():
                level = "llm"
            elif "[调试]" in text:
                level = "debug"
            elif "错误" in text or "失败" in text or "error" in text.lower():
                level = "error"
            elif any(x in text for x in ["[1/", "[2/", "[3/", "[4/", "[5/"]):
                level = "step"
            _log(self.task_id, level, text)


# ── stderr 捕获（LLM 流式输出写到 stderr） ────────────────────────
class StderrCapture:
    def __init__(self, task_id: str):
        self.task_id = task_id
        self._original = None
        self._buffer = ""

    def __enter__(self):
        import sys
        self._original = sys.stderr
        sys.stderr = self
        return self

    def __exit__(self, *args):
        import sys
        sys.stderr = self._original
        self._flush()

    def write(self, text: str) -> int:
        if not text:
            return 0
        self._buffer += text
        if "\n" in self._buffer:
            lines = self._buffer.split("\n")
            for line in lines[:-1]:
                line = _strip_ansi(line).strip()
                if line:
                    _log(self.task_id, "llm", line)
            self._buffer = lines[-1]
        return len(text)

    def flush(self):
        return None

    def _flush(self):
        message = _strip_ansi(self._buffer).strip()
        if message:
            _log(self.task_id, "llm", message)
        self._buffer = ""

    def fileno(self):
        return self._original.fileno() if self._original else -1

    def isatty(self):
        return False


# ── 后台流水线 ────────────────────────────────────────────────────
def _run_pipeline(task_id: str):
    task = _tasks[task_id]
    input_value = task["input_value"]
    output_dir = Path(task["output_dir"])
    llm_config = task.get("llm_config")
    prefer_project_fasta = bool(task.get("prefer_project_fasta"))
    review_overrides = dict(task.get("review_overrides") or {})
    if not isinstance(llm_config, dict):
        _set_task_terminal_status(task_id, "failed")
        _log(task_id, "error", "缺少本次任务的 API Key 配置。")
        _start_ready_queued_tasks()
        return

    reporter = WebReporter(task_id)

    try:
        from agent.input.normalizer import normalize_input
        from agent.orchestrator.pipeline import AgentService

        _log(task_id, "info", f"任务开始：{input_value}")
        _log(task_id, "info", f"输出目录：{output_dir}")
        _log(task_id, "info", f"LLM 模型：{llm_config['model']}  Base URL：{llm_config['base_url']}")

        # ── 步骤 1 ──
        _step(task_id, 1, "[1/5] 解析 PRIDE 项目")
        _log(task_id, "info", "正在初始化 AgentService…")
        service = AgentService(reporter=reporter, llm_reasoner=_task_llm_reasoner(llm_config))
        _log(task_id, "info", "AgentService 初始化完成")
        task_obj = normalize_input(input_value)
        _log(task_id, "info", f"输入规范化：{task_obj.file_name}")

        _log(task_id, "info", "正在查询 PRIDE API 并调用大模型推断参数…")
        with StderrCapture(task_id):
            result = service.plan_dda_run_from_pride(
                task=task_obj,
                output_dir=output_dir,
                prefer_project_fasta=prefer_project_fasta,
            )
        if review_overrides:
            result = service.apply_review_overrides_to_result(
                result,
                review_overrides,
                task_obj,
                output_dir,
                prefer_project_fasta=prefer_project_fasta,
            )
            _log(task_id, "info", "已应用人工复核选择，重新生成执行计划。")
        _set_review_summary(task_id, result)
        _log(task_id, "info", "PRIDE 查询和大模型推断完成")

        primary = result.resolution.primary_project
        if primary:
            _log(task_id, "info", f"项目：{primary.project_accession}  匹配文件：{primary.matched_file}  置信度：{result.resolution.resolution_confidence:.2f}")

        _log(task_id, "info", f"采集模式：{result.attributes.acquisition_mode.value}  物种：{result.attributes.species.value}")
        _log(task_id, "info", f"仪器：{result.attributes.instrument_name.value}  酶：{result.attributes.enzyme.value}")

        hints = result.attributes.search_parameter_hints.value
        if isinstance(hints, dict):
            _log(task_id, "info", f"推荐 workflow：{hints.get('recommended_workflow_name', '无')}")
            _log(task_id, "info", f"推荐 FASTA：{result.plan.fasta_path.name}")
            if result.plan.fasta_download_url:
                _log(task_id, "info", f"FASTA 下载源：{result.plan.fasta_download_url}")

        prepared_path = None
        if result.plan.needs_review and service._can_retry_with_mzml_instrument(result.plan):
            _log(task_id, "info", "检测到项目级多个仪器，先下载/转换 mzML，并从 mzML 读取文件级仪器信息。")
            _step(task_id, 2, "[2/5] 下载 PRIDE 数据文件")
            with StderrCapture(task_id):
                prepared_path = service.prepare_asset(result.asset)
            _log(task_id, "info", f"数据文件已就绪：{prepared_path}")
            result = service.replan_with_mzml_instrument(
                result,
                prepared_path,
                task_obj,
                output_dir,
                prefer_project_fasta=prefer_project_fasta,
            )
            _set_review_summary(task_id, result)
            _log(task_id, "info", f"仪器复核后计划状态：{'需要人工复核' if result.plan.needs_review else '可继续运行'}")

        if result.plan.needs_review:
            task["blocking_issues"] = result.plan.blocking_issues
            for issue in result.plan.blocking_issues:
                _log(task_id, "error", f"[阻断] {issue}")
            service.write_task_bundle(output_dir, result.resolution, result.context, result.attributes, result.plan, asset=result.asset)
            _set_task_terminal_status(task_id, "blocked")
            return

        _log(task_id, "info", f"workflow：{result.plan.fragpipe_workflow_path.name}  FASTA：{result.plan.fasta_path.name}（{result.plan.fasta_selection_mode}）")

        # ── 步骤 2 ──
        if prepared_path is None:
            _step(task_id, 2, "[2/5] 下载 PRIDE 数据文件")
            with StderrCapture(task_id):
                prepared_path = service.prepare_asset(result.asset)
            _log(task_id, "info", f"数据文件已就绪：{prepared_path}")
        else:
            _log(task_id, "info", f"复用已准备的数据文件：{prepared_path}")

        # ── 步骤 3 ──
        _step(task_id, 3, "[3/5] 生成 MSDT-Converter 输入包")
        from agent.execution.bundle import materialize_dda_task_bundle
        with StderrCapture(task_id):
            bundle = materialize_dda_task_bundle(
                task=task_obj,
                project_resolution=result.resolution,
                project_context=result.context,
                attributes=result.attributes,
                source_data_path=prepared_path,
                output_dir=output_dir,
                prefer_project_fasta=prefer_project_fasta,
                report=reporter,
            )
        service.write_task_bundle(output_dir, result.resolution, result.context, result.attributes, bundle.plan, asset=result.asset)
        _log(task_id, "info", f"输入包已生成：{output_dir}")
        _log(task_id, "info", f"converter_config：{bundle.converter_config_path}")
        _log(task_id, "info", f"workflow：{bundle.materialized_workflow_path}")
        _log(task_id, "info", f"FASTA：{bundle.materialized_fasta_path}")

        # ── 步骤 4 ──
        _step(task_id, 4, "[4/5] 运行 MSDT-Converter Docker")
        from agent.msdt_converter.docker_runner import DockerMSDTConverterRunner
        docker_runner = DockerMSDTConverterRunner(image="guomics2017/msdt-converter:v1.3", report=reporter)
        with StderrCapture(task_id):
            docker_result = docker_runner.run(bundle)

        # ── 步骤 5 ──
        _step(task_id, 5, "[5/5] 处理结果")
        if docker_result.returncode == 0:
            _log(task_id, "info", "=" * 50)
            _log(task_id, "info", "全部运行完成！")
            if output_dir.exists():
                for f in sorted(output_dir.rglob("*")):
                    if f.is_file():
                        size = f.stat().st_size
                        if size > 1024 * 1024:
                            size_str = f"{size / 1024 / 1024:.1f} MB"
                        elif size > 1024:
                            size_str = f"{size / 1024:.1f} KB"
                        else:
                            size_str = f"{size} B"
                        _log(task_id, "info", f"  {f.relative_to(output_dir)}  ({size_str})")
            _log(task_id, "info", "=" * 50)
            if not _has_downloadable_result_file(output_dir):
                raise RuntimeError("No downloadable result files were produced.")
            _log(task_id, "info", "开始压缩打包结果 ZIP，打包完成后才会显示下载按钮。")
            _zip_output_dir(output_dir, report=lambda message: _log(task_id, "info", message))
            _log(task_id, "info", "结果 ZIP 已压缩打包完成，可以下载。")
            _set_task_terminal_status(task_id, "completed")
        else:
            _log(task_id, "error", f"Docker 运行失败，返回码：{docker_result.returncode}")
            if docker_result.stdout:
                _log(task_id, "error", f"[stdout]\n{docker_result.stdout[-2000:]}")
            if docker_result.stderr:
                _log(task_id, "error", f"[stderr]\n{docker_result.stderr[-2000:]}")
            _set_task_terminal_status(task_id, "failed")

    except Exception as exc:
        import traceback
        _log(task_id, "error", f"运行出错：{exc}")
        _log(task_id, "debug", traceback.format_exc())
        _set_task_terminal_status(task_id, "failed")
    finally:
        _start_ready_queued_tasks()


# ── API 端点 ──────────────────────────────────────────────────────
@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    if task_id not in _tasks:
        history = _find_history_record(task_id)
        if history is not None:
            return _task_detail_from_history(task_id, history)
        return {"error": "任务不存在"}
    task = _tasks[task_id]
    with _tasks_lock:
        queue_state = _queue_state_locked(task_id)
    output_dir_raw = task.get("output_dir")
    output_dir = Path(output_dir_raw) if output_dir_raw else None
    can_download = bool(
        task.get("status") == "completed"
        and output_dir
        and output_dir.exists()
        and _is_download_zip_ready(output_dir)
    )
    return {
        "task_id": task["task_id"],
        "input_value": task["input_value"],
        "submitter": task.get("submitter", "未填写"),
        "status": task["status"],
        "step": task.get("step", 0),
        "total_steps": task.get("total_steps", 5),
        "log_count": len(task["logs"]),
        "logs": _public_logs_from_task(task),
        "blocking_issues": task.get("blocking_issues", []),
        "review_summary": task.get("review_summary"),
        "fasta_preference": "project" if task.get("prefer_project_fasta") else "llm",
        "can_download": can_download,
        "archived": False,
        **queue_state,
    }


def _clean_review_overrides(body: dict[str, Any]) -> dict[str, str]:
    raw = body.get("overrides") if isinstance(body.get("overrides"), dict) else body
    overrides: dict[str, str] = {}
    for field in ("species", "instrument_name"):
        value = _clean_text(raw.get(field)) if isinstance(raw, dict) else ""
        if value:
            overrides[field] = value
    return overrides


@app.post("/api/tasks/{task_id}/review")
async def submit_task_review(task_id: str, body: dict[str, Any]):
    overrides = _clean_review_overrides(body)
    if not overrides:
        return {"error": "请选择至少一个复核参数。"}
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task is None:
            return {"error": "任务不存在"}
        if task.get("status") not in {"blocked", "failed"}:
            return {"error": "当前任务不在可复核状态。"}
        if not isinstance(task.get("llm_config"), dict):
            return {"error": "服务器内存中没有本次任务的 API Key，无法继续；请重新提交任务。"}
        merged = dict(task.get("review_overrides") or {})
        merged.update(overrides)
        task["review_overrides"] = merged
        task["status"] = "queued"
        task["step"] = 0
        task["blocking_issues"] = []
        task.pop("finished_at", None)
        task["logs"].append(
            {
                "type": "log",
                "ts": _now_time(),
                "level": "info",
                "message": "已提交人工复核选择，任务重新进入队列。",
            }
        )
        queue_state = _queue_state_locked(task_id)
    _write_task_history(task_id)
    _start_ready_queued_tasks()
    with _tasks_lock:
        status = _tasks.get(task_id, {}).get("status", "queued")
        queue_state = _queue_state_locked(task_id)
    return {"task_id": task_id, "status": status, "review_overrides": overrides, **queue_state}


@app.get("/api/tasks/{task_id}/download")
async def download_results(task_id: str):
    if task_id not in _tasks:
        history = _find_history_record(task_id)
        if history is not None:
            if history.get("status") != "completed":
                return {"error": "Task is not completed; results cannot be downloaded."}
            output_dir_name = str(history.get("output_dir") or "")
            output_dir = _runs_dir / output_dir_name if output_dir_name else None
            if output_dir is None or not output_dir.exists():
                return {"error": "Result directory does not exist."}
            if not _has_downloadable_result_file(output_dir):
                return {"error": "Result directory has no downloadable files."}
            if not _is_download_zip_ready(output_dir):
                return {"error": "Result ZIP is not ready yet."}
            zip_path = _download_zip_path(output_dir)
            stem = safe_output_stem(str(history.get("input_value") or output_dir_name or task_id))
            return FileResponse(
                path=str(zip_path),
                filename=f"{stem}_results.zip",
                media_type="application/zip",
            )
        return {"error": "任务不存在"}
    task = _tasks[task_id]
    if task.get("status") != "completed":
        return {"error": "任务未完成，不能下载结果"}
    output_dir = Path(task["output_dir"])
    if not output_dir.exists():
        return {"error": "结果目录不存在"}
    if not _has_downloadable_result_file(output_dir):
        return {"error": "结果目录没有可下载文件"}

    if not _is_download_zip_ready(output_dir):
        return {"error": "结果 ZIP 尚未打包完成，请等待任务日志提示后再下载。"}
    zip_path = _download_zip_path(output_dir)
    stem = safe_output_stem(task["input_value"])
    return FileResponse(
        path=str(zip_path),
        filename=f"{stem}_results.zip",
        media_type="application/zip",
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
