from __future__ import annotations

import json
import os
import csv
from collections import Counter
from pathlib import Path
from typing import Any, Callable
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

from pydantic import Field

from agent.models import JsonModel
from agent.oneclick.preflight import normalize_run_mode, run_preflight
from agent.utils import write_json


class BatchSubmissionReport(JsonModel):
    status: str
    execute: bool = False
    web_url: str | None = None
    request_path: str | None = None
    output_dir: str | None = None
    input_count: int = 0
    run_mode: str = "parameters"
    repository: str = "pride"
    preflight: dict[str, Any] = Field(default_factory=dict)
    response: dict[str, Any] | None = None
    blocking_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    next_step: str = ""


class BatchResultItem(JsonModel):
    index: int
    input: str
    status: str
    output_dir: str
    error: str = ""
    started_at: str | None = None
    finished_at: str | None = None


class BatchResultReport(JsonModel):
    batch_id: str
    status: str
    run_mode: str = "parameters"
    repository: str = "pride"
    item_count: int = 0
    completed_items: int = 0
    failed_items: int = 0
    needs_review_items: int = 0
    queued_or_running_items: int = 0
    success_rate: float = 0.0
    failure_rate: float = 0.0
    needs_review_rate: float = 0.0
    excel_path: str | None = None
    excel_exists: bool = False
    output_dir: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    status_counts: dict[str, int] = Field(default_factory=dict)
    error_counts: dict[str, int] = Field(default_factory=dict)
    items: list[BatchResultItem] = Field(default_factory=list)


HttpPostJson = Callable[[str, dict[str, Any]], dict[str, Any]]
PreflightRunner = Callable[..., dict[str, Any]]


def load_batch_parameters_request(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Batch request JSON must contain an object.")
    return payload


def _env_llm_config() -> dict[str, str]:
    api_key = (
        os.getenv("AGENT_LLM_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or ""
    ).strip()
    if not api_key:
        return {}
    base_url = (
        os.getenv("AGENT_LLM_BASE_URL")
        or os.getenv("DEEPSEEK_BASE_URL")
        or "https://api.deepseek.com"
    ).strip()
    model = (
        os.getenv("AGENT_LLM_MODEL")
        or os.getenv("DEEPSEEK_MODEL")
        or "deepseek-chat"
    ).strip()
    timeout = (
        os.getenv("AGENT_LLM_TIMEOUT")
        or os.getenv("DEEPSEEK_TIMEOUT")
        or "300"
    ).strip()
    return {
        "api_key": api_key,
        "base_url": base_url.rstrip("/"),
        "model": model,
        "timeout": timeout,
    }


def _normalize_llm_config(value: Any, *, use_env: bool) -> dict[str, str]:
    config = dict(value) if isinstance(value, dict) else {}
    api_key = str(config.get("api_key") or "").strip()
    if not api_key and use_env:
        return _env_llm_config()
    if not api_key:
        return {}
    return {
        "api_key": api_key,
        "base_url": str(config.get("base_url") or os.getenv("AGENT_LLM_BASE_URL") or "https://api.deepseek.com").strip().rstrip("/"),
        "model": str(config.get("model") or os.getenv("AGENT_LLM_MODEL") or "deepseek-chat").strip(),
        "timeout": str(config.get("timeout") or os.getenv("AGENT_LLM_TIMEOUT") or "300").strip(),
    }


def _clean_input_records(raw_records: Any, inputs: list[str]) -> list[dict[str, Any]]:
    if not isinstance(raw_records, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for raw in raw_records:
        if not isinstance(raw, dict):
            continue
        file_name = str(raw.get("file_name") or raw.get("input") or raw.get("input_value") or "").strip()
        if not file_name:
            continue
        record = dict(raw)
        record["file_name"] = file_name
        record["input"] = str(record.get("input") or file_name).strip() or file_name
        cleaned.append(record)
    if not inputs:
        return cleaned
    if len(cleaned) == len(inputs) and all(record.get("file_name") == inputs[index] for index, record in enumerate(cleaned)):
        return cleaned
    by_name: dict[str, dict[str, Any]] = {}
    for record in cleaned:
        by_name.setdefault(str(record.get("file_name") or ""), record)
    ordered = [by_name[input_value] for input_value in inputs if input_value in by_name]
    return ordered


def redact_batch_request(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(payload)
    llm_config = redacted.get("llm_config")
    if isinstance(llm_config, dict) and llm_config.get("api_key"):
        redacted["llm_config"] = {**llm_config, "api_key": "***redacted***"}
    return redacted


def normalize_batch_parameters_request(
    payload: dict[str, Any],
    *,
    use_env_llm: bool = False,
) -> dict[str, Any]:
    inputs_raw = payload.get("inputs") or []
    input_records_raw = payload.get("input_records")
    if isinstance(inputs_raw, str):
        inputs = [line.strip() for line in inputs_raw.splitlines() if line.strip()]
    elif isinstance(inputs_raw, list):
        inputs = [str(item).strip() for item in inputs_raw if str(item).strip()]
    else:
        inputs = []
    input_records = _clean_input_records(input_records_raw, inputs)
    if not inputs and input_records:
        inputs = [str(record["file_name"]) for record in input_records]
    request = dict(payload)
    request["inputs"] = inputs
    if input_records:
        request["input_records"] = input_records
        request["input_record_mode"] = str(request.get("input_record_mode") or "discovery_handoff_v1")
    else:
        request.pop("input_records", None)
        request.pop("input_record_mode", None)
    request["run_mode"] = normalize_run_mode(request.get("run_mode"), default="parameters")
    if request["run_mode"] != "parameters":
        raise ValueError("Discovery batch bridge only supports run_mode=parameters.")
    request["repository"] = str(request.get("repository") or "pride").strip().lower() or "pride"
    request["resource_policy"] = str(request.get("resource_policy") or "balanced").strip().lower() or "balanced"
    try:
        jobs = int(request.get("jobs") or 1)
    except (TypeError, ValueError):
        jobs = 1
    request["jobs"] = max(1, jobs)
    request["submitter"] = str(request.get("submitter") or "discovery_handoff").strip() or "discovery_handoff"
    llm_config = _normalize_llm_config(request.get("llm_config"), use_env=use_env_llm)
    if llm_config:
        request["llm_config"] = llm_config
    elif "llm_config" in request:
        request.pop("llm_config", None)
    return request


def _default_http_post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=120) as response:
            text = response.read().decode("utf-8")
    except HTTPError as exc:
        try:
            text = exc.read().decode("utf-8")
        except Exception:
            text = str(exc)
        raise RuntimeError(f"Batch API returned HTTP {exc.code}: {text}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach batch API: {exc}") from exc
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Batch API returned non-JSON response: {text[:500]}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Batch API returned a non-object JSON response.")
    return parsed


def build_batch_submission_report(
    payload: dict[str, Any],
    *,
    output_dir: str | Path,
    request_path: str | Path | None = None,
    execute: bool = False,
    web_url: str = "http://127.0.0.1:8000",
    use_env_llm: bool = True,
    preflight_runner: PreflightRunner = run_preflight,
    http_post_json: HttpPostJson = _default_http_post_json,
) -> BatchSubmissionReport:
    normalized = normalize_batch_parameters_request(payload, use_env_llm=use_env_llm)
    output_dir = Path(output_dir)
    if not normalized["inputs"]:
        preflight = {
            "status": "blocked",
            "run_mode": "parameters",
            "resource_policy": normalized["resource_policy"],
            "repository": normalized["repository"],
            "input_count": 0,
            "checks": [],
            "blocking_issues": ["Batch request has no inputs."],
            "warnings": [],
            "required_disk_bytes": 0,
        }
    else:
        preflight = preflight_runner(
            inputs=normalized["inputs"],
            run_mode="parameters",
            repository=normalized["repository"],
            output_root=output_dir,
            resource_policy=normalized["resource_policy"],
        )
    blocking = [str(issue) for issue in preflight.get("blocking_issues", []) or []]
    warnings = [str(issue) for issue in preflight.get("warnings", []) or []]
    if execute and not (isinstance(normalized.get("llm_config"), dict) and normalized["llm_config"].get("api_key")):
        blocking.append("No llm_config.api_key is available for Web batch submission.")
    can_submit = preflight.get("status") in {"ok", "warning"} and not blocking and bool(normalized["inputs"])

    response: dict[str, Any] | None = None
    status = "ready" if can_submit else "blocked"
    next_step = "run_with_execute_flag" if can_submit and not execute else "fix_request_or_handoff"
    if execute and can_submit:
        endpoint = web_url.rstrip("/") + "/api/batches/parameters"
        response = http_post_json(endpoint, normalized)
        if response.get("error"):
            status = "failed"
            blocking.append(str(response["error"]))
            next_step = "inspect_batch_api_error"
        else:
            status = "submitted"
            next_step = "watch_batch_status"
    elif execute and not can_submit:
        next_step = "fix_request_or_handoff"

    return BatchSubmissionReport(
        status=status,
        execute=execute,
        web_url=web_url,
        request_path=str(request_path) if request_path is not None else None,
        output_dir=str(output_dir),
        input_count=len(normalized["inputs"]),
        run_mode=normalized["run_mode"],
        repository=normalized["repository"],
        preflight=preflight,
        response=response,
        blocking_issues=blocking,
        warnings=warnings,
        next_step=next_step,
    )


def write_batch_submission_report(
    request_payload: dict[str, Any],
    output_dir: str | Path,
    *,
    request_path: str | Path | None = None,
    execute: bool = False,
    web_url: str = "http://127.0.0.1:8000",
    use_env_llm: bool = True,
    preflight_runner: PreflightRunner = run_preflight,
    http_post_json: HttpPostJson = _default_http_post_json,
) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report = build_batch_submission_report(
        request_payload,
        output_dir=output_dir,
        request_path=request_path,
        execute=execute,
        web_url=web_url,
        use_env_llm=use_env_llm,
        preflight_runner=preflight_runner,
        http_post_json=http_post_json,
    )
    paths = {
        "batch_submission_report": output_dir / "batch_submission_report.json",
        "normalized_batch_request": output_dir / "normalized_batch_parameters_request.json",
    }
    normalized = normalize_batch_parameters_request(request_payload, use_env_llm=use_env_llm)
    write_json(paths["normalized_batch_request"], redact_batch_request(normalized))
    write_json(paths["batch_submission_report"], report.model_dump(mode="json"))
    return paths


def load_batch_manifest(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Batch manifest JSON must contain an object.")
    return payload


def build_batch_result_report(batch_manifest: dict[str, Any]) -> BatchResultReport:
    raw_items = batch_manifest.get("items") or []
    items: list[BatchResultItem] = []
    for index, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, dict):
            continue
        items.append(
            BatchResultItem(
                index=int(raw.get("index") or index),
                input=str(raw.get("input") or ""),
                status=str(raw.get("status") or "unknown"),
                output_dir=str(raw.get("output_dir") or ""),
                error=str(raw.get("error") or ""),
                started_at=raw.get("started_at"),
                finished_at=raw.get("finished_at"),
            )
        )
    status_counts = Counter(item.status for item in items)
    error_counts = Counter(item.error for item in items if item.error)
    item_count = len(items)
    completed = status_counts.get("completed", 0)
    failed = status_counts.get("failed", 0)
    needs_review = status_counts.get("needs_review", 0)
    queued_or_running = sum(
        count
        for status, count in status_counts.items()
        if status in {"queued", "running"} or status not in {"completed", "failed", "needs_review"}
    )
    excel_path = str(batch_manifest.get("excel_path") or "").strip() or None
    output_dir = str(batch_manifest.get("output_dir") or "").strip() or None
    return BatchResultReport(
        batch_id=str(batch_manifest.get("batch_id") or ""),
        status=str(batch_manifest.get("status") or "unknown"),
        run_mode=str(batch_manifest.get("run_mode") or "parameters"),
        repository=str(batch_manifest.get("repository") or "pride"),
        item_count=item_count,
        completed_items=completed,
        failed_items=failed,
        needs_review_items=needs_review,
        queued_or_running_items=queued_or_running,
        success_rate=round(completed / item_count, 6) if item_count else 0.0,
        failure_rate=round(failed / item_count, 6) if item_count else 0.0,
        needs_review_rate=round(needs_review / item_count, 6) if item_count else 0.0,
        excel_path=excel_path,
        excel_exists=Path(excel_path).exists() if excel_path else False,
        output_dir=output_dir,
        started_at=batch_manifest.get("started_at"),
        finished_at=batch_manifest.get("finished_at"),
        status_counts=dict(sorted(status_counts.items())),
        error_counts=dict(sorted(error_counts.items())),
        items=items,
    )


def write_batch_result_report(
    batch_manifest: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report = build_batch_result_report(batch_manifest)
    paths = {
        "batch_result_report": output_dir / "batch_result_report.json",
        "batch_result_items": output_dir / "batch_result_items.csv",
    }
    write_json(paths["batch_result_report"], report.model_dump(mode="json"))
    with paths["batch_result_items"].open("w", encoding="utf-8", newline="") as handle:
        fieldnames = ["index", "input", "status", "output_dir", "error", "started_at", "finished_at"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in report.items:
            writer.writerow(item.model_dump(mode="json"))
    return paths
