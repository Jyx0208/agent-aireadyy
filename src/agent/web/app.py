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
from types import SimpleNamespace
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import BackgroundTasks, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from agent.agent_core.harness import run_agent_harness
from agent.ai_ready.agent_run_bridge import build_ai_ready_from_agent_run
from agent.ai_ready.agent_run_locator import locate_agent_run_inputs
from agent.ai_ready.input_locator import locate_ai_ready_inputs, select_ai_ready_inputs
from agent.ai_ready.input_profile import profile_ai_ready_inputs
from agent.ai_ready.data_scientist_loop import run_data_scientist_agent_loop
from agent.ai_ready.data_scientist_report import make_data_scientist_agent_report
from agent.ai_ready.curation_memory import apply_curation_decisions_to_memory
from agent.ai_ready.dataset_recipe import make_dataset_recipe
from agent.ai_ready.guidance_alignment import make_guidance_alignment_report
from agent.ai_ready.mini_e2e import validate_agent_run_ai_ready_mini
from agent.ai_ready.mini_e2e_batch import validate_agent_runs_ai_ready_batch
from agent.ai_ready.model_informed_discovery import (
    discovery_payload_from_model_request as build_model_informed_discovery_payload,
    model_informed_repository_plan as build_model_informed_repository_plan,
    model_request_records as model_informed_request_records,
)
from agent.ai_ready.model_loop import run_dataset_model_loop
from agent.ai_ready.real_smoke import run_ai_ready_real_smoke
from agent.ai_ready.validation import validate_ai_ready_build
from agent.control_plane.models import AgentBudget, AgentEvent, DynamicBudgetLimits
from agent.control_plane.openai_agents import run_openai_agents_discovery
from agent.discovery.agentic import OpenAICompatibleDiscoveryLLM, default_agentic_discovery_planner, default_discovery_llm_client
from agent.discovery.agentic_runner import run_agentic_discovery
from agent.discovery.features import extract_file_features, extract_project_features
from agent.discovery.manifest import write_dataset_manifest
from agent.discovery.memory import (
    VALID_REVIEW_DECISIONS,
    VALID_REVIEW_REASONS,
    DiscoveryMemory,
    DiscoveryReviewDecision,
    build_run_record,
    generate_discovery_run_id,
    load_dataset_manifest,
    now_utc_iso,
)
from agent.discovery.models import DatasetManifest, DatasetRequest, DiscoveredFile, DiscoveredProject, DiscoveryEvidence
from agent.discovery.ontology import (
    general_query_terms_from_text,
    interpret_immunopeptide_metadata,
    is_immunopeptidomics_goal,
    normalize_labeling_strategy,
    normalize_ptm_type,
    normalize_species_values,
    species_from_text,
)
from agent.discovery.pride_discovery import discover_pride_dataset
from agent.discovery.repository_discovery import discover_repository_dataset
from agent.discovery.scoring import build_discovered_project, classify_file_role, score_file, score_project
from agent.discovery.task_readiness import annotate_manifest_task_readiness, normalize_task_type
from agent.discovery.task_profiles import get_task_profile
from agent.discovery.validity import assess_file_validity, assess_project_validity
from agent.input.normalizer import safe_output_stem
from agent.metadata.context import detect_sdrf_file, load_sdrf_rows, select_sdrf_rows_for_file
from agent.oneclick.preflight import normalize_resource_policy, normalize_run_mode, run_preflight
from agent.progress import render_download_progress
from agent.pride.client import PrideClient
from agent.repositories.smoke import run_repository_smoke
from agent.repositories.iprox_adapter import IproxAdapter, refresh_public_iprox_index
from agent.repositories.massive_adapter import MassiveAdapter
from agent.repositories.pride_adapter import PrideAdapter
from agent.repositories.registry import RepositoryRegistry
from agent.runtime.system_metrics import collect_system_metrics
from agent.utils import write_json
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
_batch_history_cache: dict[str, Any] = {"ts": 0.0, "records": []}
_batch_history_cache_lock = threading.Lock()
_discovery_jobs: dict[str, dict[str, Any]] = {}
_discovery_jobs_lock = threading.RLock()
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
_DISCOVERY_DIR_NAME = "discovery"
_DISCOVERY_MEMORY_DIR_NAME = "discovery_memory"
_AI_READY_BUILDS_DIR_NAME = "ai_ready_builds"
_DOWNLOAD_RESULT_DIRS = {"ai_ready", "msdt", "rawspectrum", "logs"}
_DOWNLOAD_ROOT_SUFFIXES = {".json", ".txt", ".log", ".tsv", ".csv"}
_DOWNLOAD_FRAGPIPE_PARAMETER_FILES = {"fragger.params", "msbooster_params.txt"}
_DISCOVERY_DOWNLOAD_FILES = {
    "dataset_request": ("dataset_request.json", "application/json"),
    "candidate_projects": ("candidate_projects.json", "application/json"),
    "dataset_manifest_json": ("dataset_manifest.json", "application/json"),
    "dataset_manifest_csv": ("dataset_manifest.csv", "text/csv"),
    "dataset_manifest_valid_csv": ("dataset_manifest_valid.csv", "text/csv"),
    "dataset_manifest_usable_csv": ("dataset_manifest_usable.csv", "text/csv"),
    "dataset_manifest_task_ready_csv": ("dataset_manifest_task_ready.csv", "text/csv"),
    "batch_inputs": ("batch_inputs.txt", "text/plain"),
    "batch_inputs_valid": ("batch_inputs_valid.txt", "text/plain"),
    "batch_inputs_usable": ("batch_inputs_usable.txt", "text/plain"),
    "batch_inputs_task_ready": ("batch_inputs_task_ready.txt", "text/plain"),
    "discovery_summary": ("discovery_summary.json", "application/json"),
    "quality_report": ("quality_report.json", "application/json"),
    "repository_audit_json": ("repository_audit.json", "application/json"),
    "repository_audit_csv": ("repository_audit.csv", "text/csv"),
    "repository_audit_md": ("repository_audit.md", "text/markdown"),
    "task_ai_readiness_matrix_json": ("task_ai_readiness_matrix.json", "application/json"),
    "task_ai_readiness_matrix_csv": ("task_ai_readiness_matrix.csv", "text/csv"),
    "data_value_ranking_json": ("data_value_ranking.json", "application/json"),
    "data_value_ranking_csv": ("data_value_ranking.csv", "text/csv"),
    "data_value_report_md": ("data_value_report.md", "text/markdown"),
    "data_value_strategy_eval_json": ("data_value_strategy_eval.json", "application/json"),
    "data_value_strategy_eval_csv": ("data_value_strategy_eval.csv", "text/csv"),
    "data_value_strategy_eval_md": ("data_value_strategy_eval.md", "text/markdown"),
    "agentic_plan": ("agentic_plan.json", "application/json"),
    "agentic_rounds": ("agentic_rounds.json", "application/json"),
    "agents_discovery_summary_json": ("agents_discovery_summary.json", "application/json"),
    "agents_discovery_events_json": ("agents_discovery_events.json", "application/json"),
    "agents_discovery_report_md": ("agents_discovery_report.md", "text/markdown"),
    "agents_discovery_budget_json": ("agents_discovery_budget.json", "application/json"),
}
_AI_READY_DOWNLOAD_FILES = {
    "input_profile_json": ("ai_ready_input_profile.json", "application/json"),
    "input_profile_csv": ("ai_ready_input_profile.csv", "text/csv"),
    "validation_report_json": ("ai_ready_validation_report.json", "application/json"),
    "validation_report_csv": ("ai_ready_validation_report.csv", "text/csv"),
    "build_summary_json": ("ai_ready_build_summary.json", "application/json"),
    "build_report_md": ("ai_ready_build_report.md", "text/markdown"),
    "builder_summary_json": ("agentic_dataset_build_summary.json", "application/json"),
    "builder_report_md": ("agentic_dataset_build_report.md", "text/markdown"),
    "builder_recommendations_json": ("agentic_dataset_build_recommendations.json", "application/json"),
    "input_locations_json": ("ai_ready_input_locations.json", "application/json"),
    "input_locations_csv": ("ai_ready_input_locations.csv", "text/csv"),
    "agent_run_input_locations_json": ("agent_run_input_locations.json", "application/json"),
    "agent_run_input_locations_csv": ("agent_run_input_locations.csv", "text/csv"),
    "agent_run_build_summary_json": ("agent_run_build_summary.json", "application/json"),
    "agent_run_build_report_md": ("agent_run_build_report.md", "text/markdown"),
    "mini_e2e_summary_json": ("mini_e2e_summary.json", "application/json"),
    "mini_e2e_report_md": ("mini_e2e_report.md", "text/markdown"),
    "mini_e2e_upstream_recovery_json": ("upstream_recovery/agent_recovery_report.json", "application/json"),
    "mini_e2e_upstream_recovery_md": ("upstream_recovery/agent_recovery_report.md", "text/markdown"),
    "mini_e2e_batch_summary_json": ("mini_e2e_batch_summary.json", "application/json"),
    "mini_e2e_batch_summary_csv": ("mini_e2e_batch_summary.csv", "text/csv"),
    "mini_e2e_batch_report_md": ("mini_e2e_batch_report.md", "text/markdown"),
    "real_smoke_summary_json": ("real_smoke_summary.json", "application/json"),
    "real_smoke_report_md": ("real_smoke_report.md", "text/markdown"),
    "discovery_feedback_preview_json": ("discovery_feedback_preview.json", "application/json"),
    "rt_report_json": ("rt_ai_ready/rt_export_report.json", "application/json"),
    "fragment_report_json": ("fragment_intensity_ai_ready/fragment_intensity_export_report.json", "application/json"),
    "psm_report_json": ("psm_scoring_ai_ready/psm_scoring_export_report.json", "application/json"),
    "denovo_report_json": ("denovo_ai_ready/denovo_export_report.json", "application/json"),
    "ptm_denovo_report_json": ("ptm_denovo_ai_ready/ptm_denovo_export_report.json", "application/json"),
    "chimeric_report_json": ("chimeric_ai_ready/chimeric_export_report.json", "application/json"),
    "chimeric_train_parquet": ("chimeric_ai_ready/chimeric_train.parquet", "application/octet-stream"),
    "repository_smoke_summary_json": ("repository_smoke_summary.json", "application/json"),
    "repository_smoke_summary_csv": ("repository_smoke_summary.csv", "text/csv"),
    "repository_smoke_report_md": ("repository_smoke_report.md", "text/markdown"),
    "repository_resolution_json": ("repository_resolution.json", "application/json"),
    "repository_context_json": ("repository_context.json", "application/json"),
    "repository_asset_json": ("repository_asset.json", "application/json"),
    "repository_audit_json": ("repository_audit.json", "application/json"),
    "repository_audit_csv": ("repository_audit.csv", "text/csv"),
    "repository_audit_md": ("repository_audit.md", "text/markdown"),
    "iprox_index_summary_json": ("iprox_index_summary.json", "application/json"),
    "iprox_project_index_jsonl": ("iprox_project_index.jsonl", "application/x-jsonlines"),
    "iprox_file_index_jsonl": ("iprox_file_index.jsonl", "application/x-jsonlines"),
    "agent_harness_summary_json": ("agent_harness_summary.json", "application/json"),
    "agent_harness_summary_csv": ("agent_harness_summary.csv", "text/csv"),
    "agent_harness_report_md": ("agent_harness_report.md", "text/markdown"),
    "agent_decision_trace_json": ("agent_decision_trace.json", "application/json"),
    "dataset_recipe_json": ("dataset_recipe.json", "application/json"),
    "dataset_recipe_md": ("dataset_recipe.md", "text/markdown"),
    "selected_files_csv": ("selected_files.csv", "text/csv"),
    "excluded_files_csv": ("excluded_files.csv", "text/csv"),
    "dataset_split_plan_json": ("dataset_split_plan.json", "application/json"),
    "dataset_split_plan_csv": ("dataset_split_plan.csv", "text/csv"),
    "split_rationale_md": ("split_rationale.md", "text/markdown"),
    "leakage_risk_report_json": ("leakage_risk_report.json", "application/json"),
    "leakage_risk_report_md": ("leakage_risk_report.md", "text/markdown"),
    "split_baseline_evaluation_json": ("split_baseline_evaluation.json", "application/json"),
    "split_baseline_evaluation_csv": ("split_baseline_evaluation.csv", "text/csv"),
    "split_baseline_evaluation_md": ("split_baseline_evaluation.md", "text/markdown"),
    "hard_benchmark_manifest_json": ("hard_benchmark_manifest.json", "application/json"),
    "hard_benchmark_manifest_csv": ("hard_benchmark_manifest.csv", "text/csv"),
    "counterfactual_benchmark_manifest_json": ("counterfactual_benchmark_manifest.json", "application/json"),
    "counterfactual_benchmark_manifest_csv": ("counterfactual_benchmark_manifest.csv", "text/csv"),
    "counterfactual_benchmark_report_md": ("counterfactual_benchmark_report.md", "text/markdown"),
    "coverage_gap_report_json": ("coverage_gap_report.json", "application/json"),
    "coverage_gap_report_md": ("coverage_gap_report.md", "text/markdown"),
    "agent_expansion_plan_json": ("agent_expansion_plan.json", "application/json"),
    "evidence_graph_json": ("evidence_graph.json", "application/json"),
    "evidence_graph_summary_md": ("evidence_graph_summary.md", "text/markdown"),
    "curation_queue_csv": ("curation_queue.csv", "text/csv"),
    "curation_queue_json": ("curation_queue.json", "application/json"),
    "curation_efficiency_report_json": ("curation_efficiency_report.json", "application/json"),
    "curation_efficiency_report_csv": ("curation_efficiency_report.csv", "text/csv"),
    "curation_efficiency_report_md": ("curation_efficiency_report.md", "text/markdown"),
    "curation_memory_update_json": ("curation_memory_update.json", "application/json"),
    "curation_memory_update_csv": ("curation_memory_update.csv", "text/csv"),
    "curation_memory_update_md": ("curation_memory_update.md", "text/markdown"),
    "model_eval_summary_json": ("model_eval_summary.json", "application/json"),
    "model_adapter_contract_json": ("model_adapter_contract.json", "application/json"),
    "model_adapter_contract_md": ("model_adapter_contract.md", "text/markdown"),
    "model_adapter_input_manifest_json": ("model_adapter_input_manifest.json", "application/json"),
    "model_adapter_input_manifest_csv": ("model_adapter_input_manifest.csv", "text/csv"),
    "external_model_metrics_json": ("external_model_metrics.json", "application/json"),
    "model_adapter_log": ("model_adapter.log", "text/plain"),
    "model_failure_modes_json": ("model_failure_modes.json", "application/json"),
    "model_loop_report_md": ("model_loop_report.md", "text/markdown"),
    "model_informed_gap_report_json": ("model_informed_gap_report.json", "application/json"),
    "model_informed_gap_report_md": ("model_informed_gap_report.md", "text/markdown"),
    "model_informed_expansion_plan_json": ("model_informed_expansion_plan.json", "application/json"),
    "model_informed_discovery_requests_json": ("model_informed_discovery_requests.json", "application/json"),
    "model_informed_discovery_requests_csv": ("model_informed_discovery_requests.csv", "text/csv"),
    "model_informed_discovery_requests_md": ("model_informed_discovery_requests.md", "text/markdown"),
    "model_informed_discovery_payloads_json": ("model_informed_discovery_payloads.json", "application/json"),
    "model_informed_discovery_payloads_csv": ("model_informed_discovery_payloads.csv", "text/csv"),
    "model_informed_discovery_payloads_md": ("model_informed_discovery_payloads.md", "text/markdown"),
    "model_informed_discovery_payload_queue_json": ("model_informed_discovery_payload_queue.json", "application/json"),
    "model_informed_discovery_payload_queue_csv": ("model_informed_discovery_payload_queue.csv", "text/csv"),
    "model_informed_discovery_payload_queue_md": ("model_informed_discovery_payload_queue.md", "text/markdown"),
    "model_informed_curation_queue_json": ("model_informed_curation_queue.json", "application/json"),
    "model_informed_curation_queue_csv": ("model_informed_curation_queue.csv", "text/csv"),
    "model_informed_curation_queue_md": ("model_informed_curation_queue.md", "text/markdown"),
    "model_strategy_comparison_json": ("model_strategy_comparison.json", "application/json"),
    "model_strategy_comparison_csv": ("model_strategy_comparison.csv", "text/csv"),
    "model_strategy_comparison_md": ("model_strategy_comparison.md", "text/markdown"),
    "real_data_scientist_agent_report_md": ("real_data_scientist_agent_report.md", "text/markdown"),
    "real_data_scientist_agent_summary_json": ("real_data_scientist_agent_summary.json", "application/json"),
    "guidance_alignment_report_json": ("guidance_alignment_report.json", "application/json"),
    "guidance_alignment_report_md": ("guidance_alignment_report.md", "text/markdown"),
    "data_scientist_agent_loop_summary_json": ("data_scientist_agent_loop_summary.json", "application/json"),
    "data_scientist_agent_loop_report_md": ("data_scientist_agent_loop_report.md", "text/markdown"),
}
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


def _manifest_path_candidates(value: Any) -> list[Path]:
    text = _clean_text(value).replace("\\", "/")
    if not text:
        return []
    candidates: list[Path] = []
    path = Path(text)
    candidates.append(path if path.is_absolute() else Path.cwd() / path)
    marker = "data/local_mzml_samples/"
    if marker in text:
        candidates.append(Path.cwd() / text[text.index(marker) :])
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate.resolve(strict=False))
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _same_resolved_path(left: Path, right: Path) -> bool:
    return left.resolve(strict=False) == right.resolve(strict=False)


def _is_reusable_context_dir(path: Path) -> bool:
    return (path / "metadata.json").exists() and (path / "attributes.json").exists()


def _local_sample_context_dir_from_runs(project_accession: str, file_name: str) -> str | None:
    runs_dir = Path.cwd() / "runs"
    if not runs_dir.exists():
        return None
    stem = Path(file_name).stem
    candidates = [path for path in runs_dir.glob(f"{stem}*") if path.is_dir()]
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for candidate in candidates:
        if not _is_reusable_context_dir(candidate):
            continue
        task_state_path = candidate / "task_state.json"
        if task_state_path.exists():
            try:
                task_state = json.loads(task_state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                task_state = {}
            if (
                isinstance(task_state, dict)
                and _clean_text(task_state.get("project_accession"))
                and _clean_text(task_state.get("project_accession")).upper() != project_accession.upper()
            ):
                continue
        return str(candidate)
    return None


def _local_sample_context_dir_for_path(source_path: Path, project_accession: str, file_name: str) -> str | None:
    manifest_path = Path.cwd() / "data" / "local_mzml_samples" / "local_mzml_samples_manifest.json"
    if not manifest_path.exists():
        return None
    try:
        records = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(records, list):
        return None
    for record in records:
        if not isinstance(record, dict):
            continue
        if _clean_text(record.get("project_accession")).upper() != project_accession.upper():
            continue
        record_file_name = _clean_text(record.get("file_name"))
        local_paths = [
            *_manifest_path_candidates(record.get("local_path")),
            *_manifest_path_candidates(record.get("prepared_mzml_path")),
        ]
        path_matches = any(_same_resolved_path(source_path, candidate) for candidate in local_paths)
        if not path_matches and record_file_name != file_name:
            continue
        for context_dir in _manifest_path_candidates(record.get("web_full_run_dir")):
            if _is_reusable_context_dir(context_dir):
                return str(context_dir)
        return _local_sample_context_dir_from_runs(project_accession, file_name)
    return None


def _known_local_source_from_input(value: Any) -> dict[str, str] | None:
    text = _clean_text(value)
    if not text or re.match(r"(?i)^(https?|ftp)://", text):
        return None
    path = Path(text)
    if not path.exists() or not path.is_file():
        return None
    lower = path.name.lower()
    if not lower.endswith((".raw", ".raw.zip", ".mzml", ".mzml.gz", ".mzxml", ".mzxml.gz")):
        return None
    project_accession = None
    for part in reversed(path.parts):
        if re.fullmatch(r"(?i)PXD\d{6,}", part):
            project_accession = part.upper()
            break
    if not project_accession:
        return None
    source = {
        "source_path": str(path),
        "project_accession": project_accession,
        "matched_file": path.name,
    }
    context_dir = _local_sample_context_dir_for_path(path, project_accession, path.name)
    if context_dir:
        source["context_dir"] = context_dir
    return source


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
    if repository in {"iprox", "ipx"}:
        return "iprox"
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
    if item.get("usable_partial_outputs"):
        item["status_group"] = "partial"
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


def _protected_result_dir_names() -> set[str]:
    defaults = {
        "baseline_validation",
        "real_smoke",
        "multi_mini_validation",
        "local_validation",
        "ai_ready_builds",
    }
    raw = os.getenv("AGENT_PROTECTED_RESULT_DIRS", "")
    extra = {item.strip() for item in raw.split(",") if item.strip()}
    return defaults | extra


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


def _discovery_root_dir() -> Path:
    return _runs_dir / _DISCOVERY_DIR_NAME


def _discovery_memory_dir() -> Path:
    return _runs_dir / _DISCOVERY_MEMORY_DIR_NAME


def _ai_ready_root_dir() -> Path:
    return _runs_dir / _AI_READY_BUILDS_DIR_NAME


def _safe_discovery_dir(discovery_id: str) -> Path | None:
    if not discovery_id or safe_output_stem(discovery_id) != discovery_id:
        return None
    root = _discovery_root_dir().resolve()
    candidate = (_discovery_root_dir() / discovery_id).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _safe_ai_ready_dir(build_id: str) -> Path | None:
    if not build_id or safe_output_stem(build_id) != build_id:
        return None
    root = _ai_ready_root_dir().resolve()
    candidate = (_ai_ready_root_dir() / build_id).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


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


def _clean_batch_input_records(body: dict[str, Any], inputs: list[str]) -> list[dict[str, Any] | None]:
    raw_records = body.get("input_records")
    if not isinstance(raw_records, list):
        return [None for _ in inputs]
    cleaned: list[dict[str, Any]] = []
    allowed_keys = {
        "input",
        "repository",
        "project_accession",
        "project_title",
        "file_name",
        "download_url",
        "file_type",
        "file_role",
        "species_policy",
        "canonical_species",
        "organism_taxon_id",
        "ptm_type",
        "ptm_subtype",
        "ptm_evidence_terms",
        "ptm_enrichment_methods",
        "semantic_metadata_confidence",
        "modification_scope",
        "immunopeptide_scope",
        "hla_class",
        "hla_alleles",
        "immunopeptide_evidence_terms",
        "immunopeptide_enrichment_methods",
        "immunopeptide_metadata_confidence",
        "labeling_strategy",
        "validity_status",
        "task_type",
        "task_readiness_status",
        "evidence_level",
        "sdrf_match_status",
        "instrument_families",
        "fragmentation_methods",
        "lc_gradient_minutes",
    }
    for raw in raw_records:
        if not isinstance(raw, dict):
            continue
        file_name = _clean_text(raw.get("file_name") or raw.get("input") or raw.get("input_value"))
        if not file_name:
            continue
        record: dict[str, Any] = {"file_name": file_name, "input": _clean_text(raw.get("input")) or file_name}
        for key in allowed_keys:
            if key in {"input", "file_name"} or key not in raw:
                continue
            value = raw.get(key)
            if isinstance(value, list):
                record[key] = [_clean_text(item) for item in value if _clean_text(item)]
            elif key == "lc_gradient_minutes":
                try:
                    record[key] = float(value) if value is not None and str(value).strip() else None
                except (TypeError, ValueError):
                    record[key] = None
            else:
                text = _clean_text(value)
                if text:
                    record[key] = text
        cleaned.append(record)
    if len(cleaned) == len(inputs) and all(record.get("file_name") == inputs[index] for index, record in enumerate(cleaned)):
        return cleaned
    by_name: dict[str, dict[str, Any]] = {}
    for record in cleaned:
        by_name.setdefault(str(record.get("file_name") or ""), record)
    return [by_name.get(input_value) for input_value in inputs]


def _batch_jobs(value: Any, item_count: int) -> int:
    try:
        requested = int(value)
    except (TypeError, ValueError):
        requested = 3
    return max(1, min(requested, item_count, _max_batch_jobs()))


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _agent_discovery_configuration(
    body: dict[str, Any],
) -> tuple[str, AgentBudget, DynamicBudgetLimits]:
    mode = _clean_text(os.getenv("AGENT_DISCOVERY_MODE") or "single_agent").lower()
    if mode not in {"single_agent", "multi_agent"}:
        mode = "single_agent"
    budget = AgentBudget(
        max_turns=_bounded_int(os.getenv("AGENT_MAX_MODEL_TURNS"), default=50, minimum=1, maximum=50),
        max_tool_calls=_bounded_int(os.getenv("AGENT_MAX_TOOL_CALLS"), default=100, minimum=1, maximum=100),
        max_discovery_rounds=3,
    )
    limits = DynamicBudgetLimits(
        max_query_units=_bounded_int(os.getenv("AGENT_MAX_QUERY_UNITS"), default=30, minimum=1, maximum=500),
        max_repository_requests=_bounded_int(
            os.getenv("AGENT_MAX_REPOSITORY_REQUESTS"), default=200, minimum=1, maximum=5000
        ),
        max_elapsed_seconds=_bounded_int(
            os.getenv("AGENT_MAX_ELAPSED_SECONDS"), default=1200, minimum=30, maximum=86400
        ),
        budget_agent_max_turns=_bounded_int(
            os.getenv("AGENT_BUDGET_AGENT_MAX_TURNS"), default=3, minimum=2, maximum=10
        ),
    )
    return mode, budget, limits


def _clean_discovery_species(value: Any, *, default: list[str] | None = None) -> list[str]:
    if isinstance(value, list):
        values = [_clean_text(item) for item in value]
    else:
        values = re.split(r"[,;\n]+", _clean_text(value))
    species = [item for item in values if item]
    return species or list(default or [])


def _clean_discovery_ptm_types(value: Any, *, default: list[str] | None = None) -> list[str]:
    if isinstance(value, list):
        values = [_clean_text(item) for item in value]
    else:
        values = re.split(r"[,;\n]+", _clean_text(value)) if value is not None else []
    normalized: list[str] = []
    for item in values:
        ptm = normalize_ptm_type(item)
        if ptm and ptm not in normalized:
            normalized.append(ptm)
    return normalized or list(default or [])


def _clean_dataset_request(body: dict[str, Any]) -> DatasetRequest:
    acquisition = _clean_text(body.get("acquisition_mode") or body.get("acquisition") or "dda").lower()
    repository = _clean_repository(body.get("repository") or "pride")
    goal = _clean_text(body.get("goal") or "general").lower()
    if goal not in {"general", "ptm"} and is_immunopeptidomics_goal(goal):
        goal = "immunopeptidomics"
    if goal == "general":
        ptm_type = "unknown_ptm"
        ptm_types: list[str] = []
    else:
        default_ptm = "unknown_ptm" if is_immunopeptidomics_goal(goal) else "phospho"
        ptm_types = _clean_discovery_ptm_types(body.get("ptm_types"), default=[])
        if not ptm_types:
            ptm_types = _clean_discovery_ptm_types(body.get("ptm_type") or body.get("ptm"), default=[default_ptm])
        ptm_type = ptm_types[0] if ptm_types else default_ptm
    raw_query_terms = body.get("query_terms")
    if isinstance(raw_query_terms, list):
        query_terms = [_clean_text(item) for item in raw_query_terms if _clean_text(item)]
    else:
        query_terms = []
    if goal == "general":
        query_terms = [*query_terms, *general_query_terms_from_text(_clean_text(body.get("prompt")))]
    raw_species = body.get("species")
    species = _clean_discovery_species(raw_species, default=[])
    species_policy = _clean_text(body.get("species_policy") or "open").lower()
    if species_policy not in {"open", "include_only", "exclude"}:
        species_policy = "open"
    canonical_species, taxon_ids = normalize_species_values(species)
    immunopeptide = interpret_immunopeptide_metadata(" ".join([goal, _clean_text(body.get("prompt")), _clean_text(body.get("immunopeptide_context"))]))
    return DatasetRequest(
        repository=repository,
        goal=goal,
        ptm_type=ptm_type,
        ptm_types=ptm_types,
        query_terms=list(dict.fromkeys(query_terms)),
        species=species,
        species_policy=species_policy,
        canonical_species=canonical_species,
        organism_taxon_id=taxon_ids,
        modification_scope=None if goal == "general" else ";".join(ptm_types or [ptm_type]),
        immunopeptide_scope=immunopeptide.scope if is_immunopeptidomics_goal(goal) else None,
        hla_class=list(immunopeptide.hla_classes),
        hla_alleles=list(immunopeptide.hla_alleles),
        immunopeptide_evidence_terms=list(immunopeptide.evidence_terms),
        immunopeptide_enrichment_methods=list(immunopeptide.enrichment_methods),
        immunopeptide_metadata_confidence=immunopeptide.confidence,
        labeling_strategy=normalize_labeling_strategy(body.get("labeling_strategy") or body.get("labeling") or "label_free"),
        acquisition_mode=acquisition,
        max_projects=_bounded_int(body.get("max_projects"), default=5, minimum=1, maximum=100),
        max_files=_bounded_int(body.get("max_files"), default=50, minimum=1, maximum=2000),
        max_candidate_projects=_bounded_int(body.get("max_candidate_projects"), default=50, minimum=1, maximum=300),
        max_files_per_project=_bounded_int(body.get("max_files_per_project"), default=20, minimum=1, maximum=100),
    )


def _model_request_records(payload: Any) -> list[dict[str, Any]]:
    return model_informed_request_records(payload)


def _find_model_informed_discovery_request(body: dict[str, Any]) -> dict[str, Any]:
    direct = body.get("request")
    if isinstance(direct, dict):
        records = _model_request_records(direct)
        if records:
            return records[0]

    build_id = _clean_text(body.get("build_id") or body.get("ai_ready_build_id"))
    if build_id:
        output_dir = _safe_ai_ready_dir(build_id)
        if output_dir is None or not output_dir.exists():
            raise ValueError("AI-ready build not found.")
        payload = _read_json_if_exists(output_dir / "model_informed_discovery_requests.json")
        records = _model_request_records(payload)
        if not records:
            raise ValueError("No model-informed discovery requests found for this build.")

        request_id = _clean_text(body.get("request_id") or body.get("model_request_id"))
        if not request_id:
            if len(records) == 1:
                return records[0]
            raise ValueError("request_id is required when a build has multiple discovery requests.")
        for record in records:
            if _clean_text(record.get("request_id")) == request_id:
                return record
        raise ValueError(f"Model-informed discovery request not found: {request_id}")

    if any(key in body for key in ("request_id", "query", "constraints", "dimension", "target")):
        records = _model_request_records(body)
        if records:
            return records[0]
    raise ValueError("Provide either request or build_id.")


def _first_clean_request_value(*values: Any) -> str:
    for value in values:
        if isinstance(value, list):
            for item in value:
                text = _clean_text(item)
                if text:
                    return text
            continue
        text = _clean_text(value)
        if text:
            return text
    return ""


def _species_from_model_request(request: dict[str, Any], constraints: dict[str, Any]) -> list[str]:
    raw = (
        constraints.get("species")
        or constraints.get("organism")
        or constraints.get("organism_preference")
        or request.get("species")
        or request.get("organism")
    )
    if raw:
        return _clean_discovery_species(raw)
    return ["human"]


def _repository_from_model_request(request: dict[str, Any]) -> str:
    repository = _clean_text(request.get("repository"))
    if repository:
        return _clean_repository(repository, default="auto")
    repositories = request.get("repositories")
    if isinstance(repositories, list):
        cleaned = [_clean_repository(item, default="") for item in repositories]
        cleaned = [item for item in cleaned if item]
        if len(set(cleaned)) == 1:
            return cleaned[0]
        if cleaned:
            return "auto"
    return "auto"


def _ptm_from_model_request(request: dict[str, Any], constraints: dict[str, Any]) -> str:
    raw = _first_clean_request_value(
        constraints.get("modification_scope"),
        constraints.get("ptm_type"),
        constraints.get("ptm"),
        request.get("ptm_type"),
        request.get("modification_scope"),
    )
    if not raw and _clean_text(request.get("dimension")).lower() in {"ptm", "modification", "modification_scope"}:
        raw = _clean_text(request.get("target"))
    if raw in {"any_ptm", "modified_peptides", "modified_peptide"}:
        return "unknown_ptm"
    return normalize_ptm_type(raw or "unknown_ptm")


def _query_from_model_request(request: dict[str, Any], payload: dict[str, Any]) -> str:
    query = _clean_text(request.get("query") or payload.get("query") or payload.get("prompt"))
    if query:
        return query
    pieces = [
        _clean_text(request.get("task_type")),
        _clean_text(request.get("target")),
        _clean_text(request.get("dimension")),
        _clean_text(request.get("reason")),
    ]
    return " ".join(piece for piece in pieces if piece).strip()


def _discovery_payload_from_model_request(
    request: dict[str, Any],
    *,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_model_informed_discovery_payload(request, overrides=overrides)


def _run_model_informed_discovery_payload(body: dict[str, Any]) -> dict[str, Any]:
    request = _find_model_informed_discovery_request(body)
    payload = _discovery_payload_from_model_request(request, overrides=body)
    return {
        "status": "ready",
        "request_id": payload.get("model_informed_request_id"),
        "requires_user_confirmation": payload.get("requires_user_confirmation", True),
        "payload": payload,
        "request": request,
    }


_DISCOVERY_TASK_TYPES = {
    "",
    "rt_prediction",
    "fragment_intensity_prediction",
    "psm_scoring",
    "denovo",
    "ptm_denovo",
    "chimeric_interpretation",
}
_DISCOVERY_DIVERSITY_STRATEGIES = {"balanced", "high", "off"}
_DISCOVERY_REPOSITORIES = {"pride", "massive", "iprox", "auto"}
_DISCOVERY_GOALS = {"general", "ptm", "immunopeptidomics"}


def _explicit_discovery_goal_overrides(prompt: str) -> dict[str, Any]:
    text = _clean_text(prompt).lower()
    overrides: dict[str, Any] = {}
    round_match = re.search(
        r"(?:agentic\s*)?rounds?\s*[:=]?\s*([12])|\b([12])\s*(?:agentic\s*)?rounds?\b",
        text,
    )
    if round_match:
        round_value = next((item for item in round_match.groups() if item), "")
        if round_value:
            overrides["agentic_rounds"] = int(round_value)
    elif any(marker in text for marker in ("one round", "single round", "round 1")):
        overrides["agentic_rounds"] = 1
    elif any(marker in text for marker in ("two rounds", "second round", "round 2")):
        overrides["agentic_rounds"] = 2
    return overrides


def _normalise_discovery_goal_parse(raw: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    payload = raw.get("fields") if isinstance(raw.get("fields"), dict) else raw
    warnings = [str(item) for item in raw.get("warnings", []) if str(item).strip()] if isinstance(raw.get("warnings"), list) else []

    raw_goal = _clean_text(payload.get("goal") or current.get("goal") or "general").lower()
    species = payload.get("species", current.get("species") if "species" in current else [])
    species_values = _clean_discovery_species(species, default=[])
    species_policy = _clean_text(payload.get("species_policy") or current.get("species_policy") or "open").lower()
    if species_policy not in {"open", "include_only", "exclude"}:
        species_policy = "open"
    task_type = _clean_text(payload.get("task_type") or current.get("task_type")).lower()
    if task_type not in _DISCOVERY_TASK_TYPES:
        warnings.append(f"Unsupported task_type '{task_type}' was ignored.")
        task_type = _clean_text(current.get("task_type")).lower()
        task_type = task_type if task_type in _DISCOVERY_TASK_TYPES else ""

    diversity_strategy = _clean_text(payload.get("diversity_strategy") or current.get("diversity_strategy") or "balanced").lower()
    if diversity_strategy not in _DISCOVERY_DIVERSITY_STRATEGIES:
        diversity_strategy = "balanced"
    repository = _clean_repository(payload.get("repository") or current.get("repository") or "pride")
    if repository not in _DISCOVERY_REPOSITORIES:
        warnings.append(f"Unsupported repository '{repository}' was ignored; using PRIDE.")
        repository = "pride"

    goal = raw_goal
    if goal not in _DISCOVERY_GOALS:
        if is_immunopeptidomics_goal(goal):
            goal = "immunopeptidomics"
        else:
            warnings.append(f"Unsupported discovery target '{goal}' was ignored; using general.")
            goal = "general"
    if goal == "general":
        ptm_type = "unknown_ptm"
        ptm_types: list[str] = []
    else:
        default_ptm = "unknown_ptm" if goal == "immunopeptidomics" else "phospho"
        ptm_types = _clean_discovery_ptm_types(payload.get("ptm_types"), default=[])
        if not ptm_types:
            ptm_types = _clean_discovery_ptm_types(payload.get("ptm_type") or current.get("ptm_types") or current.get("ptm_type"), default=[default_ptm])
        ptm_type = ptm_types[0] if ptm_types else default_ptm
    labeling_strategy = normalize_labeling_strategy(
        payload.get("labeling_strategy") or current.get("labeling_strategy") or "label_free"
    )

    acquisition = _clean_text(payload.get("acquisition_mode") or current.get("acquisition_mode") or "dda").lower()
    if acquisition != "dda":
        warnings.append(f"Acquisition '{acquisition}' is not supported in this DDA-first workflow; using dda.")
        acquisition = "dda"
    canonical_species, taxon_ids = normalize_species_values(species_values)
    raw_query_terms = payload.get("query_terms") or current.get("query_terms") or []
    query_terms = [_clean_text(item) for item in raw_query_terms if _clean_text(item)] if isinstance(raw_query_terms, list) else []

    return {
        "fields": {
            "repository": repository,
            "goal": goal,
            "ptm_type": ptm_type,
            "ptm_types": ptm_types,
            "query_terms": list(dict.fromkeys([*query_terms, *general_query_terms_from_text(_clean_text(payload.get("prompt") or current.get("prompt") or ""))])),
            "species": species_values,
            "species_policy": species_policy,
            "canonical_species": canonical_species,
            "organism_taxon_id": taxon_ids,
            "modification_scope": None if goal == "general" else ";".join(ptm_types or [ptm_type]),
            "labeling_strategy": labeling_strategy,
            "acquisition_mode": acquisition,
            "task_type": task_type,
            "max_projects": _bounded_int(payload.get("max_projects"), default=_bounded_int(current.get("max_projects"), default=5, minimum=1, maximum=100), minimum=1, maximum=100),
            "max_files": _bounded_int(payload.get("max_files"), default=_bounded_int(current.get("max_files"), default=50, minimum=1, maximum=2000), minimum=1, maximum=2000),
            "max_files_per_project": _bounded_int(payload.get("max_files_per_project"), default=_bounded_int(current.get("max_files_per_project"), default=20, minimum=1, maximum=100), minimum=1, maximum=100),
            "agentic_rounds": _bounded_int(payload.get("agentic_rounds"), default=_bounded_int(current.get("agentic_rounds"), default=1, minimum=1, maximum=2), minimum=1, maximum=2),
            "diversity_strategy": diversity_strategy,
            "agentic": True,
        },
        "warnings": warnings,
        "reasoning": _clean_text(raw.get("reasoning") or raw.get("rationale")),
    }


def _discovery_goal_parse_system_prompt() -> str:
    return (
        "You parse proteomics dataset discovery goals into safe UI fields. "
        "Return JSON only. Do not search PRIDE and do not invent unsupported capabilities. "
        "Supported repository values: pride, massive, iprox, auto. "
        "Broad remote discovery is PRIDE-first; MassIVE/iProX v1 use repository-smoke for known accessions/files. "
        "Supported discovery target values: general, ptm, immunopeptidomics. Default to goal=general for broad or future dataset searches. "
        "Use goal=ptm only when the user explicitly asks for PTM-enriched data. Use goal=immunopeptidomics only when the user explicitly wants HLA/MHC ligandome/immunopeptidomics as the discovery target. "
        "For general HLA, drug-treatment, disease, perturbation, cell-line, or future task searches, keep goal=general and put useful search phrases in query_terms. "
        "For HLA/MHC ligandome, eluted ligand, neoantigen, or immunopeptidome goals set ptm_type=unknown_ptm and ptm_types=[] unless a modification is explicitly requested. "
        "Supported ptm_type values: phospho, acetyl, ubiquitin, glyco, methyl, unknown_ptm; when goal=ptm, return ptm_types as a list and allow multiple values. acquisition_mode=dda. "
        "Supported labeling_strategy values: label_free, TMT, iTRAQ, unknown. "
        "Species policy defaults to open; use include_only only when the user explicitly says only/strict species, and exclude only when the user explicitly excludes species. "
        "PTM interpretation should normalize semantic terms and enrichment methods such as pSer/pThr/pTyr, kinase signaling, phosphosite localization, Ti/Fe/Ga/Ti4+-IMAC, MOAC, PolyMAC, Titansphere, GlyGly/K-GG, Kac, HILIC, lectin enrichment, Kme/Rme. "
        "Immunopeptidomics interpretation should normalize HLA/MHC ligandome, immunopeptidome, HLA/MHC eluted ligands, neoantigen, antigen presentation, HLA-IP/MHC-IP, W6/32, pan-HLA, HLA class I/II, MHC class I/II, and HLA alleles such as HLA-A*02:01. "
        "Supported task_type values: rt_prediction, fragment_intensity_prediction, psm_scoring, "
        "denovo, ptm_denovo, chimeric_interpretation, or empty string. "
        "Supported diversity_strategy values: balanced, high, off. "
        "If the user asks for DIA/PRM/SRM/MRM, keep dda and add a warning."
    )


def _run_discovery_goal_parse(body: dict[str, Any]) -> dict[str, Any]:
    prompt = _clean_text(body.get("prompt"))
    if not prompt:
        raise ValueError("Please enter a discovery request.")
    llm_config = body.get("llm_config")
    if isinstance(llm_config, dict) and _clean_text(llm_config.get("api_key")):
        config, config_error = _build_llm_config(llm_config)
        if config_error or config is None:
            raise ValueError(config_error or "Invalid LLM configuration.")
        client = OpenAICompatibleDiscoveryLLM(
            api_key=config["api_key"],
            base_url=config["base_url"],
            model=config["model"],
            timeout=_positive_float(config["timeout"], 120.0),
        )
    else:
        client = default_discovery_llm_client()
    if client is None:
        raise ValueError("No discovery LLM API key found. Fill API Configuration or set DEEPSEEK_API_KEY.")
    current = body.get("current") if isinstance(body.get("current"), dict) else {}
    user_prompt = (
        "Parse this discovery request into a JSON object with fields, warnings, and reasoning.\n\n"
        f"Discovery request:\n{prompt}\n\n"
        f"Current UI fields:\n{json.dumps(current, ensure_ascii=False, indent=2)}\n\n"
        "Expected JSON shape:\n"
        "{\n"
        '  "fields": {\n'
        '    "repository": "pride",\n'
        '    "goal": "general",\n'
        '    "ptm_type": "phospho",\n'
        '    "ptm_types": ["phospho", "acetyl"],\n'
        '    "query_terms": ["drug treatment DDA proteomics"],\n'
        '    "species": ["human"],\n'
        '    "species_policy": "open",\n'
        '    "labeling_strategy": "label_free",\n'
        '    "acquisition_mode": "dda",\n'
        '    "task_type": "rt_prediction",\n'
        '    "max_projects": 5,\n'
        '    "max_files": 50,\n'
        '    "max_files_per_project": 20,\n'
        '    "agentic_rounds": 1,\n'
        '    "diversity_strategy": "high"\n'
        "  },\n"
        '  "warnings": [],\n'
        '  "reasoning": "short explanation"\n'
        "}"
    )
    raw = client.complete_json(system_prompt=_discovery_goal_parse_system_prompt(), user_prompt=user_prompt)
    parsed = _normalise_discovery_goal_parse(raw, {**current, "prompt": prompt})
    if parsed["fields"].get("goal") == "general":
        parsed["fields"]["query_terms"] = list(
            dict.fromkeys([*(parsed["fields"].get("query_terms") or []), *general_query_terms_from_text(prompt)])
        )
    explicit_overrides = _explicit_discovery_goal_overrides(prompt)
    if explicit_overrides:
        parsed["fields"].update(explicit_overrides)
    return {
        "status": "completed",
        "parser": "llm",
        "prompt": prompt,
        **parsed,
    }


def _public_discovery_record(
    *,
    discovery_id: str,
    output_dir: Path,
    manifest: DatasetManifest,
    paths: dict[str, Path] | None = None,
    memory_saved: bool = False,
    status: str = "completed",
    runtime: str = "workflow",
    agent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    persisted_agent = manifest.summary.get("agent_runtime")
    if agent is None and isinstance(persisted_agent, dict):
        agent = dict(persisted_agent)
    if agent is not None:
        runtime = "openai_agents"
        status = _clean_text(agent.get("status")) or status
    download_files = paths or {
        key: output_dir / filename
        for key, (filename, _media_type) in _DISCOVERY_DOWNLOAD_FILES.items()
    }
    files = [
        file.model_dump(mode="json", exclude={"raw_record"})
        for file in manifest.files
    ]
    projects = [
        project.model_dump(mode="json", exclude={"raw_metadata"})
        for project in manifest.projects
    ]
    needs_review_count = sum(1 for file in manifest.files if file.needs_review)
    valid_count = sum(1 for file in manifest.files if file.validity_status == "valid")
    weak_keep_count = sum(1 for file in manifest.files if file.validity_status == "weak_keep")
    usable_count = valid_count + weak_keep_count
    return {
        "discovery_id": discovery_id,
        "run_id": manifest.run_id,
        "status": status,
        "runtime": runtime,
        "agent": agent,
        "request": manifest.request.model_dump(mode="json"),
        "summary": {
            **manifest.summary,
            "valid_files": valid_count,
            "weak_keep_files": weak_keep_count,
            "usable_files": usable_count,
            "needs_review_files": needs_review_count,
            "memory_saved": memory_saved,
        },
        "project_count": len(projects),
        "file_count": len(files),
        "projects": projects,
        "files": files,
        "output_dir": str(output_dir),
        "downloads": {
            key: f"/api/discovery/{discovery_id}/download?file={key}"
            for key, path in download_files.items()
            if path.exists()
        },
    }


_LOCAL_SPECIES_TOKENS = {
    "human": ("human", "homo sapiens", "hela", "hek293", "hct116", "a549", "jurkat", "293t", "9606"),
    "mouse": ("mouse", "murine", "mus musculus", "10090"),
    "rat": ("rat", "rattus", "rattus norvegicus", "10116"),
    "yeast": ("yeast", "saccharomyces", "saccharomyces cerevisiae", "559292"),
    "e coli": ("e coli", "e. coli", "ecoli", "escherichia coli", "escherichia", "562"),
}
_LOCAL_PROJECT_ACCESSION_RE = re.compile(r"\b(PXD\d{6,}|PRIDE\d+)\b", re.IGNORECASE)


def _local_text_has_token(text: str, token: str) -> bool:
    folded = text.casefold()
    token_folded = token.casefold()
    if re.fullmatch(r"[a-z0-9]+", token_folded):
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(token_folded)}(?![a-z0-9])", folded))
    return token_folded in folded


def _infer_species_from_text(text: str) -> list[str]:
    inferred, _taxon_ids = species_from_text(text)
    return inferred


def _infer_local_species_from_path(path: Path, root: Path) -> list[str]:
    try:
        text = str(path.relative_to(root))
    except ValueError:
        text = path.name
    return _infer_species_from_text(text)


def _canonical_species_set(values: list[str]) -> set[str]:
    canonical, _taxon_ids = normalize_species_values(values)
    return {item.casefold() for item in canonical}


def _local_species_conflicts_request(inferred_species: list[str], requested_species: list[str], species_policy: str = "open") -> bool:
    if species_policy == "open":
        return False
    if not inferred_species or not requested_species:
        return False
    overlap = bool(_canonical_species_set(inferred_species) & _canonical_species_set(requested_species))
    if species_policy == "exclude":
        return overlap
    return not overlap


def _local_project_accession_from_path(path: Path, root: Path) -> str | None:
    try:
        text = str(path.relative_to(root))
    except ValueError:
        text = str(path)
    match = _LOCAL_PROJECT_ACCESSION_RE.search(text)
    return match.group(1).upper() if match else None


def _metadata_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_metadata_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_metadata_text(item) for item in value)
    return str(value)


def _project_species_from_metadata(project_record: dict[str, Any], sdrf_rows: list[dict[str, Any]]) -> list[str]:
    text = " ".join(
        [
            _metadata_text(project_record.get("organisms")),
            _metadata_text(project_record.get("title")),
            _metadata_text(project_record.get("projectDescription")),
            _metadata_text(sdrf_rows[:200]),
        ]
    )
    return _infer_species_from_text(text)


def _local_file_record(path: Path, size_bytes: int | None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "fileName": path.name,
        "name": path.name,
        "publicFileLocations": [{"value": str(path)}],
    }
    if size_bytes is not None:
        record["fileSizeBytes"] = size_bytes
    return record


def _project_file_name(record: dict[str, Any]) -> str:
    return str(record.get("fileName") or record.get("name") or "")


def _match_project_file(local_path: Path, records: list[dict[str, Any]]) -> dict[str, Any] | None:
    local_name = local_path.name.casefold()
    local_stem = local_path.stem.casefold()
    for record in records:
        remote_name = _project_file_name(record)
        if remote_name.casefold() == local_name:
            return record
    for record in records:
        remote_name = _project_file_name(record)
        if Path(remote_name).stem.casefold() == local_stem:
            return record
    return None


def _local_fallback_project(root: Path, request: DatasetRequest) -> DiscoveredProject:
    open_species = request.species_policy == "open"
    return DiscoveredProject(
        repository="pride",
        project_accession="LOCAL",
        project_title=f"Local directory: {root}",
        species=[],
        species_policy=request.species_policy,
        canonical_species=[],
        organism_taxon_id=[],
        acquisition_mode=request.acquisition_mode,
        ptm_type=request.ptm_type,
        modification_scope=request.modification_scope or request.ptm_type,
        labeling_strategy=request.labeling_strategy,
        project_score=35.0,
        confidence=0.55,
        trust_score=0.5,
        evidence_completeness=0.35,
        validity_status="weak_keep" if open_species else "needs_review",
        validity_reasons=["local_directory_candidate", "metadata_not_verified", "missing_species_evidence"],
        needs_review=not open_species,
        evidence=[
            DiscoveryEvidence(field="source", source="local_directory", text=str(root), weight=5.0),
            DiscoveryEvidence(field="requested_species", source="user_constraint", text=", ".join(request.species), weight=1.0),
        ],
        raw_metadata={"local_dir": str(root)},
    )


def _load_local_pride_context(
    accession: str,
    request: DatasetRequest,
    pride: PrideClient,
) -> dict[str, Any]:
    project_record = pride.get_project(accession)
    project_files = pride.list_project_files(accession, max_files=max(request.max_files_per_project * 10, 100))
    sdrf_rows: list[dict[str, Any]] = []
    sdrf_candidates: list[dict[str, Any]] = []
    try:
        sdrf_candidates = pride.list_project_files(accession, keyword="sdrf", max_files=5)
    except Exception:
        sdrf_candidates = []
    sdrf_file = detect_sdrf_file([*project_files, *sdrf_candidates])
    if sdrf_file:
        sdrf_url = PrideClient.first_download_url(sdrf_file)
        if sdrf_url:
            sdrf_rows = load_sdrf_rows(pride.download_text(sdrf_url))

    project_score = score_project(project_record, request)
    project_features = extract_project_features(project_record, sdrf_rows)
    project = build_discovered_project(
        project_record,
        request,
        project_score,
        features=project_features,
    )
    actual_species = _project_species_from_metadata(project_record, sdrf_rows)
    if actual_species:
        actual_canonical_species, actual_taxon_ids = normalize_species_values(actual_species)
        project = project.model_copy(
            update={
                "species": actual_species,
                "canonical_species": actual_canonical_species,
                "organism_taxon_id": actual_taxon_ids,
                "diversity_tags": [
                    *[f"species:{item}" for item in actual_species],
                    *[f"instrument:{item}" for item in project.instrument_families or ["unknown"]],
                    *[f"fragmentation:{item}" for item in project.fragmentation_methods or ["unknown"]],
                ],
            }
        )
    if _local_species_conflicts_request(project.species, request.species, request.species_policy):
        project = project.model_copy(
            update={
                "validity_status": "exclude",
                "validity_reasons": list(dict.fromkeys([*project.validity_reasons, "species_mismatch"])),
                "needs_review": True,
                "trust_score": 0.0,
            }
        )
    return {
        "project": project,
        "project_record": project_record,
        "project_files": project_files,
        "project_features": project_features,
        "sdrf_rows": sdrf_rows,
    }


def _local_discovery_manifest(
    request: DatasetRequest,
    local_dir: str | Path,
    report: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> DatasetManifest:
    if not str(local_dir or "").strip():
        raise ValueError("Local discovery directory is required.")
    root = Path(_container_repo_path_hint(str(local_dir))).expanduser()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Local discovery directory not found: {root}")

    def _report(message: str) -> None:
        if report is not None:
            report(message)

    def _check_cancel() -> None:
        if should_cancel is not None and should_cancel():
            raise InterruptedError("Discovery cancelled.")

    _report(f"Scanning local discovery directory: {root}")

    def fallback_project(accession: str) -> DiscoveredProject:
        project = _local_fallback_project(root, request)
        if accession == "LOCAL":
            return project
        return project.model_copy(
            update={
                "project_accession": accession,
                "project_title": f"Local project directory: {accession}",
                "raw_metadata": {"local_dir": str(root), "project_accession_hint": accession},
            }
        )

    pride: PrideClient | None = None
    context_cache: dict[str, dict[str, Any] | None] = {}
    failures: list[dict[str, str]] = []
    projects_by_accession: dict[str, DiscoveredProject] = {}
    files: list[DiscoveredFile] = []
    excluded_files = 0

    def get_context(accession: str) -> dict[str, Any] | None:
        nonlocal pride
        if accession in context_cache:
            return context_cache[accession]
        _check_cancel()
        if pride is None:
            pride = PrideClient(timeout=8.0, read_timeout=8.0)
        try:
            _report(f"Enriching local project hint {accession} with PRIDE metadata.")
            context = _load_local_pride_context(accession, request, pride)
            _report(f"{accession}: metadata enrichment completed.")
        except Exception as exc:  # pragma: no cover - network boundary
            failures.append({"stage": "local_project_metadata", "project": accession, "error": str(exc)})
            _report(f"{accession}: metadata enrichment failed: {exc}")
            context = None
        _check_cancel()
        context_cache[accession] = context
        return context

    try:
        for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: str(item).casefold()):
            _check_cancel()
            role = classify_file_role(path.name)
            if role.role not in {"raw_acquisition", "converted_peaklist"} or role.file_type is None:
                excluded_files += 1
                continue
            _report(f"Local candidate: {path.name}")
            try:
                size_bytes = path.stat().st_size
            except OSError:
                size_bytes = None

            accession = _local_project_accession_from_path(path, root) or "LOCAL"
            context = get_context(accession) if accession != "LOCAL" else None
            project = context["project"] if context else fallback_project(accession)
            projects_by_accession.setdefault(accession, project)

            local_evidence = [
                DiscoveryEvidence(field="file_name", source="local_path", text=str(path), weight=8.0),
                DiscoveryEvidence(field="file_role", source="suffix", text=role.role, weight=4.0),
            ]
            inferred_species = _infer_local_species_from_path(path, root)
            if inferred_species:
                local_evidence.append(
                    DiscoveryEvidence(
                        field="species",
                        source="local_path_name",
                        text=", ".join(inferred_species),
                        weight=3.0,
                    )
                )

            local_record = _local_file_record(path, size_bytes)
            if context:
                remote_record = _match_project_file(path, context["project_files"]) or local_record
                remote_name = _project_file_name(remote_record) or path.name
                sdrf_rows = context["sdrf_rows"]
                matched_sdrf_rows = select_sdrf_rows_for_file(sdrf_rows, remote_name) if sdrf_rows and remote_name else []
                if not sdrf_rows:
                    sdrf_match_status = "no_sdrf"
                elif matched_sdrf_rows:
                    sdrf_match_status = "matched"
                else:
                    sdrf_match_status = "no_file_match"
                file_features = extract_file_features(remote_record, context["project_features"], matched_sdrf_rows)
                scored_file = score_file(
                    remote_record,
                    project,
                    request,
                    features=file_features,
                    sdrf_match_status=sdrf_match_status,
                )
                file = scored_file or score_file(
                    local_record,
                    project,
                    request,
                    features=file_features,
                    sdrf_match_status=sdrf_match_status,
                )
            else:
                file = None

            if file is None:
                file_canonical_species, file_taxon_ids = normalize_species_values(inferred_species)
                file = DiscoveredFile(
                    repository="pride",
                    project_accession=accession,
                    project_title=project.project_title,
                    file_name=str(path),
                    download_url=str(path),
                    file_type=role.file_type,
                    file_role=role.role,
                    file_role_reasons=role.reasons,
                    sdrf_match_status="not_checked",
                    evidence_level="file",
                    file_level_evidence_count=2,
                    expected_size_bytes=size_bytes,
                    species=inferred_species,
                    species_policy=request.species_policy,
                    canonical_species=file_canonical_species or project.canonical_species,
                    organism_taxon_id=file_taxon_ids or project.organism_taxon_id,
                    acquisition_mode=request.acquisition_mode,
                    ptm_type=request.ptm_type,
                    ptm_subtype=project.ptm_subtype,
                    ptm_evidence_terms=project.ptm_evidence_terms,
                    ptm_enrichment_methods=project.ptm_enrichment_methods,
                    semantic_metadata_confidence=project.semantic_metadata_confidence,
                    semantic_interpretation_trace=project.semantic_interpretation_trace,
                    modification_scope=request.modification_scope or request.ptm_type,
                    labeling_strategy=request.labeling_strategy,
                    project_score=project.project_score,
                    file_score=52.0 if role.role == "raw_acquisition" else 45.0,
                    confidence=0.6,
                    trust_score=0.58,
                    evidence_completeness=0.45 if size_bytes is not None else 0.35,
                    validity_status="weak_keep",
                    validity_reasons=["local_file_candidate", "metadata_not_verified"],
                    needs_review=False,
                    evidence=local_evidence,
                    diversity_tags=[f"species:{item}" for item in inferred_species],
                    raw_record={"local_path": str(path), "local_dir": str(root)},
                )
                if _local_species_conflicts_request(inferred_species, request.species, request.species_policy):
                    file = file.model_copy(
                        update={
                            "validity_status": "exclude",
                            "validity_reasons": [*file.validity_reasons, "species_mismatch"],
                            "needs_review": True,
                            "trust_score": 0.0,
                        }
                    )
                else:
                    decision = assess_file_validity(file, request)
                    file = file.model_copy(
                        update={
                            "validity_status": decision.status,
                            "validity_reasons": list(dict.fromkeys([*file.validity_reasons, *decision.reasons])),
                            "needs_review": decision.needs_review,
                        }
                    )
            else:
                species_values = file.species or project.species or inferred_species
                file_canonical_species, file_taxon_ids = normalize_species_values(species_values)
                validity_reasons = list(dict.fromkeys([*file.validity_reasons, "local_file_candidate"]))
                validity_status = file.validity_status
                needs_review = file.needs_review
                trust_score = file.trust_score
                if _local_species_conflicts_request(species_values, request.species, request.species_policy):
                    validity_status = "exclude"
                    validity_reasons = list(dict.fromkeys([*validity_reasons, "species_mismatch"]))
                    needs_review = True
                    trust_score = 0.0
                file = file.model_copy(
                    update={
                        "project_accession": accession,
                        "project_title": project.project_title,
                        "file_name": str(path),
                        "download_url": str(path),
                        "file_type": role.file_type,
                        "file_role": role.role,
                        "file_role_reasons": role.reasons,
                        "expected_size_bytes": size_bytes,
                        "species": species_values,
                        "species_policy": request.species_policy,
                        "canonical_species": file_canonical_species or file.canonical_species or project.canonical_species,
                        "organism_taxon_id": file_taxon_ids or file.organism_taxon_id or project.organism_taxon_id,
                        "modification_scope": file.modification_scope or project.modification_scope or request.modification_scope,
                        "labeling_strategy": file.labeling_strategy or project.labeling_strategy or request.labeling_strategy,
                        "validity_status": validity_status,
                        "validity_reasons": validity_reasons,
                        "needs_review": needs_review,
                        "trust_score": trust_score,
                        "evidence": [*local_evidence, *file.evidence],
                        "raw_record": {
                            "local_path": str(path),
                            "local_dir": str(root),
                            "project_accession_hint": accession,
                            "remote_record": file.raw_record,
                        },
                    }
                )

            files.append(file)
            if len(files) >= request.max_files:
                break
    finally:
        if pride is not None:
            pride.close()

    final_projects: list[DiscoveredProject] = []
    for accession, project in projects_by_accession.items():
        project_files = [file for file in files if file.project_accession == accession]
        project_species = project.species or sorted({species for file in project_files for species in file.species})
        project_canonical_species, project_taxon_ids = normalize_species_values(project_species)
        updated_project = project.model_copy(
            update={
                "species": project_species,
                "canonical_species": project_canonical_species or project.canonical_species,
                "organism_taxon_id": project_taxon_ids or project.organism_taxon_id,
            }
        )
        if updated_project.validity_status != "exclude":
            project_decision = assess_project_validity(updated_project, request)
            updated_project = updated_project.model_copy(
                update={
                    "validity_status": project_decision.status,
                    "validity_reasons": list(dict.fromkeys([*updated_project.validity_reasons, *project_decision.reasons])),
                    "needs_review": updated_project.needs_review or project_decision.needs_review,
                }
            )
        updated_project = updated_project.model_copy(
            update={
                "file_count": len(project_files),
                "selected_file_count": len([file for file in project_files if file.validity_status != "exclude"]),
            }
        )
        final_projects.append(updated_project)

    diversity = {
        "species_distribution": dict(Counter(species for file in files for species in (file.species or ["unknown"]))),
        "instrument_family_distribution": dict(Counter(value for file in files for value in (file.instrument_families or ["unknown"]))),
        "fragmentation_method_distribution": dict(Counter(value for file in files for value in (file.fragmentation_methods or ["unknown"]))),
        "lc_gradient_distribution": dict(Counter(file.lc_gradient or "unknown" for file in files)),
        "unknown_counts": {
            "species": sum(1 for file in files if not file.species),
            "instrument_family": sum(1 for file in files if not file.instrument_families),
            "fragmentation_method": sum(1 for file in files if not file.fragmentation_methods),
            "lc_gradient": sum(1 for file in files if not file.lc_gradient),
        },
    }
    validity_counts = dict(Counter(file.validity_status for file in files))
    validity_reason_counts = dict(Counter(reason for file in files for reason in file.validity_reasons))
    evidence_warning_counts = dict(Counter(warning for file in files for warning in file.evidence_warnings))
    summary = {
        "repository": "local",
        "source": "local_dir",
        "local_dir": str(root),
        "metadata_enrichment": "pride_project_hint",
        "goal": request.goal,
        "ptm_type": request.ptm_type,
        "modification_scope": request.modification_scope or request.ptm_type,
        "labeling_strategy": request.labeling_strategy,
        "canonical_species": request.canonical_species,
        "organism_taxon_id": request.organism_taxon_id,
        "queries": [str(root)],
        "candidate_projects_seen": len(final_projects),
        "eligible_projects_seen": len([project for project in final_projects if project.selected_file_count > 0]),
        "excluded_projects": sum(1 for project in final_projects if project.validity_status == "exclude"),
        "excluded_files": excluded_files + sum(1 for file in files if file.validity_status == "exclude"),
        "selected_projects": len([project for project in final_projects if project.file_count > 0]),
        "selected_files": len(files),
        "max_projects": request.max_projects,
        "max_files": request.max_files,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "failures": failures,
        "memory_used": False,
        "diversity": diversity,
        "species_distribution": diversity["species_distribution"],
        "instrument_family_distribution": diversity["instrument_family_distribution"],
        "fragmentation_method_distribution": diversity["fragmentation_method_distribution"],
        "lc_gradient_distribution": diversity["lc_gradient_distribution"],
        "unknown_counts": diversity["unknown_counts"],
        "validity": {"validity_status_counts": validity_counts, "validity_reason_counts": validity_reason_counts},
        "validity_status_counts": validity_counts,
        "validity_reason_counts": validity_reason_counts,
        "file_context": {
            "evidence_level_distribution": dict(Counter(file.evidence_level for file in files)),
            "sdrf_match_status_distribution": dict(Counter(file.sdrf_match_status for file in files)),
            "evidence_warning_counts": evidence_warning_counts,
        },
        "evidence_level_distribution": dict(Counter(file.evidence_level for file in files)),
        "sdrf_match_status_distribution": dict(Counter(file.sdrf_match_status for file in files)),
        "evidence_warning_counts": evidence_warning_counts,
    }
    return DatasetManifest(request=request, projects=final_projects if files else [], files=files, summary=summary)


def _run_web_discovery(
    body: dict[str, Any],
    report: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    agent_event_callback: Callable[[AgentEvent], None] | None = None,
) -> dict[str, Any]:
    def _report(message: str) -> None:
        if report is not None:
            report(message)

    def _check_cancel() -> None:
        if should_cancel is not None and should_cancel():
            raise InterruptedError("Discovery cancelled.")

    request = _clean_dataset_request(body)
    source = _clean_text(body.get("source") or body.get("discovery_source") or "remote").lower()
    if source in {"local", "local_dir", "local_directory"}:
        _check_cancel()
        _report("Starting local directory discovery.")
        discovery_id = safe_output_stem(
            f"local_{datetime.now(_APP_TZ).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        )
        output_dir = _discovery_root_dir() / discovery_id
        manifest = _local_discovery_manifest(
            request,
            _clean_text(body.get("local_dir") or body.get("local_directory")),
            report=_report,
            should_cancel=should_cancel,
        )
        task_type = _clean_text(body.get("task_type"))
        if task_type:
            _report(f"Annotating task readiness: {task_type}")
            manifest = annotate_manifest_task_readiness(manifest, task_type)
        _check_cancel()
        manifest = manifest.model_copy(update={"run_id": discovery_id})
        paths = write_dataset_manifest(manifest, output_dir)
        _report(f"Local discovery manifest written: {output_dir}")
        return _public_discovery_record(
            discovery_id=discovery_id,
            output_dir=output_dir,
            manifest=manifest,
            paths=paths,
            memory_saved=False,
        )
    use_memory = body.get("use_memory", True) is not False
    prior_memory = DiscoveryMemory(_discovery_memory_dir()) if use_memory else None
    agentic_enabled = body.get("agentic") is True
    agentic_plan = None
    agentic_round_records = []
    agentic_fallback: dict[str, Any] | None = None
    task_type = _clean_text(body.get("task_type"))
    task_profile = get_task_profile(task_type) if task_type else None

    def _discover_for_web(
        discovery_request: DatasetRequest,
        memory: DiscoveryMemory | None = None,
        queries: list[str] | None = None,
    ) -> DatasetManifest:
        pride_client = PrideClient(timeout=15.0, read_timeout=15.0)
        try:
            discovery_kwargs = {
                "memory": memory,
                "queries": queries,
                "report": _report,
                "should_cancel": should_cancel,
                "early_stop_on_limits": True,
            }
            if discovery_request.repository == "pride":
                return discover_pride_dataset(
                    discovery_request,
                    client=pride_client,
                    **discovery_kwargs,
                )
            return discover_repository_dataset(
                discovery_request,
                client=pride_client,
                **discovery_kwargs,
            )
        finally:
            pride_client.close()

    runtime = _clean_text(body.get("runtime") or body.get("discovery_runtime") or "workflow").lower()
    if runtime in {"agent", "agents", "openai-agent", "openai_agents_sdk"}:
        runtime = "openai_agents"
    if runtime not in {"workflow", "openai_agents"}:
        raise ValueError(f"Unsupported discovery runtime: {runtime}")

    if runtime == "openai_agents":
        _check_cancel()
        prompt = _clean_text(body.get("prompt"))
        if not prompt:
            raise ValueError("Discovery request is required for OpenAI Agents mode.")
        web_llm_config = body.get("llm_config")
        agent_llm_config: dict[str, str] | None = None
        if isinstance(web_llm_config, dict) and _clean_text(web_llm_config.get("api_key")):
            agent_llm_config, config_error = _build_llm_config(web_llm_config)
            if config_error or agent_llm_config is None:
                raise ValueError(config_error or "Invalid LLM configuration.")
        discovery_id = safe_output_stem(
            f"agents_{datetime.now(_APP_TZ).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        )
        output_dir = _discovery_root_dir() / discovery_id
        normalized_task_type = normalize_task_type(task_type) if task_type else None
        discovery_mode, budget, dynamic_limits = _agent_discovery_configuration(body)

        def _agent_discovery_func(
            discovery_request: DatasetRequest,
            memory: DiscoveryMemory | None = None,
            queries: list[str] | None = None,
        ) -> DatasetManifest:
            _check_cancel()
            query_list = list(queries or [])
            preview = "; ".join(query_list[:4])
            _report(f"Act: repository search with {len(query_list)} query term(s){': ' + preview if preview else ''}")
            observed = _discover_for_web(discovery_request, memory=memory, queries=query_list)
            _report(
                "Observe: "
                f"{int(observed.summary.get('selected_projects') or len(observed.projects))} project(s), "
                f"{int(observed.summary.get('selected_files') or len(observed.files))} file(s)."
            )
            return observed

        _report(
            "Reason: OpenAI Agents SDK is planning repository search within server safety ceilings."
        )
        result = run_openai_agents_discovery(
            prompt=prompt,
            request=request,
            output_dir=output_dir,
            task_type=normalized_task_type,
            state_db=output_dir / "agent_control.sqlite",
            memory=prior_memory,
            budget=budget,
            mode=discovery_mode,
            dynamic_limits=dynamic_limits,
            run_id=discovery_id,
            discovery_func=_agent_discovery_func,
            llm_config=agent_llm_config,
            event_callback=agent_event_callback,
            stream_events=True,
        )
        _check_cancel()
        if result.status == "failed":
            detail = "; ".join(result.blockers or result.warnings) or "OpenAI Agents discovery failed."
            raise RuntimeError(detail)
        manifest_path = Path(
            result.selected_manifest_path
            or result.files.get("dataset_manifest_json")
            or output_dir / "dataset_manifest.json"
        )
        if not manifest_path.exists():
            raise RuntimeError("OpenAI Agents discovery finished without a persisted dataset manifest.")
        manifest = DatasetManifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8")))
        control_summary = _read_json_if_exists(output_dir / "agents_discovery_summary.json")
        dynamic_usage = control_summary.get("dynamic_usage") or {}
        budget_audit = control_summary.get("budget_audit") or {}
        save_memory = body.get("save_memory", True) is not False
        agent_summary = {
            "runtime": "openai_agents",
            "status": result.status,
            "run_id": result.run_id,
            "discovery_rounds": result.discovery_round_count,
            "tool_calls": int(control_summary.get("tool_call_count") or 0),
            "stop_reason": _clean_text(control_summary.get("stop_reason")),
            "final_output": result.final_output,
            "warnings": list(result.warnings),
            "blockers": list(result.blockers),
            "selected_round_index": result.selected_round_index,
            "selection_rationale": result.selection_rationale,
            "mode": discovery_mode,
            "budget": budget.model_dump(mode="json"),
            "dynamic_limits": dynamic_limits.model_dump(mode="json"),
            "query_units": int(dynamic_usage.get("query_units") or 0),
            "repository_requests": int(dynamic_usage.get("repository_requests") or 0),
            "search_batches": int(dynamic_usage.get("search_batches") or 0),
            "budget_reviews": int(dynamic_usage.get("budget_reviews") or 0),
            "pooled_selected_files": int(manifest.summary.get("selected_files") or len(manifest.files)),
            "hard_limits_reached": bool(budget_audit.get("hard_limits_reached")),
        }
        summary = {
            **manifest.summary,
            "run_id": result.run_id,
            "memory_used": use_memory,
            "memory_saved": save_memory,
            "agent_runtime": agent_summary,
        }
        manifest = manifest.model_copy(update={"run_id": result.run_id, "summary": summary})
        paths = write_dataset_manifest(manifest, output_dir)
        for key, raw_path in result.files.items():
            path = Path(raw_path)
            if key in _DISCOVERY_DOWNLOAD_FILES and path.exists():
                paths[key] = path
        if save_memory:
            memory = DiscoveryMemory(_discovery_memory_dir())
            memory.append_run(
                build_run_record(
                    run_id=result.run_id,
                    manifest=manifest,
                    output_dir=output_dir,
                    manifest_path=paths["dataset_manifest_json"],
                )
            )
        _report(
            f"Final: Agent discovery {result.status}; "
            f"{len(manifest.projects)} project(s), {len(manifest.files)} file(s)."
        )
        return _public_discovery_record(
            discovery_id=discovery_id,
            output_dir=output_dir,
            manifest=manifest,
            paths=paths,
            memory_saved=save_memory,
            status=result.status,
            runtime="openai_agents",
            agent=agent_summary,
        )

    if agentic_enabled:
        _check_cancel()
        _report("Starting LLM agentic discovery planning.")
        try:
            planner = default_agentic_discovery_planner()
        except Exception as exc:
            planner = None
            agentic_fallback = {
                "reason": "llm_planner_initialization_failed",
                "message": str(exc),
            }
        if planner is None:
            if agentic_fallback is None:
                agentic_fallback = {
                    "reason": "llm_unavailable",
                    "message": "No discovery LLM API key found; using deterministic discovery.",
                }
            _report(f"LLM query planning unavailable; using deterministic discovery. Reason: {agentic_fallback['reason']}")
            manifest = _discover_for_web(request, memory=prior_memory)
        else:
            prompt = _clean_text(body.get("prompt")) or (
                f"Find {', '.join(request.species)} {request.ptm_type} {request.acquisition_mode} "
                f"{request.repository.upper()} projects/files "
                "for model-building datasets. Prefer trustworthy file-level evidence and useful diversity."
            )
            def _discovery_func(request: DatasetRequest, memory: DiscoveryMemory | None = None, queries: list[str] | None = None) -> DatasetManifest:
                return _discover_for_web(request, memory=memory, queries=queries)

            try:
                result = run_agentic_discovery(
                    request=request,
                    planner=planner,
                    prompt=prompt,
                    memory=prior_memory,
                    max_rounds=_bounded_int(body.get("agentic_rounds"), default=1, minimum=1, maximum=2),
                    task_profile=task_profile,
                    discovery_func=_discovery_func,
                )
                manifest = result.manifest
                agentic_plan = result.plan
                agentic_round_records = result.rounds
            except Exception as exc:
                agentic_fallback = {
                    "reason": "llm_agentic_discovery_failed",
                    "message": str(exc),
                }
                _report(f"LLM query planning failed; using deterministic discovery. Reason: {exc}")
                manifest = _discover_for_web(request, memory=prior_memory)
    else:
        _report("Starting remote repository discovery.")
        manifest = _discover_for_web(request, memory=prior_memory)
    if task_type:
        _report(f"Annotating task readiness: {task_type}")
        manifest = annotate_manifest_task_readiness(manifest, normalize_task_type(task_type))
    _check_cancel()
    run_id = generate_discovery_run_id(request)
    discovery_id = safe_output_stem(run_id)
    if (_discovery_root_dir() / discovery_id).exists():
        run_id = f"{run_id}_{uuid.uuid4().hex[:6]}"
        discovery_id = safe_output_stem(run_id)
    output_dir = _discovery_root_dir() / discovery_id
    summary = {**manifest.summary, "run_id": run_id, "memory_used": use_memory}
    if agentic_plan is not None:
        summary["agentic"] = {
            "enabled": True,
            "rounds": len(agentic_round_records),
            "queries": agentic_plan.queries,
            "warnings": agentic_plan.warnings,
            "suggested_next_queries": agentic_plan.suggested_next_queries,
            "trace_steps": len(agentic_plan.trace),
        }
    elif agentic_fallback is not None:
        summary["agentic"] = {
            "enabled": False,
            "requested": True,
            "fallback": agentic_fallback,
            "warnings": [agentic_fallback["reason"]],
        }
    manifest = manifest.model_copy(update={"run_id": run_id, "summary": summary})
    paths = write_dataset_manifest(manifest, output_dir)
    _report(f"Discovery manifest written: {output_dir}")
    if agentic_plan is not None:
        agentic_plan_path = output_dir / "agentic_plan.json"
        write_json(agentic_plan_path, agentic_plan.model_dump(mode="json"))
        paths["agentic_plan"] = agentic_plan_path
    if agentic_round_records:
        agentic_rounds_path = output_dir / "agentic_rounds.json"
        write_json(agentic_rounds_path, [item.model_dump(mode="json") for item in agentic_round_records])
        paths["agentic_rounds"] = agentic_rounds_path

    save_memory = body.get("save_memory", True) is not False
    if save_memory:
        memory = DiscoveryMemory(_discovery_memory_dir())
        memory.append_run(
            build_run_record(
                run_id=run_id,
                manifest=manifest,
                output_dir=output_dir,
                manifest_path=paths["dataset_manifest_json"],
            )
        )
    return _public_discovery_record(
        discovery_id=discovery_id,
        output_dir=output_dir,
        manifest=manifest,
        paths=paths,
        memory_saved=save_memory,
    )


def _discovery_review_decisions_from_body(
    *,
    manifest: DatasetManifest,
    body: dict[str, Any],
) -> list[DiscoveryReviewDecision]:
    run_id = manifest.run_id or str(manifest.summary.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("Discovery manifest has no run_id.")
    raw_reviews = body.get("reviews")
    if not isinstance(raw_reviews, list):
        raise ValueError("Request body must include a reviews list.")

    files_by_key = {(file.project_accession, file.file_name): file for file in manifest.files}
    decisions: list[DiscoveryReviewDecision] = []
    for index, raw_review in enumerate(raw_reviews, start=1):
        if not isinstance(raw_review, dict):
            raise ValueError(f"Review item {index} must be an object.")
        project_accession = _clean_text(raw_review.get("project_accession"))
        file_name = _clean_text(raw_review.get("file_name"))
        decision = _clean_text(raw_review.get("decision"))
        reason = _clean_text(raw_review.get("reason") or ("correct" if decision == "keep" else "unclear"))
        note = _clean_text(raw_review.get("note"))
        if not decision:
            continue
        if not project_accession or not file_name:
            raise ValueError(f"Review item {index} is missing project_accession or file_name.")
        if (project_accession, file_name) not in files_by_key:
            raise ValueError(f"Review item {index} does not match a file in this discovery manifest.")
        if decision not in VALID_REVIEW_DECISIONS:
            raise ValueError(f"Review item {index} has invalid decision: {decision!r}")
        if reason not in VALID_REVIEW_REASONS:
            raise ValueError(f"Review item {index} has invalid reason: {reason!r}")
        file = files_by_key[(project_accession, file_name)]
        decisions.append(
            DiscoveryReviewDecision(
                review_id=uuid.uuid4().hex,
                run_id=run_id,
                created_at=now_utc_iso(),
                repository=file.repository,
                project_accession=project_accession,
                file_name=file_name,
                decision=decision,
                reason=reason,
                note=note,
            )
        )
    return decisions


def _manifest_with_review_decisions(
    manifest: DatasetManifest,
    decisions: list[DiscoveryReviewDecision],
) -> DatasetManifest:
    latest = {(decision.project_accession, decision.file_name): decision for decision in decisions}
    files = []
    for file in manifest.files:
        decision = latest.get((file.project_accession, file.file_name))
        if decision is None:
            files.append(file)
            continue
        files.append(
            file.model_copy(
                update={
                    "review_decision": decision.decision,
                    "review_reason": decision.reason,
                    "review_note": decision.note,
                }
            )
        )
    return manifest.model_copy(update={"files": files})


def _save_discovery_reviews(discovery_id: str, body: dict[str, Any]) -> dict[str, Any]:
    output_dir = _safe_discovery_dir(discovery_id)
    if output_dir is None:
        return {"error": "Discovery run not found."}
    manifest_path = output_dir / "dataset_manifest.json"
    if not manifest_path.exists():
        return {"error": "Discovery run not found."}
    try:
        manifest = load_dataset_manifest(manifest_path)
        decisions = _discovery_review_decisions_from_body(manifest=manifest, body=body)
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": f"Discovery manifest is not readable: {_redact_secrets(str(exc))}"}

    memory = DiscoveryMemory(_discovery_memory_dir())
    if decisions:
        memory.append_review_decisions(decisions)
        manifest = _manifest_with_review_decisions(manifest, decisions)
        write_dataset_manifest(manifest, output_dir)

    return {
        "status": "completed",
        "run_id": manifest.run_id,
        "review_decisions": len(decisions),
        "memory_summary": memory.summary(),
        "record": _public_discovery_record(
            discovery_id=discovery_id,
            output_dir=output_dir,
            manifest=manifest,
            memory_saved=bool(decisions),
        ),
    }


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


def _result_with_plan(result: Any, plan: Any | None) -> Any:
    if plan is None or getattr(result, "plan", None) is plan:
        return result
    model_copy = getattr(result, "model_copy", None)
    if callable(model_copy):
        return model_copy(update={"plan": plan})
    values = dict(vars(result)) if hasattr(result, "__dict__") else {}
    if not values:
        values = {
            "resolution": getattr(result, "resolution", None),
            "context": getattr(result, "context", None),
            "asset": getattr(result, "asset", None),
            "attributes": getattr(result, "attributes", None),
        }
    values["plan"] = plan
    return SimpleNamespace(**values)


def _write_agent_audit_package(
    output_dir: Path,
    result: Any,
    *,
    plan: Any | None = None,
    report: Callable[[str], None] | None = None,
) -> None:
    try:
        from agent.agent_core.audit import write_agent_audit_for_result

        write_agent_audit_for_result(output_dir, _result_with_plan(result, plan))
    except Exception as exc:
        if report is not None:
            try:
                report(f"Failed to write agent audit files: {exc}")
            except Exception:
                pass


def _write_recovery_audit_package(
    output_dir: Path,
    task_obj: Any,
    *,
    stage: str,
    run_mode: str,
    events: list[Any],
    result: Any | None = None,
    plan: Any | None = None,
    artifacts: dict[str, str | Path | None] | None = None,
    report: Callable[[str], None] | None = None,
) -> None:
    try:
        from agent.agent_core.recovery import build_recovery_audit, write_recovery_audit

        resolution = getattr(result, "resolution", None)
        primary = getattr(resolution, "primary_project", None)
        context = getattr(result, "context", None)
        audit = build_recovery_audit(
            task_id=str(getattr(task_obj, "task_id", "")),
            input_file=str(getattr(task_obj, "file_name", "")),
            output_dir=output_dir,
            run_mode=run_mode,
            repository=str(getattr(context, "repository", "unknown")),
            project_accession=getattr(primary, "project_accession", None),
            stage=stage,
            events=events,
            artifacts=artifacts,
            current_threads=getattr(plan, "thread_num", None) if plan is not None else None,
            detected_by="agent.web.app",
        )
        write_recovery_audit(output_dir, audit)
    except Exception as exc:
        if report is not None:
            try:
                report(f"Failed to write recovery audit file: {exc}")
            except Exception:
                pass


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
    audit_files = [
        "project_resolution.json",
        "metadata.json",
        "asset_resolution.json",
        "attributes.json",
        "decision_trace.json",
        "agent_observation.json",
        "agent_plan.json",
        "agent_decision_trace.json",
        "parameter_audit.json",
        "task_state.json",
        "logs/runtime.log",
    ]
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
        "audit_files": [path for path in audit_files if (output_dir / path).exists()],
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


def _batch_history_record(batch: dict[str, Any], include_file_stats: bool = True) -> dict[str, Any]:
    public = _public_batch_record(batch)
    batch_id = str(public.get("batch_id") or "").strip()
    output_dir = Path(public.get("output_dir") or "")
    file_count = 0
    size_bytes = 0
    if include_file_stats and output_dir.exists():
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


def _list_parameter_batch_history_records(use_cache: bool = True, include_file_stats: bool = True) -> list[dict[str, Any]]:
    if use_cache and not include_file_stats:
        with _batch_history_cache_lock:
            cached_ts = float(_batch_history_cache.get("ts") or 0.0)
            if time.time() - cached_ts < 20:
                return [dict(item) for item in _batch_history_cache.get("records") or []]
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    with _batches_lock:
        memory_batches = [dict(batch) for batch in _batches.values()]
    for batch in memory_batches:
        batch_id = str(batch.get("batch_id") or "").strip()
        if not batch_id:
            continue
        seen.add(batch_id)
        records.append(_batch_history_record(batch, include_file_stats=include_file_stats))

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
        records.append(_batch_history_record(batch, include_file_stats=include_file_stats))
    if use_cache and not include_file_stats:
        with _batch_history_cache_lock:
            _batch_history_cache["ts"] = time.time()
            _batch_history_cache["records"] = [dict(item) for item in records]
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
        "workflow_outcome": task.get("workflow_outcome"),
        "usable_partial_outputs": bool(task.get("usable_partial_outputs")),
        "recovery_primary_issue": task.get("recovery_primary_issue"),
        "recovery_recommended_next_step": task.get("recovery_recommended_next_step"),
        "recovery_report_json": task.get("recovery_report_json"),
        "recovery_report_md": task.get("recovery_report_md"),
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
    for batch in _list_parameter_batch_history_records(use_cache=False):
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
        "workflow_outcome": record.get("workflow_outcome"),
        "usable_partial_outputs": bool(record.get("usable_partial_outputs")),
        "recovery_primary_issue": record.get("recovery_primary_issue"),
        "recovery_recommended_next_step": record.get("recovery_recommended_next_step"),
        "recovery_report_json": record.get("recovery_report_json"),
        "recovery_report_md": record.get("recovery_report_md"),
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
    protected_names = _protected_result_dir_names()
    for run_dir in _runs_dir.iterdir():
        if not run_dir.is_dir():
            continue
        if run_dir.name == _BATCHES_DIR_NAME or run_dir.name in protected_names or (run_dir / ".agent_keep").exists():
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

    for batch in _list_parameter_batch_history_records(use_cache=False):
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


def _list_project_history_records_fast() -> list[dict[str, Any]]:
    _repair_interrupted_history_index()
    with _tasks_lock:
        active_task_ids, active_history_ids = _active_history_identity_sets_locked()
    records: list[dict[str, Any]] = []
    for record in _read_history_index():
        item = with_history_identity(record)
        if not item.get("project_key"):
            continue
        if item.get("status") in _ACTIVE_STATUSES and not _history_item_is_active(item, active_task_ids, active_history_ids):
            item = _mark_interrupted_history_item(item)
        output_dir_name = item.get("output_dir")
        if output_dir_name and not item.get("result_id"):
            item["result_id"] = str(output_dir_name)
        if output_dir_name and not item.get("run_id"):
            item["run_id"] = str(output_dir_name)
        item["file_count"] = int(item.get("file_count") or 0)
        item["size_bytes"] = int(item.get("size_bytes") or 0)
        item["can_download"] = bool(item.get("can_download"))
        records.append(_decorate_history_item(item))

    for batch in _list_parameter_batch_history_records(include_file_stats=True):
        if batch.get("status") in _ACTIVE_STATUSES:
            continue
        records.append(_decorate_history_item(batch))

    for result in _list_public_results():
        task_updated_at = result.get("task_updated_at") or result.get("finished_at") or result.get("started_at") or result.get("created_at")
        if task_updated_at:
            result["updated_at"] = task_updated_at
        if result.get("status") in _ACTIVE_STATUSES and not _history_item_is_active(result, active_task_ids, active_history_ids):
            result = _mark_interrupted_history_item(result)
        records.append(_decorate_history_item(result))

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
    protected_names = _protected_result_dir_names()
    for run_dir in _runs_dir.iterdir():
        if not run_dir.is_dir():
            continue
        if run_dir.name == _BATCHES_DIR_NAME or run_dir.name in protected_names or (run_dir / ".agent_keep").exists():
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


def _attach_workflow_recovery_summary(task_id: str, output_dir: Path) -> None:
    task = _tasks.get(task_id)
    if task is None or not output_dir.exists():
        return
    try:
        from agent.agent_core.recovery_report import analyze_agent_recovery

        paths = analyze_agent_recovery(output_dir)
        payload = json.loads(paths["agent_recovery_report_json"].read_text(encoding="utf-8"))
    except Exception as exc:
        task["recovery_report_error"] = str(exc)
        return
    task["workflow_outcome"] = payload.get("workflow_outcome")
    task["usable_partial_outputs"] = bool(payload.get("usable_partial_outputs"))
    task["recovery_primary_issue"] = payload.get("primary_issue")
    task["recovery_recommended_next_step"] = payload.get("recommended_next_step")
    task["recovery_report_json"] = str(paths["agent_recovery_report_json"])
    task["recovery_report_md"] = str(paths["agent_recovery_report_md"])


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
        "system_metrics": collect_system_metrics(_runs_dir),
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
async def list_project_history(fast: bool = True, refresh: bool = False):
    if fast and not refresh:
        if not _read_history_index():
            _sync_history_index_from_disk()
        with _tasks_lock:
            active_tasks = [
                _public_task_record_locked(task_id, task)
                for task_id, task in _tasks.items()
                if task.get("status") in _ACTIVE_STATUSES
            ]
        active_tasks.extend(batch for batch in _list_parameter_batch_history_records(include_file_stats=True) if batch.get("status") in _ACTIVE_STATUSES)
        active_tasks.sort(key=lambda item: str(item.get("created_at") or ""))
        active_task_ids = {str(item.get("task_id") or "") for item in active_tasks}
        active_history_ids = {str(item.get("history_id") or "") for item in active_tasks}
        active_history_ids.update(str(item.get("output_dir") or "") for item in active_tasks)
        active_history_ids.update(str(item.get("run_id") or "") for item in active_tasks)
        active_task_ids.discard("")
        active_history_ids.discard("")
        results = []
        for item in _list_project_history_records_fast():
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
        return {
            "retention_seconds": _result_retention_seconds(),
            "max_result_projects": _max_result_projects(),
            "removed": [],
            "summary": _history_summary(active_tasks, results),
            "active_tasks": active_tasks,
            "results": results,
            "history_mode": "fast",
        }
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
    active_tasks.extend(batch for batch in _list_parameter_batch_history_records(use_cache=False) if batch.get("status") in _ACTIVE_STATUSES)
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
        "history_mode": "full",
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
def _clean_path_list(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, list):
        values.extend(_clean_text(item) for item in value)
    else:
        text = _clean_text(value)
        values.extend(item.strip() for item in text.splitlines())
    return [item for item in values if item]


def _container_repo_path_hint(value: str) -> str:
    text = _clean_text(value)
    if not text:
        return text
    normalized = text.replace("\\", "/")
    marker = "agent-aireadyy_project/agent-aireadyy/"
    if marker in normalized and not Path(text).exists():
        suffix = normalized.split(marker, 1)[1]
        return str(Path("/app") / suffix)
    return text


def _clean_ai_ready_task_types(body: dict[str, Any]) -> list[str]:
    raw = body.get("task_types")
    if raw is None:
        raw = body.get("task_type")
    values = _clean_path_list(raw)
    if not values:
        values = ["rt_prediction"]
    result: list[str] = []
    for value in values:
        task_type = normalize_task_type(value)
        if task_type and task_type not in result:
            result.append(task_type)
    return result


def _new_ai_ready_build_id(prefix: str = "ai_ready") -> str:
    return safe_output_stem(f"{prefix}_{datetime.now(_APP_TZ).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}")


def _model_loop_output_dir(
    output_dir: Path,
    data_scientist_loop: dict[str, Any],
    data_scientist_summary: dict[str, Any],
) -> Path | None:
    candidates: list[Any] = []
    loop_files = data_scientist_loop.get("files") if isinstance(data_scientist_loop.get("files"), dict) else {}
    summary_model_loop = data_scientist_summary.get("model_loop") if isinstance(data_scientist_summary.get("model_loop"), dict) else {}
    candidates.extend(
        [
            data_scientist_loop.get("model_loop_dir"),
            loop_files.get("model_loop_dir"),
            data_scientist_summary.get("model_loop_dir"),
            summary_model_loop.get("model_loop_dir"),
            output_dir / "model_loop",
        ]
    )
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(str(candidate))
        if path.exists() and path.is_dir():
            return path
    return None


def _public_ai_ready_record(build_id: str, output_dir: Path) -> dict[str, Any]:
    input_profile = _read_json_if_exists(output_dir / "ai_ready_input_profile.json")
    input_locations = _read_json_if_exists(output_dir / "ai_ready_input_locations.json")
    agent_run_locations = _read_json_if_exists(output_dir / "agent_run_input_locations.json")
    agent_run_summary = _read_json_if_exists(output_dir / "agent_run_build_summary.json")
    mini_e2e_summary = _read_json_if_exists(output_dir / "mini_e2e_summary.json")
    mini_e2e_batch_summary = _read_json_if_exists(output_dir / "mini_e2e_batch_summary.json")
    real_smoke_summary = _read_json_if_exists(output_dir / "real_smoke_summary.json")
    repository_smoke_summary = _read_json_if_exists(output_dir / "repository_smoke_summary.json")
    repository_audit = _read_json_if_exists(output_dir / "repository_audit.json")
    iprox_index_summary = _read_json_if_exists(output_dir / "iprox_index_summary.json")
    agent_harness_summary = _read_json_if_exists(output_dir / "agent_harness_summary.json")
    dataset_recipe = _read_json_if_exists(output_dir / "dataset_recipe.json")
    leakage_risk_report = _read_json_if_exists(output_dir / "leakage_risk_report.json")
    coverage_gap_report = _read_json_if_exists(output_dir / "coverage_gap_report.json")
    hard_benchmark = _read_json_if_exists(output_dir / "hard_benchmark_manifest.json")
    counterfactual_benchmark = _read_json_if_exists(output_dir / "counterfactual_benchmark_manifest.json")
    curation_queue = _read_json_if_exists(output_dir / "curation_queue.json")
    curation_memory_update = _read_json_if_exists(output_dir / "curation_memory_update.json")
    data_scientist_summary = _read_json_if_exists(output_dir / "real_data_scientist_agent_summary.json")
    guidance_alignment = _read_json_if_exists(output_dir / "guidance_alignment_report.json")
    data_scientist_loop = _read_json_if_exists(output_dir / "data_scientist_agent_loop_summary.json")
    model_loop_dir = _model_loop_output_dir(output_dir, data_scientist_loop, data_scientist_summary)
    model_eval_summary = _read_json_if_exists(output_dir / "model_eval_summary.json")
    model_adapter_contract = _read_json_if_exists(output_dir / "model_adapter_contract.json")
    model_adapter_input = _read_json_if_exists(output_dir / "model_adapter_input_manifest.json")
    model_failure_modes = _read_json_if_exists(output_dir / "model_failure_modes.json")
    model_gap_report = _read_json_if_exists(output_dir / "model_informed_gap_report.json")
    model_discovery_requests = _read_json_if_exists(output_dir / "model_informed_discovery_requests.json")
    model_discovery_payloads = _read_json_if_exists(output_dir / "model_informed_discovery_payloads.json")
    model_discovery_payload_queue = _read_json_if_exists(output_dir / "model_informed_discovery_payload_queue.json")
    model_informed_curation_queue = _read_json_if_exists(output_dir / "model_informed_curation_queue.json")
    if model_loop_dir is not None:
        model_eval_summary = model_eval_summary or _read_json_if_exists(model_loop_dir / "model_eval_summary.json")
        model_adapter_contract = model_adapter_contract or _read_json_if_exists(model_loop_dir / "model_adapter_contract.json")
        model_adapter_input = model_adapter_input or _read_json_if_exists(model_loop_dir / "model_adapter_input_manifest.json")
        model_failure_modes = model_failure_modes or _read_json_if_exists(model_loop_dir / "model_failure_modes.json")
        model_gap_report = model_gap_report or _read_json_if_exists(model_loop_dir / "model_informed_gap_report.json")
        model_discovery_requests = model_discovery_requests or _read_json_if_exists(model_loop_dir / "model_informed_discovery_requests.json")
        model_discovery_payloads = model_discovery_payloads or _read_json_if_exists(model_loop_dir / "model_informed_discovery_payloads.json")
        model_discovery_payload_queue = model_discovery_payload_queue or _read_json_if_exists(model_loop_dir / "model_informed_discovery_payload_queue.json")
        model_informed_curation_queue = model_informed_curation_queue or _read_json_if_exists(model_loop_dir / "model_informed_curation_queue.json")
    model_repository_plan = build_model_informed_repository_plan(model_discovery_payloads, model_discovery_payload_queue)
    validation_report = _read_json_if_exists(output_dir / "ai_ready_validation_report.json")
    build_summary = _read_json_if_exists(output_dir / "ai_ready_build_summary.json")
    builder_summary = _read_json_if_exists(output_dir / "agentic_dataset_build_summary.json")
    downloads = {
        key: f"/api/ai-ready/{build_id}/download?file={key}"
        for key, (filename, _) in _AI_READY_DOWNLOAD_FILES.items()
        if (output_dir / filename).exists()
    }
    task_cards = []
    for item in input_profile.get("task_profiles") or []:
        if isinstance(item, dict):
            task_cards.append(
                {
                    "task_type": item.get("task_type"),
                    "status": item.get("input_status"),
                    "rows_out": None,
                    "warnings": item.get("warnings") or [],
                    "blockers": item.get("blockers") or [],
                    "target_schema": _task_schema(item.get("task_type")),
                }
            )
    for row in validation_report.get("rows") or []:
        if isinstance(row, dict):
            task_cards.append(
                {
                    "task_type": row.get("task_type"),
                    "status": row.get("status"),
                    "rows_out": row.get("rows_out"),
                    "warnings": row.get("warnings") or [],
                    "blockers": _validation_row_blockers(row),
                    "target_schema": _task_schema(row.get("task_type")),
                }
            )
    for item in real_smoke_summary.get("task_results") or []:
        if isinstance(item, dict):
            task_cards.append(
                {
                    "task_type": item.get("task_type"),
                    "status": item.get("status"),
                    "rows_out": item.get("rows_out"),
                    "warnings": item.get("warnings") or [],
                    "blockers": item.get("blockers") or [],
                    "target_schema": _task_schema(item.get("task_type")),
                }
            )
    for item in agent_run_summary.get("task_results") or []:
        if isinstance(item, dict):
            task_cards.append(
                {
                    "task_type": item.get("task_type"),
                    "status": item.get("status"),
                    "rows_out": item.get("rows_out"),
                    "warnings": item.get("warnings") or [],
                    "blockers": item.get("blockers") or [],
                    "target_schema": _task_schema(item.get("task_type")),
                }
            )
    for item in mini_e2e_summary.get("task_results") or []:
        if isinstance(item, dict):
            task_cards.append(
                {
                    "task_type": item.get("task_type"),
                    "status": item.get("status"),
                    "rows_out": item.get("rows_out"),
                    "warnings": item.get("warnings") or [],
                    "blockers": item.get("blockers") or [],
                    "target_schema": _task_schema(item.get("task_type")),
                }
            )
    for run in mini_e2e_batch_summary.get("run_results") or []:
        if not isinstance(run, dict):
            continue
        run_name = Path(str(run.get("agent_run_dir") or "")).name or "batch run"
        task_statuses = run.get("task_statuses") or {}
        rows_out = run.get("rows_out") or {}
        for task_type, status in task_statuses.items():
            task_cards.append(
                {
                    "task_type": task_type,
                    "status": status,
                    "rows_out": rows_out.get(task_type),
                    "warnings": run.get("warnings") or [],
                    "blockers": [f"run: {run_name}", *list(run.get("blockers") or [])],
                    "target_schema": _task_schema(task_type),
                }
            )
    if repository_smoke_summary:
        task_cards.append(
            {
                "task_type": f"repository:{repository_smoke_summary.get('repository') or repository_smoke_summary.get('requested_repository') or 'unknown'}",
                "status": repository_smoke_summary.get("status") or "unknown",
                "rows_out": None,
                "warnings": repository_smoke_summary.get("warnings") or [],
                "blockers": repository_smoke_summary.get("blockers") or [],
                "target_schema": repository_smoke_summary.get("download_url") or repository_smoke_summary.get("next_step") or "",
            }
        )
    if repository_audit:
        audit_rows = repository_audit.get("rows") if isinstance(repository_audit.get("rows"), list) else []
        blockers = [
            f"{row.get('repository')}:{row.get('blocker')}"
            for row in audit_rows
            if isinstance(row, dict) and row.get("blocker")
        ]
        attempted = repository_audit.get("repositories_attempted") or [
            row.get("repository") for row in audit_rows if isinstance(row, dict) and row.get("repository")
        ]
        task_cards.append(
            {
                "task_type": "repository_audit",
                "status": repository_audit.get("status") or ("partial" if blockers else "available"),
                "rows_out": len(audit_rows),
                "warnings": [f"attempted:{','.join(map(str, attempted))}"] if attempted else [],
                "blockers": blockers,
                "target_schema": repository_audit.get("source") or "",
            }
        )
    if iprox_index_summary:
        task_cards.append(
            {
                "task_type": "iprox_index",
                "status": iprox_index_summary.get("status") or "available",
                "rows_out": iprox_index_summary.get("file_count"),
                "warnings": [f"projects:{iprox_index_summary.get('project_count') or 0}"],
                "blockers": [
                    str(item.get("error") or item)
                    for item in iprox_index_summary.get("failures") or []
                    if item
                ],
                "target_schema": iprox_index_summary.get("next_step") or "",
            }
        )
    for case in agent_harness_summary.get("case_results") or []:
        if isinstance(case, dict):
            inferred = case.get("inferred") if isinstance(case.get("inferred"), dict) else {}
            task_cards.append(
                {
                    "task_type": f"harness:{case.get('id') or 'case'}",
                    "status": case.get("status") or "unknown",
                    "rows_out": None,
                    "warnings": case.get("warnings") or [],
                    "blockers": case.get("blockers") or [],
                    "target_schema": inferred.get("next_action_category") or "",
                }
            )
    if dataset_recipe:
        recipe_blockers = []
        if leakage_risk_report.get("status") in {"fail", "warn"}:
            recipe_blockers.append(f"leakage:{leakage_risk_report.get('status')}")
        gap_count = len(coverage_gap_report.get("gaps") or [])
        if gap_count:
            recipe_blockers.append(f"gaps:{gap_count}")
        hard_count = int((hard_benchmark.get("row_count") if isinstance(hard_benchmark.get("row_count"), int) else None) or len(hard_benchmark.get("rows") or []))
        counterfactual_count = int((counterfactual_benchmark.get("row_count") if isinstance(counterfactual_benchmark.get("row_count"), int) else None) or len(counterfactual_benchmark.get("rows") or []))
        curation_count = int((curation_queue.get("row_count") if isinstance(curation_queue.get("row_count"), int) else None) or len(curation_queue.get("rows") or []))
        recipe_warnings = list(leakage_risk_report.get("warnings") or [])
        if hard_count:
            recipe_warnings.append(f"hard_benchmark:{hard_count}")
        if counterfactual_count:
            recipe_warnings.append(f"counterfactual_benchmark:{counterfactual_count}")
        if curation_count:
            recipe_warnings.append(f"curation_queue:{curation_count}")
        task_cards.append(
            {
                "task_type": "dataset_recipe",
                "status": dataset_recipe.get("status") or "available",
                "rows_out": len(dataset_recipe.get("selected_files") or []),
                "warnings": recipe_warnings,
                "blockers": recipe_blockers,
                "target_schema": dataset_recipe.get("split_strategy_resolved") or dataset_recipe.get("split_policy") or dataset_recipe.get("split_level") or "",
            }
        )
    if model_eval_summary:
        failure_count = len(model_failure_modes.get("failure_modes") or [])
        gap_count = len(model_gap_report.get("gaps") or [])
        model_blockers = list((model_eval_summary.get("validation") or {}).get("blockers") or [])
        model_warnings = list((model_eval_summary.get("validation") or {}).get("warnings") or [])
        for warning in model_eval_summary.get("adapter_contract_warnings") or []:
            model_warnings.append(f"adapter_contract:{warning}")
        if not model_adapter_contract:
            model_warnings.append("model_adapter_contract_missing")
        if failure_count:
            model_warnings.append(f"failure_modes:{failure_count}")
        if gap_count:
            model_warnings.append(f"model_gaps:{gap_count}")
        discovery_request_count = int(model_discovery_requests.get("request_count") or len(model_discovery_requests.get("requests") or []))
        if discovery_request_count:
            model_warnings.append(f"discovery_requests:{discovery_request_count}")
        planned_repositories = model_repository_plan.get("planned_repositories") or []
        if planned_repositories:
            model_warnings.append(f"planned_repositories:{','.join(map(str, planned_repositories))}")
        model_rows_out = (model_eval_summary.get("metrics") or {}).get("total_rows")
        if model_rows_out is None:
            model_rows_out = ((model_adapter_input.get("summary") or {}).get("total_rows_out") if model_adapter_input else None)
        task_cards.append(
            {
                "task_type": f"model_loop:{model_eval_summary.get('task_type') or 'task'}",
                "status": model_eval_summary.get("status") or "available",
                "rows_out": model_rows_out,
                "warnings": model_warnings,
                "blockers": model_blockers,
                "target_schema": model_repository_plan.get("repository_strategy")
                or f"{model_eval_summary.get('adapter') or ''}:{model_eval_summary.get('metric_status') or ''}",
            }
        )
    if data_scientist_summary:
        ds_warnings = list(data_scientist_summary.get("warnings") or [])
        leakage_status = ((data_scientist_summary.get("leakage") or {}).get("status") or "not_evaluated")
        if leakage_status in {"fail", "warn"}:
            ds_warnings.append(f"leakage:{leakage_status}")
        task_cards.append(
            {
                "task_type": "data_scientist_agent_report",
                "status": data_scientist_summary.get("status") or "available",
                "rows_out": data_scientist_summary.get("selected_count"),
                "warnings": ds_warnings,
                "blockers": [],
                "target_schema": f"model_loop:{(data_scientist_summary.get('model_loop') or {}).get('status') or 'not_available'}",
            }
        )
    if guidance_alignment:
        summary = guidance_alignment.get("summary") if isinstance(guidance_alignment.get("summary"), dict) else {}
        task_cards.append(
            {
                "task_type": "guidance_alignment",
                "status": guidance_alignment.get("status") or "available",
                "rows_out": summary.get("achieved"),
                "warnings": [f"partial:{summary.get('partial', 0)}", f"missing:{summary.get('missing', 0)}"],
                "blockers": [],
                "target_schema": "260617_to_do_alignment",
            }
        )
    if data_scientist_loop:
        task_cards.append(
            {
                "task_type": "data_scientist_agent_loop",
                "status": data_scientist_loop.get("status") or "available",
                "rows_out": data_scientist_loop.get("selected_count"),
                "warnings": data_scientist_loop.get("warnings") or [],
                "blockers": data_scientist_loop.get("blockers") or [],
                "target_schema": f"guidance:{data_scientist_loop.get('guidance_alignment_status') or 'not_available'}",
            }
        )
    if curation_memory_update:
        task_cards.append(
            {
                "task_type": "active_curation_memory",
                "status": curation_memory_update.get("status") or "available",
                "rows_out": curation_memory_update.get("imported_decision_count") or 0,
                "warnings": [f"skipped:{curation_memory_update.get('skipped_count') or 0}"],
                "blockers": [],
                "target_schema": "discovery_memory_review_decisions",
            }
        )
    recovery_cards = _ai_ready_recovery_cards(mini_e2e_summary, mini_e2e_batch_summary)
    return {
        "status": curation_memory_update.get("status") or data_scientist_loop.get("status") or guidance_alignment.get("status") or data_scientist_summary.get("status") or model_eval_summary.get("status") or dataset_recipe.get("status") or agent_harness_summary.get("status") or iprox_index_summary.get("status") or repository_smoke_summary.get("status") or mini_e2e_batch_summary.get("status") or mini_e2e_summary.get("status") or agent_run_summary.get("status") or real_smoke_summary.get("status") or build_summary.get("status") or validation_report.get("status") or input_profile.get("status") or agent_run_locations.get("status") or input_locations.get("status") or "available",
        "build_id": build_id,
        "output_dir": str(output_dir),
        "input_locations": input_locations,
        "agent_run_input_locations": agent_run_locations,
        "agent_run_build_summary": agent_run_summary,
        "mini_e2e_summary": mini_e2e_summary,
        "mini_e2e_batch_summary": mini_e2e_batch_summary,
        "input_profile": input_profile,
        "real_smoke_summary": real_smoke_summary,
        "repository_smoke_summary": repository_smoke_summary,
        "repository_audit": repository_audit,
        "iprox_index_summary": iprox_index_summary,
        "agent_harness_summary": agent_harness_summary,
        "dataset_recipe": dataset_recipe,
        "leakage_risk_report": leakage_risk_report,
        "coverage_gap_report": coverage_gap_report,
        "hard_benchmark": hard_benchmark,
        "counterfactual_benchmark": counterfactual_benchmark,
        "curation_queue": curation_queue,
        "curation_memory_update": curation_memory_update,
        "model_eval_summary": model_eval_summary,
        "model_adapter_contract": model_adapter_contract,
        "model_adapter_input_manifest": model_adapter_input,
        "model_failure_modes": model_failure_modes,
        "model_informed_gap_report": model_gap_report,
        "model_informed_discovery_requests": model_discovery_requests,
        "model_informed_discovery_payloads": model_discovery_payloads,
        "model_informed_discovery_payload_queue": model_discovery_payload_queue,
        "model_informed_repository_plan": model_repository_plan,
        "model_informed_curation_queue": model_informed_curation_queue,
        "real_data_scientist_agent_summary": data_scientist_summary,
        "guidance_alignment_report": guidance_alignment,
        "data_scientist_agent_loop_summary": data_scientist_loop,
        "validation_report": validation_report,
        "build_summary": build_summary,
        "builder_summary": builder_summary,
        "task_cards": task_cards,
        "recovery_cards": recovery_cards,
        "downloads": downloads,
    }


def _ai_ready_recovery_cards(mini_e2e_summary: dict[str, Any], mini_e2e_batch_summary: dict[str, Any]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    if mini_e2e_summary:
        upstream_issue = mini_e2e_summary.get("upstream_primary_issue")
        upstream_status = mini_e2e_summary.get("upstream_recovery_status")
        if upstream_issue or upstream_status:
            cards.append(
                {
                    "scope": "upstream_full",
                    "label": "Upstream full",
                    "status": upstream_status or "not_run",
                    "workflow_outcome": mini_e2e_summary.get("upstream_workflow_outcome") or "unknown",
                    "usable_partial_outputs": bool(mini_e2e_summary.get("upstream_usable_partial_outputs")),
                    "primary_issue": upstream_issue or "none",
                    "recommended_next_step": mini_e2e_summary.get("upstream_recommended_next_step") or "",
                }
            )
        task_issue = mini_e2e_summary.get("primary_issue")
        task_status = mini_e2e_summary.get("recovery_status")
        if task_issue or task_status:
            cards.append(
                {
                    "scope": "task_build",
                    "label": "Task build",
                    "status": task_status or "not_run",
                    "ai_ready_outcome": mini_e2e_summary.get("ai_ready_outcome") or "unknown",
                    "usable_partial_outputs": bool(mini_e2e_summary.get("usable_partial_outputs")),
                    "primary_issue": task_issue or "none",
                    "recommended_next_step": mini_e2e_summary.get("recommended_next_step") or "",
                }
            )
    for run in mini_e2e_batch_summary.get("run_results") or []:
        if not isinstance(run, dict):
            continue
        run_name = Path(str(run.get("agent_run_dir") or "")).name or "batch run"
        upstream_issue = run.get("upstream_primary_issue")
        upstream_status = run.get("upstream_recovery_status")
        if upstream_issue or upstream_status:
            cards.append(
                {
                    "scope": "upstream_full",
                    "label": f"{run_name} upstream",
                    "status": upstream_status or "not_run",
                    "workflow_outcome": run.get("upstream_workflow_outcome") or "unknown",
                    "usable_partial_outputs": bool(run.get("upstream_usable_partial_outputs")),
                    "primary_issue": upstream_issue or "none",
                    "recommended_next_step": run.get("upstream_recommended_next_step") or "",
                }
            )
    return cards


def _run_ai_ready_input_profile(body: dict[str, Any]) -> dict[str, Any]:
    build_id = safe_output_stem(_clean_text(body.get("build_id")) or _new_ai_ready_build_id("profile"))
    output_dir = _safe_ai_ready_dir(build_id)
    if output_dir is None:
        raise ValueError("Invalid build_id.")
    search_results = _clean_path_list(body.get("search_results") or body.get("search_result"))
    peaklists = _clean_path_list(body.get("peaklists") or body.get("peaklist"))
    search_dir = _clean_text(body.get("search_dir"))
    if search_dir and not search_results:
        locator = locate_ai_ready_inputs(search_dir=search_dir, output_dir=output_dir)
        located_search_results, located_peaklists = select_ai_ready_inputs(
            locator,
            task_type=(_clean_ai_ready_task_types(body) or ["rt_prediction"])[0],
        )
        search_results = [str(path) for path in located_search_results]
        if not peaklists:
            peaklists = [str(path) for path in located_peaklists]
    result = profile_ai_ready_inputs(
        search_results=search_results,
        peaklists=peaklists,
        task_types=_clean_ai_ready_task_types(body),
        output_dir=output_dir,
    )
    record = _public_ai_ready_record(build_id, output_dir)
    record["profile_rows_in"] = result.rows_in
    return record


def _run_ai_ready_input_locator(body: dict[str, Any]) -> dict[str, Any]:
    build_id = safe_output_stem(_clean_text(body.get("build_id")) or _new_ai_ready_build_id("locator"))
    output_dir = _safe_ai_ready_dir(build_id)
    if output_dir is None:
        raise ValueError("Invalid build_id.")
    search_dir = _clean_text(body.get("search_dir"))
    if not search_dir:
        raise ValueError("Search output directory is required.")
    locate_ai_ready_inputs(search_dir=search_dir, output_dir=output_dir)
    return _public_ai_ready_record(build_id, output_dir)


def _run_agent_run_input_locator(body: dict[str, Any]) -> dict[str, Any]:
    build_id = safe_output_stem(_clean_text(body.get("build_id")) or _new_ai_ready_build_id("agent_run_locator"))
    output_dir = _safe_ai_ready_dir(build_id)
    if output_dir is None:
        raise ValueError("Invalid build_id.")
    agent_run_dir = _clean_text(body.get("agent_run_dir"))
    if not agent_run_dir:
        raise ValueError("Original agent run directory is required.")
    locate_agent_run_inputs(
        agent_run_dir=agent_run_dir,
        output_dir=output_dir,
        max_input_file_mb=int(body.get("max_input_file_mb") or 2048),
        allow_large_input=bool(body.get("allow_large_input")),
    )
    return _public_ai_ready_record(build_id, output_dir)


def _run_agent_run_ai_ready_build(body: dict[str, Any]) -> dict[str, Any]:
    build_id = safe_output_stem(_clean_text(body.get("build_id")) or _new_ai_ready_build_id("agent_run_build"))
    output_dir = _safe_ai_ready_dir(build_id)
    if output_dir is None:
        raise ValueError("Invalid build_id.")
    agent_run_dir = _clean_text(body.get("agent_run_dir"))
    if not agent_run_dir:
        raise ValueError("Original agent run directory is required.")
    build_ai_ready_from_agent_run(
        agent_run_dir=agent_run_dir,
        task_types=_clean_ai_ready_task_types(body),
        output_dir=output_dir,
        max_input_file_mb=int(body.get("max_input_file_mb") or 2048),
        allow_large_input=bool(body.get("allow_large_input")),
        peaklists=_clean_path_list(body.get("peaklists") or body.get("peaklist")),
    )
    return _public_ai_ready_record(build_id, output_dir)


def _run_agent_run_mini_e2e(body: dict[str, Any]) -> dict[str, Any]:
    build_id = safe_output_stem(_clean_text(body.get("build_id")) or _new_ai_ready_build_id("mini_e2e"))
    output_dir = _safe_ai_ready_dir(build_id)
    if output_dir is None:
        raise ValueError("Invalid build_id.")
    agent_run_dir = _clean_text(body.get("agent_run_dir"))
    if not agent_run_dir:
        raise ValueError("Original agent run directory is required.")
    validate_agent_run_ai_ready_mini(
        agent_run_dir=agent_run_dir,
        task_types=_clean_ai_ready_task_types(body),
        output_dir=output_dir,
        max_input_file_mb=int(body.get("max_input_file_mb") or 2048),
        allow_large_input=bool(body.get("allow_large_input")),
        peaklists=_clean_path_list(body.get("peaklists") or body.get("peaklist")),
    )
    return _public_ai_ready_record(build_id, output_dir)


def _run_agent_runs_mini_e2e_batch(body: dict[str, Any]) -> dict[str, Any]:
    build_id = safe_output_stem(_clean_text(body.get("build_id")) or _new_ai_ready_build_id("mini_e2e_batch"))
    output_dir = _safe_ai_ready_dir(build_id)
    if output_dir is None:
        raise ValueError("Invalid build_id.")
    agent_run_dirs = _clean_path_list(body.get("agent_run_dirs") or body.get("agent_run_dir"))
    if not agent_run_dirs:
        raise ValueError("At least one original agent run directory is required.")
    validate_agent_runs_ai_ready_batch(
        agent_run_dirs=agent_run_dirs,
        task_types=_clean_ai_ready_task_types(body),
        output_dir=output_dir,
        max_input_file_mb=int(body.get("max_input_file_mb") or 2048),
        allow_large_input=bool(body.get("allow_large_input")),
        peaklists=_clean_path_list(body.get("peaklists") or body.get("peaklist")),
        auto_recover=bool(body.get("auto_recover", True)),
    )
    return _public_ai_ready_record(build_id, output_dir)


def _run_ai_ready_real_smoke(body: dict[str, Any]) -> dict[str, Any]:
    build_id = safe_output_stem(_clean_text(body.get("build_id")) or _new_ai_ready_build_id("real_smoke"))
    output_dir = _safe_ai_ready_dir(build_id)
    if output_dir is None:
        raise ValueError("Invalid build_id.")
    search_dir = _clean_text(body.get("search_dir"))
    if not search_dir:
        raise ValueError("Search output directory is required.")
    run_ai_ready_real_smoke(
        search_dir=search_dir,
        task_types=_clean_ai_ready_task_types(body),
        output_dir=output_dir,
    )
    return _public_ai_ready_record(build_id, output_dir)


def _run_repository_smoke_web(body: dict[str, Any]) -> dict[str, Any]:
    build_id = safe_output_stem(_clean_text(body.get("build_id")) or _new_ai_ready_build_id("repository_smoke"))
    output_dir = _safe_ai_ready_dir(build_id)
    if output_dir is None:
        raise ValueError("Invalid build_id.")
    repository = _clean_text(body.get("repository")) or "auto"
    input_value = _clean_text(body.get("input_value") or body.get("accession") or body.get("file"))
    if not input_value:
        raise ValueError("Repository accession or file path is required.")
    mode = _clean_text(body.get("mode")) or "parameters"
    if mode not in {"parameters", "prepare", "full"}:
        raise ValueError("Mode must be parameters, prepare, or full.")
    iprox_index_dir = _clean_text(body.get("iprox_index_dir")) or None
    registry = None
    if iprox_index_dir:
        registry = RepositoryRegistry(adapters=[PrideAdapter(), MassiveAdapter(), IproxAdapter(index_path=iprox_index_dir)])
    run_repository_smoke(
        repository=repository,
        input_value=input_value,
        mode=mode,  # type: ignore[arg-type]
        output_dir=output_dir,
        registry=registry,
    )
    return _public_ai_ready_record(build_id, output_dir)


def _split_clean_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        raw_items = value
    else:
        raw_items = re.split(r"[\r\n,;]+", str(value))
    return [text for item in raw_items if (text := _clean_text(item))]


def _run_iprox_index_refresh_web(body: dict[str, Any]) -> dict[str, Any]:
    build_id = safe_output_stem(_clean_text(body.get("build_id")) or _new_ai_ready_build_id("iprox_index"))
    output_dir = _safe_ai_ready_dir(build_id)
    if output_dir is None:
        raise ValueError("Invalid build_id.")
    years: list[int] = []
    for item in _split_clean_text(body.get("years")):
        if not str(item).isdigit():
            raise ValueError(f"Invalid iProX year: {item}")
        years.append(int(item))
    projects = _split_clean_text(body.get("projects") or body.get("project_ids") or body.get("project"))
    max_projects_raw = _clean_text(body.get("max_projects")) or None
    max_projects = int(max_projects_raw) if max_projects_raw and str(max_projects_raw).isdigit() else None
    if not years and not projects:
        raise ValueError("At least one iProX year or project accession is required.")
    refresh_public_iprox_index(
        years=years,
        project_ids=projects,
        output_dir=output_dir,
        max_projects=max_projects,
    )
    return _public_ai_ready_record(build_id, output_dir)


def _run_agent_harness_web(body: dict[str, Any]) -> dict[str, Any]:
    build_id = safe_output_stem(_clean_text(body.get("build_id")) or _new_ai_ready_build_id("agent_harness"))
    output_dir = _safe_ai_ready_dir(build_id)
    if output_dir is None:
        raise ValueError("Invalid build_id.")
    case_file = _clean_text(body.get("case_file")) or "tests/fixtures/agent_harness_cases.json"
    use_llm = bool(body.get("use_llm", True))
    run_agent_harness(
        case_file=case_file,
        output_dir=output_dir,
        use_llm=use_llm,
    )
    return _public_ai_ready_record(build_id, output_dir)


def _run_dataset_recipe_web(body: dict[str, Any]) -> dict[str, Any]:
    build_id = safe_output_stem(_clean_text(body.get("build_id")) or _new_ai_ready_build_id("dataset_recipe"))
    output_dir = _safe_ai_ready_dir(build_id)
    if output_dir is None:
        raise ValueError("Invalid build_id.")
    batch_dir = _clean_text(body.get("batch_dir"))
    if not batch_dir:
        raise ValueError("Mini E2E batch directory is required.")
    discovery_manifest = _clean_text(body.get("discovery_manifest"))
    repository_audit = _clean_text(body.get("repository_audit"))
    split_strategy = _clean_text(body.get("split_strategy")) or "auto"
    make_dataset_recipe(
        batch_dir=batch_dir,
        output_dir=output_dir,
        discovery_manifest=Path(discovery_manifest) if discovery_manifest else None,
        repository_audit=Path(repository_audit) if repository_audit else None,
        split_strategy=split_strategy,
    )
    return _public_ai_ready_record(build_id, output_dir)


def _path_list_from_body(value: Any) -> list[Path]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        raw_items = value
    else:
        raw_items = re.split(r"[\r\n;]+", str(value))
    paths: list[Path] = []
    for item in raw_items:
        text = _clean_text(item)
        if text:
            paths.append(Path(text))
    return paths


def _run_apply_curation_decisions_web(body: dict[str, Any]) -> dict[str, Any]:
    build_id = safe_output_stem(_clean_text(body.get("build_id")) or _new_ai_ready_build_id("curation_memory"))
    output_dir = _safe_ai_ready_dir(build_id)
    if output_dir is None:
        raise ValueError("Invalid build_id.")
    curation_queue = _clean_text(body.get("curation_queue"))
    if not curation_queue:
        recipe_dir = _clean_text(body.get("recipe_dir"))
        if recipe_dir:
            curation_queue = str(Path(recipe_dir) / "curation_queue.json")
    if not curation_queue:
        raise ValueError("curation_queue or recipe_dir is required.")
    decisions_csv = _clean_text(body.get("decisions_csv")) or None
    default_decision = _clean_text(body.get("default_decision")) or None
    memory_dir = _clean_text(body.get("memory_dir")) or str(_discovery_memory_dir())
    run_id = _clean_text(body.get("run_id")) or "active_curation"
    apply_curation_decisions_to_memory(
        curation_queue=curation_queue,
        output_dir=output_dir,
        memory_dir=memory_dir,
        decisions_csv=Path(decisions_csv) if decisions_csv else None,
        default_decision=default_decision,
        run_id=run_id,
    )
    return _public_ai_ready_record(build_id, output_dir)


def _run_dataset_model_loop_web(body: dict[str, Any]) -> dict[str, Any]:
    build_id = safe_output_stem(_clean_text(body.get("build_id")) or _new_ai_ready_build_id("model_loop"))
    output_dir = _safe_ai_ready_dir(build_id)
    if output_dir is None:
        raise ValueError("Invalid build_id.")
    recipe_dir = _clean_text(body.get("recipe_dir"))
    if not recipe_dir:
        raise ValueError("Recipe directory is required.")
    task_type = normalize_task_type(_clean_text(body.get("task_type")) or "rt_prediction")
    if task_type is None:
        raise ValueError("Task type is required.")
    mode = _clean_text(body.get("mode")) or "smoke"
    adapter = _clean_text(body.get("adapter")) or "dry_run"
    adapter_command = _clean_text(body.get("adapter_command")) or None
    metrics_file = _clean_text(body.get("metrics_file")) or None
    run_dataset_model_loop(
        recipe_dir=recipe_dir,
        task_type=task_type,
        output_dir=output_dir,
        mode=mode,  # type: ignore[arg-type]
        adapter=adapter,
        adapter_command=adapter_command,
        metrics_file=Path(metrics_file) if metrics_file else None,
    )
    return _public_ai_ready_record(build_id, output_dir)


def _run_data_scientist_agent_report_web(body: dict[str, Any]) -> dict[str, Any]:
    build_id = safe_output_stem(_clean_text(body.get("build_id")) or _new_ai_ready_build_id("data_scientist_report"))
    output_dir = _safe_ai_ready_dir(build_id)
    if output_dir is None:
        raise ValueError("Invalid build_id.")
    recipe_dir = _clean_text(body.get("recipe_dir"))
    if not recipe_dir:
        raise ValueError("Recipe directory is required.")
    model_loop_dir = _clean_text(body.get("model_loop_dir")) or None
    benchmark_dir = _clean_text(body.get("benchmark_dir")) or None
    discovery_manifest = _clean_text(body.get("discovery_manifest")) or None
    guidance_alignment_dir = _clean_text(body.get("guidance_alignment_dir")) or None
    make_data_scientist_agent_report(
        recipe_dir=recipe_dir,
        output_dir=output_dir,
        model_loop_dir=Path(model_loop_dir) if model_loop_dir else None,
        benchmark_dir=Path(benchmark_dir) if benchmark_dir else None,
        discovery_manifest=Path(discovery_manifest) if discovery_manifest else None,
        guidance_alignment_dir=Path(guidance_alignment_dir) if guidance_alignment_dir else None,
    )
    return _public_ai_ready_record(build_id, output_dir)


def _run_guidance_alignment_report_web(body: dict[str, Any]) -> dict[str, Any]:
    build_id = safe_output_stem(_clean_text(body.get("build_id")) or _new_ai_ready_build_id("guidance_alignment"))
    output_dir = _safe_ai_ready_dir(build_id)
    if output_dir is None:
        raise ValueError("Invalid build_id.")
    recipe_dir = _clean_text(body.get("recipe_dir")) or None
    discovery_dir = _clean_text(body.get("discovery_dir")) or None
    discovery_manifest = _clean_text(body.get("discovery_manifest")) or None
    model_loop_dir = _clean_text(body.get("model_loop_dir")) or None
    benchmark_dir = _clean_text(body.get("benchmark_dir")) or None
    make_guidance_alignment_report(
        output_dir=output_dir,
        recipe_dir=Path(recipe_dir) if recipe_dir else None,
        discovery_dir=Path(discovery_dir) if discovery_dir else None,
        discovery_manifest=Path(discovery_manifest) if discovery_manifest else None,
        model_loop_dir=Path(model_loop_dir) if model_loop_dir else None,
        benchmark_dir=Path(benchmark_dir) if benchmark_dir else None,
    )
    return _public_ai_ready_record(build_id, output_dir)


def _run_data_scientist_agent_loop_web(body: dict[str, Any]) -> dict[str, Any]:
    build_id = safe_output_stem(_clean_text(body.get("build_id")) or _new_ai_ready_build_id("data_scientist_loop"))
    output_dir = _safe_ai_ready_dir(build_id)
    if output_dir is None:
        raise ValueError("Invalid build_id.")
    batch_dir = _clean_text(body.get("batch_dir"))
    if not batch_dir:
        raise ValueError("Batch/benchmark directory is required.")
    discovery_manifest = _clean_text(body.get("discovery_manifest")) or None
    task_type = _clean_text(body.get("task_type")) or "auto"
    split_strategy = _clean_text(body.get("split_strategy")) or "auto"
    adapter_command = _clean_text(body.get("adapter_command")) or None
    metrics_file = _clean_text(body.get("metrics_file")) or None
    strategy_case = _clean_text(body.get("strategy_comparison_case_file")) or None
    curation_decisions_csv = _clean_text(body.get("curation_decisions_csv")) or None
    curation_default_decision = _clean_text(body.get("curation_default_decision")) or None
    curation_memory_dir = _clean_text(body.get("curation_memory_dir")) or None
    repository_smoke_dirs = _path_list_from_body(body.get("repository_smoke_dirs"))
    run_data_scientist_agent_loop(
        batch_dir=Path(batch_dir),
        output_dir=output_dir,
        task_type=task_type,
        discovery_manifest=Path(discovery_manifest) if discovery_manifest else None,
        split_strategy=split_strategy,
        mode="smoke",
        adapter="external_command" if adapter_command else "dry_run",
        adapter_command=adapter_command,
        metrics_file=Path(metrics_file) if metrics_file else None,
        strategy_comparison_case_file=Path(strategy_case) if strategy_case else None,
        curation_decisions_csv=Path(curation_decisions_csv) if curation_decisions_csv else None,
        curation_default_decision=curation_default_decision,
        curation_memory_dir=Path(curation_memory_dir) if curation_memory_dir else None,
        repository_smoke_dirs=repository_smoke_dirs or None,
    )
    return _public_ai_ready_record(build_id, output_dir)


def _run_ai_ready_validation(body: dict[str, Any]) -> dict[str, Any]:
    build_id = safe_output_stem(_clean_text(body.get("build_id")) or _new_ai_ready_build_id("validate"))
    requested_dir = _clean_text(body.get("build_dir"))
    if requested_dir:
        output_dir = Path(requested_dir)
        build_id = safe_output_stem(_clean_text(body.get("build_id")) or output_dir.name or build_id)
    else:
        output_dir = _safe_ai_ready_dir(build_id)
        if output_dir is None:
            raise ValueError("Invalid build_id.")
    task_type = normalize_task_type(_clean_text(body.get("task_type")) or "rt_prediction")
    if task_type is None:
        raise ValueError("Task type is required.")
    validate_ai_ready_build(output_dir, task_type)
    return _public_ai_ready_record(build_id, output_dir)


def _task_schema(task_type: Any) -> str:
    try:
        return get_task_profile(str(task_type or "")).ai_ready_target_schema
    except Exception:
        return ""


def _validation_row_blockers(row: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if row.get("status") in {"export_missing", "export_empty", "planned_not_exported"}:
        blockers.append(str(row.get("status")))
    try:
        rows_out = int(row.get("rows_out") or 0)
    except (TypeError, ValueError):
        rows_out = 0
    if rows_out > 0:
        return blockers
    filter_counts = row.get("filter_counts") if isinstance(row.get("filter_counts"), dict) else {}
    if filter_counts.get("spectrum_not_matched"):
        blockers.append("spectrum_not_matched")
    if filter_counts.get("no_multi_peptide_assignment"):
        blockers.append("no_multi_peptide_assignment")
    missing = row.get("missing_required_column_counts")
    if isinstance(missing, dict):
        blockers.extend(f"missing_column:{key}" for key in sorted(missing))
    return blockers


def _read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _now_app_iso() -> str:
    return datetime.now(_APP_TZ).isoformat()


def _discovery_job_public(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "cancel_requested": bool(job.get("cancel_requested")),
        "logs": list(job.get("logs") or []),
        "record": job.get("record"),
        "error": job.get("error"),
    }


def _discovery_jobs_dir() -> Path:
    return _runs_dir / "discovery_jobs"


def _discovery_job_path(job_id: str) -> Path:
    return _discovery_jobs_dir() / f"{safe_output_stem(job_id)}.json"


def _persist_discovery_job(job: dict[str, Any]) -> None:
    try:
        write_json(_discovery_job_path(str(job.get("job_id") or "")), _discovery_job_public(job))
    except Exception:
        # Job status persistence is best-effort; the in-memory job remains authoritative.
        return


def _load_discovery_job(job_id: str) -> dict[str, Any] | None:
    payload = _read_json_if_exists(_discovery_job_path(job_id))
    if not payload:
        return None
    payload.setdefault("job_id", job_id)
    payload.setdefault("logs", [])
    payload.setdefault("cancel_requested", False)
    payload.setdefault("record", None)
    payload.setdefault("error", None)
    return payload


def _mark_interrupted_discovery_job(job: dict[str, Any]) -> dict[str, Any]:
    if job.get("status") not in {"queued", "running"}:
        return job
    logs = job.setdefault("logs", [])
    message = "Discovery job was interrupted by a server reload. Please start it again."
    if not any(item.get("message") == message for item in logs if isinstance(item, dict)):
        logs.append({"ts": _now_app_iso(), "level": "error", "message": message})
    job["status"] = "failed"
    job["error"] = "discovery_job_interrupted_by_server_reload"
    job["finished_at"] = job.get("finished_at") or _now_app_iso()
    return job


def _append_discovery_job_log(job_id: str, level: str, message: str) -> None:
    with _discovery_jobs_lock:
        job = _discovery_jobs.get(job_id)
        if not job:
            return
        logs = job.setdefault("logs", [])
        sequence = max((int(item.get("sequence") or 0) for item in logs if isinstance(item, dict)), default=0) + 1
        logs.append(
            {
                "sequence": sequence,
                "ts": _now_app_iso(),
                "level": level,
                "actor": "Discovery Agent",
                "type": "job_message",
                "message": _redact_secrets(str(message)),
                "reasoning_summary": "",
                "evidence_refs": [],
                "metrics": {},
                "payload": {},
            }
        )
        if len(logs) > _MAX_PERSISTED_LOGS:
            del logs[: len(logs) - _MAX_PERSISTED_LOGS]
        _persist_discovery_job(job)


def _event_actor(event_type: str) -> str:
    if event_type.startswith("budget_"):
        return "Budget Agent"
    if "grant" in event_type or event_type == "dynamic_search_stopped":
        return "BudgetGovernor"
    if event_type.startswith("sdk_"):
        return "OpenAI Agents SDK"
    if event_type.startswith("tool_") or event_type == "repository_request_started":
        return "Repository tool"
    return "Discovery Agent"


def _event_level(event_type: str) -> str:
    if "invalid" in event_type or "rejected" in event_type or "failed" in event_type:
        return "warning"
    return "info"


def _event_message(event: AgentEvent) -> str:
    payload = event.payload
    return _redact_secrets(
        str(
            payload.get("reasoning_summary")
            or payload.get("reason")
            or payload.get("message")
            or event.event_type.replace("_", " ")
        )
    )


def _sanitize_log_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize_log_payload(item)
            for key, item in value.items()
            if str(key).casefold() not in {"api_key", "authorization", "sdk_state_json"}
        }
    if isinstance(value, list):
        return [_sanitize_log_payload(item) for item in value]
    if isinstance(value, str):
        return _redact_secrets(value)
    return _json_safe(value)


def _append_discovery_job_event(job_id: str, event: AgentEvent) -> None:
    entry = {
        "source_sequence": event.sequence,
        "ts": event.created_at,
        "level": _event_level(event.event_type),
        "actor": _event_actor(event.event_type),
        "type": event.event_type,
        "message": _event_message(event),
        "reasoning_summary": _redact_secrets(str(event.payload.get("reasoning_summary") or "")),
        "evidence_refs": _sanitize_log_payload(event.payload.get("evidence_refs") or []),
        "metrics": _sanitize_log_payload(
            event.payload if event.event_type == "round_value_evaluated" else event.payload.get("metrics") or {}
        ),
        "payload": _sanitize_log_payload(event.payload),
    }
    with _discovery_jobs_lock:
        job = _discovery_jobs.get(job_id)
        if not job:
            return
        logs = job.setdefault("logs", [])
        entry["sequence"] = max(
            (int(item.get("sequence") or 0) for item in logs if isinstance(item, dict)),
            default=0,
        ) + 1
        logs.append(entry)
        if len(logs) > _MAX_PERSISTED_LOGS:
            del logs[: len(logs) - _MAX_PERSISTED_LOGS]
        _persist_discovery_job(job)


def _discovery_cancel_requested(job_id: str) -> bool:
    with _discovery_jobs_lock:
        job = _discovery_jobs.get(job_id)
        return bool(job and job.get("cancel_requested"))


def _run_discovery_job(job_id: str) -> None:
    with _discovery_jobs_lock:
        job = _discovery_jobs.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["started_at"] = _now_app_iso()
        body = dict(job.get("body") or {})
        # Keep the request credential only in this worker's local copy.
        job["body"].pop("llm_config", None)
        _persist_discovery_job(job)
    _append_discovery_job_log(job_id, "info", "Discovery job started.")

    def report(message: str) -> None:
        _append_discovery_job_log(job_id, "info", message)

    def should_cancel() -> bool:
        return _discovery_cancel_requested(job_id)

    try:
        if should_cancel():
            raise InterruptedError("Discovery cancelled.")
        record = _run_web_discovery(
            body,
            report=report,
            should_cancel=should_cancel,
            agent_event_callback=lambda event: _append_discovery_job_event(job_id, event),
        )
        with _discovery_jobs_lock:
            job = _discovery_jobs.get(job_id)
            if not job:
                return
            job["record"] = record
            job["status"] = "cancelled" if should_cancel() else "completed"
            job["finished_at"] = _now_app_iso()
            _persist_discovery_job(job)
        _append_discovery_job_log(job_id, "info", "Discovery job completed.")
    except InterruptedError as exc:
        with _discovery_jobs_lock:
            job = _discovery_jobs.get(job_id)
            if job:
                job["status"] = "cancelled"
                job["error"] = str(exc)
                job["finished_at"] = _now_app_iso()
                _persist_discovery_job(job)
        _append_discovery_job_log(job_id, "warning", str(exc))
    except Exception as exc:  # pragma: no cover - defensive job boundary
        with _discovery_jobs_lock:
            job = _discovery_jobs.get(job_id)
            if job:
                job["status"] = "failed"
                job["error"] = _redact_secrets(str(exc))
                job["finished_at"] = _now_app_iso()
                _persist_discovery_job(job)
        _append_discovery_job_log(job_id, "error", f"Discovery failed: {exc}")


def _start_discovery_job_thread(job_id: str) -> None:
    thread = threading.Thread(target=_run_discovery_job, args=(job_id,), daemon=True)
    thread.start()


@app.post("/api/discovery/jobs")
async def start_discovery_job(body: dict[str, Any], background_tasks: BackgroundTasks = None):
    job_id = safe_output_stem(f"discovery_job_{datetime.now(_APP_TZ).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}")
    job = {
        "job_id": job_id,
        "status": "queued",
        "created_at": _now_app_iso(),
        "started_at": None,
        "finished_at": None,
        "cancel_requested": False,
        "logs": [{"ts": _now_app_iso(), "level": "info", "message": "Discovery job queued."}],
        "body": dict(body or {}),
        "record": None,
        "error": None,
    }
    with _discovery_jobs_lock:
        _discovery_jobs[job_id] = job
        _persist_discovery_job(job)
    if background_tasks is None:
        _start_discovery_job_thread(job_id)
    else:
        background_tasks.add_task(_start_discovery_job_thread, job_id)
    return _discovery_job_public(job)


@app.get("/api/discovery/jobs/{job_id}")
async def get_discovery_job(job_id: str):
    with _discovery_jobs_lock:
        job = _discovery_jobs.get(job_id)
        if not job:
            job = _load_discovery_job(job_id)
            if not job:
                return {"error": "Discovery job not found."}
            job = _mark_interrupted_discovery_job(job)
            _discovery_jobs[job_id] = job
            _persist_discovery_job(job)
        return _discovery_job_public(job)


@app.post("/api/discovery/jobs/{job_id}/cancel")
async def cancel_discovery_job(job_id: str):
    with _discovery_jobs_lock:
        job = _discovery_jobs.get(job_id)
        if not job:
            job = _load_discovery_job(job_id)
            if not job:
                return {"error": "Discovery job not found."}
            job = _mark_interrupted_discovery_job(job)
            _discovery_jobs[job_id] = job
            _persist_discovery_job(job)
            return _discovery_job_public(job)
        if job.get("status") in {"completed", "failed", "cancelled"}:
            return _discovery_job_public(job)
        job["cancel_requested"] = True
        _persist_discovery_job(job)
    _append_discovery_job_log(job_id, "warning", "Cancel requested. The current network call may finish before the job stops.")
    with _discovery_jobs_lock:
        return _discovery_job_public(_discovery_jobs[job_id])


@app.post("/api/discovery")
async def create_discovery(body: dict[str, Any]):
    try:
        return await asyncio.to_thread(_run_web_discovery, body)
    except Exception as exc:
        return {"error": _redact_secrets(str(exc))}


@app.post("/api/discovery/parse-goal")
async def parse_discovery_goal(body: dict[str, Any]):
    try:
        return await asyncio.to_thread(_run_discovery_goal_parse, body)
    except Exception as exc:
        return {"error": _redact_secrets(str(exc))}


@app.get("/api/discovery/{discovery_id}")
async def get_discovery(discovery_id: str):
    output_dir = _safe_discovery_dir(discovery_id)
    if output_dir is None:
        return {"error": "Discovery run not found."}
    manifest_path = output_dir / "dataset_manifest.json"
    if not manifest_path.exists():
        return {"error": "Discovery run not found."}
    try:
        manifest = load_dataset_manifest(manifest_path)
    except Exception as exc:
        return {"error": f"Discovery manifest is not readable: {_redact_secrets(str(exc))}"}
    return _public_discovery_record(discovery_id=discovery_id, output_dir=output_dir, manifest=manifest)


@app.post("/api/discovery/{discovery_id}/review")
async def review_discovery_run(discovery_id: str, body: dict[str, Any]):
    return await asyncio.to_thread(_save_discovery_reviews, discovery_id, body)


@app.get("/api/discovery/{discovery_id}/download")
async def download_discovery_file(discovery_id: str, file: str = "dataset_manifest_csv"):
    output_dir = _safe_discovery_dir(discovery_id)
    if output_dir is None or not output_dir.exists():
        return {"error": "Discovery run not found."}
    file_key = _clean_text(file)
    entry = _DISCOVERY_DOWNLOAD_FILES.get(file_key)
    if entry is None:
        return {"error": "Discovery file not available."}
    filename, media_type = entry
    path = output_dir / filename
    if not path.exists():
        return {"error": "Discovery file not available."}
    return FileResponse(path=str(path), filename=f"{discovery_id}_{filename}", media_type=media_type)


@app.post("/api/ai-ready/profile-inputs")
async def profile_ai_ready_inputs_api(body: dict[str, Any]):
    try:
        return await asyncio.to_thread(_run_ai_ready_input_profile, body)
    except Exception as exc:
        return {"error": _redact_secrets(str(exc))}


@app.post("/api/ai-ready/locate-inputs")
async def locate_ai_ready_inputs_api(body: dict[str, Any]):
    try:
        return await asyncio.to_thread(_run_ai_ready_input_locator, body)
    except Exception as exc:
        return {"error": _redact_secrets(str(exc))}


@app.post("/api/ai-ready/locate-agent-run")
async def locate_agent_run_ai_ready_inputs_api(body: dict[str, Any]):
    try:
        return await asyncio.to_thread(_run_agent_run_input_locator, body)
    except Exception as exc:
        return {"error": _redact_secrets(str(exc))}


@app.post("/api/ai-ready/build-from-agent-run")
async def build_ai_ready_from_agent_run_api(body: dict[str, Any]):
    try:
        return await asyncio.to_thread(_run_agent_run_ai_ready_build, body)
    except Exception as exc:
        return {"error": _redact_secrets(str(exc))}


@app.post("/api/ai-ready/mini-e2e")
async def validate_agent_run_mini_e2e_api(body: dict[str, Any]):
    try:
        return await asyncio.to_thread(_run_agent_run_mini_e2e, body)
    except Exception as exc:
        return {"error": _redact_secrets(str(exc))}


@app.post("/api/ai-ready/mini-e2e-batch")
async def validate_agent_runs_mini_e2e_batch_api(body: dict[str, Any]):
    try:
        return await asyncio.to_thread(_run_agent_runs_mini_e2e_batch, body)
    except Exception as exc:
        return {"error": _redact_secrets(str(exc))}


@app.post("/api/ai-ready/real-smoke")
async def run_ai_ready_real_smoke_api(body: dict[str, Any]):
    try:
        return await asyncio.to_thread(_run_ai_ready_real_smoke, body)
    except Exception as exc:
        return {"error": _redact_secrets(str(exc))}


@app.post("/api/ai-ready/repository-smoke")
async def run_repository_smoke_api(body: dict[str, Any]):
    try:
        return await asyncio.to_thread(_run_repository_smoke_web, body)
    except Exception as exc:
        return {"error": _redact_secrets(str(exc))}


@app.post("/api/ai-ready/refresh-iprox-index")
async def refresh_iprox_index_api(body: dict[str, Any]):
    try:
        return await asyncio.to_thread(_run_iprox_index_refresh_web, body)
    except Exception as exc:
        return {"error": _redact_secrets(str(exc))}


@app.post("/api/ai-ready/agent-harness")
async def run_agent_harness_api(body: dict[str, Any]):
    try:
        return await asyncio.to_thread(_run_agent_harness_web, body)
    except Exception as exc:
        return {"error": _redact_secrets(str(exc))}


@app.post("/api/ai-ready/make-dataset-recipe")
async def make_dataset_recipe_api(body: dict[str, Any]):
    try:
        return await asyncio.to_thread(_run_dataset_recipe_web, body)
    except Exception as exc:
        return {"error": _redact_secrets(str(exc))}


@app.post("/api/ai-ready/apply-curation-decisions")
async def apply_curation_decisions_api(body: dict[str, Any]):
    try:
        return await asyncio.to_thread(_run_apply_curation_decisions_web, body)
    except Exception as exc:
        return {"error": _redact_secrets(str(exc))}


@app.post("/api/ai-ready/model-loop")
async def run_dataset_model_loop_api(body: dict[str, Any]):
    try:
        return await asyncio.to_thread(_run_dataset_model_loop_web, body)
    except Exception as exc:
        return {"error": _redact_secrets(str(exc))}


@app.post("/api/ai-ready/model-informed-discovery-payload")
async def model_informed_discovery_payload_api(body: dict[str, Any]):
    try:
        return await asyncio.to_thread(_run_model_informed_discovery_payload, body)
    except Exception as exc:
        return {"error": _redact_secrets(str(exc))}


@app.post("/api/ai-ready/data-scientist-report")
async def make_data_scientist_agent_report_api(body: dict[str, Any]):
    try:
        return await asyncio.to_thread(_run_data_scientist_agent_report_web, body)
    except Exception as exc:
        return {"error": _redact_secrets(str(exc))}


@app.post("/api/ai-ready/guidance-alignment")
async def make_guidance_alignment_report_api(body: dict[str, Any]):
    try:
        return await asyncio.to_thread(_run_guidance_alignment_report_web, body)
    except Exception as exc:
        return {"error": _redact_secrets(str(exc))}


@app.post("/api/ai-ready/data-scientist-loop")
async def run_data_scientist_agent_loop_api(body: dict[str, Any]):
    try:
        return await asyncio.to_thread(_run_data_scientist_agent_loop_web, body)
    except Exception as exc:
        return {"error": _redact_secrets(str(exc))}


@app.post("/api/ai-ready/validate-build")
async def validate_ai_ready_build_api(body: dict[str, Any]):
    try:
        return await asyncio.to_thread(_run_ai_ready_validation, body)
    except Exception as exc:
        return {"error": _redact_secrets(str(exc))}


@app.get("/api/ai-ready/{build_id}")
async def get_ai_ready_build(build_id: str):
    output_dir = _safe_ai_ready_dir(build_id)
    if output_dir is None or not output_dir.exists():
        return {"error": "AI-ready build not found."}
    return _public_ai_ready_record(build_id, output_dir)


@app.get("/api/ai-ready/{build_id}/download")
async def download_ai_ready_build_file(build_id: str, file: str = "build_report_md"):
    output_dir = _safe_ai_ready_dir(build_id)
    if output_dir is None or not output_dir.exists():
        return {"error": "AI-ready build not found."}
    file_key = _clean_text(file)
    entry = _AI_READY_DOWNLOAD_FILES.get(file_key)
    if entry is None:
        return {"error": "AI-ready file not available."}
    filename, media_type = entry
    path = output_dir / filename
    if not path.exists():
        return {"error": "AI-ready file not available."}
    return FileResponse(path=str(path), filename=f"{build_id}_{Path(filename).name}", media_type=media_type)


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
    from agent.execution.outputs import ExecutionFailureEvent
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
    if not (output_dir / "recovery_audit.json").exists():
        _write_recovery_audit_package(
            output_dir,
            SimpleNamespace(task_id=task_id, file_name=source_file),
            stage="planning",
            run_mode="batch",
            events=[
                ExecutionFailureEvent(
                    category=str(error.get("category") or "unknown"),
                    reason=str(error.get("technical_message") or public_message),
                    evidence_kind="exception",
                    marker=str(error.get("exception_type") or type(exc).__name__),
                )
            ],
            artifacts={
                "error_json": output_dir / "error.json",
                "task_state_json": output_dir / "task_state.json",
            },
        )
    return public_message


def _batch_item_recovery_fields(output_dir: Path) -> dict[str, Any]:
    try:
        from agent.agent_core.recovery_report import analyze_agent_recovery

        paths = analyze_agent_recovery(run_dir=output_dir, output_dir=output_dir)
        report_path = paths.get("agent_recovery_report_json")
        if report_path is None:
            return {}
        report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(report, dict):
        return {}
    workflow_outcome = _clean_text(report.get("workflow_outcome"))
    primary_issue = _clean_text(report.get("primary_issue"))
    fields: dict[str, Any] = {
        "workflow_outcome": workflow_outcome,
        "usable_partial_outputs": bool(report.get("usable_partial_outputs")),
        "recovery_primary_issue": primary_issue,
        "agent_recovery_report": str(output_dir / "agent_recovery_report.json"),
    }
    recommended = _clean_text(report.get("recommended_next_step"))
    if recommended:
        fields["recovery_recommended_next_step"] = recommended
    return {key: value for key, value in fields.items() if value not in {"", None}}


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
    safe_match_types = {"exact", "stem", "known_project_local_source"}
    if match_type not in safe_match_types or match_score < 90:
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
    discovery_context = dict(item.get("discovery_context") or {})
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
        local_source = _known_local_source_from_input(input_value)
        if run_mode in {_RUN_MODE_PREPARE, _RUN_MODE_FULL}:
            if local_source:
                _append_batch_event(
                    batch_id,
                    "info",
                    f"{input_value} using local cached source {local_source['project_accession']}.",
                    item_index=index,
                )
                bundle, result, prepared_path = service.prepare_known_project_local_msdt_docker_input(
                    task=task,
                    source_data_path=local_source["source_path"],
                    project_accession=local_source["project_accession"],
                    output_dir=output_dir,
                    repository="pride",
                    matched_file=local_source["matched_file"],
                    prefer_project_fasta=prefer_project_fasta,
                    context_dir=local_source.get("context_dir"),
                )
            else:
                bundle, result, prepared_path = service.prepare_repository_msdt_docker_input(
                    task=task,
                    output_dir=output_dir,
                    repository=repository,
                    prefer_project_fasta=prefer_project_fasta,
                )
            _write_agent_audit_package(output_dir, result, plan=bundle.plan, report=reporter)
            _write_parameter_audit_files(output_dir, batch_id, index, input_value, result)
            project_error = _primary_project_error(result)
            if project_error:
                raise RuntimeError(project_error)
            if run_mode == _RUN_MODE_FULL:
                from agent.audit.review import build_task_state_snapshot, write_task_state
                from agent.execution.outputs import execution_failure_events, execution_failure_reasons
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
                    failure_events = execution_failure_events(
                        bundle.plan,
                        docker_result.returncode,
                        docker_result.stdout,
                        docker_result.stderr,
                    )
                    _write_recovery_audit_package(
                        output_dir,
                        task,
                        stage="execution",
                        run_mode=_RUN_MODE_FULL,
                        events=failure_events,
                        result=result,
                        plan=bundle.plan,
                        artifacts={
                            "task_state_json": output_dir / "task_state.json",
                            "runtime_log": output_dir / "logs" / "runtime.log",
                        },
                        report=reporter,
                    )
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

        discovery_project = _clean_text(discovery_context.get("project_accession"))
        if local_source:
            _append_batch_event(
                batch_id,
                "info",
                f"{input_value} using local cached source {local_source['project_accession']}.",
                item_index=index,
            )
            result = service.plan_dda_run_from_known_project_local_source(
                task=task,
                source_data_path=local_source["source_path"],
                project_accession=local_source["project_accession"],
                output_dir=output_dir,
                repository="pride",
                matched_file=local_source["matched_file"],
                prefer_project_fasta=prefer_project_fasta,
                context_dir=local_source.get("context_dir"),
            )
        elif discovery_project and callable(getattr(service, "plan_dda_run_from_known_project", None)):
            _append_batch_event(
                batch_id,
                "info",
                f"{input_value} using discovery handoff project {discovery_project}.",
                item_index=index,
            )
            result = service.plan_dda_run_from_known_project(
                task=task,
                project_accession=discovery_project,
                output_dir=output_dir,
                repository=repository,
                matched_file=_clean_text(discovery_context.get("file_name")) or task.file_name,
                prefer_project_fasta=prefer_project_fasta,
            )
        else:
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
                prepared_path = service.prepare_local_asset(result.asset) if local_source else service.prepare_asset(result.asset)
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
        _write_agent_audit_package(output_dir, result, report=reporter)
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
        recovery_fields = _batch_item_recovery_fields(output_dir)
        _update_batch_item(
            batch_id,
            index,
            status="failed",
            finished_at=_now_iso(),
            error=message,
            **recovery_fields,
        )
        workflow_outcome = recovery_fields.get("workflow_outcome")
        if workflow_outcome:
            _append_batch_event(batch_id, "warning", f"{input_value} recovery outcome: {workflow_outcome}", item_index=index)
        _append_batch_event(batch_id, "error", f"{input_value} failed: {message}", item_index=index)
        return {"status": "failed", "error": message, **recovery_fields}
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
                    recovery_fields = _batch_item_recovery_fields(Path(item.get("output_dir", "")))
                    _update_batch_item(
                        batch_id,
                        index,
                        status="failed",
                        finished_at=_now_iso(),
                        error=message,
                        **recovery_fields,
                    )

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
    input_records = _clean_batch_input_records(body, inputs)
    items = [
        {
            "index": index,
            "input": input_value,
            "status": "queued",
            "output_dir": str(_batch_item_dir(batch_dir, index, input_value)),
            "error": "",
            **({"discovery_context": input_records[index - 1]} if input_records[index - 1] else {}),
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


def _notify_agent_audit_ready(task_id: str) -> None:
    """Notify the frontend that agent audit files are available for real-time visualization."""
    _emit(task_id, "agent_audit_ready")


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
        local_source = _known_local_source_from_input(input_value)
        _log(task_id, "info", f"输入规范化：{task_obj.file_name}")

        if local_source:
            _log(
                task_id,
                "info",
                "Detected local cached PRIDE source; using known-project local mode "
                f"({local_source['project_accession']} / {local_source['matched_file']}).",
            )
            with StderrCapture(task_id):
                result = service.plan_dda_run_from_known_project_local_source(
                    task=task_obj,
                    source_data_path=local_source["source_path"],
                    project_accession=local_source["project_accession"],
                    output_dir=output_dir,
                    repository="pride",
                    matched_file=local_source["matched_file"],
                    reviewed_fasta_path=reviewed_fasta_path,
                    reviewed_fasta_url=reviewed_fasta_url,
                    reviewed_fasta_name=reviewed_fasta_name,
                    prefer_project_fasta=prefer_project_fasta,
                    context_dir=local_source.get("context_dir"),
                )
        else:
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

        _write_agent_audit_package(output_dir, result, report=lambda message: _log(task_id, "debug", message))
        _notify_agent_audit_ready(task_id)

        prepared_path = None
        if not parameter_only and result.plan.needs_review and service._can_retry_with_mzml_instrument(result.plan):
            _log(task_id, "info", "检测到项目级多个仪器，先下载/转换 mzML，并从 mzML 读取文件级仪器信息。")
            _step(task_id, 2, "[2/5] 下载 PRIDE 数据文件")
            with StderrCapture(task_id):
                prepared_path = service.prepare_local_asset(result.asset) if local_source else service.prepare_asset(result.asset)
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
            _write_agent_audit_package(output_dir, result, report=lambda message: _log(task_id, "debug", message))
            _notify_agent_audit_ready(task_id)

        if result.plan.needs_review:
            task["blocking_issues"] = result.plan.blocking_issues
            for issue in result.plan.blocking_issues:
                _log(task_id, "error", f"[阻断] {issue}")
            service.write_task_bundle(output_dir, result.resolution, result.context, result.attributes, result.plan, asset=result.asset)
            _write_agent_audit_package(output_dir, result, report=lambda message: _log(task_id, "debug", message))
            _notify_agent_audit_ready(task_id)
            _set_task_terminal_status(task_id, "blocked")
            return

        _log(task_id, "info", f"workflow：{result.plan.fragpipe_workflow_path.name}  FASTA：{result.plan.fasta_path.name}（{result.plan.fasta_selection_mode}）")

        if parameter_only:
            _step(task_id, 5, "[5/5] 参数推断完成")
            _log(task_id, "info", f"Parameter-only mode completed: {repository_label} project resolution, file attribute inference, workflow/FASTA/search-parameter planning, and audit package generation are complete.")
            service.write_task_bundle(output_dir, result.resolution, result.context, result.attributes, result.plan, asset=result.asset)
            _write_agent_audit_package(output_dir, result, report=lambda message: _log(task_id, "debug", message))
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
                prepared_path = service.prepare_local_asset(result.asset) if local_source else service.prepare_asset(result.asset)
            _log(task_id, "info", f"数据文件已就绪：{prepared_path}")
        else:
            _log(task_id, "info", f"复用已准备的数据文件：{prepared_path}")

        # ── 步骤 3 ──
        _step(task_id, 3, "[3/5] 生成 MSDT-Converter 输入包")
        result = service.validate_prepared_data_for_plan(result, prepared_path)
        if result.plan.needs_review:
            task["blocking_issues"] = result.plan.blocking_issues
            for issue in result.plan.blocking_issues:
                _log(task_id, "error", f"[阻断] {issue}")
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
        _write_agent_audit_package(output_dir, result, plan=bundle.plan, report=lambda message: _log(task_id, "debug", message))
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
        from agent.execution.outputs import execution_failure_events, execution_failure_reasons
        failure_reasons = execution_failure_reasons(
            bundle.plan,
            docker_result.returncode,
            docker_result.stdout,
            docker_result.stderr,
        )
        failure_events = execution_failure_events(
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
                _write_recovery_audit_package(
                    output_dir,
                    task_obj,
                    stage="execution",
                    run_mode=_RUN_MODE_FULL,
                    events=failure_events,
                    result=result,
                    plan=bundle.plan,
                    artifacts={
                        "task_state_json": output_dir / "task_state.json",
                        "review_queue_json": output_dir / "review_queue.json",
                        "runtime_log": output_dir / "logs" / "runtime.log",
                    },
                    report=lambda message: _log(task_id, "debug", message),
                )
            except Exception as audit_exc:
                _log(task_id, "debug", f"Failed to write execution failure audit files: {audit_exc}")
            _attach_workflow_recovery_summary(task_id, output_dir)
            _set_task_terminal_status(task_id, "failed")

    except Exception as exc:
        from agent.errors import build_error_record, public_error_summary, write_error_record
        from agent.execution.outputs import ExecutionFailureEvent

        error_record = build_error_record(exc, stage="pipeline", input_file=input_value)
        write_error_record(output_dir / "error.json", error_record)
        summary = public_error_summary(error_record)
        task["error_summary"] = summary
        recovery_task = locals().get("task_obj") or SimpleNamespace(task_id=task_id, file_name=Path(input_value).name)
        recovery_result = locals().get("result")
        recovery_plan = getattr(locals().get("bundle"), "plan", None) or getattr(recovery_result, "plan", None)
        _write_recovery_audit_package(
            output_dir,
            recovery_task,
            stage="pipeline",
            run_mode=_clean_run_mode(task.get("run_mode")),
            events=[
                ExecutionFailureEvent(
                    category=str(error_record.get("category") or "unknown"),
                    reason=str(error_record.get("technical_message") or summary.get("public_message") or exc),
                    evidence_kind="exception",
                    marker=str(error_record.get("exception_type") or type(exc).__name__),
                )
            ],
            result=recovery_result,
            plan=recovery_plan,
            artifacts={"error_json": output_dir / "error.json"},
            report=lambda message: _log(task_id, "debug", message),
        )
        task["blocking_issues"] = [summary.get("public_message") or "任务运行失败。"]
        _log(task_id, "error", f"运行出错：{summary.get('public_message')}（{summary.get('category')}）")
        if "traceback" in error_record:
            _log(task_id, "debug", error_record["traceback"])
        _attach_workflow_recovery_summary(task_id, output_dir)
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
        "workflow_outcome": task.get("workflow_outcome"),
        "usable_partial_outputs": bool(task.get("usable_partial_outputs")),
        "recovery_primary_issue": task.get("recovery_primary_issue"),
        "recovery_recommended_next_step": task.get("recovery_recommended_next_step"),
        "recovery_report_json": task.get("recovery_report_json"),
        "recovery_report_md": task.get("recovery_report_md"),
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


def _resolve_task_output_dir(task_id: str) -> Path | None:
    with _tasks_lock:
        task = _tasks.get(task_id)
    if task is not None:
        output_dir_raw = task.get("output_dir")
        if output_dir_raw:
            return Path(output_dir_raw)
    history = _find_history_record(task_id)
    if history is not None:
        output_dir_name = str(history.get("output_dir") or "")
        if output_dir_name:
            return _runs_dir / output_dir_name
    candidate = _runs_dir / safe_output_stem(task_id)
    if candidate.exists() and candidate.is_dir():
        return candidate
    return None


_AGENT_AUDIT_FILES = {
    "observation": "agent_observation.json",
    "plan": "agent_plan.json",
    "decision_trace": "agent_decision_trace.json",
    "recovery": "recovery_audit.json",
}


def _read_agent_audit_file(output_dir: Path, filename: str) -> tuple[dict[str, Any] | None, str | None]:
    path = output_dir / filename
    if not path.exists() or not path.is_file():
        return None, "missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "invalid"
    return (data, None) if isinstance(data, dict) else (None, "invalid")


@app.get("/api/tasks/{task_id}/agent-audit")
async def get_agent_audit(task_id: str):
    output_dir = _resolve_task_output_dir(task_id)
    if output_dir is None or not output_dir.exists():
        return {
            "error": "Task output directory not found.",
            "available": False,
            "available_files": [],
            "missing_files": list(_AGENT_AUDIT_FILES.values()),
            "invalid_files": [],
        }

    payloads: dict[str, dict[str, Any] | None] = {}
    available_files: list[str] = []
    missing_files: list[str] = []
    invalid_files: list[str] = []
    for key, filename in _AGENT_AUDIT_FILES.items():
        data, state = _read_agent_audit_file(output_dir, filename)
        payloads[key] = data
        if state == "missing":
            missing_files.append(filename)
        elif state == "invalid":
            invalid_files.append(filename)
        else:
            available_files.append(filename)

    if not any(payloads.values()):
        return {
            "error": "No agent audit files found.",
            "available": False,
            "available_files": available_files,
            "missing_files": missing_files,
            "invalid_files": invalid_files,
        }

    return {
        "available": True,
        "output_dir": str(output_dir),
        "available_files": available_files,
        "missing_files": missing_files,
        "invalid_files": invalid_files,
        "observation": payloads["observation"],
        "plan": payloads["plan"],
        "decision_trace": payloads["decision_trace"],
        "recovery": payloads["recovery"],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
