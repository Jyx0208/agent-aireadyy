from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from collections import Counter, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from agent.input.normalizer import safe_output_stem
from agent.oneclick.preflight import normalize_resource_policy, normalize_run_mode, run_preflight
from agent.progress import render_download_progress
from agent.web.history import history_timestamp, merge_project_history_records, with_history_identity


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _runs_dir.mkdir(exist_ok=True)
    _sync_history_index_from_disk()
    _repair_interrupted_history_index()
    _start_result_cleanup_worker()
    yield


app = FastAPI(title="PRIDE AI-ready Agent", version="0.3.1", lifespan=lifespan)

_tasks: dict[str, dict[str, Any]] = {}
_tasks_lock = threading.Lock()
_batches: dict[str, dict[str, Any]] = {}
_batches_lock = threading.Lock()
_runs_dir = Path("runs")
_templates_dir = Path(__file__).parent / "templates"
_ACTIVE_STATUSES = {"queued", "running"}
_TERMINAL_STATUSES = {"completed", "failed", "blocked"}
_cleanup_thread_started = False
_PUBLIC_HISTORY_FILE = "task_history.json"
_HISTORY_INDEX_FILE = "project_history.json"
_DOWNLOAD_CACHE_DIR = ".download_cache"
_DOWNLOAD_ZIP_NAME = "results-compressed.zip"
_BATCHES_DIR_NAME = "_batches"
_BATCH_MANIFEST_FILE = "batch_manifest.json"
_BATCH_EXCEL_FILE = "benchmark_results.xlsx"
_BATCH_AUDIT_ZIP_NAME = "batch_parameter_audit.zip"
_DOWNLOAD_RESULT_DIRS = {"ai_ready", "msdt", "rawspectrum", "logs"}
_DOWNLOAD_ROOT_SUFFIXES = {".json", ".txt", ".log", ".tsv", ".csv"}
_DOWNLOAD_FRAGPIPE_PARAMETER_FILES = {"fragger.params", "msbooster_params.txt"}
_MAX_PERSISTED_LOGS = 2000
_INTERRUPTED_HISTORY_MESSAGE = "服务重启或任务被手动停止，任务已中断。"
_RUN_MODE_FULL = "full"
_RUN_MODE_PREPARE = "prepare"
_RUN_MODE_PARAMETERS = "parameters"
_RUN_MODES = {_RUN_MODE_FULL, _RUN_MODE_PREPARE, _RUN_MODE_PARAMETERS}
_UI_LANGUAGES = {"en", "zh"}

# 默认配置（不从 .env 加载，由用户在页面填写）
_DEFAULT_CONFIG = {
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-v4-flash",
    "timeout": "1200",
}
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\[\d{1,3}(?:;\d{1,3})*m")
_CJK_RE = re.compile(r"[\u3400-\u9fff\u3000-\u303f\uff00-\uffef]")
def _app_timezone():
    timezone_name = os.getenv("TZ", "Asia/Shanghai")
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=8), "CST")


_APP_TZ = _app_timezone()


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


def _env_flag(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _full_workflow_enabled() -> bool:
    if _env_flag("AGENT_DISABLE_FULL_WORKFLOW", default=False):
        return False
    return _env_flag("AGENT_WEB_FULL_WORKFLOW_ENABLED", default=False)


def _clean_submitter(value: Any) -> str:
    submitter = _clean_text(value)
    submitter = re.sub(r"[\x00-\x1f\x7f]+", " ", submitter).strip()
    if not submitter:
        return "未填写"
    return submitter[:80]


def _clean_run_mode(value: Any) -> str:
    default = _RUN_MODE_FULL if _full_workflow_enabled() else _RUN_MODE_PREPARE
    mode = normalize_run_mode(value, default=default)
    if mode == _RUN_MODE_FULL and not _full_workflow_enabled():
        return _RUN_MODE_PREPARE
    return mode


def _clean_batch_run_mode(value: Any) -> str:
    mode = normalize_run_mode(value, default=_RUN_MODE_PARAMETERS)
    if mode == _RUN_MODE_FULL and not _full_workflow_enabled():
        return _RUN_MODE_PREPARE
    return mode


def _clean_resource_policy(value: Any) -> str:
    return normalize_resource_policy(value)


def _clean_reviewed_fasta(value: Any) -> tuple[str | None, str | None]:
    fasta = _clean_text(value)
    if not fasta:
        return None, None
    if re.match(r"(?i)^(https?|ftp)://", fasta):
        return None, fasta
    return fasta, None


def _run_mode_label(value: Any) -> str:
    mode = _clean_run_mode(value)
    if mode == _RUN_MODE_PARAMETERS:
        return "Parameters only"
    if mode == _RUN_MODE_PREPARE:
        return "Prepare input package"
    return "Full workflow"


def _clean_repository(value: Any, default: str = "pride") -> str:
    repository = _clean_text(value).lower().replace("-", "_")
    if repository in {"auto", "all"}:
        return "auto"
    if repository in {"pride", "px", "proteomexchange"}:
        return "pride"
    if repository in {"massive", "massive_ucsd", "msv", "gnps"}:
        return "massive"
    return default


def _clean_ui_language(value: Any) -> str:
    language = _clean_text(value).lower()
    if language in {"zh", "zh_cn", "zh-cn", "cn", "chinese"}:
        return "zh"
    if language in {"en", "en_us", "en-us", "english"}:
        return "en"
    return "en"


def _strip_ansi(value: Any) -> str:
    return _ANSI_RE.sub("", str(value)).replace("\r", "")


def _redact_secrets(value: Any) -> str:
    text = _strip_ansi(value)
    text = re.sub(r"(?i)(api[_ -]?key\s*[:=]\s*)\S+", r"\1[redacted]", text)
    text = re.sub(r"sk-[A-Za-z0-9_\-]{6,}", "[redacted-api-key]", text)
    return text


def _contains_cjk(value: Any) -> bool:
    return bool(_CJK_RE.search(str(value)))


def _task_ui_language(task_id: str) -> str:
    task = _tasks.get(task_id)
    if not task:
        return "en"
    return _clean_ui_language(task.get("ui_language"))


def _english_punctuation(text: str) -> str:
    replacements = {
        "：": ": ",
        "；": "; ",
        "，": ", ",
        "。": ".",
        "（": " (",
        "）": ") ",
        "、": ", ",
        "…": "...",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "！": "!",
        "？": "?",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


_EN_LOG_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\[(\d)/5\] 正在根据文件名解析 PRIDE 项目：(.+)$"), r"[\1/5] Resolving PRIDE project from file name: \2"),
    (re.compile(r"^\[(\d)/5\] 项目上下文已准备完成。SDRF 行数：(\d+)$"), r"[\1/5] Project context prepared. SDRF rows: \2"),
    (re.compile(r"^\[(\d)/5\] 已解析数据文件：(.+) （类型=(.+)，是否需要转换=(.+)）$"), r"[\1/5] Data file resolved: \2 (type=\3, requires_conversion=\4)"),
    (re.compile(r"^\[(\d)/5\] 文件属性推断完成。采集模式=(.+)$"), r"[\1/5] File attribute inference completed. Acquisition mode=\2"),
    (re.compile(r"^\[(\d)/5\] DDA 执行计划已生成。workflow=(.+)$"), r"[\1/5] DDA execution plan generated. workflow=\2"),
    (re.compile(r"^任务开始：(.+)$"), r"Task started: \1"),
    (re.compile(r"^输出目录：(.+)$"), r"Output directory: \1"),
    (re.compile(r"^运行模式：仅搜参数$"), "Run mode: parameter planning only"),
    (re.compile(r"^运行模式：完整流程$"), "Run mode: full workflow"),
    (re.compile(r"^任务已进入队列，当前位置 (\d+)/(\d+)。$"), r"Task queued. Position \1/\2."),
    (re.compile(r"^任务已从队列启动。$"), "Task started from queue."),
    (re.compile(r"^输入规范化：(.+)$"), r"Input normalized: \1"),
    (re.compile(r"^已选择主项目：(.+)$"), r"Selected primary project: \1"),
    (re.compile(r"^解析原因：(.+)$"), r"Resolution reason: \1"),
    (re.compile(r"^FASTA 下载源：(.+)$"), r"FASTA download source: \1"),
    (re.compile(r"^推荐 workflow：(.+)$"), r"Recommended workflow: \1"),
    (re.compile(r"^推荐 FASTA：(.+)$"), r"Recommended FASTA: \1"),
    (re.compile(r"^数据文件已就绪：(.+)$"), r"Data file ready: \1"),
    (re.compile(r"^输入包已生成：(.+)$"), r"Input bundle generated: \1"),
    (re.compile(r"^转换完成：(.+)$"), r"Conversion completed: \1"),
    (re.compile(r"^下载完成：(.+)$"), r"Download completed: \1"),
    (re.compile(r"^正在下载：(.+)$"), r"Downloading: \1"),
    (re.compile(r"^正在运行命令：(.+)$"), r"Running command: \1"),
    (re.compile(r"^\[阻断\]\s*(.+)$"), r"[blocked] \1"),
)

_EN_LOG_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("解析 PRIDE 项目", "Resolve PRIDE project"),
    ("下载 PRIDE 数据文件", "Download PRIDE data file"),
    ("生成 MSDT-Converter 输入包", "Generate MSDT-Converter input bundle"),
    ("运行 MSDT-Converter Docker", "Run MSDT-Converter Docker"),
    ("处理结果", "Process results"),
    ("正在初始化 AgentService…", "Initializing AgentService..."),
    ("AgentService 初始化完成", "AgentService initialized"),
    ("正在查询 PRIDE API 并调用大模型推断参数…", "Querying PRIDE API and inferring parameters with the LLM..."),
    ("正在查询 PRIDE Archive API 并匹配项目/文件…", "Querying PRIDE Archive API and matching project/file..."),
    ("PRIDE 查询完成。", "PRIDE query completed."),
    ("项目解析摘要：", "Project resolution summary: "),
    ("项目元数据摘要：", "Project metadata summary: "),
    ("文件资产判断：", "File asset decision: "),
    ("未找到 SDRF 行；将结合 PRIDE 项目描述、协议、文件名和参数/FASTA 文件线索推断搜库参数。", "No matching SDRF row was found; PRIDE metadata, protocols, file name, parameter files, and FASTA clues will be used to infer search parameters."),
    ("正在调用大模型确认文件属性和搜库参数。", "Calling the LLM to confirm file attributes and search parameters."),
    ("大模型正在阅读 PRIDE 元数据并生成搜库参数…", "The LLM is reading PRIDE metadata and generating search parameters..."),
    ("大模型确认结果已合并到属性推断中。", "LLM confirmation was merged into attribute inference."),
    ("PRIDE 查询和大模型推断完成", "PRIDE query and LLM inference completed"),
    ("属性判断：", "Attribute decision: "),
    ("搜库参数判断：", "Search parameter decision: "),
    ("数据适配提示：", "Data compatibility hint: "),
    ("执行计划：", "Execution plan: "),
    ("预期输出：", "Expected outputs: "),
    ("采集模式", "acquisition mode"),
    ("物种", "species"),
    ("仪器", "instrument"),
    ("酶", "enzyme"),
    ("项目", "project"),
    ("匹配文件", "matched file"),
    ("匹配类型", "match type"),
    ("匹配分数", "match score"),
    ("解析置信度", "resolution confidence"),
    ("置信度", "confidence"),
    ("实验类型", "experiment type"),
    ("解析类型", "resolved type"),
    ("是否需要转换", "requires conversion"),
    ("资产置信度", "asset confidence"),
    ("参数", "parameters"),
    ("固定修饰", "fixed modifications"),
    ("可变修饰", "variable modifications"),
    ("数据类型", "data type"),
    ("无", "none"),
    ("线程数", "threads"),
    ("原始数据类型", "raw data type"),
    ("正在下载数据文件", "Downloading data file"),
    ("下载完成", "Download complete"),
    ("已硬链接缓存的 PRIDE 文件", "Hard-linked cached PRIDE file"),
    ("已复制缓存的 PRIDE 文件", "Copied cached PRIDE file"),
    ("复用已下载的数据文件", "Reusing downloaded data file"),
    ("复用项目缓存中的 PRIDE 文件", "Reusing PRIDE project cache file"),
    ("数据文件需要格式转换", "Data file requires format conversion"),
    ("正在使用本地 msconvert 转换质谱文件", "Converting mass spectrometry file with local msconvert"),
    ("正在使用 Docker ProteoWizard 转换质谱文件", "Converting mass spectrometry file with Docker ProteoWizard"),
    ("主转换器失败", "Primary converter failed"),
    ("正在切换到备用转换器", "Switching to fallback converter"),
    ("数据文件已可直接用于执行", "Data file can be used directly"),
    ("正在解压", "Extracting"),
    ("解压完成", "Extraction completed"),
    ("已写入 Docker MSDT-Converter 配置", "Docker MSDT-Converter config written"),
    ("正在启动 MSDT-Converter Docker 镜像", "Starting MSDT-Converter Docker image"),
    ("MSDT-Converter 内部步骤失败，任务已标记为失败，不打包下载 ZIP。", "An MSDT-Converter internal step failed; the task was marked as failed and no ZIP will be packaged."),
    ("全部运行完成！", "Full workflow completed."),
    ("开始压缩打包结果 ZIP，打包完成后才会显示下载按钮。", "Compressing result ZIP; the download button will appear after packaging finishes."),
    ("结果 ZIP 已压缩打包完成，可以下载。", "Result ZIP is ready to download."),
    ("结果 ZIP 已存在，复用缓存", "Result ZIP already exists; reusing cache"),
    ("开始打包下载 ZIP", "Packaging download ZIP"),
    ("ZIP 打包进度", "ZIP packaging progress"),
    ("结果 ZIP 打包完成", "Result ZIP packaging completed"),
    ("仅搜参数模式", "Parameter-only mode"),
    ("已完成 PRIDE 项目解析、文件属性推断、workflow/FASTA/搜库参数计划生成。", "PRIDE project resolution, file attribute inference, and workflow/FASTA/search-parameter planning are complete."),
    ("参数推断完成", "Parameter inference completed"),
    ("人工已确认搜库参数；继续处理剩余步骤。", "Manual search-parameter review confirmed; continuing."),
    ("检测到项目级多个仪器；先准备/转换 mzML，并尝试从 mzML 解析文件级仪器。", "Multiple project-level instruments detected; preparing/converting mzML and reading file-level instrument metadata."),
    ("已从 mzML 解析文件级仪器", "File-level instrument parsed from mzML"),
    ("当前计划需要人工复核，暂不下载或准备数据文件。原因", "The current plan needs manual review; data download/preparation is paused. Reason"),
    ("未找到匹配的 SDRF 行，且项目包含多个物种；无法确定文件级物种信息。", "No matching SDRF row was found, and the project contains multiple species; file-level species cannot be determined."),
    ("未找到匹配的 SDRF 行，且项目包含多个仪器；无法确定文件级仪器信息。", "No matching SDRF row was found, and the project contains multiple instruments; file-level instrument cannot be determined."),
    ("当前 bottom-up MSDT 搜库流程不支持 Top-down 蛋白质组学项目。", "The current bottom-up MSDT search workflow does not support top-down proteomics projects."),
    ("大模型推荐的 workflow", "The LLM-recommended workflow"),
    ("不存在于 profiles/fragpipe/ 目录中。请检查 workflow 名称是否正确。", "does not exist in profiles/fragpipe/. Check the workflow name."),
    ("大模型未推荐 workflow。必须配置 LLM API 并确保大模型能正确推荐 workflow。请检查 AGENT_LLM_API_KEY 配置。", "The LLM did not recommend a workflow. Configure the LLM API and ensure it can recommend a workflow."),
    ("任务运行失败。", "Task execution failed."),
    ("网络连接失败。", "Network connection failed."),
    ("Docker 服务不可用。", "Docker service is unavailable."),
    ("内存不足导致任务失败。", "The task failed because memory was insufficient."),
    ("外部命令执行失败。", "An external command failed."),
    ("任务需要人工复核。", "The task needs manual review."),
    ("运行出错", "Run failed"),
    ("错误", "error"),
    ("失败", "failed"),
    ("成功", "succeeded"),
    ("完成", "completed"),
    ("正在", "in progress"),
    ("已", ""),
)


def _ascii_fallback(text: str, level: str = "") -> str:
    ascii_text = _CJK_RE.sub(" ", _english_punctuation(text))
    ascii_text = re.sub(r"[^A-Za-z0-9_./:;=+\-()[\]{}|,@#%&?\\\s]", " ", ascii_text)
    ascii_text = re.sub(r"\s{2,}", " ", ascii_text).strip(" ;,")
    if ascii_text and re.search(r"[A-Za-z0-9]", ascii_text):
        return f"Backend message: {ascii_text}"
    if str(level).lower() == "llm":
        return "LLM reasoning output was not shown in English logs; structured parameters were saved in the audit files."
    return "Backend message omitted in English mode because it was not localized."


def _english_fasta_review_message(text: str) -> str | None:
    if "UniProt" not in text or "FASTA" not in text:
        return None
    if "proteome ID" not in text and "占位" not in text and "鍗犱綅" not in text:
        return None
    species_match = re.search(r"environmental samples(?:\s*<[^>]+>)?", text, re.IGNORECASE)
    if species_match:
        species = species_match.group(0).strip()
    else:
        species = "the selected sample"
    prefix = "[blocked] " if "[阻断]" in text or "[blocked]" in text else ""
    return (
        f"{prefix}No real UniProt FASTA could be selected for species: {species}. "
        "Provide a reviewed FASTA URL or local FASTA path before running the full workflow."
    )


def _to_english_log_message(message: Any, level: str = "") -> str:
    text = _redact_secrets(message).strip()
    if not text:
        return ""
    for pattern, replacement in _EN_LOG_PATTERNS:
        text = pattern.sub(replacement, text)
    for old, new in sorted(_EN_LOG_REPLACEMENTS, key=lambda item: len(item[0]), reverse=True):
        text = text.replace(old, new)
    text = _english_punctuation(text)
    fasta_review = _english_fasta_review_message(text)
    if fasta_review:
        return fasta_review
    if not _contains_cjk(text):
        return text
    if str(level).lower() == "llm":
        return "LLM reasoning output was not shown in English logs; structured parameters were saved in the audit files."
    return _ascii_fallback(text, level=level)


def _localize_public_message(message: Any, language: str, level: str = "") -> str:
    text = _redact_secrets(message).strip()
    if _clean_ui_language(language) == "en":
        return _to_english_log_message(text, level=level)
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
    ui_language = _clean_ui_language(task.get("ui_language"))
    public_logs: list[dict[str, Any]] = []
    for entry in logs[-_MAX_PERSISTED_LOGS:]:
        sanitized = _sanitize_log_entry(entry)
        if sanitized:
            if "message" in sanitized:
                sanitized["message"] = _localize_public_message(
                    sanitized["message"],
                    ui_language,
                    level=str(sanitized.get("level") or sanitized.get("type") or "info"),
                )
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


_HISTORY_DISPLAY_TIME_FIELDS = ("started_at", "created_at", "finished_at", "updated_at")


def _history_basename(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return Path(text).name


def _history_display_name(item: dict[str, Any]) -> str:
    input_name = _history_basename(item.get("input_value"))
    if input_name:
        return input_name
    for field in ("output_dir", "run_id", "result_id", "name", "history_id", "task_id"):
        value = _history_basename(item.get(field))
        if value:
            return value
    return ""


def _history_run_label(item: dict[str, Any]) -> str:
    for field in ("output_dir", "run_id", "result_id", "name", "history_id", "project_key"):
        value = _history_basename(item.get(field))
        if value:
            return value
    return _history_display_name(item)


def _history_time_label(item: dict[str, Any]) -> str:
    for field in _HISTORY_DISPLAY_TIME_FIELDS:
        if item.get(field):
            return field
    return "history_time" if item.get("history_time") else ""


def _history_duration_seconds(item: dict[str, Any]) -> int | None:
    started = _parse_history_timestamp(item.get("started_at") or item.get("created_at"))
    finished = _parse_history_timestamp(item.get("finished_at") or item.get("updated_at"))
    if started is None or finished is None or finished < started:
        return None
    return int(finished - started)


def _history_status_group(status: Any) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in _ACTIVE_STATUSES:
        return "active"
    if normalized == "completed":
        return "success"
    if normalized == "blocked":
        return "blocked"
    if normalized == "failed":
        return "failed"
    return "unknown"


def _history_primary_action(item: dict[str, Any]) -> str:
    status = str(item.get("status") or "").strip().lower()
    if status in _ACTIVE_STATUSES:
        return "watch"
    if item.get("can_download"):
        return "download"
    if status in {"failed", "blocked"} or item.get("blocking_issues"):
        return "inspect"
    return "view"


def _decorate_history_item(record: dict[str, Any]) -> dict[str, Any]:
    item = with_history_identity(record)
    run_label = _history_run_label(item)
    if run_label:
        item.setdefault("run_id", run_label)
        item.setdefault("result_id", run_label)
    item["display_name"] = _history_display_name(item) or run_label
    item["run_label"] = run_label or item["display_name"]
    item["time_label"] = _history_time_label(item)
    item["duration_seconds"] = _history_duration_seconds(item)
    item["status_group"] = _history_status_group(item.get("status"))
    item["primary_action"] = _history_primary_action(item)
    return item


def _history_summary(active_tasks: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[str, Any]:
    items = [*active_tasks, *results]
    status_counts = Counter(str(item.get("status") or "unknown") for item in items)
    status_group_counts = Counter(str(item.get("status_group") or _history_status_group(item.get("status"))) for item in items)
    storage_bytes = 0
    for item in items:
        try:
            storage_bytes += int(item.get("size_bytes") or 0)
        except (TypeError, ValueError):
            continue
    return {
        "total": len(items),
        "active": len(active_tasks),
        "results": len(results),
        "downloadable": sum(1 for item in items if item.get("can_download")),
        "storage_bytes": storage_bytes,
        "failed": status_counts.get("failed", 0),
        "blocked": status_counts.get("blocked", 0),
        "interrupted": sum(1 for item in items if item.get("interrupted")),
        "status_counts": dict(status_counts),
        "status_group_counts": dict(status_group_counts),
    }


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


def _max_batch_items() -> int:
    raw = os.getenv("AGENT_MAX_BATCH_ITEMS", "100")
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return 100
    return max(1, parsed)


def _max_batch_jobs() -> int:
    raw = os.getenv("AGENT_MAX_BATCH_JOBS", "4")
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return 4
    return max(1, parsed)


def _batch_root_dir() -> Path:
    return _runs_dir / _BATCHES_DIR_NAME


def _batch_dir(batch_id: str) -> Path:
    return _batch_root_dir() / safe_output_stem(batch_id)


def _batch_manifest_path(batch_id: str) -> Path:
    return _batch_dir(batch_id) / _BATCH_MANIFEST_FILE


def _clean_batch_inputs(body: dict[str, Any]) -> list[str]:
    raw_inputs = body.get("inputs")
    inputs: list[str] = []
    if isinstance(raw_inputs, list):
        inputs.extend(_clean_text(item) for item in raw_inputs)
    else:
        text = _clean_text(body.get("input_text") or body.get("batch_input") or body.get("input_value"))
        inputs.extend(line.strip() for line in text.splitlines())
    return [item for item in inputs if item and not item.startswith("#")]


def _batch_jobs(value: Any, item_count: int) -> int:
    try:
        requested = int(value)
    except (TypeError, ValueError):
        requested = 3
    return max(1, min(requested, item_count, _max_batch_jobs()))


def _batch_item_dir(batch_dir: Path, index: int, input_value: str) -> Path:
    stem = safe_output_stem(input_value) or f"item_{index:03d}"
    return batch_dir / "items" / f"{index:03d}_{stem}"


def _json_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _tail_text_file(path: Path, max_lines: int = 80, max_chars: int = 20000) -> list[str]:
    if not path.exists() or not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if len(text) > max_chars:
        text = text[-max_chars:]
    return [_redact_secrets(line) for line in text.splitlines()[-max_lines:]]


def _append_batch_event_unlocked(
    batch: dict[str, Any],
    level: str,
    message: Any,
    item_index: int | None = None,
) -> None:
    event = {
        "ts": _now_iso(),
        "level": str(level or "info").lower(),
        "message": _redact_secrets(message).strip(),
    }
    if item_index is not None:
        event["item_index"] = item_index
    events = list(batch.get("events") or [])
    events.append(event)
    batch["events"] = events[-500:]
    batch["updated_at"] = event["ts"]


def _append_batch_event(batch_id: str, level: str, message: Any, item_index: int | None = None) -> None:
    with _batches_lock:
        batch = _batches.get(batch_id)
        if batch is None:
            return
        _append_batch_event_unlocked(batch, level, message, item_index=item_index)
        _write_batch_manifest(batch)


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_plain(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _plain(model_dump(mode="json"))
        except TypeError:
            return _plain(model_dump())
    if hasattr(value, "__dict__"):
        return {key: _plain(item) for key, item in vars(value).items() if not key.startswith("_")}
    return str(value)


def _attribute_value(attributes: Any, name: str) -> Any:
    attr = getattr(attributes, name, None)
    if attr is None:
        return None
    return getattr(attr, "value", attr)


def _search_parameter_hints(attributes: Any) -> dict[str, Any]:
    hints = _attribute_value(attributes, "search_parameter_hints")
    return dict(hints) if isinstance(hints, dict) else {}


def _plan_output_path(plan: Any, key: str) -> str:
    outputs = getattr(plan, "output_paths", {}) or {}
    if isinstance(outputs, dict) and outputs.get(key) is not None:
        return str(outputs[key])
    return ""


def _materialize_parameter_workflow(output_dir: Path, attributes: Any, plan: Any) -> Path | None:
    workflow = getattr(plan, "fragpipe_workflow_path", None)
    if workflow is None:
        return None
    source = Path(workflow)
    if not source.exists() or not source.is_file():
        return None
    destination = output_dir / "workflows" / source.name
    try:
        from agent.execution.workflow import materialize_workflow_with_attributes

        materialize_workflow_with_attributes(source, destination, attributes)
    except Exception:
        return None
    return destination


def _rewrite_converter_config_workflow(config_path: Path, workflow_path: Path | None) -> None:
    if workflow_path is None or not config_path.exists():
        return
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(config, dict):
        return
    fragpipe = config.get("generate_fragpipe_search_result")
    if isinstance(fragpipe, dict):
        fragpipe["workflow_path"] = _workspace_container_path(config_path.parent, workflow_path)
    _rewrite_config_paths_for_workspace(config, config_path.parent)
    _json_write(config_path, config)


def _workspace_container_path(root: Path, path: Any) -> str:
    if path in (None, ""):
        return ""
    text = str(path)
    try:
        resolved = Path(text).resolve()
        relative = resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return text
    return f"/workspace/{relative.as_posix()}"


def _rewrite_config_paths_for_workspace(value: Any, root: Path, key: str = "") -> None:
    if isinstance(value, dict):
        for child_key, child in value.items():
            if isinstance(child, str) and child and _looks_like_converter_path_key(str(child_key)):
                value[child_key] = _workspace_container_path(root, child)
            else:
                _rewrite_config_paths_for_workspace(child, root, str(child_key))
    elif isinstance(value, list):
        for item in value:
            _rewrite_config_paths_for_workspace(item, root, key)


def _looks_like_converter_path_key(key: str) -> bool:
    return key in {"data_path", "fasta_path", "workflow_path", "manifest_path", "workdir", "output"} or key.endswith("_path")


def _write_task_runtime_log(task_id: str, output_dir: Path) -> Path:
    log_path = output_dir / "logs" / "runtime.log"
    with _tasks_lock:
        task = dict(_tasks.get(task_id) or {})
    lines: list[str] = []
    for entry in _public_logs_from_task(task):
        level = str(entry.get("level") or "info").upper()
        ts = str(entry.get("ts") or "")
        message = _redact_secrets(entry.get("message") or "")
        lines.append(f"{ts}\t{level}\t{message}".strip())
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return log_path


def _write_parameter_audit_files(output_dir: Path, batch_id: str, index: int, input_value: str, result: Any) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    resolution = getattr(result, "resolution", None)
    primary = getattr(resolution, "primary_project", None)
    context = getattr(result, "context", None)
    attributes = getattr(result, "attributes", None)
    plan = getattr(result, "plan", None)
    asset = getattr(result, "asset", None)
    hints = _search_parameter_hints(attributes)
    asset_payload: dict[str, Any] = {}
    try:
        loaded_asset = json.loads((output_dir / "asset_resolution.json").read_text(encoding="utf-8"))
        if isinstance(loaded_asset, dict):
            asset_payload = loaded_asset
    except (OSError, json.JSONDecodeError):
        asset_payload = {}

    def asset_field(name: str) -> Any:
        value = getattr(asset, name, None)
        return value if value not in (None, "") else asset_payload.get(name)

    def first_field(*values: Any) -> Any:
        for value in values:
            if value not in (None, ""):
                return value
        return None

    repository = _clean_repository(
        first_field(
            getattr(context, "repository", None),
            getattr(primary, "repository", None),
            asset_field("repository"),
            asset_payload.get("repository"),
        )
    )

    materialized_workflow = _materialize_parameter_workflow(output_dir, attributes, plan) if attributes is not None and plan is not None else None
    converter_config = output_dir / "converter_config.json"
    _rewrite_converter_config_workflow(converter_config, materialized_workflow)

    workflow_template = getattr(plan, "fragpipe_workflow_path", None) if plan is not None else None
    fasta_path = getattr(plan, "fasta_path", None) if plan is not None else None
    fasta_url = getattr(plan, "fasta_download_url", None) if plan is not None else None
    audit = {
        "batch_id": batch_id,
        "index": index,
        "repository": repository,
        "input_value": input_value,
        "generated_at": _now_iso(),
        "project": {
            "repository": repository,
            "accession": getattr(primary, "project_accession", None),
            "native_accession": first_field(getattr(primary, "native_accession", None), getattr(context, "native_accession", None)),
            "px_accession": first_field(getattr(primary, "px_accession", None), getattr(context, "px_accession", None)),
            "matched_file": getattr(primary, "matched_file", None),
            "match_type": getattr(primary, "match_type", None),
            "match_score": getattr(primary, "match_score", None),
            "needs_review": bool(getattr(resolution, "needs_review", False)) if resolution is not None else False,
        },
        "input": {
            "original_file_name": asset_field("original_file_name") or getattr(plan, "source_file_name", None),
            "matched_project_file": asset_field("matched_project_file") or getattr(primary, "matched_file", None),
            "logical_path": asset_field("logical_path"),
            "asset_type": asset_field("resolved_asset_type"),
            "download_url": asset_field("download_url"),
            "download_urls": asset_field("download_urls") or [],
            "transfer_method": asset_field("transfer_method"),
            "expected_size_bytes": asset_field("expected_size_bytes"),
            "requires_conversion": asset_field("requires_conversion"),
        },
        "plan": {
            "source_file_name": getattr(plan, "source_file_name", None),
            "source_data_path": str(getattr(plan, "source_data_path", "")) if plan is not None else "",
            "raw_data_type": getattr(plan, "raw_data_type", None),
            "thread_num": getattr(plan, "thread_num", None),
            "needs_review": bool(getattr(plan, "needs_review", False)) if plan is not None else False,
        },
        "workflow": {
            "name": Path(str(workflow_template)).name if workflow_template else "",
            "template_path": str(workflow_template) if workflow_template else "",
            "materialized_path": str(materialized_workflow) if materialized_workflow else "",
            "parameter_overrides": hints.get("workflow_parameter_overrides")
            or hints.get("fragpipe_workflow_overrides")
            or hints.get("msfragger_parameter_overrides")
            or {},
        },
        "fasta": {
            "name": Path(str(fasta_path)).name if fasta_path else "",
            "path": str(fasta_path) if fasta_path else "",
            "selection_mode": getattr(plan, "fasta_selection_mode", None) if plan is not None else None,
            "download_url": fasta_url or hints.get("recommended_fasta_url") or hints.get("fasta_url"),
        },
        "search_parameters": {
            "acquisition_mode": _attribute_value(attributes, "acquisition_mode"),
            "species": _attribute_value(attributes, "species"),
            "instrument_name": _attribute_value(attributes, "instrument_name"),
            "enzyme": _attribute_value(attributes, "enzyme"),
            "labeling_strategy": _attribute_value(attributes, "labeling_strategy"),
            "fixed_mods": _attribute_value(attributes, "fixed_mods"),
            "variable_mods": _attribute_value(attributes, "variable_mods"),
            "hints": hints,
        },
        "files": {
            "converter_config": str(converter_config),
            "fragpipe_manifest": str(getattr(plan, "manifest_path", "")) if plan is not None else "",
            "decision_trace": str(output_dir / "decision_trace.json"),
            "attributes": str(output_dir / "attributes.json"),
            "asset_resolution": str(output_dir / "asset_resolution.json"),
        },
        "expected_outputs": {
            "rawspectrum": str(getattr(plan, "rawspectrum_output_path", "")) if plan is not None else "",
            "fp_pin": str(getattr(plan, "expected_pin_path", "")) if plan is not None else "",
            "fp_msdt": _plan_output_path(plan, "fp_msdt") if plan is not None else "",
        },
        "blocking_issues": list(getattr(plan, "blocking_issues", []) or []) if plan is not None else [],
    }
    audit = _plain(audit)
    _json_write(output_dir / "parameter_audit.json", audit)
    manifest = {
        "package_type": "parameter_only_msdt_input_preview",
        "generated_at": _now_iso(),
        "repository": audit.get("repository"),
        "input_file": audit.get("input", {}).get("original_file_name"),
        "project_accession": audit.get("project", {}).get("accession"),
        "run_without_full_execution": True,
        "note": (
            "This package contains the planned MSDT-Converter configuration and audit files. "
            "RAW/mzML data and FASTA sequences are not downloaded in parameter-only mode."
        ),
        "msdt_converter_inputs": {
            "converter_config": "converter_config.json",
            "workflow": _relative_package_path(output_dir, materialized_workflow) if materialized_workflow else "",
            "source_data_path_expected": audit.get("plan", {}).get("source_data_path", ""),
            "fasta_path_expected": audit.get("fasta", {}).get("path", ""),
            "fasta_download_url": audit.get("fasta", {}).get("download_url", ""),
            "fragpipe_manifest_expected": audit.get("files", {}).get("fragpipe_manifest", ""),
        },
        "audit_files": [
            "project_resolution.json",
            "metadata.json",
            "asset_resolution.json",
            "attributes.json",
            "decision_trace.json",
            "parameter_audit.json",
            "task_state.json",
            "logs/runtime.log",
        ],
    }
    _json_write(output_dir / "msdt_input_manifest.json", _plain(manifest))
    return audit


def _relative_package_path(root: Path, path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _batch_audit_zip_path(batch: dict[str, Any]) -> Path:
    return Path(batch.get("output_dir", "")) / _BATCH_AUDIT_ZIP_NAME


def _include_batch_audit_file(root: Path, file: Path) -> bool:
    try:
        rel = file.relative_to(root)
    except ValueError:
        return False
    parts = {part.lower() for part in rel.parts}
    if file.name == _BATCH_AUDIT_ZIP_NAME:
        return False
    if {"downloads", "prepared", "input", "fasta"} & parts:
        return False
    if file.suffix.lower() in {".raw", ".mzml", ".mzxml", ".wiff", ".scan", ".d", ".fasta", ".fa", ".fas", ".gz", ".zip"}:
        return False
    return True


def _ensure_batch_audit_zip(batch: dict[str, Any]) -> Path | None:
    root = Path(batch.get("output_dir", ""))
    if not root.exists() or not root.is_dir():
        return None
    zip_path = root / _BATCH_AUDIT_ZIP_NAME
    files = [file for file in root.rglob("*") if file.is_file() and _include_batch_audit_file(root, file)]
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(files):
            archive.write(file, file.relative_to(root).as_posix())
    return zip_path


def _write_batch_manifest(batch: dict[str, Any]) -> None:
    manifest = {key: value for key, value in batch.items() if key not in {"llm_config"}}
    _json_write(Path(batch["output_dir"]) / _BATCH_MANIFEST_FILE, manifest)


def _public_batch_record(batch: dict[str, Any]) -> dict[str, Any]:
    output_dir = Path(batch.get("output_dir", ""))
    excel_path = output_dir / _BATCH_EXCEL_FILE
    ui_language = _clean_ui_language(batch.get("ui_language"))
    items = [dict(item) for item in batch.get("items") or []]
    for item in items:
        item_dir = Path(item.get("output_dir", ""))
        item["error"] = _localize_public_message(item.get("error", ""), ui_language, level="error")
        item["log_tail"] = [
            _localize_public_message(line, ui_language, level="info")
            for line in _tail_text_file(item_dir / "logs" / "runtime.log", max_lines=40, max_chars=12000)
        ]
        audit_path = item_dir / "parameter_audit.json"
        item["audit_path"] = str(audit_path) if audit_path.exists() else ""
    events = []
    for event in list(batch.get("events") or [])[-500:]:
        public_event = dict(event)
        public_event["message"] = _localize_public_message(
            public_event.get("message", ""),
            ui_language,
            level=str(public_event.get("level") or "info"),
        )
        events.append(public_event)
    return {
        "batch_id": batch.get("batch_id", ""),
        "status": batch.get("status", "unknown"),
        "submitter": batch.get("submitter", ""),
        "created_at": batch.get("created_at"),
        "started_at": batch.get("started_at"),
        "finished_at": batch.get("finished_at"),
        "updated_at": batch.get("updated_at") or batch.get("finished_at") or batch.get("started_at") or batch.get("created_at"),
        "item_count": len(items),
        "completed_items": sum(1 for item in items if item.get("status") == "completed"),
        "failed_items": sum(1 for item in items if item.get("status") == "failed"),
        "needs_review_items": sum(1 for item in items if item.get("status") in {"needs_review", "blocked"}),
        "jobs": batch.get("jobs", 1),
        "ui_language": ui_language,
        "repository": _clean_repository(batch.get("repository")),
        "run_mode": _clean_batch_run_mode(batch.get("run_mode")),
        "resource_policy": _clean_resource_policy(batch.get("resource_policy")),
        "fasta_preference": "project" if batch.get("prefer_project_fasta") else "llm",
        "output_dir": str(output_dir),
        "excel_path": str(excel_path),
        "can_download": batch.get("status") == "completed" and excel_path.exists(),
        "audit_zip_path": str(output_dir / _BATCH_AUDIT_ZIP_NAME),
        "can_download_audit": batch.get("status") in _TERMINAL_STATUSES and output_dir.exists(),
        "items": items,
        "events": events,
        "errors": [_localize_public_message(error, ui_language, level="error") for error in list(batch.get("errors") or [])],
        "interrupted": bool(batch.get("interrupted")),
    }


def _mark_interrupted_batch(batch: dict[str, Any]) -> dict[str, Any]:
    if batch.get("status") not in _ACTIVE_STATUSES:
        return batch
    repaired = dict(batch)
    repaired["status"] = "failed"
    repaired["interrupted"] = True
    repaired["finished_at"] = repaired.get("finished_at") or repaired.get("updated_at") or repaired.get("started_at") or repaired.get("created_at")
    repaired["updated_at"] = repaired.get("updated_at") or repaired.get("finished_at")
    errors = [str(value) for value in repaired.get("errors") or [] if str(value)]
    if _INTERRUPTED_HISTORY_MESSAGE not in errors:
        errors.append(_INTERRUPTED_HISTORY_MESSAGE)
    repaired["errors"] = errors
    return repaired


def _batch_history_record(batch: dict[str, Any]) -> dict[str, Any]:
    public = _public_batch_record(batch)
    batch_id = str(public.get("batch_id") or "").strip()
    output_dir = Path(public.get("output_dir") or "")
    file_count = 0
    size_bytes = 0
    if output_dir.exists():
        file_count, size_bytes, _latest_mtime = _path_file_stats(output_dir)
    public.update(
        {
            "kind": "batch",
            "task_id": f"batch-{batch_id}" if batch_id else "",
            "project_key": f"batch-{batch_id}" if batch_id else "batch",
            "history_id": f"batch-{batch_id}" if batch_id else "",
            "run_id": output_dir.name if output_dir.name else batch_id,
            "result_id": batch_id,
            "name": "Batch Excel report",
            "input_value": "Batch Excel report",
            "run_mode": _clean_batch_run_mode(batch.get("run_mode")),
            "file_count": file_count,
            "size_bytes": size_bytes,
        }
    )
    return _decorate_history_item(public)


def _load_batch_from_disk(batch_id: str) -> dict[str, Any] | None:
    manifest_path = _batch_manifest_path(batch_id)
    if not manifest_path.exists():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _list_parameter_batch_history_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    with _batches_lock:
        memory_batches = [dict(batch) for batch in _batches.values()]
    for batch in memory_batches:
        batch_id = str(batch.get("batch_id") or "").strip()
        if not batch_id:
            continue
        seen.add(batch_id)
        records.append(_batch_history_record(batch))

    batch_root = _batch_root_dir()
    if not batch_root.exists() or not batch_root.is_dir():
        return records
    for batch_dir in batch_root.iterdir():
        if not batch_dir.is_dir():
            continue
        batch_id = batch_dir.name
        if batch_id in seen:
            continue
        batch = _load_batch_from_disk(batch_id)
        if batch is None:
            continue
        if batch.get("status") in _ACTIVE_STATUSES:
            batch = _mark_interrupted_batch(batch)
            _write_batch_manifest(batch)
        seen.add(batch_id)
        records.append(_batch_history_record(batch))
    return records


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


def _project_key_for_input(input_value: str) -> str:
    return safe_output_stem(input_value)


def _history_output_names_for_project(project_key: str) -> set[str]:
    names: set[str] = set()
    if not project_key:
        return names
    for record in _read_history_index():
        item = with_history_identity(record)
        if str(item.get("project_key") or "") != project_key:
            continue
        for field in ("output_dir", "result_id", "history_id", "run_id", "name"):
            value = str(item.get(field) or "").strip()
            if value:
                names.add(Path(value).name)
    return names


def _next_output_dir_locked(project_key: str, task_id: str) -> Path:
    base = safe_output_stem(project_key) or safe_output_stem(task_id)
    used = _history_output_names_for_project(base)
    used.update(path.name for path in _active_output_dirs_locked())
    if (_runs_dir / base).exists():
        used.add(base)
    if base not in used:
        return _runs_dir / base

    timestamp = datetime.now(_APP_TZ).strftime("%Y%m%d-%H%M%S")
    task_suffix = safe_output_stem(task_id)[:8] or uuid.uuid4().hex[:8]
    stem = f"{base}__{timestamp}__{task_suffix}"
    candidate = stem
    counter = 2
    while candidate in used or (_runs_dir / candidate).exists():
        candidate = f"{stem}-{counter}"
        counter += 1
    return _runs_dir / candidate


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
        "project_key": task.get("project_key") or _project_key_for_input(str(task.get("input_value", ""))),
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
        "run_id": Path(output_dir).name if output_dir else "",
        "log_count": len(logs),
        "blocking_issues": list(task.get("blocking_issues") or []),
        "error_summary": task.get("error_summary"),
        "review_summary": task.get("review_summary"),
        "fasta_preference": "project" if task.get("prefer_project_fasta") else "llm",
        "run_mode": _clean_run_mode(task.get("run_mode")),
        "resource_policy": _clean_resource_policy(task.get("resource_policy")),
        "ui_language": _clean_ui_language(task.get("ui_language")),
        "repository": _clean_repository(task.get("repository")),
        "can_download": can_download,
    }
    if include_logs:
        record["logs"] = logs
    return _decorate_history_item(record)


def _history_index_path() -> Path:
    return _runs_dir / _HISTORY_INDEX_FILE


def _history_index_backup_path() -> Path:
    return _runs_dir / f"{_HISTORY_INDEX_FILE}.bak"


def _is_legacy_batches_history_record(record: dict[str, Any]) -> bool:
    names = {
        str(record.get("task_id") or ""),
        str(record.get("input_value") or ""),
        _identity_name(record.get("output_dir")),
        _identity_name(record.get("history_id")),
        _identity_name(record.get("run_id")),
        _identity_name(record.get("result_id")),
        _identity_name(record.get("name")),
        str(record.get("project_key") or ""),
    }
    names.discard("")
    return _BATCHES_DIR_NAME in names or names == {"batches"}


def _read_history_index_file(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict) and not _is_legacy_batches_history_record(item)]


def _read_history_index() -> list[dict[str, Any]]:
    records = _read_history_index_file(_history_index_path())
    if records:
        return records
    return _read_history_index_file(_history_index_backup_path())


def _upsert_history_index(record: dict[str, Any]) -> None:
    indexed_record = with_history_identity(record)
    if not indexed_record.get("project_key"):
        return
    records = merge_project_history_records([*_read_history_index(), indexed_record], limit=200)
    _write_history_index(records)


def _write_history_index(records: list[dict[str, Any]]) -> None:
    try:
        _runs_dir.mkdir(parents=True, exist_ok=True)
        cleaned = [with_history_identity(record) for record in records if not _is_legacy_batches_history_record(record)]
        payload = json.dumps(cleaned, indent=2, ensure_ascii=False)
        path = _history_index_path()
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, path)
        _history_index_backup_path().write_text(payload, encoding="utf-8")
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


def _identity_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return Path(text).name


def _active_history_identity_sets_locked() -> tuple[set[str], set[str]]:
    task_ids: set[str] = set()
    history_ids: set[str] = set()
    for task_id, task in _tasks.items():
        if task.get("status") not in _ACTIVE_STATUSES:
            continue
        task_ids.add(str(task_id))
        task_ids.add(str(task.get("task_id") or ""))
        for field in ("history_id", "run_id", "result_id", "name", "output_dir"):
            name = _identity_name(task.get(field))
            if name:
                history_ids.add(name)
    task_ids.discard("")
    history_ids.discard("")
    return task_ids, history_ids


def _history_item_is_active(item: dict[str, Any], active_task_ids: set[str], active_history_ids: set[str]) -> bool:
    task_ids = {str(item.get("task_id") or ""), *(str(value or "") for value in item.get("task_ids") or [])}
    history_ids = {_identity_name(item.get(field)) for field in ("history_id", "output_dir", "run_id", "result_id", "name")}
    task_ids.discard("")
    history_ids.discard("")
    return bool(task_ids & active_task_ids or history_ids & active_history_ids)


def _mark_interrupted_history_item(item: dict[str, Any]) -> dict[str, Any]:
    if item.get("status") not in _ACTIVE_STATUSES:
        return item
    repaired = dict(item)
    repaired["status"] = "failed"
    repaired["interrupted"] = True
    repaired["finished_at"] = repaired.get("finished_at") or repaired.get("updated_at") or repaired.get("started_at") or repaired.get("created_at")
    repaired["updated_at"] = repaired.get("updated_at") or repaired.get("finished_at")
    issues = [str(value) for value in repaired.get("blocking_issues") or [] if str(value)]
    if _INTERRUPTED_HISTORY_MESSAGE not in issues:
        issues.append(_INTERRUPTED_HISTORY_MESSAGE)
    repaired["blocking_issues"] = issues
    return with_history_identity(repaired)


def _repair_interrupted_history_index() -> None:
    with _tasks_lock:
        active_task_ids, active_history_ids = _active_history_identity_sets_locked()
    repaired: list[dict[str, Any]] = []
    changed = False
    for record in _read_history_index():
        item = with_history_identity(record)
        if item.get("status") in _ACTIVE_STATUSES and not _history_item_is_active(item, active_task_ids, active_history_ids):
            item = _mark_interrupted_history_item(item)
            changed = True
        repaired.append(item)
    if changed:
        _write_history_index(merge_project_history_records(repaired, limit=200))


def _disk_task_history_records() -> list[dict[str, Any]]:
    if not _runs_dir.exists():
        return []
    records: list[dict[str, Any]] = []
    for run_dir in _runs_dir.iterdir():
        if not run_dir.is_dir() or run_dir.name == _BATCHES_DIR_NAME:
            continue
        history = _read_public_history(run_dir)
        if not history:
            continue
        history.setdefault("output_dir", run_dir.name)
        records.append(_decorate_history_item(history))
    return records


def _sync_history_index_from_disk() -> None:
    records = [*_read_history_index(), *_disk_task_history_records()]
    for batch in _list_parameter_batch_history_records():
        if batch.get("status") in _ACTIVE_STATUSES:
            continue
        records.append(batch)
    if not records:
        return
    _write_history_index(merge_project_history_records(records, limit=200))


def _find_history_record(task_id: str) -> dict[str, Any] | None:
    needle = str(task_id or "")
    for record in reversed(_read_history_index()):
        item = with_history_identity(record)
        aliases = {
            str(item.get("task_id") or ""),
            str(item.get("output_dir") or ""),
            str(item.get("history_id") or ""),
            str(item.get("run_id") or ""),
            str(item.get("result_id") or ""),
            str(item.get("name") or ""),
        }
        aliases.update(str(value or "") for value in item.get("task_ids") or [])
        if needle in aliases:
            return item
    if not _runs_dir.exists():
        return None
    for run_dir in _runs_dir.iterdir():
        if not run_dir.is_dir():
            continue
        record = _read_public_history(run_dir)
        if not record:
            continue
        item = with_history_identity({**record, "output_dir": record.get("output_dir") or run_dir.name})
        aliases = {
            str(item.get("task_id") or ""),
            str(item.get("output_dir") or run_dir.name),
            str(item.get("history_id") or ""),
            str(item.get("run_id") or ""),
            str(item.get("result_id") or ""),
            str(item.get("name") or ""),
        }
        aliases.update(str(value or "") for value in item.get("task_ids") or [])
        if needle in aliases:
            return item
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
    record = with_history_identity(record)
    output_dir_name = str(record.get("output_dir") or "")
    output_dir = _runs_dir / output_dir_name if output_dir_name else None
    can_download = bool(
        record.get("status") == "completed"
        and output_dir
        and output_dir.exists()
        and _is_download_zip_ready(output_dir)
    )
    logs = _public_logs_from_history(record)
    detail = {
        "task_id": str(record.get("task_id") or task_id),
        "input_value": record.get("input_value", ""),
        "submitter": record.get("submitter", "未填写"),
        "status": record.get("status", "unknown"),
        "created_at": record.get("created_at"),
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
        "updated_at": record.get("updated_at") or record.get("finished_at") or record.get("started_at") or record.get("created_at"),
        "history_time": record.get("history_time"),
        "project_key": record.get("project_key"),
        "task_ids": record.get("task_ids") or [],
        "step": record.get("step", 5 if record.get("status") == "completed" else 0),
        "total_steps": record.get("total_steps", 5),
        "log_count": len(logs),
        "logs": logs,
        "blocking_issues": list(record.get("blocking_issues") or []),
        "error_summary": record.get("error_summary"),
        "review_summary": record.get("review_summary"),
        "fasta_preference": record.get("fasta_preference", "llm"),
        "run_mode": _clean_run_mode(record.get("run_mode")),
        "resource_policy": _clean_resource_policy(record.get("resource_policy")),
        "ui_language": _clean_ui_language(record.get("ui_language")),
        "repository": _clean_repository(record.get("repository")),
        "can_download": can_download,
        "archived": True,
        "queue_position": 0,
        "queue_length": 0,
        "queued_tasks": 0,
    }
    return _decorate_history_item(detail)


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
        if run_dir.name == _BATCHES_DIR_NAME:
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
        file_updated_at = datetime.fromtimestamp(latest_mtime, _APP_TZ).isoformat()
        result_updated_at = datetime.fromtimestamp(updated_at_ts, _APP_TZ).isoformat()
        expires_at_ts = retention_start + retention
        results.append(
            {
                "result_id": run_dir.name,
                "task_id": history.get("task_id", ""),
                "name": run_dir.name,
                "input_value": history.get("input_value", run_dir.name),
                "project_key": history.get("project_key") or _project_key_for_input(str(history.get("input_value", run_dir.name))),
                "run_id": history.get("run_id") or run_dir.name,
                "history_id": history.get("history_id") or run_dir.name,
                "submitter": history.get("submitter", "未填写"),
                "status": status,
                "path": str(run_dir),
                "file_count": file_count,
                "size_bytes": size_bytes,
                "created_at": history.get("created_at"),
                "started_at": history.get("started_at"),
                "finished_at": history.get("finished_at"),
                "task_updated_at": history.get("updated_at"),
                "run_mode": _clean_run_mode(history.get("run_mode")),
                "ui_language": _clean_ui_language(history.get("ui_language")),
                "repository": _clean_repository(history.get("repository")),
                "file_updated_at": file_updated_at,
                "result_updated_at": result_updated_at,
                "updated_at": result_updated_at,
                "expires_at": datetime.fromtimestamp(expires_at_ts, _APP_TZ).isoformat(),
                "expires_in_seconds": max(0, int(expires_at_ts - now)),
                "can_download": status == "completed" and _is_download_zip_ready(run_dir),
            }
        )
    results = [_decorate_history_item(item) for item in results]
    results.sort(key=lambda item: item["updated_at"], reverse=True)
    return results


def _list_project_history_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    _repair_interrupted_history_index()
    with _tasks_lock:
        active_task_ids, active_history_ids = _active_history_identity_sets_locked()
    for record in _read_history_index():
        item = with_history_identity(record)
        if not item.get("project_key"):
            continue
        if item.get("status") in _ACTIVE_STATUSES and not _history_item_is_active(item, active_task_ids, active_history_ids):
            item = _mark_interrupted_history_item(item)
        output_dir_name = item.get("output_dir")
        output_dir = _runs_dir / str(output_dir_name) if output_dir_name else None
        if output_dir_name and not item.get("result_id"):
            item["result_id"] = str(output_dir_name)
        if output_dir_name and not item.get("run_id"):
            item["run_id"] = str(output_dir_name)
        file_count = 0
        size_bytes = 0
        if output_dir and output_dir.exists():
            file_count, size_bytes, latest_mtime = _path_file_stats(
                output_dir,
                excluded_names={_PUBLIC_HISTORY_FILE, _HISTORY_INDEX_FILE},
                excluded_dir_names={_DOWNLOAD_CACHE_DIR},
            )
            if latest_mtime:
                item["file_updated_at"] = datetime.fromtimestamp(latest_mtime, _APP_TZ).isoformat()
        item["file_count"] = file_count
        item["size_bytes"] = size_bytes
        item["can_download"] = bool(item.get("status") == "completed" and output_dir and output_dir.exists() and _is_download_zip_ready(output_dir))
        records.append(_decorate_history_item(item))

    for result in _list_public_results():
        task_updated_at = result.get("task_updated_at") or result.get("finished_at") or result.get("started_at") or result.get("created_at")
        if task_updated_at:
            result["updated_at"] = task_updated_at
        if result.get("status") in _ACTIVE_STATUSES and not _history_item_is_active(result, active_task_ids, active_history_ids):
            result = _mark_interrupted_history_item(result)
        records.append(_decorate_history_item(result))

    for batch in _list_parameter_batch_history_records():
        if batch.get("status") in _ACTIVE_STATUSES:
            continue
        records.append(_decorate_history_item(batch))

    with _tasks_lock:
        for task_id, task in _tasks.items():
            if task.get("status") in _ACTIVE_STATUSES:
                continue
            records.append(_public_task_record_locked(task_id, task))

    items = [_decorate_history_item(item) for item in merge_project_history_records(records)]
    for item in items:
        item.pop("logs", None)
    items.sort(key=lambda item: (history_timestamp(item), str(item.get("history_time") or "")), reverse=True)
    return items


def _cleanup_expired_results() -> list[str]:
    if not _runs_dir.exists():
        return []
    with _tasks_lock:
        active_dirs = _active_output_dirs_locked()
        has_active_tasks = any(task.get("status") in _ACTIVE_STATUSES for task in _tasks.values())
    with _batches_lock:
        has_active_batches = any(batch.get("status") in _ACTIVE_STATUSES for batch in _batches.values())
    now = time.time()
    retention = _result_retention_seconds()
    candidates: list[tuple[float, Path]] = []
    removed: list[str] = []
    for run_dir in _runs_dir.iterdir():
        if not run_dir.is_dir():
            continue
        if run_dir.name == _BATCHES_DIR_NAME:
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
    if not has_active_tasks and not has_active_batches:
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
    if first == "fragpipe" and len(relative.parts) == 2:
        if file.name in _DOWNLOAD_FRAGPIPE_PARAMETER_FILES or file.suffix.lower() == ".workflow":
            return True
    if first == "workflows" and len(relative.parts) == 2 and file.suffix.lower() == ".workflow":
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


def _zip_contains_download_files(zip_path: Path, output_dir: Path, files: list[Path]) -> bool:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile):
        return False
    return all(file.relative_to(output_dir).as_posix() in names for file in files)


def _is_download_zip_ready(output_dir: Path) -> bool:
    zip_path = _download_zip_path(output_dir)
    try:
        files = _download_result_files(output_dir)
        source_mtime = _download_source_mtime(output_dir, files)
        return (
            source_mtime > 0
            and zip_path.exists()
            and zip_path.is_file()
            and zip_path.stat().st_size > 0
            and zip_path.stat().st_mtime >= source_mtime
            and _zip_contains_download_files(zip_path, output_dir, files)
        )
    except OSError:
        return False


def _ensure_existing_download_zip_ready(output_dir: Path) -> bool:
    if _is_download_zip_ready(output_dir):
        return True
    zip_path = _download_zip_path(output_dir)
    if not zip_path.exists():
        return False
    try:
        _zip_output_dir(output_dir)
    except OSError:
        return False
    return _is_download_zip_ready(output_dir)


def _zip_output_dir(output_dir: Path, report: Callable[[str], None] | None = None) -> Path:
    files = _download_result_files(output_dir)
    source_mtime = _download_source_mtime(output_dir, files)
    zip_path = _download_zip_path(output_dir)
    try:
        if files and zip_path.exists() and zip_path.stat().st_mtime >= source_mtime and _zip_contains_download_files(zip_path, output_dir, files):
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


def _normalized_fasta_hint_item(key: str, plan: Any) -> tuple[Any, str, float | None] | None:
    if key == "recommended_fasta_name":
        fasta_name = _path_name(getattr(plan, "fasta_path", None))
        return (fasta_name, "plan", None) if fasta_name else None
    if key == "recommended_fasta_url":
        fasta_url = getattr(plan, "fasta_download_url", None)
        return (fasta_url, "plan", None) if fasta_url else None
    if key == "recommended_fasta_source":
        fasta_url = str(getattr(plan, "fasta_download_url", "") or "")
        if "uniprot.org" in fasta_url.lower():
            return "UniProt", "plan", None
    return None


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
            "workflow_parameter_overrides",
            "recommended_fasta_name",
            "recommended_fasta_url",
            "recommended_fasta_source",
        ):
            if key in hints:
                normalized = _normalized_fasta_hint_item(key, plan)
                if normalized is None:
                    value, source, confidence = hints[key], hint_source, hint_confidence
                else:
                    value, source, confidence = normalized
                _append_review_item(items, key, value, source=source, confidence=confidence)

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
    return await _check_llm_api(config)


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
        "full_workflow_enabled": _full_workflow_enabled(),
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
        "full_workflow_enabled": _full_workflow_enabled(),
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
    _sync_history_index_from_disk()
    removed = _cleanup_expired_results()
    if removed:
        _sync_history_index_from_disk()
    with _tasks_lock:
        active_tasks = [
            _public_task_record_locked(task_id, task)
            for task_id, task in _tasks.items()
            if task.get("status") in _ACTIVE_STATUSES
        ]
    active_tasks.extend(batch for batch in _list_parameter_batch_history_records() if batch.get("status") in _ACTIVE_STATUSES)
    active_tasks.sort(key=lambda item: str(item.get("created_at") or ""))
    active_task_ids = {str(item.get("task_id") or "") for item in active_tasks}
    active_history_ids = {str(item.get("history_id") or "") for item in active_tasks}
    active_history_ids.update(str(item.get("output_dir") or "") for item in active_tasks)
    active_history_ids.update(str(item.get("run_id") or "") for item in active_tasks)
    active_task_ids.discard("")
    active_history_ids.discard("")
    results = []
    for item in _list_project_history_records():
        task_ids = {str(item.get("task_id") or ""), *(str(value or "") for value in item.get("task_ids") or [])}
        history_ids = {
            str(item.get("history_id") or ""),
            str(item.get("output_dir") or ""),
            str(item.get("run_id") or ""),
            str(item.get("result_id") or ""),
            str(item.get("name") or ""),
        }
        task_ids.discard("")
        history_ids.discard("")
        if task_ids & active_task_ids or history_ids & active_history_ids:
            continue
        results.append(item)
    summary = _history_summary(active_tasks, results)
    return {
        "retention_seconds": _result_retention_seconds(),
        "max_result_projects": _max_result_projects(),
        "removed": removed,
        "summary": summary,
        "active_tasks": active_tasks,
        "results": results,
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

    if not _ensure_existing_download_zip_ready(output_dir):
        return {"error": "结果 ZIP 尚未打包完成，请等待任务日志提示后再下载。"}
    zip_path = _download_zip_path(output_dir)
    return FileResponse(
        path=str(zip_path),
        filename=f"{result_id}_results.zip",
        media_type="application/zip",
    )


# ── 创建任务 ──────────────────────────────────────────────────────
class BatchFileReporter:
    def __init__(self, output_dir: Path, ui_language: str = "en") -> None:
        self.path = output_dir / "logs" / "runtime.log"
        self.ui_language = _clean_ui_language(ui_language)
        self._lock = threading.Lock()

    def __call__(self, message: Any) -> None:
        if isinstance(message, dict):
            text = json.dumps(message, ensure_ascii=False, default=str)
        else:
            text = _redact_secrets(message)
        text = _localize_public_message(text, self.ui_language, level="info")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(text + "\n")


def _update_batch_item(batch_id: str, index: int, **fields: Any) -> None:
    with _batches_lock:
        batch = _batches.get(batch_id)
        if batch is None:
            return
        items = batch.get("items") or []
        if index < 0 or index >= len(items):
            return
        items[index].update(fields)
        batch["updated_at"] = _now_iso()
        _write_batch_manifest(batch)


def _write_batch_item_error(output_dir: Path, input_value: str, exc: BaseException) -> str:
    from agent.audit.review import build_task_state_snapshot, write_task_state
    from agent.errors import build_error_record, write_error_record
    from agent.input.normalizer import normalize_input

    output_dir.mkdir(parents=True, exist_ok=True)
    task_id = ""
    source_file = Path(input_value).name
    try:
        task = normalize_input(input_value)
        task_id = task.task_id
        source_file = task.file_name
    except Exception:
        task_id = f"batch-{safe_output_stem(input_value)}"
    error = build_error_record(exc, stage="planning", input_file=input_value)
    write_error_record(output_dir / "error.json", error)
    public_message = str(error.get("public_message") or error.get("message") or exc)
    write_task_state(
        output_dir / "task_state.json",
        build_task_state_snapshot(
            task_id=task_id,
            status="failed",
            stage="planning",
            source_file=source_file,
            project_accession=None,
            notes=[public_message],
        ),
    )
    return public_message


def _primary_project_error(result: Any) -> str:
    resolution = getattr(result, "resolution", None)
    primary = getattr(resolution, "primary_project", None)
    if primary is None:
        return "No exact PRIDE project match found."
    match_type = str(getattr(primary, "match_type", "") or "")
    try:
        match_score = int(getattr(primary, "match_score", 0) or 0)
    except (TypeError, ValueError):
        match_score = 0
    if match_type not in {"exact", "stem"} or match_score < 90:
        return (
            f"Non-exact PRIDE project match: {getattr(primary, 'project_accession', 'unknown')}, "
            f"match_type={match_type}, score={match_score}, matched_file={getattr(primary, 'matched_file', '')}"
        )
    if bool(getattr(resolution, "needs_review", False)):
        return f"Ambiguous PRIDE project match: {getattr(resolution, 'resolution_reason', 'manual review required')}"
    return ""


def _path_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def _cleanup_batch_instrument_probe_files(output_dir: Path, result: Any, prepared_path: Path | None = None) -> None:
    if str(os.getenv("AGENT_BATCH_KEEP_INSTRUMENT_PROBE_FILES", "")).strip().lower() in {"1", "true", "yes", "on"}:
        return
    assets_dir = output_dir / "assets"
    allowed_roots = [assets_dir / "downloads", assets_dir / "prepared"]
    asset = getattr(result, "asset", None)
    candidates: list[Path] = []
    for raw_path in (
        prepared_path,
        getattr(asset, "prepared_path", None),
        getattr(asset, "local_path", None),
    ):
        if raw_path:
            candidates.append(Path(raw_path))
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate.resolve(strict=False))
        if key in seen:
            continue
        seen.add(key)
        if not any(_path_within(candidate, root) for root in allowed_roots):
            continue
        try:
            if candidate.is_file():
                candidate.unlink()
            elif candidate.is_dir():
                shutil.rmtree(candidate)
        except OSError:
            pass


def _run_parameter_batch_item(batch_id: str, index: int) -> dict[str, Any]:
    with _batches_lock:
        batch = _batches[batch_id]
        item = dict(batch["items"][index])
        llm_config = dict(batch["llm_config"])
        prefer_project_fasta = bool(batch.get("prefer_project_fasta"))
        ui_language = _clean_ui_language(batch.get("ui_language"))
        repository = _clean_repository(batch.get("repository"))
        run_mode = _clean_batch_run_mode(batch.get("run_mode"))

    input_value = str(item["input"])
    output_dir = Path(item["output_dir"])
    _update_batch_item(batch_id, index, status="running", started_at=_now_iso(), error="")
    _append_batch_event(batch_id, "info", f"Started {input_value}", item_index=index)
    service = None
    try:
        from agent.input.normalizer import normalize_input
        from agent.orchestrator.pipeline import AgentService

        reporter = BatchFileReporter(output_dir, ui_language=ui_language)
        service = AgentService(reporter=reporter, llm_reasoner=_task_llm_reasoner(llm_config))
        task = normalize_input(input_value)
        if run_mode in {_RUN_MODE_PREPARE, _RUN_MODE_FULL}:
            bundle, result, prepared_path = service.prepare_repository_msdt_docker_input(
                task=task,
                output_dir=output_dir,
                repository=repository,
                prefer_project_fasta=prefer_project_fasta,
            )
            _write_parameter_audit_files(output_dir, batch_id, index, input_value, result)
            project_error = _primary_project_error(result)
            if project_error:
                raise RuntimeError(project_error)
            if run_mode == _RUN_MODE_FULL:
                from agent.audit.review import build_task_state_snapshot, write_task_state
                from agent.execution.outputs import execution_failure_reasons
                from agent.msdt_converter.docker_runner import DockerMSDTConverterRunner

                _append_batch_event(batch_id, "info", f"{input_value} running MSDT-Converter Docker.", item_index=index)
                docker_runner = DockerMSDTConverterRunner(image="guomics2017/msdt-converter:v1.3", report=reporter)
                docker_result = docker_runner.run(bundle)
                failure_reasons = execution_failure_reasons(
                    bundle.plan,
                    docker_result.returncode,
                    docker_result.stdout,
                    docker_result.stderr,
                )
                if failure_reasons:
                    raise RuntimeError("; ".join(failure_reasons))
                project_accession = result.resolution.primary_project.project_accession if result.resolution.primary_project else None
                write_task_state(
                    output_dir / "task_state.json",
                    build_task_state_snapshot(
                        task_id=task.task_id,
                        status="completed",
                        stage="execution",
                        source_file=task.file_name,
                        project_accession=project_accession,
                        notes=[],
                    ),
                )
                _zip_output_dir(output_dir, report=reporter)
            else:
                from agent.audit.review import build_task_state_snapshot, write_task_state

                project_accession = result.resolution.primary_project.project_accession if result.resolution.primary_project else None
                write_task_state(
                    output_dir / "task_state.json",
                    build_task_state_snapshot(
                        task_id=task.task_id,
                        status="completed",
                        stage="packaging",
                        source_file=task.file_name,
                        project_accession=project_accession,
                        notes=["Prepare input package mode completed; Docker execution was not run."],
                    ),
                )
            _update_batch_item(batch_id, index, status="completed", finished_at=_now_iso(), error="", run_mode=run_mode)
            _append_batch_event(batch_id, "info", f"{input_value} {run_mode} completed", item_index=index)
            return {"status": "completed", "error": ""}

        result = service.plan_dda_run_from_repository(
            task=task,
            output_dir=output_dir,
            repository=repository,
            prefer_project_fasta=prefer_project_fasta,
        )
        if (
            bool(getattr(result.plan, "needs_review", False))
            and callable(getattr(service, "_can_retry_with_mzml_instrument", None))
            and service._can_retry_with_mzml_instrument(result.plan)
        ):
            prepared_path: Path | None = None
            _append_batch_event(
                batch_id,
                "warning",
                f"{input_value} needs file-level instrument; downloading/converting mzML probe.",
                item_index=index,
            )
            try:
                prepared_path = service.prepare_asset(result.asset)
                result = service.replan_with_mzml_instrument(
                    result,
                    prepared_path,
                    task,
                    output_dir,
                    prefer_project_fasta=prefer_project_fasta,
                )
                _append_batch_event(
                    batch_id,
                    "info",
                    f"{input_value} mzML instrument probe completed.",
                    item_index=index,
                )
            except Exception as probe_exc:
                reporter(f"mzML instrument probe failed; keeping needs_review. Reason: {probe_exc}")
                _append_batch_event(
                    batch_id,
                    "warning",
                    f"{input_value} mzML instrument probe failed: {probe_exc}",
                    item_index=index,
                )
            finally:
                _cleanup_batch_instrument_probe_files(output_dir, result, prepared_path)
        service.write_task_bundle(output_dir, result.resolution, result.context, result.attributes, result.plan, asset=result.asset)
        _write_parameter_audit_files(output_dir, batch_id, index, input_value, result)
        project_error = _primary_project_error(result)
        if project_error:
            raise RuntimeError(project_error)
        status = "needs_review" if bool(getattr(result.plan, "needs_review", False)) else "completed"
        error = "; ".join(str(issue) for issue in getattr(result.plan, "blocking_issues", []) or [])
        _update_batch_item(batch_id, index, status=status, finished_at=_now_iso(), error=error)
        level = "warning" if status == "needs_review" else "info"
        _append_batch_event(batch_id, level, f"{input_value} {status}", item_index=index)
        return {"status": status, "error": error}
    except Exception as exc:
        message = _write_batch_item_error(output_dir, input_value, exc)
        _update_batch_item(batch_id, index, status="failed", finished_at=_now_iso(), error=message)
        _append_batch_event(batch_id, "error", f"{input_value} failed: {message}", item_index=index)
        return {"status": "failed", "error": message}
    finally:
        if service is not None:
            pride_client = getattr(service, "pride_client", None)
            close = getattr(pride_client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


def _run_parameter_batch(batch_id: str) -> None:
    with _batches_lock:
        batch = _batches.get(batch_id)
        if batch is None:
            return
        batch["status"] = "running"
        batch["started_at"] = _now_iso()
        batch["updated_at"] = batch["started_at"]
        jobs = int(batch.get("jobs") or 1)
        item_count = len(batch.get("items") or [])
        _append_batch_event_unlocked(batch, "info", f"Batch started; {item_count} files; jobs={jobs}")
        _write_batch_manifest(batch)

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            futures = {pool.submit(_run_parameter_batch_item, batch_id, index): index for index in range(item_count)}
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    index = futures[future]
                    with _batches_lock:
                        item = dict(_batches.get(batch_id, {}).get("items", [{}])[index])
                    message = _write_batch_item_error(Path(item.get("output_dir", "")), str(item.get("input", "")), exc)
                    _update_batch_item(batch_id, index, status="failed", finished_at=_now_iso(), error=message)

        from scripts.export_benchmark_excel import ResultSource, summarize_source, write_xlsx

        with _batches_lock:
            batch = _batches[batch_id]
            sources = [ResultSource(label=str(item["input"]), path=Path(item["output_dir"])) for item in batch["items"]]
            output_dir = Path(batch["output_dir"])
        rows = [summarize_source(source) for source in sources]
        write_xlsx(rows, output_dir / _BATCH_EXCEL_FILE)
        _append_batch_event(batch_id, "info", f"Excel report written: {_BATCH_EXCEL_FILE}")

        with _batches_lock:
            batch = _batches[batch_id]
            batch["status"] = "completed"
            batch["finished_at"] = _now_iso()
            batch["updated_at"] = batch["finished_at"]
            batch["excel_path"] = str(Path(batch["output_dir"]) / _BATCH_EXCEL_FILE)
            _append_batch_event_unlocked(batch, "info", "Batch completed")
            _write_batch_manifest(batch)
    except Exception as exc:
        with _batches_lock:
            batch = _batches.get(batch_id)
            if batch is None:
                return
            batch["status"] = "failed"
            batch["finished_at"] = _now_iso()
            batch["updated_at"] = batch["finished_at"]
            batch.setdefault("errors", []).append(_redact_secrets(str(exc)))
            _append_batch_event_unlocked(batch, "error", f"Batch failed: {exc}")
            _write_batch_manifest(batch)


def _start_parameter_batch_thread(batch_id: str) -> None:
    thread = threading.Thread(target=_run_parameter_batch, args=(batch_id,), daemon=True)
    thread.start()


@app.post("/api/batches/parameters")
async def create_parameter_batch(body: dict[str, Any]):
    inputs = _clean_batch_inputs(body)
    if not inputs:
        return {"error": "Please enter at least one PRIDE file name."}
    max_items = _max_batch_items()
    if len(inputs) > max_items:
        return {"error": f"Too many batch inputs: {len(inputs)}; maximum is {max_items}."}
    submitter = _clean_submitter(body.get("submitter"))
    ui_language = _clean_ui_language(body.get("ui_language"))
    repository = _clean_repository(body.get("repository"))
    run_mode = _clean_batch_run_mode(body.get("run_mode"))
    resource_policy = _clean_resource_policy(body.get("resource_policy"))
    fasta_preference = _clean_text(body.get("fasta_preference")).lower()
    prefer_project_fasta = fasta_preference == "project" or body.get("prefer_project_fasta") is True
    reviewed_fasta_path, reviewed_fasta_url = _clean_reviewed_fasta(body.get("reviewed_fasta"))
    explicit_reviewed_fasta_path = _clean_text(body.get("reviewed_fasta_path"))
    explicit_reviewed_fasta_url = _clean_text(body.get("reviewed_fasta_url"))
    if explicit_reviewed_fasta_path:
        reviewed_fasta_path = explicit_reviewed_fasta_path
        reviewed_fasta_url = None
    if explicit_reviewed_fasta_url:
        reviewed_fasta_url = explicit_reviewed_fasta_url
        reviewed_fasta_path = None
    reviewed_fasta_name = _clean_text(body.get("reviewed_fasta_name")) or None
    llm_config = body.get("llm_config", {})
    if not isinstance(llm_config, dict):
        llm_config = {}
    config, config_error = _build_llm_config(llm_config)
    if config_error or config is None:
        return {"error": config_error}
    ok, message = await _run_llm_check(config)
    if not ok:
        return {"error": message}

    batch_id = uuid.uuid4().hex[:12]
    batch_dir = _batch_dir(batch_id)
    jobs = _batch_jobs(body.get("jobs"), len(inputs))
    items = [
        {
            "index": index,
            "input": input_value,
            "status": "queued",
            "output_dir": str(_batch_item_dir(batch_dir, index, input_value)),
            "error": "",
        }
        for index, input_value in enumerate(inputs, start=1)
    ]
    batch = {
        "batch_id": batch_id,
        "status": "queued",
        "submitter": submitter,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "jobs": jobs,
        "ui_language": ui_language,
        "repository": repository,
        "run_mode": run_mode,
        "resource_policy": resource_policy,
        "prefer_project_fasta": prefer_project_fasta,
        "output_dir": str(batch_dir),
        "excel_path": str(batch_dir / _BATCH_EXCEL_FILE),
        "items": items,
        "errors": [],
        "events": [
            {
                "ts": _now_iso(),
                "level": "info",
                "message": f"Batch created with {len(items)} files; jobs={jobs}; submitter={submitter}",
            }
        ],
        "llm_config": dict(config),
    }
    with _batches_lock:
        batch_dir.mkdir(parents=True, exist_ok=True)
        _batches[batch_id] = batch
        _write_batch_manifest(batch)
    _start_parameter_batch_thread(batch_id)
    return _public_batch_record(batch)


@app.get("/api/batches/{batch_id}")
async def get_parameter_batch(batch_id: str):
    safe_id = safe_output_stem(batch_id)
    if safe_id != batch_id:
        return {"error": "Batch not found."}
    with _batches_lock:
        batch = _batches.get(batch_id)
        if batch is not None:
            return _public_batch_record(batch)
    batch = _load_batch_from_disk(batch_id)
    if batch is None:
        return {"error": "Batch not found."}
    return _public_batch_record(batch)


@app.get("/api/batches/{batch_id}/download")
async def download_parameter_batch(batch_id: str):
    safe_id = safe_output_stem(batch_id)
    if safe_id != batch_id:
        return {"error": "Batch not found."}
    batch = _load_batch_from_disk(batch_id)
    if batch is None:
        with _batches_lock:
            batch = _batches.get(batch_id)
    if batch is None:
        return {"error": "Batch not found."}
    excel_path = Path(batch.get("excel_path") or Path(batch.get("output_dir", "")) / _BATCH_EXCEL_FILE)
    if not excel_path.exists():
        return {"error": "Excel report is not ready."}
    return FileResponse(
        path=str(excel_path),
        filename=f"{batch_id}_benchmark_results.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/api/batches/{batch_id}/audit.zip")
async def download_parameter_batch_audit(batch_id: str):
    safe_id = safe_output_stem(batch_id)
    if safe_id != batch_id:
        return {"error": "Batch not found."}
    batch = _load_batch_from_disk(batch_id)
    if batch is None:
        with _batches_lock:
            batch = _batches.get(batch_id)
    if batch is None:
        return {"error": "Batch not found."}
    zip_path = _ensure_batch_audit_zip(batch)
    if zip_path is None or not zip_path.exists():
        return {"error": "Audit package is not ready."}
    return FileResponse(
        path=str(zip_path),
        filename=f"{batch_id}_audit.zip",
        media_type="application/zip",
    )


@app.post("/api/preflight")
async def preflight(body: dict[str, Any]):
    inputs = _clean_batch_inputs(body)
    if not inputs:
        single = _clean_text(body.get("input_value"))
        if single:
            inputs = [single]
    if not inputs:
        return {"status": "blocked", "blocking_issues": ["No input files were provided."], "checks": []}
    return run_preflight(
        inputs=inputs,
        run_mode=_clean_run_mode(body.get("run_mode")),
        repository=_clean_repository(body.get("repository"), default="auto"),
        output_root=_runs_dir,
        resource_policy=_clean_resource_policy(body.get("resource_policy")),
    )


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
    run_mode = _clean_run_mode(body.get("run_mode"))
    resource_policy = _clean_resource_policy(body.get("resource_policy"))
    ui_language = _clean_ui_language(body.get("ui_language"))
    repository = _clean_repository(body.get("repository"))

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
    project_key = _project_key_for_input(input_value)
    reviewed_fasta_path, reviewed_fasta_url = _clean_reviewed_fasta(body.get("reviewed_fasta"))
    explicit_reviewed_fasta_path = _clean_text(body.get("reviewed_fasta_path"))
    explicit_reviewed_fasta_url = _clean_text(body.get("reviewed_fasta_url"))
    if explicit_reviewed_fasta_path:
        reviewed_fasta_path = explicit_reviewed_fasta_path
        reviewed_fasta_url = None
    if explicit_reviewed_fasta_url:
        reviewed_fasta_url = explicit_reviewed_fasta_url
        reviewed_fasta_path = None
    reviewed_fasta_name = _clean_text(body.get("reviewed_fasta_name")) or None

    with _tasks_lock:
        output_dir = _next_output_dir_locked(project_key, task_id)
        _tasks[task_id] = {
            "task_id": task_id,
            "input_value": input_value,
            "project_key": project_key,
            "submitter": submitter,
            "output_dir": str(output_dir),
            "status": "queued",
            "created_at": _now_iso(),
            "logs": deque(maxlen=5000),
            "step": 0,
            "total_steps": 5,
            "blocking_issues": [],
            "prefer_project_fasta": prefer_project_fasta,
            "reviewed_fasta_path": reviewed_fasta_path,
            "reviewed_fasta_url": reviewed_fasta_url,
            "reviewed_fasta_name": reviewed_fasta_name,
            "run_mode": run_mode,
            "resource_policy": resource_policy,
            "ui_language": ui_language,
            "repository": repository,
            "llm_config": dict(config),
        }
        queue_state = _queue_state_locked(task_id)
        _tasks[task_id]["logs"].append(
            {
                "type": "log",
                "ts": _now_time(),
                "level": "info",
                "message": _localize_public_message(
                    f"任务已进入队列，当前位置 {queue_state['queue_position']}/{queue_state['queue_length']}。",
                    ui_language,
                    level="info",
                ),
            }
        )
    _write_task_history(task_id)
    _start_ready_queued_tasks()
    with _tasks_lock:
        status = _tasks[task_id]["status"]
        queue_state = _queue_state_locked(task_id)
    return {
        "task_id": task_id,
        "submitter": submitter,
        "output_dir": str(output_dir),
        "status": status,
        "run_mode": run_mode,
        "resource_policy": resource_policy,
        "ui_language": ui_language,
        "repository": repository,
        **queue_state,
    }


# ── WebSocket 实时日志 ────────────────────────────────────────────
@app.websocket("/ws/tasks/{task_id}")
async def task_ws(websocket: WebSocket, task_id: str):
    await websocket.accept()

    with _tasks_lock:
        task = _tasks.get(task_id)
    if task is None:
        await websocket.send_json({"type": "error", "message": f"任务 {task_id} 不存在"})
        await websocket.close()
        return

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
                new_logs = list(task["logs"])[sent:]
            await websocket.send_json(queue_message)
            for log in new_logs:
                await websocket.send_json(log)
            sent += len(new_logs)
            await asyncio.sleep(1.0)
        while task["status"] == "running":
            await asyncio.sleep(0.3)
            with _tasks_lock:
                new_logs = list(task["logs"])[sent:]
            for log in new_logs:
                await websocket.send_json(log)
            sent += len(new_logs)
        with _tasks_lock:
            new_logs = list(task["logs"])[sent:]
            final_status = task["status"]
        for log in new_logs:
            await websocket.send_json(log)
        await websocket.send_json({"type": "done", "status": final_status})
    except WebSocketDisconnect:
        pass


# ── 日志工具 ──────────────────────────────────────────────────────
def _emit(task_id: str, msg_type: str, data: Any = None, **kwargs):
    task = _tasks.get(task_id)
    if task is None:
        return
    if "message" in kwargs:
        kwargs["message"] = _localize_public_message(
            _strip_ansi(kwargs["message"]).strip(),
            _clean_ui_language(task.get("ui_language")),
            level=str(kwargs.get("level") or msg_type),
        )
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
    reviewed_fasta_path = _clean_text(task.get("reviewed_fasta_path")) or None
    reviewed_fasta_url = _clean_text(task.get("reviewed_fasta_url")) or None
    reviewed_fasta_name = _clean_text(task.get("reviewed_fasta_name")) or None
    run_mode = _clean_run_mode(task.get("run_mode"))
    repository = _clean_repository(task.get("repository"))
    parameter_only = run_mode == _RUN_MODE_PARAMETERS
    prepare_only = run_mode == _RUN_MODE_PREPARE
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
        _log(task_id, "info", f"运行模式：{_run_mode_label(run_mode)}")

        # ── 步骤 1 ──
        repository_label = repository.upper() if repository != "auto" else "Auto"
        _step(task_id, 1, f"[1/5] Resolve {repository_label} project")
        _log(task_id, "info", "正在初始化 AgentService…")
        service = AgentService(reporter=reporter, llm_reasoner=_task_llm_reasoner(llm_config))
        _log(task_id, "info", "AgentService 初始化完成")
        task_obj = normalize_input(input_value)
        _log(task_id, "info", f"输入规范化：{task_obj.file_name}")

        _log(task_id, "info", f"Querying {repository_label} metadata and inferring parameters with the LLM...")
        with StderrCapture(task_id):
            result = service.plan_dda_run_from_repository(
                task=task_obj,
                output_dir=output_dir,
                repository=repository,
                reviewed_fasta_path=reviewed_fasta_path,
                reviewed_fasta_url=reviewed_fasta_url,
                reviewed_fasta_name=reviewed_fasta_name,
                prefer_project_fasta=prefer_project_fasta,
            )
        if review_overrides:
            result = service.apply_review_overrides_to_result(
                result,
                review_overrides,
                task_obj,
                output_dir,
                prefer_project_fasta=prefer_project_fasta,
                reviewed_fasta_path=reviewed_fasta_path,
                reviewed_fasta_url=reviewed_fasta_url,
                reviewed_fasta_name=reviewed_fasta_name,
            )
            _log(task_id, "info", "已应用人工复核选择，重新生成执行计划。")
        _set_review_summary(task_id, result)
        _log(task_id, "info", f"{repository_label} metadata query and LLM inference completed")

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
        if not parameter_only and result.plan.needs_review and service._can_retry_with_mzml_instrument(result.plan):
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
                reviewed_fasta_path=reviewed_fasta_path,
                reviewed_fasta_url=reviewed_fasta_url,
                reviewed_fasta_name=reviewed_fasta_name,
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

        if parameter_only:
            _step(task_id, 5, "[5/5] 参数推断完成")
            _log(task_id, "info", f"Parameter-only mode completed: {repository_label} project resolution, file attribute inference, workflow/FASTA/search-parameter planning, and audit package generation are complete.")
            service.write_task_bundle(output_dir, result.resolution, result.context, result.attributes, result.plan, asset=result.asset)
            try:
                from agent.audit.review import build_task_state_snapshot, write_task_state

                project_accession = result.resolution.primary_project.project_accession if result.resolution.primary_project else None
                write_task_state(
                    output_dir / "task_state.json",
                    build_task_state_snapshot(
                        task_id=task_obj.task_id,
                        status="completed",
                        stage="planning",
                        source_file=task_obj.file_name,
                        project_accession=project_accession,
                        notes=["Parameter-only mode completed; full execution was not run."],
                    ),
                )
                _write_parameter_audit_files(output_dir, task_id, 1, input_value, result)
                _write_task_runtime_log(task_id, output_dir)
                _log(task_id, "info", "Parameter package generated: converter_config, workflow, decision_trace, attributes, parameter_audit and runtime.log.")
                _log(task_id, "info", "Compressing parameter ZIP; parameter-only mode excludes RAW/mzML/FASTA payload files.")
                _zip_output_dir(output_dir, report=lambda message: _log(task_id, "info", message))
                _log(task_id, "info", "Parameter ZIP is ready to download.")
            except Exception as audit_exc:
                _log(task_id, "debug", f"Failed to write parameter-only audit files: {audit_exc}")
            _set_task_terminal_status(task_id, "completed")
            return

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
        result = service.validate_prepared_data_for_plan(result, prepared_path)
        if result.plan.needs_review:
            task["blocking_issues"] = result.plan.blocking_issues
            for issue in result.plan.blocking_issues:
                _log(task_id, "error", f"[闃绘柇] {issue}")
            service.write_task_bundle(output_dir, result.resolution, result.context, result.attributes, result.plan, asset=result.asset)
            _set_review_summary(task_id, result)
            _set_task_terminal_status(task_id, "blocked")
            return

        from agent.execution.bundle import materialize_dda_task_bundle
        with StderrCapture(task_id):
            bundle = materialize_dda_task_bundle(
                task=task_obj,
                project_resolution=result.resolution,
                project_context=result.context,
                attributes=result.attributes,
                source_data_path=prepared_path,
                output_dir=output_dir,
                reviewed_fasta_path=reviewed_fasta_path,
                reviewed_fasta_url=reviewed_fasta_url,
                reviewed_fasta_name=reviewed_fasta_name,
                prefer_project_fasta=prefer_project_fasta,
                report=reporter,
            )
        service.write_task_bundle(output_dir, result.resolution, result.context, result.attributes, bundle.plan, asset=result.asset)
        if prepare_only:
            _step(task_id, 4, "[4/5] Package MSDT-Converter input")
            from agent.msdt_converter.docker_runner import DockerMSDTConverterRunner

            docker_runner = DockerMSDTConverterRunner(image="guomics2017/msdt-converter:v1.3", report=reporter)
            docker_runner.write_container_config(bundle)
            try:
                from agent.audit.review import build_task_state_snapshot, write_task_state

                project_accession = result.resolution.primary_project.project_accession if result.resolution.primary_project else None
                _write_parameter_audit_files(output_dir, task_id, 1, input_value, result)
                write_task_state(
                    output_dir / "task_state.json",
                    build_task_state_snapshot(
                        task_id=task_obj.task_id,
                        status="completed",
                        stage="packaging",
                        source_file=task_obj.file_name,
                        project_accession=project_accession,
                        notes=["Prepare input package mode completed; Docker execution was not run."],
                    ),
                )
                _write_task_runtime_log(task_id, output_dir)
                _log(task_id, "info", "Prepared input package generated: converter_config, workflow, FASTA reference, decision_trace, attributes, parameter_audit and runtime.log.")
                _log(task_id, "info", "Compressing input-package ZIP; large RAW/mzML payload files remain in the run directory and are not duplicated in the ZIP.")
                _zip_output_dir(output_dir, report=lambda message: _log(task_id, "info", message))
                _log(task_id, "info", "Input-package ZIP is ready to download.")
            except Exception as audit_exc:
                _log(task_id, "debug", f"Failed to write prepare-mode audit files: {audit_exc}")
            _step(task_id, 5, "[5/5] Input package ready")
            _set_task_terminal_status(task_id, "completed")
            return
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
        from agent.execution.outputs import execution_failure_reasons
        failure_reasons = execution_failure_reasons(
            bundle.plan,
            docker_result.returncode,
            docker_result.stdout,
            docker_result.stderr,
        )
        if not failure_reasons:
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
            try:
                from agent.audit.review import build_task_state_snapshot, write_task_state

                project_accession = result.resolution.primary_project.project_accession if result.resolution.primary_project else None
                write_task_state(
                    output_dir / "task_state.json",
                    build_task_state_snapshot(
                        task_id=task_obj.task_id,
                        status="completed",
                        stage="execution",
                        source_file=task_obj.file_name,
                        project_accession=project_accession,
                        notes=[],
                    ),
                )
            except Exception as audit_exc:
                _log(task_id, "debug", f"Failed to write execution success audit files: {audit_exc}")
            _set_task_terminal_status(task_id, "completed")
        else:
            _log(task_id, "error", "MSDT-Converter 内部步骤失败，任务已标记为失败，不打包下载 ZIP。")
            for reason in failure_reasons:
                _log(task_id, "error", f"[failure] {reason}")
            if docker_result.stdout:
                _log(task_id, "error", f"[stdout]\n{docker_result.stdout[-2000:]}")
            if docker_result.stderr:
                _log(task_id, "error", f"[stderr]\n{docker_result.stderr[-2000:]}")
            try:
                from agent.audit.review import append_review_item, build_review_item, build_task_state_snapshot, write_task_state

                project_accession = result.resolution.primary_project.project_accession if result.resolution.primary_project else None
                write_task_state(
                    output_dir / "task_state.json",
                    build_task_state_snapshot(
                        task_id=task_obj.task_id,
                        status="failed",
                        stage="execution",
                        source_file=task_obj.file_name,
                        project_accession=project_accession,
                        notes=failure_reasons,
                    ),
                )
                append_review_item(
                    output_dir / "review_queue.json",
                    build_review_item(
                        task_id=task_obj.task_id,
                        source_file=task_obj.file_name,
                        project_accession=project_accession,
                        stage="execution",
                        reasons=failure_reasons,
                    ),
                )
            except Exception as audit_exc:
                _log(task_id, "debug", f"Failed to write execution failure audit files: {audit_exc}")
            _set_task_terminal_status(task_id, "failed")

    except Exception as exc:
        from agent.errors import build_error_record, public_error_summary, write_error_record

        error_record = build_error_record(exc, stage="pipeline", input_file=input_value)
        write_error_record(output_dir / "error.json", error_record)
        summary = public_error_summary(error_record)
        task["error_summary"] = summary
        task["blocking_issues"] = [summary.get("public_message") or "任务运行失败。"]
        _log(task_id, "error", f"运行出错：{summary.get('public_message')}（{summary.get('category')}）")
        if "traceback" in error_record:
            _log(task_id, "debug", error_record["traceback"])
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
        "error_summary": task.get("error_summary"),
        "review_summary": task.get("review_summary"),
        "fasta_preference": "project" if task.get("prefer_project_fasta") else "llm",
        "run_mode": _clean_run_mode(task.get("run_mode")),
        "resource_policy": _clean_resource_policy(task.get("resource_policy")),
        "ui_language": _clean_ui_language(task.get("ui_language")),
        "repository": _clean_repository(task.get("repository")),
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
                "message": _localize_public_message(
                    "已提交人工复核选择，任务重新进入队列。",
                    _clean_ui_language(task.get("ui_language")),
                    level="info",
                ),
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
            if not _ensure_existing_download_zip_ready(output_dir):
                return {"error": "Result ZIP is not ready yet."}
            zip_path = _download_zip_path(output_dir)
            stem = safe_output_stem(str(history.get("input_value") or output_dir_name or task_id))
            suffix = "parameters" if _clean_run_mode(history.get("run_mode")) == _RUN_MODE_PARAMETERS else "results"
            return FileResponse(
                path=str(zip_path),
                filename=f"{stem}_{suffix}.zip",
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

    if not _ensure_existing_download_zip_ready(output_dir):
        return {"error": "结果 ZIP 尚未打包完成，请等待任务日志提示后再下载。"}
    zip_path = _download_zip_path(output_dir)
    stem = safe_output_stem(task["input_value"])
    suffix = "parameters" if _clean_run_mode(task.get("run_mode")) == _RUN_MODE_PARAMETERS else "results"
    return FileResponse(
        path=str(zip_path),
        filename=f"{stem}_{suffix}.zip",
        media_type="application/zip",
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
