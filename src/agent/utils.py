from __future__ import annotations

import json
import subprocess
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
) -> subprocess.CompletedProcess[str]:
    emit(report, f"正在运行命令：{' '.join(command)}")
    process = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    lines: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        line = line.rstrip()
        if line:
            emit(report, line)
            lines.append(line)
    return_code = process.wait()
    output = "\n".join(lines)
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, command, output=output)
    return subprocess.CompletedProcess(command, return_code, stdout=output, stderr="")
