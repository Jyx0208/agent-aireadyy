from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import tempfile
import threading
import uuid
import zipfile
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from starlette.background import BackgroundTask

from agent.input.normalizer import safe_output_stem
from agent.progress import render_download_progress

app = FastAPI(title="PRIDE AI-ready Agent", version="0.3.1")

_tasks: dict[str, dict[str, Any]] = {}
_tasks_lock = threading.Lock()
_runs_dir = Path("runs")
_runs_dir.mkdir(exist_ok=True)
_templates_dir = Path(__file__).parent / "templates"
_ACTIVE_STATUSES = {"queued", "running"}
_TERMINAL_STATUSES = {"completed", "failed", "blocked"}

# 默认配置（不从 .env 加载，由用户在页面填写）
_DEFAULT_CONFIG = {
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-v4-flash",
    "timeout": "1200",
}
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\[(?:\d{1,3};?)*m")


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _strip_ansi(value: Any) -> str:
    return _ANSI_RE.sub("", str(value)).replace("\r", "")


def _remove_file(path: str | Path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


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
    task["started_at"] = datetime.now(UTC).isoformat()
    task["logs"].append(
        {
            "type": "log",
            "ts": datetime.now(UTC).strftime("%H:%M:%S"),
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
        "updated_at": datetime.now(UTC).strftime("%H:%M:%S"),
        "needs_review": bool(getattr(plan, "needs_review", False)),
        "issues": issues,
        "items": items,
    }


def _set_review_summary(task_id: str, result: Any) -> None:
    task = _tasks.get(task_id)
    if task is None:
        return
    summary = _build_review_summary(result)
    task["review_summary"] = summary
    _emit(task_id, "review", summary=summary)


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
            "output_dir": str(output_dir),
            "status": "queued",
            "created_at": datetime.now(UTC).isoformat(),
            "logs": deque(maxlen=5000),
            "step": 0,
            "total_steps": 5,
            "blocking_issues": [],
            "llm_config": dict(config),
        }
        queue_state = _queue_state_locked(task_id)
        _tasks[task_id]["logs"].append(
            {
                "type": "log",
                "ts": datetime.now(UTC).strftime("%H:%M:%S"),
                "level": "info",
                "message": f"任务已进入队列，当前位置 {queue_state['queue_position']}/{queue_state['queue_length']}。",
            }
        )
    _start_ready_queued_tasks()
    with _tasks_lock:
        status = _tasks[task_id]["status"]
        queue_state = _queue_state_locked(task_id)
    return {"task_id": task_id, "output_dir": str(output_dir), "status": status, **queue_state}


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
    entry = {"type": msg_type, "ts": datetime.now(UTC).strftime("%H:%M:%S"), **kwargs}
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

    def __call__(self, message):
        if isinstance(message, dict):
            kind = message.get("kind", "")
            if kind == "download_progress":
                msg = render_download_progress(message, width=16)
                if message.get("complete"):
                    msg = f"下载完成 {msg}"
                label = _clean_text(message.get("label")) or "download"
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
    if not isinstance(llm_config, dict):
        task["status"] = "failed"
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
            result = service.plan_dda_run_from_pride(task=task_obj, output_dir=output_dir)
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
            _log(task_id, "info", f"推荐 FASTA：{hints.get('recommended_fasta_name', '无')}")

        if result.plan.needs_review:
            task["status"] = "blocked"
            task["blocking_issues"] = result.plan.blocking_issues
            for issue in result.plan.blocking_issues:
                _log(task_id, "error", f"[阻断] {issue}")
            service.write_task_bundle(output_dir, result.resolution, result.context, result.attributes, result.plan, asset=result.asset)
            return

        _log(task_id, "info", f"workflow：{result.plan.fragpipe_workflow_path.name}  FASTA：{result.plan.fasta_path.name}（{result.plan.fasta_selection_mode}）")

        # ── 步骤 2 ──
        _step(task_id, 2, "[2/5] 下载 PRIDE 数据文件")
        with StderrCapture(task_id):
            prepared_path = service.prepare_asset(result.asset)
        _log(task_id, "info", f"数据文件已就绪：{prepared_path}")

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
            task["status"] = "completed"
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
            _log(task_id, "info", "点击【下载结果文件】按钮获取 ZIP 压缩包")
        else:
            task["status"] = "failed"
            _log(task_id, "error", f"Docker 运行失败，返回码：{docker_result.returncode}")
            if docker_result.stdout:
                _log(task_id, "error", f"[stdout]\n{docker_result.stdout[-2000:]}")
            if docker_result.stderr:
                _log(task_id, "error", f"[stderr]\n{docker_result.stderr[-2000:]}")

    except Exception as exc:
        import traceback
        task["status"] = "failed"
        _log(task_id, "error", f"运行出错：{exc}")
        _log(task_id, "debug", traceback.format_exc())
    finally:
        _start_ready_queued_tasks()


# ── API 端点 ──────────────────────────────────────────────────────
@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    if task_id not in _tasks:
        return {"error": "任务不存在"}
    task = _tasks[task_id]
    with _tasks_lock:
        queue_state = _queue_state_locked(task_id)
    output_dir_raw = task.get("output_dir")
    output_dir = Path(output_dir_raw) if output_dir_raw else None
    return {
        "task_id": task["task_id"],
        "input_value": task["input_value"],
        "status": task["status"],
        "step": task.get("step", 0),
        "total_steps": task.get("total_steps", 5),
        "log_count": len(task["logs"]),
        "blocking_issues": task.get("blocking_issues", []),
        "review_summary": task.get("review_summary"),
        "can_download": bool(output_dir and output_dir.exists() and any(path.is_file() for path in output_dir.rglob("*"))),
        **queue_state,
    }


@app.get("/api/tasks/{task_id}/download")
async def download_results(task_id: str):
    if task_id not in _tasks:
        return {"error": "任务不存在"}
    task = _tasks[task_id]
    output_dir = Path(task["output_dir"])
    if not output_dir.exists():
        return {"error": "结果目录不存在"}

    with tempfile.NamedTemporaryFile(prefix="pride-agent-", suffix=".zip", delete=False) as temp_file:
        zip_path = Path(temp_file.name)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in output_dir.rglob("*"):
            if file.is_file():
                zf.write(file, file.relative_to(output_dir))

    stem = safe_output_stem(task["input_value"])
    return FileResponse(
        path=str(zip_path),
        filename=f"{stem}_results.zip",
        media_type="application/zip",
        background=BackgroundTask(_remove_file, zip_path),
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
