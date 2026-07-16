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
    text = re.sub(
        r"(?i)((?:api[_ -]?key|authorization|access[_ -]?token|refresh[_ -]?token|token|password|client[_ -]?secret|secret)\s*[:=]\s*)(?:bearer\s+)?\S+",
        r"\1[redacted]",
        text,
    )
    text = re.sub(r"sk-[A-Za-z0-9_-]{6,}", "[redacted-api-key]", text)
    return text


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
        poll_interval: float = 0.05,
    ) -> None:
        self.registry = registry
        self.start_discovery = start_discovery
        self.get_discovery = get_discovery
        self.cancel_discovery = cancel_discovery
        self.start_review = start_review
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
                "pool_id": None,
                "review_job_id": None,
                "review_status": "not_requested" if action == "build_only" else "pending",
                "review_start_attempts": 0,
                "review_error": None,
                "error": None,
                "cancel_requested": False,
                "discovery_request": _safe_value(dict(discovery_request)),
                "review": _safe_value(dict(review or {})),
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
        if str(record.get("status") or "") in {"queued", "discovering"}:
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
        finally:
            with _BUILDS_LOCK:
                _RUNNING_BUILD_IDS.discard(build_id)

    def _run(self, build_id: str) -> None:
        with _BUILDS_LOCK:
            record = self._load(build_id)
            if record is None or record.get("cancel_requested"):
                self._transient_discovery_requests.pop(build_id, None)
                return
            discovery_job_id = str(record.get("discovery_job_id") or "")
            if not discovery_job_id:
                # Prefer the one-process request while it exists so callers can
                # supply a request-scoped LLM credential without it ever reaching
                # the checkpoint.  After restart only the redacted request remains.
                discovery_request = self._transient_discovery_requests.get(
                    build_id,
                    dict(record.get("discovery_request") or {}),
                )
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
                record["discovery_job_id"] = discovery_job_id
                record["discovery_status"] = str(started.get("status") or "queued")
                record["status"] = "discovering"
                record["updated_at"] = _utc_now()
                self._persist(record)
        while True:
            with _BUILDS_LOCK:
                record = self._load(build_id)
                if record is None:
                    return
                if record.get("cancel_requested"):
                    record["status"] = "cancelled"
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
                record["discovery_status"] = status or None
                record["updated_at"] = _utc_now()
                self._persist(record)
            if status not in _TERMINAL_DISCOVERY_STATUSES:
                time.sleep(self.poll_interval)
                continue
            if status != "completed":
                self._fail(record, f"discovery_{status or 'failed'}")
                return
            self._register_pool(record, discovery)
            return

    def _register_pool(self, record: dict[str, Any], discovery: Mapping[str, Any]) -> None:
        with _BUILDS_LOCK:
            record = self._load(str(record.get("build_id") or "")) or record
            if record.get("pool_id"):
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
                    record["updated_at"] = _utc_now()
                    self._persist(record)
            return
        with _BUILDS_LOCK:
            record = self._load(build_id)
            if record is not None:
                record["review_job_id"] = review_job_id
                record["review_status"] = str(started.get("status") or "queued")
                record["status"] = "completed"
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

    def _fail(self, record: dict[str, Any], error: str) -> None:
        with _BUILDS_LOCK:
            record["status"] = "failed"
            record["error"] = _safe_error(error)
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
            os.replace(temporary, path)
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
        return payload
