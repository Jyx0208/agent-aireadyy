from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, BaseModel):
        return value.model_dump()
    raise TypeError(f"Object of type {type(value)!r} is not JSON serializable")


def write_json(path: str | Path, payload: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")
    return path


def emit(report: Callable[[Any], None] | None, message: Any) -> None:
    if report is not None:
        report(message)


def run_command_streaming(
    command: list[str],
    *,
    report: Callable[[Any], None] | None = None,
    cwd: str | Path | None = None,
    timeout_seconds: float | None = None,
    idle_timeout_seconds: float | None = None,
    abort_predicate: Callable[[str, list[str]], str | None] | None = None,
    on_abort: Callable[[str], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    emit(report, f"Running command: {' '.join(command)}")
    process = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    lines: list[str] = []
    output_queue: queue.Queue[str | None] = queue.Queue()

    def _read_stdout() -> None:
        try:
            assert process.stdout is not None
            for raw_line in process.stdout:
                output_queue.put(raw_line.rstrip())
        finally:
            output_queue.put(None)

    reader = threading.Thread(target=_read_stdout, daemon=True)
    reader.start()
    start_time = time.monotonic()
    last_output_time = start_time
    aborted_reason: str | None = None
    reader_done = False
    while not reader_done:
        try:
            line = output_queue.get(timeout=0.2)
        except queue.Empty:
            now = time.monotonic()
            if timeout_seconds and timeout_seconds > 0 and now - start_time >= timeout_seconds:
                aborted_reason = "command_timeout"
            elif idle_timeout_seconds and idle_timeout_seconds > 0 and now - last_output_time >= idle_timeout_seconds:
                aborted_reason = "command_idle_timeout"
            if aborted_reason:
                _abort_process(process, aborted_reason, report=report, on_abort=on_abort)
                reader_done = True
            continue
        if line is None:
            reader_done = True
            continue
        if not line:
            continue
        last_output_time = time.monotonic()
        emit(report, line)
        lines.append(line)
        if abort_predicate is not None:
            reason = abort_predicate(line, lines)
            if reason:
                aborted_reason = reason
                _abort_process(process, aborted_reason, report=report, on_abort=on_abort)
                reader_done = True

    return_code = process.wait()
    reader.join(timeout=1.0)
    output = "\n".join(lines)
    if aborted_reason:
        output = "\n".join([output, f"agent_watchdog_abort:{aborted_reason}"]).strip()
        raise subprocess.CalledProcessError(return_code if return_code else 124, command, output=output)
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, command, output=output)
    return subprocess.CompletedProcess(command, return_code, stdout=output, stderr="")


def _abort_process(
    process: subprocess.Popen[str],
    reason: str,
    *,
    report: Callable[[Any], None] | None,
    on_abort: Callable[[str], None] | None,
) -> None:
    emit(report, f"agent_watchdog_abort:{reason}")
    if on_abort is not None:
        try:
            on_abort(reason)
        except Exception as exc:
            emit(report, f"agent_watchdog_cleanup_failed:{exc}")
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
