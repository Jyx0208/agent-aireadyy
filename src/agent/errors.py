from __future__ import annotations

import os
import re
import subprocess
import traceback
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from agent.utils import write_json


_SECRET_RE = re.compile(r"sk-[A-Za-z0-9_\-]{6,}|(api[_ -]?key\s*[:=]\s*)\S+", re.IGNORECASE)


@dataclass(frozen=True)
class ErrorClassification:
    category: str
    public_message: str
    operator_hint: str
    retryable: bool


def redact_secrets(value: Any) -> str:
    text = str(value or "")

    def replace(match: re.Match[str]) -> str:
        if match.group(1):
            return f"{match.group(1)}[redacted]"
        return "[redacted-api-key]"

    return _SECRET_RE.sub(replace, text)


def _text_for_error(exc: BaseException) -> str:
    output = getattr(exc, "output", "") or ""
    stderr = getattr(exc, "stderr", "") or ""
    return redact_secrets(f"{type(exc).__name__}: {exc}\n{output}\n{stderr}").lower()


def classify_error(exc: BaseException, *, stage: str = "unknown") -> ErrorClassification:
    text = _text_for_error(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        if status_code in {401, 403}:
            return ErrorClassification("auth", "API Key 无效或没有权限。", "检查本次任务填写的 API Key、Base URL 和模型权限。", False)
        if status_code == 404:
            return ErrorClassification("not_found", "远程资源不存在或接口路径不可用。", "检查 PRIDE accession、文件名、Base URL 或模型名称。", False)
        if status_code == 429:
            return ErrorClassification("rate_limited", "远程服务限流或额度不足。", "降低并发或稍后重试；LLM 额度不足时需要更换 Key。", True)
        if status_code >= 500:
            return ErrorClassification("remote_service", f"远程服务暂时不可用（HTTP {status_code}）。", "稍后重试；若持续失败，保留 error.json 供排查。", True)
        return ErrorClassification("http_error", f"远程请求失败（HTTP {status_code}）。", "检查请求目标和输入文件名。", False)
    if isinstance(exc, httpx.TimeoutException) or "timed out" in text or "timeout" in text:
        return ErrorClassification("timeout", "远程请求超时。", "降低并发、稍后重试，或增大超时时间。", True)
    if isinstance(exc, httpx.RequestError):
        return ErrorClassification("network", "网络连接失败。", "检查服务器网络、DNS、代理和 PRIDE/LLM 服务可用性。", True)
    if "permission denied" in text and "docker" in text:
        return ErrorClassification("docker_permission", "当前用户没有 Docker 访问权限。", "将运行用户加入 docker 组后重新登录，或用有权限的用户启动服务。", False)
    if "docker" in text and ("daemon" in text or "dockerdesktop" in text or "var/run/docker.sock" in text):
        return ErrorClassification("docker_unavailable", "Docker 服务不可用。", "启动 Docker daemon/Docker Desktop，并确认 docker run 可执行。", True)
    if "insufficient memory" in text or "outofmemory" in text or "out of memory" in text:
        return ErrorClassification("insufficient_memory", "内存不足导致任务失败。", "降低线程数、增加 swap，或改用更大内存机器；大 FASTA/TMT/开放搜索尤其容易触发。", False)
    if isinstance(exc, FileNotFoundError) or "no such file or directory" in text:
        return ErrorClassification("missing_tool", "缺少本地工具或文件。", "检查 msconvert、Docker、workflow、FASTA 或输入文件路径是否存在。", False)
    if isinstance(exc, PermissionError):
        return ErrorClassification("filesystem_permission", "文件系统权限不足。", "检查输出目录、缓存目录、Docker volume 目录权限。", False)
    if isinstance(exc, subprocess.CalledProcessError):
        return ErrorClassification("process_failed", "外部命令执行失败。", "查看 runtime.log/run.log；常见原因是 FragPipe 参数、内存、Docker 或输入格式问题。", False)
    if "review" in text or "needs_review" in text or "人工复核" in text:
        return ErrorClassification("needs_review", "任务需要人工复核。", "查看 review_queue.json，根据物种、仪器、FASTA 或 workflow 提示补充选择。", False)
    return ErrorClassification("unknown", "任务运行失败。", f"查看日志定位 {stage} 阶段的具体异常。", False)


def build_error_record(
    exc: BaseException,
    *,
    stage: str,
    input_file: str,
    include_traceback: bool | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    classification = classify_error(exc, stage=stage)
    if include_traceback is None:
        include_traceback = os.getenv("AGENT_DEBUG_TRACEBACK", "").lower() in {"1", "true", "yes"}
    record: dict[str, Any] = {
        "error_id": uuid.uuid4().hex[:12],
        "status": "failed",
        "stage": stage,
        "input_file": input_file,
        "category": classification.category,
        "public_message": classification.public_message,
        "operator_hint": classification.operator_hint,
        "retryable": classification.retryable,
        "exception_type": type(exc).__name__,
        "technical_message": redact_secrets(str(exc)),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    if extra:
        record.update({key: redact_secrets(value) if isinstance(value, str) else value for key, value in extra.items()})
    if include_traceback:
        record["traceback"] = redact_secrets("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    return record


def write_error_record(path: str | Path, record: dict[str, Any]) -> Path:
    return write_json(path, record)


def public_error_summary(record: dict[str, Any]) -> dict[str, Any]:
    keys = ("error_id", "stage", "category", "public_message", "operator_hint", "retryable", "exception_type")
    return {key: record[key] for key in keys if key in record}
