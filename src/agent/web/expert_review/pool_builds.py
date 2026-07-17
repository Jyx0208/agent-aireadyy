from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.web.expert_review.pool_builder import build_blinded_pool_from_discovery
from agent.web.expert_review.pool_registry import ExpertPoolRegistry, strip_pool_for_mode


_BUILDS_LOCK = threading.RLock()
_RUNNING_BUILD_IDS: set[str] = set()
_TERMINAL_DISCOVERY_STATUSES = {"completed", "failed", "cancelled"}
_PROGRESS_PERCENT = {
    "queued": 0,
    "parsing_prompt": 10,
    "starting_discovery": 15,
    "discovering": 25,
    "registering_pool": 70,
    "pool_ready": 80,
    "starting_review": 90,
    "completed": 100,
    "failed": 100,
    "cancelled": 100,
}
_PROGRESS_MESSAGES = {
    "en": {
        "queued": "Build queued.",
        "parsing_prompt": "Understanding the request and preparing repository search terms.",
        "starting_discovery": "Starting repository discovery.",
        "discovering": "Searching repositories and inspecting candidate projects.",
        "registering_pool": "Validating candidates and registering the review pool.",
        "pool_ready": "Review pool is ready.",
        "starting_review": "Starting the heterogeneous model-expert review.",
        "completed": "Pool build and review handoff completed.",
        "failed": "Pool build failed.",
        "cancelled": "Pool build cancelled.",
    },
    "zh": {
        "queued": "构建任务已排队。",
        "parsing_prompt": "正在理解需求并生成仓库检索词。",
        "starting_discovery": "正在启动数据发现。",
        "discovering": "正在检索仓库并检查候选项目。",
        "registering_pool": "正在校验候选并注册评审池。",
        "pool_ready": "评审池已就绪。",
        "starting_review": "正在启动异构模型专家评审。",
        "completed": "评审池构建和评审交接已完成。",
        "failed": "评审池构建失败。",
        "cancelled": "评审池构建已取消。",
    },
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _is_secret_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", str(key).casefold())
    return normalized == "authorization" or normalized.endswith(("apikey", "token", "password", "secret"))


def _safe_value(value: Any) -> Any:
    """Drop credentials before a request or build record reaches disk."""
    if isinstance(value, Mapping):
        return {
            str(key): _safe_value(item)
            for key, item in value.items()
            if not _is_secret_key(key)
        }
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    if isinstance(value, str):
        return _safe_error(value)
    return value


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-._")
    return cleaned[:80]


def _safe_error(value: Any) -> str:
    text = str(value or "")
    normalized = text.casefold()
    if "prompt_parse_failed" in normalized and any(
        marker in normalized
        for marker in (
            "401",
            "403",
            "authorization required",
            "unauthorized",
            "authentication failed",
            "invalid api key",
        )
    ):
        return (
            "prompt_parse_failed:评审池构建模型认证失败。"
            "请在“评审池构建模型配置”中更新 API Key，或更换提供商和模型后重试。"
        )
    text = re.sub(
        r"(?i)((?:api[_ -]?key|authorization|access[_ -]?token|refresh[_ -]?token|token|password|client[_ -]?secret|secret)\s*[:=]\s*)(?:bearer\s+)?\S+",
        r"\1[redacted]",
        text,
    )
    text = re.sub(r"sk-[A-Za-z0-9_-]{6,}", "[redacted-api-key]", text)
    text = re.sub(r"(?i)for more information check:\s*https?://\S+", "", text)
    text = re.sub(r"https?://\S+", "[provider endpoint]", text, flags=re.IGNORECASE)
    return text


def _nonnegative_int(value: Any, *, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return max(0, int(default))
    return max(0, parsed)


class ExpertPoolBuildManager:
    """Durable orchestration from a discovery job to a registered review pool.

    The manager deliberately owns no discovery or LLM credentials.  Those operations
    are injected by the web layer, while this class persists only public identifiers
    and state needed to resume/reconcile a build after a process restart.
    """

    def __init__(
        self,
        registry: ExpertPoolRegistry,
        *,
        start_discovery: Callable[[dict[str, Any]], Mapping[str, Any]],
        get_discovery: Callable[[str], Mapping[str, Any] | None],
        cancel_discovery: Callable[[str], Mapping[str, Any] | None],
        start_review: Callable[[str, dict[str, Any]], Mapping[str, Any]] | None = None,
        prepare_discovery_request: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
        poll_interval: float = 0.05,
    ) -> None:
        self.registry = registry
        self.start_discovery = start_discovery
        self.get_discovery = get_discovery
        self.cancel_discovery = cancel_discovery
        self.start_review = start_review
        self.prepare_discovery_request = prepare_discovery_request
        self.poll_interval = max(0.001, float(poll_interval))
        # Request credentials may be required to launch discovery, but must never
        # be persisted with the durable build checkpoint.  The entry exists only
        # until the discovery job has accepted the request.
        self._transient_discovery_requests: dict[str, dict[str, Any]] = {}

    @property
    def root(self) -> Path:
        return self.registry.root / "_pool_builds"

    def list_builds(self) -> list[dict[str, Any]]:
        with _BUILDS_LOCK:
            if not self.root.exists():
                return []
            builds = [record for path in self.root.glob("*.json") if (record := self._load_path(path)) is not None]
            builds.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
            for record in builds:
                self._resume_if_needed(record)
            return [self._public(record) for record in builds]

    def get_build(self, build_id: str) -> dict[str, Any] | None:
        with _BUILDS_LOCK:
            record = self._load(build_id)
            if record is not None:
                self._resume_if_needed(record)
            return self._public(record) if record else None

    def start_build(
        self,
        *,
        discovery_request: Mapping[str, Any],
        action: str = "build_only",
        label: str | None = None,
        preset_id: str = "default/v1",
        review: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if action not in {"build_only", "build_and_review"}:
            raise ValueError("invalid_build_action")
        if preset_id != "default/v1":
            raise ValueError("unsupported_preset_id")
        request_key = _safe_id(idempotency_key or "")
        with _BUILDS_LOCK:
            if request_key:
                existing = self._find_by_idempotency_key(request_key)
                if existing is not None:
                    self._resume_if_needed(existing)
                    return self._public(existing)
            build_id = f"pool_build_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
            record = {
                "build_id": build_id,
                "idempotency_key": request_key or None,
                "action": action,
                "preset_id": preset_id,
                "label": str(label or "").strip() or None,
                "status": "queued",
                "created_at": _utc_now(),
                "updated_at": _utc_now(),
                "finished_at": None,
                "discovery_job_id": None,
                "discovery_status": None,
                "discovery_execution": {
                    "runtime": str(discovery_request.get("runtime") or "").strip() or None,
                    "mode": None,
                    "status": "queued",
                    "current_stage": "agent_starting",
                    "search_round": 0,
                    "discovery_round": 0,
                    "max_discovery_rounds": None,
                    "candidate_count": 0,
                    "project_judgments": {
                        "evidence_stage": "search",
                        "assessed_projects": 0,
                        "qualified_projects": 0,
                        "qualified_target": _nonnegative_int(discovery_request.get("max_projects")),
                        "investigate_projects": 0,
                        "rejected_projects": 0,
                        "grade_counts": {"0": 0, "1": 0, "2": 0, "3": 0, "unknown": 0},
                    },
                    "stop_reason": None,
                    "search_stop_reason": None,
                },
                "pool_id": None,
                "review_job_id": None,
                "review_status": "not_requested" if action == "build_only" else "pending",
                "review_start_attempts": 0,
                "review_error": None,
                "error": None,
                "cancel_requested": False,
                "discovery_request": _safe_value(dict(discovery_request)),
                "review": _safe_value(dict(review or {})),
                "prompt_parse": {
                    "status": "pending",
                    "parser": None,
                    "goal": None,
                    "query_terms": [],
                    "scale_mode": str(discovery_request.get("scale_mode") or "").strip() or None,
                    "output_language": str(discovery_request.get("output_language") or "").strip() or None,
                    "warnings": [],
                    "reasoning": "",
                },
                "progress": self._progress_payload(
                    discovery_request,
                    phase="queued",
                ),
            }
            self._persist(record)
            self._transient_discovery_requests[build_id] = dict(discovery_request)
        self._start_worker(build_id)
        return self._public(record)

    def cancel_build(self, build_id: str) -> dict[str, Any] | None:
        with _BUILDS_LOCK:
            record = self._load(build_id)
            if record is None:
                return None
            if record.get("status") in {"pool_ready", "completed", "failed", "cancelled"}:
                return self._public(record)
            record["cancel_requested"] = True
            record["updated_at"] = _utc_now()
            discovery_job_id = str(record.get("discovery_job_id") or "")
            self._persist(record)
        if discovery_job_id:
            self.cancel_discovery(discovery_job_id)
        return self.get_build(build_id)

    def reconcile_review(self, build_id: str) -> dict[str, Any] | None:
        """Retry only the post-registration review handoff; never rebuild discovery."""
        with _BUILDS_LOCK:
            record = self._load(build_id)
            if record is None:
                return None
            if record.get("action") != "build_and_review" or not record.get("pool_id"):
                return self._public(record)
            if record.get("review_job_id"):
                return self._public(record)
            record["status"] = "pool_ready"
            record["review_status"] = "pending"
            record["review_error"] = None
            record["updated_at"] = _utc_now()
            self._persist(record)
        self._start_review(build_id)
        return self.get_build(build_id)

    def _resume_if_needed(self, record: Mapping[str, Any]) -> None:
        if str(record.get("status") or "") in {
            "queued",
            "parsing_prompt",
            "starting_discovery",
            "discovering",
            "registering_pool",
        }:
            self._start_worker(str(record.get("build_id") or ""))

    def _start_worker(self, build_id: str) -> None:
        if not build_id:
            return
        with _BUILDS_LOCK:
            if build_id in _RUNNING_BUILD_IDS:
                return
            _RUNNING_BUILD_IDS.add(build_id)
        threading.Thread(
            target=self._run_worker,
            args=(build_id,),
            name=f"expert-pool-build-{build_id}",
            daemon=True,
        ).start()

    def _run_worker(self, build_id: str) -> None:
        try:
            self._run(build_id)
        except OSError:
            # Keep the last durable checkpoint resumable. Downstream start calls
            # use deterministic idempotency keys, so replay will recover the same
            # Discovery resource rather than creating duplicate work.
            return
        finally:
            with _BUILDS_LOCK:
                _RUNNING_BUILD_IDS.discard(build_id)

    def _run(self, build_id: str) -> None:
        with _BUILDS_LOCK:
            record = self._load(build_id)
            if record is None:
                self._transient_discovery_requests.pop(build_id, None)
                return
            if record.get("cancel_requested"):
                record["status"] = "cancelled"
                record["progress"] = self._progress_payload(
                    record.get("discovery_request") or {},
                    phase="cancelled",
                    counts=self._progress_counts(record.get("progress")),
                    log_tail=self._progress_logs(record.get("progress")),
                )
                record["finished_at"] = _utc_now()
                record["updated_at"] = _utc_now()
                self._persist(record)
                self._transient_discovery_requests.pop(build_id, None)
                return
            discovery_job_id = str(record.get("discovery_job_id") or "")
        if not discovery_job_id:
            # Prompt preparation and Discovery startup may call remote models or
            # providers. Never hold the process-wide build lock across those calls.
            discovery_request = self._prepare_request(build_id)
            if discovery_request is None:
                return
            with _BUILDS_LOCK:
                record = self._load(build_id)
                if record is None:
                    self._transient_discovery_requests.pop(build_id, None)
                    return
                if record.get("cancel_requested"):
                    record["status"] = "cancelled"
                    record["progress"] = self._progress_payload(
                        record.get("discovery_request") or {},
                        phase="cancelled",
                        counts=self._progress_counts(record.get("progress")),
                        log_tail=self._progress_logs(record.get("progress")),
                    )
                    record["finished_at"] = _utc_now()
                    record["updated_at"] = _utc_now()
                    self._persist(record)
                    self._transient_discovery_requests.pop(build_id, None)
                    return
            # Prefer the one-process request while it exists so callers can
            # supply a request-scoped LLM credential without it ever reaching
            # the checkpoint. After restart only the redacted request remains.
            try:
                started = self.start_discovery(dict(discovery_request))
                discovery_job_id = str(started.get("job_id") or "")
                if not discovery_job_id:
                    raise ValueError("discovery_start_returned_no_job_id")
            except Exception as exc:
                self._transient_discovery_requests.pop(build_id, None)
                self._fail(record, f"discovery_start_failed:{exc}")
                return
            self._transient_discovery_requests.pop(build_id, None)
            cancel_started_discovery = False
            with _BUILDS_LOCK:
                record = self._load(build_id)
                if record is None:
                    cancel_started_discovery = True
                else:
                    record["discovery_job_id"] = discovery_job_id
                    record["discovery_status"] = str(started.get("status") or "queued")
                    cancel_started_discovery = bool(record.get("cancel_requested"))
                    record["status"] = "cancelled" if cancel_started_discovery else "discovering"
                    record["progress"] = self._progress_payload(
                        record.get("discovery_request") or {},
                        phase="cancelled" if cancel_started_discovery else "discovering",
                    )
                    if cancel_started_discovery:
                        record["finished_at"] = _utc_now()
                    record["updated_at"] = _utc_now()
                    self._persist(record)
            if cancel_started_discovery:
                try:
                    self.cancel_discovery(discovery_job_id)
                except Exception:
                    pass
                return
        while True:
            with _BUILDS_LOCK:
                record = self._load(build_id)
                if record is None:
                    return
                if record.get("cancel_requested"):
                    record["status"] = "cancelled"
                    record["progress"] = self._progress_payload(
                        record.get("discovery_request") or {},
                        phase="cancelled",
                        counts=self._progress_counts(record.get("progress")),
                        log_tail=self._progress_logs(record.get("progress")),
                    )
                    record["finished_at"] = _utc_now()
                    record["updated_at"] = _utc_now()
                    self._persist(record)
                    return
                discovery_job_id = str(record.get("discovery_job_id") or "")
            try:
                discovery = self.get_discovery(discovery_job_id) or {}
            except Exception as exc:
                self._fail(record, f"discovery_lookup_failed:{exc}")
                return
            status = str(discovery.get("status") or "").lower()
            with _BUILDS_LOCK:
                record = self._load(build_id)
                if record is None:
                    return
                execution = self._discovery_execution(record, discovery)
                progress = self._discovery_progress(record, discovery, execution=execution)
                changed = (
                    record.get("discovery_status") != (status or None)
                    or record.get("progress") != progress
                    or record.get("discovery_execution") != execution
                )
                if changed:
                    record["discovery_status"] = status or None
                    record["discovery_execution"] = execution
                    record["progress"] = progress
                    record["updated_at"] = _utc_now()
                    self._persist(record)
            if status not in _TERMINAL_DISCOVERY_STATUSES:
                time.sleep(self.poll_interval)
                continue
            if status != "completed":
                self._fail(record, f"discovery_{status or 'failed'}")
                return
            with _BUILDS_LOCK:
                record = self._load(build_id) or record
                record["status"] = "registering_pool"
                record["progress"] = self._progress_payload(
                    record.get("discovery_request") or {},
                    phase="registering_pool",
                    counts=self._progress_counts(record.get("progress")),
                    log_tail=self._progress_logs(record.get("progress")),
                )
                record["updated_at"] = _utc_now()
                self._persist(record)
            self._register_pool(record, discovery)
            return

    def _prepare_request(self, build_id: str) -> dict[str, Any] | None:
        with _BUILDS_LOCK:
            record = self._load(build_id)
            if record is None:
                return None
            transient = self._transient_discovery_requests.get(build_id)
            request = dict(transient or record.get("discovery_request") or {})
            prompt_parse = record.get("prompt_parse") if isinstance(record.get("prompt_parse"), Mapping) else {}
            if prompt_parse.get("status") == "completed":
                return request
            record["status"] = "parsing_prompt"
            record["prompt_parse"] = {
                **dict(prompt_parse),
                "status": "running",
            }
            record["progress"] = self._progress_payload(request, phase="parsing_prompt")
            record["updated_at"] = _utc_now()
            self._persist(record)

        if self.prepare_discovery_request is None:
            prepared = {
                "request": request,
                "parser": "passthrough",
                "warnings": [],
                "reasoning": "",
            }
        else:
            try:
                prepared = self.prepare_discovery_request(dict(request))
            except Exception as exc:
                with _BUILDS_LOCK:
                    record = self._load(build_id)
                    if record is not None:
                        record["prompt_parse"] = {
                            **dict(record.get("prompt_parse") or {}),
                            "status": "failed",
                        }
                        record["updated_at"] = _utc_now()
                        self._persist(record)
                        self._fail(record, f"prompt_parse_failed:{exc}")
                return None
        if not isinstance(prepared, Mapping):
            with _BUILDS_LOCK:
                record = self._load(build_id)
                if record is not None:
                    record["prompt_parse"] = {
                        **dict(record.get("prompt_parse") or {}),
                        "status": "failed",
                    }
                    self._fail(record, "prompt_parse_failed:prepare_result_must_be_object")
            return None
        prepared_request = prepared.get("request")
        if not isinstance(prepared_request, Mapping):
            with _BUILDS_LOCK:
                record = self._load(build_id)
                if record is not None:
                    record["prompt_parse"] = {
                        **dict(record.get("prompt_parse") or {}),
                        "status": "failed",
                    }
                    self._fail(record, "prompt_parse_failed:prepared_request_must_be_object")
            return None
        prepared_request = dict(prepared_request)
        prepared_request.setdefault("prompt", request.get("prompt"))
        # The downstream Discovery resource owns the exactly-once boundary for
        # the external start call. A resumed build must reuse the same job even
        # if this manager crashed before checkpointing the returned job ID.
        prepared_request["idempotency_key"] = f"{build_id}:discovery"
        warnings = prepared.get("warnings") if isinstance(prepared.get("warnings"), list) else []
        query_terms = prepared_request.get("query_terms")
        query_terms = [str(item) for item in query_terms if str(item).strip()] if isinstance(query_terms, list) else []
        with _BUILDS_LOCK:
            record = self._load(build_id)
            if record is None:
                return None
            record["discovery_request"] = _safe_value(prepared_request)
            self._transient_discovery_requests[build_id] = prepared_request
            record["prompt_parse"] = {
                "status": "completed",
                "parser": str(prepared.get("parser") or "unknown"),
                "goal": str(prepared_request.get("goal") or "") or None,
                "query_terms": _safe_value(query_terms),
                "scale_mode": str(prepared_request.get("scale_mode") or "").strip() or None,
                "output_language": str(prepared_request.get("output_language") or "").strip() or None,
                "warnings": _safe_value(warnings),
                "reasoning": _safe_error(prepared.get("reasoning") or ""),
            }
            record["status"] = "starting_discovery"
            record["progress"] = self._progress_payload(
                prepared_request,
                phase="starting_discovery",
            )
            record["updated_at"] = _utc_now()
            self._persist(record)
        return prepared_request

    def _register_pool(self, record: dict[str, Any], discovery: Mapping[str, Any]) -> None:
        with _BUILDS_LOCK:
            record = self._load(str(record.get("build_id") or "")) or record
            if record.get("pool_id") or record.get("cancel_requested"):
                return
            try:
                discovery_record = discovery.get("record") if isinstance(discovery.get("record"), Mapping) else discovery
                if not isinstance(discovery_record, Mapping):
                    raise ValueError("discovery_record_must_be_object")
                discovery_request = (
                    record.get("discovery_request")
                    if isinstance(record.get("discovery_request"), Mapping)
                    else {}
                )
                review = dict(record.get("review") or {})
                if record.get("action") == "build_and_review":
                    # Candidate-generation identity is server evidence.  Never
                    # trust a caller-supplied value to bypass family conflicts.
                    review["generator_identity"] = self._candidate_generation_identity(
                        discovery_record,
                        discovery_request=discovery_request,
                    )
                    record["review"] = review
                supplied_pool = discovery_record.get("pool")
                if isinstance(supplied_pool, Mapping):
                    pool = strip_pool_for_mode(self._validated_pool(supplied_pool), mode="expert")
                    pool = self._validated_pool(pool)
                    supplied_private_key = discovery_record.get("private_key")
                    if not isinstance(supplied_private_key, Mapping):
                        raise ValueError("discovery_supplied_pool_requires_private_key")
                    private_key = self._validated_private_key(pool, supplied_private_key)
                else:
                    pool, private_key = build_blinded_pool_from_discovery(
                        discovery_record,
                        prompt=str(discovery_request.get("prompt") or ""),
                        build_id=str(record.get("build_id") or ""),
                        visible_constraints=discovery_request,
                    )
                    pool = self._validated_pool(pool)
                    private_key = self._validated_private_key(pool, private_key)
                label = str(record.get("label") or "").strip() or str(record.get("build_id") or "pool")
                pool_record = self.registry.import_generated_pool(
                    pool,
                    private_key=private_key,
                    label=label,
                )
            except Exception as exc:
                self._fail(record, f"pool_registration_failed:{exc}")
                return
            # This is the durable boundary.  Review handoff failures must not rebuild it.
            record["pool_id"] = pool_record["pool_id"]
            record["status"] = "pool_ready"
            record["progress"] = self._progress_payload(
                record.get("discovery_request") or {},
                phase="pool_ready",
                counts=self._progress_counts(record.get("progress")),
                log_tail=self._progress_logs(record.get("progress")),
            )
            if record.get("action") == "build_only":
                record["progress"]["percent"] = 100
            record["updated_at"] = _utc_now()
            self._persist(record)
        if record.get("action") == "build_and_review":
            self._start_review(str(record["build_id"]))

    def _start_review(self, build_id: str) -> None:
        with _BUILDS_LOCK:
            record = self._load(build_id)
            if record is None or record.get("review_job_id") or not record.get("pool_id"):
                return
            if record.get("review_status") == "starting":
                return
            if self.start_review is None:
                record["review_status"] = "failed"
                record["review_error"] = "review_starter_not_configured"
                record["updated_at"] = _utc_now()
                self._persist(record)
                return
            # Persist intent before the external call so normal replay never duplicates it.
            record["review_status"] = "starting"
            record["progress"] = self._progress_payload(
                record.get("discovery_request") or {},
                phase="starting_review",
                counts=self._progress_counts(record.get("progress")),
                log_tail=self._progress_logs(record.get("progress")),
            )
            record["review_start_attempts"] = int(record.get("review_start_attempts") or 0) + 1
            record["updated_at"] = _utc_now()
            self._persist(record)
            pool_id = str(record["pool_id"])
            review = dict(record.get("review") or {})
        try:
            started = self.start_review(pool_id, review)
            review_job_id = str(started.get("job_id") or "")
            if not review_job_id:
                raise ValueError("review_start_returned_no_job_id")
        except Exception as exc:
            with _BUILDS_LOCK:
                record = self._load(build_id)
                if record is not None:
                    record["status"] = "pool_ready"
                    record["review_status"] = "failed"
                    record["review_error"] = _safe_error(exc)
                    record["progress"] = self._progress_payload(
                        record.get("discovery_request") or {},
                        phase="pool_ready",
                        message=self._localized_message(record, "pool_ready") + " " + _safe_error(exc),
                        counts=self._progress_counts(record.get("progress")),
                        log_tail=self._progress_logs(record.get("progress")),
                    )
                    record["updated_at"] = _utc_now()
                    self._persist(record)
            return
        with _BUILDS_LOCK:
            record = self._load(build_id)
            if record is not None:
                record["review_job_id"] = review_job_id
                record["review_status"] = str(started.get("status") or "queued")
                record["status"] = "completed"
                record["progress"] = self._progress_payload(
                    record.get("discovery_request") or {},
                    phase="completed",
                    counts=self._progress_counts(record.get("progress")),
                    log_tail=self._progress_logs(record.get("progress")),
                )
                record["finished_at"] = _utc_now()
                record["updated_at"] = _utc_now()
                self._persist(record)

    @staticmethod
    def _validated_pool(pool: Mapping[str, Any]) -> dict[str, Any]:
        """Enforce the build boundary before a pool is registered or exposed."""
        sanitized = _safe_value(pool)
        candidates = sanitized.get("candidates") if isinstance(sanitized, Mapping) else None
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("pool_requires_candidates")
        candidate_ids: set[str] = set()
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate, Mapping):
                raise ValueError(f"candidate_{index}_must_be_object")
            candidate_id = str(candidate.get("candidate_id") or "").strip()
            if not candidate_id:
                raise ValueError(f"candidate_{index}_missing_candidate_id")
            if candidate_id in candidate_ids:
                raise ValueError("pool_contains_duplicate_candidate_ids")
            candidate_ids.add(candidate_id)
        return dict(sanitized)

    @staticmethod
    def _validated_private_key(
        pool: Mapping[str, Any],
        private_key: Mapping[str, Any],
    ) -> dict[str, Any]:
        sanitized = _safe_value(private_key)
        entries = sanitized.get("candidates") if isinstance(sanitized, Mapping) else None
        if not isinstance(entries, list):
            raise ValueError("private_key_requires_candidates")
        key_candidate_ids: set[str] = set()
        for index, entry in enumerate(entries):
            if not isinstance(entry, Mapping):
                raise ValueError(f"private_key_candidate_{index}_must_be_object")
            candidate_id = str(entry.get("candidate_id") or "").strip()
            if not candidate_id:
                raise ValueError(f"private_key_candidate_{index}_missing_candidate_id")
            if candidate_id in key_candidate_ids:
                raise ValueError("private_key_contains_duplicate_candidate_ids")
            if not str(entry.get("project_accession") or "").strip():
                raise ValueError(f"private_key_candidate_{index}_missing_project_accession")
            key_candidate_ids.add(candidate_id)
        pool_candidate_ids = {
            str(candidate.get("candidate_id") or "").strip()
            for candidate in (pool.get("candidates") or [])
            if isinstance(candidate, Mapping)
        }
        if key_candidate_ids != pool_candidate_ids:
            raise ValueError("private_key_candidate_ids_mismatch")
        return dict(sanitized)

    @staticmethod
    def _candidate_generation_identity(
        discovery_record: Mapping[str, Any],
        *,
        discovery_request: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        raw_contributors = (
            discovery_request.get("_generation_contributors")
            if isinstance(discovery_request, Mapping)
            else []
        )
        contributors = [
            _safe_value(dict(item))
            for item in (raw_contributors or [])
            if isinstance(item, Mapping)
        ]
        runtime = str(discovery_record.get("runtime") or "workflow").strip().lower()
        summary = discovery_record.get("summary")
        summary = summary if isinstance(summary, Mapping) else {}
        agentic = summary.get("agentic")
        agentic = agentic if isinstance(agentic, Mapping) else {}
        if runtime == "workflow" and agentic.get("enabled") is not True:
            identity = {
                "provider": "local",
                "requested_model_id": "workflow-discovery/v1",
                "resolved_model_id": "workflow-discovery/v1",
                "model_family": "workflow-discovery",
                "runtime": "workflow",
                "endpoint_identity": "local:workflow-discovery",
                "identity_verification": "verified",
            }
            if contributors:
                identity["contributors"] = contributors
            return identity
        agent = discovery_record.get("agent")
        agent = agent if isinstance(agent, Mapping) else {}
        model = str(
            agent.get("resolved_model_id")
            or agent.get("requested_model_id")
            or agent.get("model")
            or agentic.get("model")
            or ""
        ).strip()
        identity = {
            "provider": str(agent.get("provider") or "openai_compatible"),
            "requested_model_id": str(agent.get("requested_model_id") or model) or None,
            "resolved_model_id": str(agent.get("resolved_model_id") or "") or None,
            "model_family": str(agent.get("model_family") or model) or None,
            "runtime": "agentic_workflow" if runtime == "workflow" else runtime or "unknown",
            "endpoint_identity": str(agent.get("endpoint_identity") or "") or None,
            "identity_verification": str(agent.get("identity_verification") or "unverified"),
        }
        if contributors:
            identity["contributors"] = contributors
        return identity

    def _fail(self, record: dict[str, Any], error: str) -> None:
        with _BUILDS_LOCK:
            latest = self._load(str(record.get("build_id") or ""))
            if latest is not None:
                record = latest
            if str(error).startswith("prompt_parse_failed"):
                prompt_parse = record.get("prompt_parse")
                prompt_parse = prompt_parse if isinstance(prompt_parse, Mapping) else {}
                record["prompt_parse"] = {
                    **dict(prompt_parse),
                    "status": "failed",
                }
            if record.get("cancel_requested"):
                record["status"] = "cancelled"
                phase = "cancelled"
            else:
                record["status"] = "failed"
                phase = "failed"
            record["error"] = _safe_error(error)
            record["progress"] = self._progress_payload(
                record.get("discovery_request") or {},
                phase=phase,
                message=self._localized_message(record, phase) + " " + _safe_error(error),
                counts=self._progress_counts(record.get("progress")),
                log_tail=self._progress_logs(record.get("progress")),
            )
            record["finished_at"] = _utc_now()
            record["updated_at"] = _utc_now()
            self._persist(record)

    def _path(self, build_id: str) -> Path:
        return self.root / f"{_safe_id(build_id)}.json"

    def _load(self, build_id: str) -> dict[str, Any] | None:
        return self._load_path(self._path(build_id))

    @staticmethod
    def _load_path(path: Path) -> dict[str, Any] | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _persist(self, record: Mapping[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(str(record.get("build_id") or ""))
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(_safe_value(record), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            for attempt in range(4):
                try:
                    os.replace(temporary, path)
                    break
                except PermissionError:
                    if attempt >= 3:
                        raise
                    time.sleep(0.01 * (2**attempt))
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _find_by_idempotency_key(self, key: str) -> dict[str, Any] | None:
        if not self.root.exists():
            return None
        for path in self.root.glob("*.json"):
            record = self._load_path(path)
            if record and record.get("idempotency_key") == key:
                return record
        return None

    @staticmethod
    def _public(record: Mapping[str, Any]) -> dict[str, Any]:
        payload = _safe_value(record)
        payload.pop("discovery_request", None)
        payload.pop("review", None)
        execution = payload.pop("discovery_execution", None)
        if isinstance(execution, Mapping):
            progress = payload.get("progress") if isinstance(payload.get("progress"), Mapping) else {}
            payload["progress"] = {**dict(progress), **dict(execution)}
        return payload

    @staticmethod
    def _progress_counts(progress: Any) -> dict[str, int]:
        counts = progress.get("counts") if isinstance(progress, Mapping) else {}
        counts = counts if isinstance(counts, Mapping) else {}
        return {
            "candidate_projects_seen": _nonnegative_int(counts.get("candidate_projects_seen")),
            "selected_projects": _nonnegative_int(counts.get("selected_projects")),
            "selected_files": _nonnegative_int(counts.get("selected_files")),
        }

    @staticmethod
    def _progress_logs(progress: Any) -> list[dict[str, Any]]:
        logs = progress.get("log_tail") if isinstance(progress, Mapping) else []
        return [dict(item) for item in logs if isinstance(item, Mapping)][-20:]

    @classmethod
    def _localized_message(cls, record_or_request: Mapping[str, Any], phase: str) -> str:
        request = record_or_request.get("discovery_request")
        if isinstance(request, Mapping):
            language = str(request.get("output_language") or "")
        else:
            language = str(record_or_request.get("output_language") or "")
        locale = "zh" if language.casefold().startswith("zh") else "en"
        return _PROGRESS_MESSAGES[locale].get(phase, phase)

    @classmethod
    def _progress_payload(
        cls,
        request: Mapping[str, Any],
        *,
        phase: str,
        message: str | None = None,
        counts: Mapping[str, Any] | None = None,
        log_tail: list[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        normalized_counts = {
            "candidate_projects_seen": _nonnegative_int((counts or {}).get("candidate_projects_seen")),
            "selected_projects": _nonnegative_int((counts or {}).get("selected_projects")),
            "selected_files": _nonnegative_int((counts or {}).get("selected_files")),
        }
        normalized_logs = [
            _safe_value(dict(item))
            for item in (log_tail or [])
            if isinstance(item, Mapping)
        ][-20:]
        return {
            "phase": phase,
            "percent": int(_PROGRESS_PERCENT.get(phase, 0)),
            "message": message or cls._localized_message(request, phase),
            "counts": normalized_counts,
            "log_tail": normalized_logs,
        }

    @classmethod
    def _discovery_progress(
        cls,
        record: Mapping[str, Any],
        discovery: Mapping[str, Any],
        *,
        execution: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        discovery_record = discovery.get("record") if isinstance(discovery.get("record"), Mapping) else {}
        summary = discovery_record.get("summary") if isinstance(discovery_record.get("summary"), Mapping) else {}
        previous = record.get("progress") if isinstance(record.get("progress"), Mapping) else {}
        counts = {
            "candidate_projects_seen": _nonnegative_int(summary.get("candidate_projects_seen")),
            "selected_projects": _nonnegative_int(summary.get("selected_projects")),
            "selected_files": _nonnegative_int(summary.get("selected_files")),
        }
        if not any(counts.values()):
            counts = cls._progress_counts(previous)
        if execution is not None:
            counts["candidate_projects_seen"] = max(
                counts["candidate_projects_seen"],
                _nonnegative_int(execution.get("candidate_count")),
            )
        raw_logs = discovery.get("logs") if isinstance(discovery.get("logs"), list) else []
        logs = [dict(item) for item in raw_logs if isinstance(item, Mapping)] or cls._progress_logs(previous)
        return cls._progress_payload(
            record.get("discovery_request") or {},
            phase="discovering",
            counts=counts,
            log_tail=logs,
        )

    @classmethod
    def _discovery_execution(
        cls,
        record: Mapping[str, Any],
        discovery: Mapping[str, Any],
    ) -> dict[str, Any]:
        previous = record.get("discovery_execution")
        previous = previous if isinstance(previous, Mapping) else {}
        request = record.get("discovery_request")
        request = request if isinstance(request, Mapping) else {}
        discovery_record = discovery.get("record")
        discovery_record = discovery_record if isinstance(discovery_record, Mapping) else {}
        summary = discovery_record.get("summary")
        summary = summary if isinstance(summary, Mapping) else {}
        agent = discovery_record.get("agent")
        if not isinstance(agent, Mapping):
            agent = summary.get("agent_runtime")
        agent = agent if isinstance(agent, Mapping) else {}
        logs = [item for item in (discovery.get("logs") or []) if isinstance(item, Mapping)]
        event_types = {str(item.get("type") or "").strip() for item in logs}

        runtime = str(
            agent.get("runtime")
            or discovery_record.get("runtime")
            or previous.get("runtime")
            or request.get("runtime")
            or ""
        ).strip() or None
        if runtime is None and any(
            event_type.startswith(("sdk_", "candidate_search_", "candidate_inspection_"))
            or event_type in {"round_value_evaluated", "dynamic_search_stopped", "manifest_selected"}
            for event_type in event_types
        ):
            runtime = "openai_agents"
        mode = str(agent.get("mode") or previous.get("mode") or "").strip() or None
        search_round = _nonnegative_int(previous.get("search_round"))
        discovery_round = _nonnegative_int(previous.get("discovery_round"))
        candidate_count = _nonnegative_int(previous.get("candidate_count"))
        project_judgments = previous.get("project_judgments")
        project_judgments = dict(project_judgments) if isinstance(project_judgments, Mapping) else {}
        current_event = str(previous.get("current_event") or "").strip() or None
        stop_reason = str(agent.get("stop_reason") or previous.get("stop_reason") or "").strip() or None
        search_stop_reason = str(
            agent.get("search_stop_reason") or previous.get("search_stop_reason") or ""
        ).strip() or None

        search_sequences: set[str] = set()
        for index, entry in enumerate(logs):
            event_type = str(entry.get("type") or "").strip()
            if not event_type:
                continue
            current_event = event_type
            payload = entry.get("payload")
            payload = payload if isinstance(payload, Mapping) else {}
            if event_type == "candidate_search_completed":
                sequence = str(entry.get("source_sequence") or entry.get("sequence") or index)
                search_sequences.add(sequence)
                observation = payload.get("observation")
                observation = observation if isinstance(observation, Mapping) else {}
                candidate_count = max(candidate_count, _nonnegative_int(observation.get("candidate_count")))
            if event_type == "project_judgments_recorded":
                judgment_summary = payload.get("project_judgment_summary")
                if isinstance(judgment_summary, Mapping):
                    project_judgments = dict(judgment_summary)
            round_index = payload.get("round_index")
            if round_index is None:
                observation = payload.get("observation")
                if isinstance(observation, Mapping):
                    round_index = observation.get("round_index")
            discovery_round = max(discovery_round, _nonnegative_int(round_index))
            if event_type == "dynamic_search_stopped":
                search_stop_reason = str(payload.get("reason") or "").strip() or search_stop_reason
            if event_type in {"run_completed", "run_blocked", "run_cancelled", "run_failed"}:
                stop_reason = str(payload.get("reason") or payload.get("error") or "").strip() or stop_reason

        search_round = max(search_round, len(search_sequences), _nonnegative_int(agent.get("candidate_searches")))
        discovery_round = max(discovery_round, _nonnegative_int(agent.get("discovery_rounds")))
        latest_metrics = agent.get("latest_metrics")
        latest_metrics = latest_metrics if isinstance(latest_metrics, Mapping) else {}
        metric_counts = latest_metrics.get("counts")
        metric_counts = metric_counts if isinstance(metric_counts, Mapping) else {}
        candidate_count = max(
            candidate_count,
            _nonnegative_int(metric_counts.get("candidate_projects")),
            _nonnegative_int(summary.get("candidate_projects_seen")),
        )
        budget = agent.get("budget")
        budget = budget if isinstance(budget, Mapping) else {}
        max_rounds = _nonnegative_int(budget.get("max_discovery_rounds")) or None
        agent_judgments = agent.get("project_judgment_summary")
        if isinstance(agent_judgments, Mapping):
            project_judgments = dict(agent_judgments)
        if not project_judgments:
            project_judgments = {
                "evidence_stage": "search",
                "assessed_projects": 0,
                "qualified_projects": 0,
                "qualified_target": _nonnegative_int(request.get("max_projects")),
                "investigate_projects": 0,
                "rejected_projects": 0,
                "grade_counts": {"0": 0, "1": 0, "2": 0, "3": 0, "unknown": 0},
            }

        status = str(agent.get("status") or discovery.get("status") or previous.get("status") or "").strip() or None
        if status == "failed" and stop_reason is None:
            stop_reason = str(discovery.get("error") or "discovery_failed").strip() or "discovery_failed"
        elif status == "cancelled" and stop_reason is None:
            stop_reason = "user_cancelled"
        terminal_stage = {"completed": "completed", "failed": "failed", "cancelled": "cancelled", "blocked": "failed"}
        current_stage = terminal_stage.get(str(status or "").lower())
        if current_stage is None:
            if status == "queued":
                current_stage = "agent_starting"
            else:
                current_stage = {
                    "candidate_search_started": "searching",
                    "candidate_search_completed": "planning",
                    "candidate_inspection_started": "inspecting",
                    "candidate_inspection_completed": "evaluating",
                    "project_judgments_recorded": "evaluating",
                    "round_value_evaluated": "evaluating",
                    "manifest_selected": "finalizing",
                    "dynamic_search_stopped": "evaluating",
                }.get(str(current_event or ""), "planning" if runtime == "openai_agents" else "agent_starting")
        return {
            "runtime": runtime,
            "mode": mode,
            "status": status,
            "current_stage": current_stage,
            "search_round": search_round,
            "discovery_round": discovery_round,
            "max_discovery_rounds": max_rounds,
            "candidate_count": candidate_count,
            "project_judgments": project_judgments,
            "current_event": current_event,
            "stop_reason": stop_reason,
            "search_stop_reason": search_stop_reason,
        }
