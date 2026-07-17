from __future__ import annotations

import concurrent.futures
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Mapping
from typing import Any, Callable

from agent.discovery.blind_judging import judge_blinded_pool
from agent.web.expert_review.consensus import (
    CandidateGenerationIdentity,
    ExpertConsensusEngine,
    ExpertJudgment,
    ExpertModelProfile,
    ExpertPanel,
)
from agent.web.expert_review.expert_runner import ModelExpertRunner
from agent.web.expert_review.grading import merge_machine_reviews, merge_model_expert_results
from agent.web.expert_review.openai_judge import OpenAISdkJudge, redact_text
from agent.web.expert_review.pool_registry import ExpertPoolRegistry


_JOBS_LOCK = threading.RLock()
_JOBS: dict[str, dict[str, Any]] = {}
_RUNNING = 0
_MAX_RUNNING = 2
MAX_EXPERT_JOB_WORKERS = 8
_WORKER_THREADS: dict[str, threading.Thread] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalise_job_workers(value: Any, *, default: int) -> int:
    try:
        workers = int(default if value is None else value)
    except (TypeError, ValueError) as exc:
        raise ValueError("workers_must_be_integer") from exc
    if not 1 <= workers <= MAX_EXPERT_JOB_WORKERS:
        raise ValueError(f"workers_out_of_range:1-{MAX_EXPERT_JOB_WORKERS}")
    return workers


def _read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _read_job_record(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _public_job(job: dict[str, Any], *, detail: bool = False) -> dict[str, Any]:
    payload = {
        "job_id": job.get("job_id"),
        "pool_id": job.get("pool_id"),
        "profile_id": job.get("profile_id"),
        "profile_ids": job.get("profile_ids") or [],
        "job_type": job.get("job_type") or "single_model",
        "panel": job.get("panel"),
        "model": job.get("model"),
        "base_url": job.get("base_url"),
        "judgment_source": job.get("judgment_source"),
        "status": job.get("status"),
        "cancel_requested": bool(job.get("cancel_requested")),
        "progress": job.get("progress") or {},
        "error": job.get("error"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "output_language": job.get("output_language") or "en",
        "scale_mode": job.get("scale_mode") or "auto",
        "workers": int(job.get("workers") or 1),
        "output_reviewed_path": job.get("output_reviewed_path"),
        "log_tail": (job.get("logs") or [])[-20:],
    }
    if detail:
        payload["items"] = job.get("items") or {}
        payload["logs"] = job.get("logs") or []
    return payload


class ExpertJudgeJobManager:
    def __init__(
        self,
        registry: ExpertPoolRegistry,
        *,
        resolve_profile: Callable[[str], Mapping[str, Any]],
        list_profiles: Callable[[], list[Mapping[str, Any]]] | None = None,
        max_running: int = _MAX_RUNNING,
        judge_factory: Callable[..., Any] | None = None,
        expert_runner: Callable[[ExpertModelProfile, Mapping[str, Any]], Any] | None = None,
    ) -> None:
        self.registry = registry
        self.resolve_profile = resolve_profile
        self.list_profiles = list_profiles or (lambda: [])
        self.max_running = max_running
        self.judge_factory = judge_factory or (
            lambda **kwargs: OpenAISdkJudge(**kwargs)
        )
        self.expert_runner = expert_runner or ModelExpertRunner(resolve_profile=resolve_profile)

    def list_jobs(self, pool_id: str | None = None) -> list[dict[str, Any]]:
        with _JOBS_LOCK:
            jobs = list(_JOBS.values())
            # also load from disk
            root = self.registry.root
            if root.exists():
                for pool_dir in root.iterdir():
                    jobs_dir = pool_dir / "jobs"
                    if not jobs_dir.is_dir():
                        continue
                    for path in jobs_dir.glob("*.json"):
                        if path.name.endswith(".progress.json") or path.name.endswith(".log.json"):
                            continue
                        payload = _read_job_record(path)
                        if payload is None:
                            continue
                        if payload.get("job_id"):
                            jid = str(payload["job_id"])
                            if jid not in _JOBS:
                                if payload.get("status") in {"running", "queued"}:
                                    if payload.get("cancel_requested"):
                                        payload["status"] = "cancelled"
                                        payload["finished_at"] = payload.get("finished_at") or _utc_now()
                                        payload.setdefault("logs", []).append(
                                            {"ts": _utc_now(), "level": "warning", "message": "Recovered cancelled job after restart."}
                                        )
                                    else:
                                        payload["status"] = "queued"
                                        payload["started_at"] = None
                                        payload["finished_at"] = None
                                        payload["error"] = None
                                        payload.setdefault("logs", []).append(
                                            {"ts": _utc_now(), "level": "warning", "message": "Recovered after restart; queued to resume."}
                                        )
                                _JOBS[jid] = payload
                                jobs.append(payload)
            if pool_id:
                jobs = [job for job in jobs if job.get("pool_id") == pool_id]
            jobs.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
            should_resume = any(job.get("status") == "queued" and not job.get("cancel_requested") for job in jobs)
            public = [_public_job(job) for job in jobs]
        if should_resume:
            self._kick()
        return public

    def get_job(self, job_id: str, *, detail: bool = False) -> dict[str, Any] | None:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job is None:
                job = self._load_job(job_id)
                if job is None:
                    return None
                if job.get("status") in {"running", "queued"}:
                    if job.get("cancel_requested"):
                        job["status"] = "cancelled"
                        job["finished_at"] = job.get("finished_at") or _utc_now()
                    else:
                        job["status"] = "queued"
                        job["started_at"] = None
                        job["finished_at"] = None
                _JOBS[job_id] = job
                should_resume = job.get("status") == "queued"
            else:
                should_resume = False
            public = _public_job(job, detail=detail)
        if should_resume:
            self._kick()
        return public

    def start_job(
        self,
        *,
        pool_id: str,
        profile_id: str,
        independent_model: bool = False,
        workers: int = 2,
    ) -> dict[str, Any]:
        document = self.registry.load_pool_document(pool_id, prefer_reviewed=True)
        if document is None:
            raise ValueError("pool_not_found")
        secrets = self.resolve_profile(profile_id)
        job_id = f"judge_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        candidates = [item for item in (document.get("candidates") or []) if isinstance(item, dict)]
        items = {
            str(item.get("candidate_id") or ""): "pending"
            for item in candidates
            if str(item.get("candidate_id") or "")
        }
        job = {
            "job_id": job_id,
            "pool_id": pool_id,
            "profile_id": profile_id,
            "model": secrets["model"],
            "base_url": secrets["base_url"],
            "judgment_source": (
                "provisional_independent_model"
                if independent_model
                else "provisional_same_family"
            ),
            "status": "queued",
            "cancel_requested": False,
            "progress": {
                "total": len(items),
                "done": 0,
                "failed": 0,
                "skipped_resume": 0,
            },
            "items": items,
            "failed_ids": [],
            "logs": [{"ts": _utc_now(), "level": "info", "message": "Job queued."}],
            "error": None,
            "created_at": _utc_now(),
            "started_at": None,
            "finished_at": None,
            "workers": _normalise_job_workers(workers, default=2),
            "output_reviewed_path": None,
            "api_key": secrets["api_key"],
            "timeout": secrets.get("timeout") or "120",
        }
        with _JOBS_LOCK:
            _JOBS[job_id] = job
            self._persist_job(job)
        self._kick()
        return _public_job(job)

    def start_consensus_job(
        self,
        *,
        pool_id: str,
        generator_identity: Mapping[str, Any] | None = None,
        workers: int = 1,
        idempotency_key: str | None = None,
        output_language: str = "en",
        scale_mode: str = "auto",
    ) -> dict[str, Any]:
        document = self.registry.load_pool_document(pool_id, prefer_reviewed=True)
        if document is None:
            raise ValueError("pool_not_found")
        request_key = str(idempotency_key or "").strip()
        with _JOBS_LOCK:
            if request_key:
                existing = self._find_by_idempotency_key(pool_id, request_key)
                if existing is not None:
                    return _public_job(existing)
        profiles = [
            ExpertModelProfile.from_mapping(item)
            for item in self.list_profiles()
            if isinstance(item, Mapping)
        ]
        generator = CandidateGenerationIdentity.model_validate(generator_identity or {})
        selector = ExpertConsensusEngine(self.expert_runner)
        panel = selector.select_panel(profiles, generator)
        job_id = f"consensus_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        candidates = [item for item in (document.get("candidates") or []) if isinstance(item, dict)]
        items = {
            str(item.get("candidate_id") or ""): "pending"
            for item in candidates
            if str(item.get("candidate_id") or "")
        }
        profile_ids = panel.primary_profile_ids + (
            [panel.third_profile_id] if panel.third_profile_id else []
        )
        normalized_scale = str(scale_mode or "auto").strip().casefold()
        if normalized_scale not in {"auto", "curated", "balanced", "exhaustive"}:
            normalized_scale = "auto"
        job = {
            "job_id": job_id,
            "job_type": "model_expert_consensus",
            "pool_id": pool_id,
            "profile_id": None,
            "profile_ids": profile_ids,
            "panel": panel.model_dump(mode="json"),
            "generator_identity": generator.model_dump(mode="json"),
            "idempotency_key": request_key or None,
            "model": None,
            "base_url": None,
            "judgment_source": "model_expert_provisional",
            "status": "queued",
            "cancel_requested": False,
            "progress": {"total": len(items), "done": 0, "failed": 0, "skipped_resume": 0},
            "items": items,
            "failed_ids": [],
            "logs": [{"ts": _utc_now(), "level": "info", "message": "Consensus job queued."}],
            "error": None,
            "created_at": _utc_now(),
            "started_at": None,
            "finished_at": None,
            "workers": _normalise_job_workers(workers, default=2),
            "output_language": "zh-CN" if str(output_language).casefold().startswith("zh") else "en",
            "scale_mode": normalized_scale,
            "output_reviewed_path": None,
            "consensus_summary": {},
        }
        with _JOBS_LOCK:
            _JOBS[job_id] = job
            self._persist_job(job)
        self._kick()
        return _public_job(job)

    def cancel_job(self, job_id: str) -> dict[str, Any] | None:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id) or self._load_job(job_id)
            if job is None:
                return None
            _JOBS[job_id] = job
            if job.get("status") in {"completed", "completed_with_errors", "failed", "cancelled"}:
                return _public_job(job)
            if job.get("status") == "queued":
                job["cancel_requested"] = True
                job["status"] = "cancelled"
                job["finished_at"] = _utc_now()
                self._append_log(job, "warning", "Queued job cancelled.")
            else:
                job["cancel_requested"] = True
                self._append_log(job, "warning", "Cancel requested.")
            self._persist_job(job)
            return _public_job(job)

    def delete_job(self, job_id: str) -> dict[str, Any] | None:
        if (
            not job_id.startswith(("judge_", "consensus_"))
            or any(not (character.isalnum() or character in {"_", "-"}) for character in job_id)
        ):
            return None
        with _JOBS_LOCK:
            job = _JOBS.get(job_id) or self._load_job(job_id)
            if job is None:
                return None
            status = str(job.get("status") or "")
            if status == "running":
                raise ValueError("job_running_cancel_before_delete")
            pool_id = str(job.get("pool_id") or "")
            if not pool_id:
                raise ValueError("job_pool_id_missing")
            jobs_dir = self.registry.root / pool_id / "jobs"
            for suffix in (
                ".json",
                ".progress.jsonl",
                ".consensus.progress.jsonl",
                ".judgments.progress.jsonl",
                ".reviewed.json",
            ):
                (jobs_dir / f"{job_id}{suffix}").unlink(missing_ok=True)
            _JOBS.pop(job_id, None)
            _WORKER_THREADS.pop(job_id, None)
            return {"job_id": job_id, "pool_id": pool_id, "status": status}

    def resume_job(self, job_id: str, *, workers: int | None = None) -> dict[str, Any] | None:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id) or self._load_job(job_id)
            if job is None:
                return None
            if job.get("status") not in {"cancelled", "failed", "completed_with_errors"}:
                self._append_log(job, "warning", "Resume is available only for interrupted terminal jobs.")
                self._persist_job(job)
                return _public_job(job)
            pending = [cid for cid, status in (job.get("items") or {}).items() if status in {"pending", "failed"}]
            if not pending:
                self._append_log(job, "info", "No pending candidates to resume.")
                self._persist_job(job)
                return _public_job(job)
            for cid in pending:
                job.setdefault("items", {})[cid] = "pending"
            if workers is not None:
                job["workers"] = _normalise_job_workers(workers, default=int(job.get("workers") or 2))
            job["run_targets"] = pending
            job["failed_ids"] = []
            job["progress"]["failed"] = 0
            job["status"] = "queued"
            job["cancel_requested"] = False
            job["error"] = None
            job["finished_at"] = None
            self._append_log(job, "info", f"Resuming {len(pending)} pending candidates.")
            _JOBS[job_id] = job
            self._persist_job(job)
        self._kick()
        return _public_job(job)

    def retry_failed(self, job_id: str, *, workers: int | None = None) -> dict[str, Any] | None:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id) or self._load_job(job_id)
            if job is None:
                return None
            if job.get("status") not in {"completed_with_errors", "failed", "cancelled"}:
                self._append_log(job, "warning", "Retry is available only after the job reaches a terminal state.")
                self._persist_job(job)
                return _public_job(job)
            failed_ids = list(job.get("failed_ids") or [])
            if not failed_ids:
                self._append_log(job, "info", "No failed candidates to retry.")
                self._persist_job(job)
                return _public_job(job)
            for cid in failed_ids:
                job.setdefault("items", {})[cid] = "pending"
            if workers is not None:
                job["workers"] = _normalise_job_workers(workers, default=int(job.get("workers") or 2))
            job["run_targets"] = failed_ids
            job["failed_ids"] = []
            job["progress"]["failed"] = 0
            job["status"] = "queued"
            job["cancel_requested"] = False
            job["error"] = None
            job["finished_at"] = None
            self._append_log(job, "info", f"Retrying {len(failed_ids)} failed candidates.")
            _JOBS[job_id] = job
            self._persist_job(job)
        self._kick()
        return _public_job(job)

    def _kick(self) -> None:
        with _JOBS_LOCK:
            global _RUNNING
            if _RUNNING >= self.max_running:
                return
            queued = [
                job
                for job in _JOBS.values()
                if job.get("status") == "queued" and not job.get("cancel_requested")
            ]
            if not queued:
                return
            job = sorted(queued, key=lambda item: str(item.get("created_at") or ""))[0]
            job["status"] = "running"
            job["started_at"] = job.get("started_at") or _utc_now()
            _RUNNING += 1
            self._persist_job(job)
            thread = threading.Thread(
                target=self._run_job,
                args=(job["job_id"],),
                name=f"expert-judge-{job['job_id']}",
                daemon=True,
            )
            _WORKER_THREADS[job["job_id"]] = thread
            thread.start()

    def _run_job(self, job_id: str) -> None:
        try:
            with _JOBS_LOCK:
                job = _JOBS.get(job_id)
            if job is None:
                return
            if job.get("job_type") == "model_expert_consensus":
                self._run_consensus_job(job_id)
                return
            try:
                secrets = self.resolve_profile(str(job.get("profile_id") or ""))
            except Exception as exc:
                self._finish(job_id, status="failed", error=redact_text(str(exc)))
                return
            with _JOBS_LOCK:
                current = _JOBS.get(job_id)
                if current is None:
                    return
                if (
                    str(secrets.get("base_url") or "").rstrip("/") != str(current.get("base_url") or "").rstrip("/")
                    or str(secrets.get("model") or "") != str(current.get("model") or "")
                    or str(secrets.get("timeout") or "120") != str(current.get("timeout") or "120")
                ):
                    self._finish(job_id, status="failed", error="profile_configuration_changed; create a new job")
                    return
                current["api_key"] = secrets.get("api_key", "")
                job = current
            if job.get("cancel_requested"):
                self._finish(job_id, status="cancelled", error=None)
                return
            pool_id = str(job["pool_id"])
            document = self.registry.load_pool_document(pool_id, prefer_reviewed=True)
            if document is None:
                self._finish(job_id, status="failed", error="pool_not_found")
                return
            run_targets = {str(candidate_id) for candidate_id in (job.get("run_targets") or []) if str(candidate_id)}
            if run_targets:
                document = {
                    **document,
                    "candidates": [
                        candidate
                        for candidate in (document.get("candidates") or [])
                        if isinstance(candidate, Mapping)
                        and str(candidate.get("candidate_id") or "") in run_targets
                    ],
                }
            progress_path = self._job_dir(pool_id) / f"{job_id}.progress.jsonl"
            existing = self._load_progress(progress_path)
            with _JOBS_LOCK:
                job = _JOBS[job_id]
                job["progress"]["skipped_resume"] = len(existing)
                for cid in existing:
                    job.setdefault("items", {})[cid] = "done"
                    job["progress"]["done"] = int(job["progress"].get("done") or 0)
                # recount done uniquely
                job["progress"]["done"] = sum(
                    1 for status in (job.get("items") or {}).values() if status == "done"
                )
                self._append_log(job, "info", f"Starting judge with model {job.get('model')}.")
                self._persist_job(job)

            try:
                judge = self.judge_factory(
                    api_key=str(job.get("api_key") or ""),
                    base_url=str(job.get("base_url") or ""),
                    model=str(job.get("model") or ""),
                    timeout=float(job.get("timeout") or 120),
                )
            except Exception as exc:
                self._finish(job_id, status="failed", error=redact_text(str(exc)))
                return

            def on_review(candidate: dict[str, Any]) -> None:
                cid = str(candidate.get("candidate_id") or "")
                with _JOBS_LOCK:
                    current = _JOBS.get(job_id)
                    if current is None:
                        return
                    current.setdefault("items", {})[cid] = "done"
                    current["progress"]["done"] = sum(
                        1 for status in current["items"].values() if status == "done"
                    )
                    self._append_log(current, "info", f"Judged {cid}.")
                    self._persist_job(current)
                with progress_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"candidate_id": cid, "candidate": candidate}, ensure_ascii=False) + "\n")

            def on_start(candidate_id: str) -> None:
                cid = str(candidate_id or "")
                with _JOBS_LOCK:
                    current = _JOBS.get(job_id)
                    if current is None:
                        return
                    current.setdefault("items", {})[cid] = "running"
                    self._persist_job(current)

            def on_error(candidate_id: str, exc: Exception) -> None:
                cid = str(candidate_id or "")
                message = redact_text(str(exc))
                with _JOBS_LOCK:
                    current = _JOBS.get(job_id)
                    if current is None:
                        return
                    current.setdefault("items", {})[cid] = "failed"
                    current["failed_ids"] = sorted(
                        candidate for candidate, status in current["items"].items() if status == "failed"
                    )
                    current["progress"]["failed"] = len(current["failed_ids"])
                    self._append_log(current, "error", f"Failed {cid}: {message}")
                    self._persist_job(current)

            # Filter pending only if cancel mid-flight: judge_blinded_pool uses existing_reviews.
            # For cancel, we wrap judge to check flag.
            def guarded_judge(system_prompt: str, user_prompt: str) -> Any:
                with _JOBS_LOCK:
                    current = _JOBS.get(job_id) or {}
                    if current.get("cancel_requested"):
                        raise RuntimeError("job_cancelled")
                return judge(system_prompt, user_prompt)

            try:
                reviewed = judge_blinded_pool(
                    document,
                    guarded_judge,
                    model_name=str(job.get("model") or ""),
                    judgment_source=job.get("judgment_source") or "provisional_same_family",
                    workers=int(job.get("workers") or 1),
                    existing_reviews=existing,
                    on_start=on_start,
                    on_review=on_review,
                    on_error=on_error,
                )
            except Exception as exc:
                message = redact_text(str(exc))
                if "job_cancelled" in message or (job.get("cancel_requested")):
                    # merge partial progress
                    partial = self._load_progress(progress_path)
                    if partial:
                        partial_pool = {
                            **document,
                            "candidates": list(partial.values()),
                        }

                        def merge_partial(existing_doc: dict[str, Any]) -> dict[str, Any]:
                            return merge_machine_reviews(
                                existing_doc,
                                partial_pool,
                                job_id=job_id,
                                profile_id=str(job.get("profile_id") or ""),
                                model=str(job.get("model") or ""),
                            )

                        merged, _ = self.registry.mutate_reviewed_pool(pool_id, merge_partial)
                        self._snapshot_reviewed(pool_id, job_id, merged)
                    self._finish(job_id, status="cancelled", error=None)
                    return
                self._finish(job_id, status="failed", error=message)
                return

            if job.get("cancel_requested"):
                partial = self._load_progress(progress_path)
                if partial:
                    partial_pool = {**document, "candidates": list(partial.values())}

                    def merge_cancelled(existing_doc: dict[str, Any]) -> dict[str, Any]:
                        return merge_machine_reviews(
                            existing_doc,
                            partial_pool,
                            job_id=job_id,
                            profile_id=str(job.get("profile_id") or ""),
                            model=str(job.get("model") or ""),
                        )

                    merged, _ = self.registry.mutate_reviewed_pool(pool_id, merge_cancelled)
                    self._snapshot_reviewed(pool_id, job_id, merged)
                self._finish(job_id, status="cancelled", error=None)
                return

            def merge_latest(existing_doc: dict[str, Any]) -> dict[str, Any]:
                return merge_machine_reviews(
                    existing_doc,
                    reviewed,
                    job_id=job_id,
                    profile_id=str(job.get("profile_id") or ""),
                    model=str(job.get("model") or ""),
                )

            merged, _ = self.registry.mutate_reviewed_pool(pool_id, merge_latest)
            out_path = self._snapshot_reviewed(pool_id, job_id, merged)
            with _JOBS_LOCK:
                current = _JOBS.get(job_id)
                if current is not None:
                    current["output_reviewed_path"] = str(out_path)
                    self._persist_job(current)
            with _JOBS_LOCK:
                current = _JOBS.get(job_id) or {}
                failed = int((current.get("progress") or {}).get("failed") or 0)
                pending = sum(
                    1 for status in (current.get("items") or {}).values() if status in {"pending", "running"}
                )
            issues = failed + pending
            self._finish(
                job_id,
                status="completed_with_errors" if issues else "completed",
                error=(
                    f"{failed} candidate(s) failed; {pending} candidate(s) pending"
                    if issues
                    else None
                ),
            )
        finally:
            with _JOBS_LOCK:
                global _RUNNING
                _RUNNING = max(0, _RUNNING - 1)
            self._kick()

    def _run_consensus_job(self, job_id: str) -> None:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
        if job is None:
            return
        pool_id = str(job.get("pool_id") or "")
        document = self.registry.load_pool_document(pool_id, prefer_reviewed=True)
        if document is None:
            self._finish(job_id, status="failed", error="pool_not_found")
            return
        panel = ExpertPanel.model_validate(job.get("panel") or {})
        progress_path = self._job_dir(pool_id) / f"{job_id}.consensus.progress.jsonl"
        judgment_path = self._job_dir(pool_id) / f"{job_id}.judgments.progress.jsonl"
        results = self._load_consensus_progress(progress_path)
        judgments = self._load_judgment_progress(judgment_path)
        judgment_lock = threading.Lock()
        result_lock = threading.Lock()
        with _JOBS_LOCK:
            current = _JOBS.get(job_id)
            if current is None:
                return
            current["progress"]["skipped_resume"] = len(results)
            for candidate_id in results:
                current.setdefault("items", {})[candidate_id] = "done"
            current["progress"]["done"] = len(results)
            self._append_log(current, "info", f"Starting consensus panel {', '.join(current.get('profile_ids') or [])}.")
            self._persist_job(current)
        def checkpointed_runner(
            profile: ExpertModelProfile,
            candidate: Mapping[str, Any],
        ) -> ExpertJudgment:
            key = (str(candidate.get("candidate_id") or ""), profile.profile_id)
            with judgment_lock:
                cached = judgments.get(key)
            if cached is not None:
                return ExpertJudgment.model_validate(cached)
            candidate_for_review = dict(candidate)
            candidate_for_review["_output_language"] = str(job.get("output_language") or "en")
            judgment = ExpertJudgment.model_validate(self.expert_runner(profile, candidate_for_review))
            payload = judgment.model_dump(mode="json")
            with judgment_lock:
                judgments[key] = payload
                with judgment_path.open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(
                            {
                                "candidate_id": key[0],
                                "profile_id": key[1],
                                "judgment": payload,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            return judgment

        engine = ExpertConsensusEngine(checkpointed_runner)
        run_targets = {str(candidate_id) for candidate_id in (job.get("run_targets") or []) if str(candidate_id)}
        candidates = [
            candidate
            for candidate in (document.get("candidates") or [])
            if isinstance(candidate, Mapping)
            and str(candidate.get("candidate_id") or "")
            and str(candidate.get("candidate_id") or "") not in results
            and (not run_targets or str(candidate.get("candidate_id") or "") in run_targets)
        ]

        def process_candidate(candidate: Mapping[str, Any]) -> None:
            candidate_id = str(candidate.get("candidate_id") or "")
            with _JOBS_LOCK:
                current = _JOBS.get(job_id) or {}
                if current.get("cancel_requested"):
                    return
            try:
                with _JOBS_LOCK:
                    current = _JOBS.get(job_id)
                    if current is not None:
                        current.setdefault("items", {})[candidate_id] = "running"
                        self._persist_job(current)
                result = engine.review_candidate(candidate, panel)
                payload = result.model_dump(mode="json")
                with result_lock:
                    results[candidate_id] = payload
                    with progress_path.open("a", encoding="utf-8") as handle:
                        handle.write(
                            json.dumps(
                                {"candidate_id": candidate_id, "result": payload},
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                with _JOBS_LOCK:
                    current = _JOBS.get(job_id)
                    if current is not None:
                        current.setdefault("items", {})[candidate_id] = "done"
                        current["progress"]["done"] = sum(
                            1 for status in current["items"].values() if status == "done"
                        )
                        self._append_log(current, "info", f"Consensus completed for {candidate_id}.")
                        self._persist_job(current)
            except Exception as exc:
                message = redact_text(str(exc))
                with _JOBS_LOCK:
                    current = _JOBS.get(job_id)
                    if current is not None:
                        current.setdefault("items", {})[candidate_id] = "failed"
                        current["failed_ids"] = sorted(
                            cid for cid, status in current["items"].items() if status == "failed"
                        )
                        current["progress"]["failed"] = len(current["failed_ids"])
                        self._append_log(current, "error", f"Consensus failed for {candidate_id}: {message}")
                        self._persist_job(current)

        workers = min(_normalise_job_workers(job.get("workers"), default=1), max(1, len(candidates)))
        if workers == 1:
            for candidate in candidates:
                process_candidate(candidate)
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(process_candidate, candidate) for candidate in candidates]
                for future in concurrent.futures.as_completed(futures):
                    future.result()

        if results:
            def merge_results(existing_doc: dict[str, Any]) -> dict[str, Any]:
                return merge_model_expert_results(existing_doc, results, job_id=job_id)

            merged, _ = self.registry.mutate_reviewed_pool(pool_id, merge_results)
            out_path = self._snapshot_reviewed(pool_id, job_id, merged)
            with _JOBS_LOCK:
                current = _JOBS.get(job_id)
                if current is not None:
                    current["output_reviewed_path"] = str(out_path)
                    current["judgment_source"] = merged.get("judgment_source")
                    current["consensus_summary"] = merged.get("review_summary") or {}
                    self._persist_job(current)
        with _JOBS_LOCK:
            current = _JOBS.get(job_id) or {}
            cancelled = bool(current.get("cancel_requested"))
            failed = int((current.get("progress") or {}).get("failed") or 0)
            pending = sum(
                1 for status in (current.get("items") or {}).values() if status in {"pending", "running"}
            )
        if cancelled:
            self._finish(job_id, status="cancelled", error=None)
        else:
            issues = failed + pending
            self._finish(
                job_id,
                status="completed_with_errors" if issues else "completed",
                error=(
                    f"{failed} candidate(s) failed; {pending} candidate(s) pending"
                    if issues
                    else None
                ),
            )

    def _snapshot_reviewed(self, pool_id: str, job_id: str, pool: dict[str, Any]) -> Path:
        path = self.registry.root / pool_id / "pool.reviewed.json"
        snap = self._job_dir(pool_id) / f"{job_id}.reviewed.json"
        snap.write_text(json.dumps(pool, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def _finish(self, job_id: str, *, status: str, error: str | None) -> None:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job is None:
                return
            job["status"] = status
            job["finished_at"] = _utc_now()
            job["error"] = error
            job.pop("run_targets", None)
            # strip secret before final persist public copy
            self._append_log(job, "info" if status in {"completed", "completed_with_errors"} else "error", f"Job {status}.")
            self._persist_job(job)

    def _job_dir(self, pool_id: str) -> Path:
        path = self.registry.root / pool_id / "jobs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _persist_job(self, job: dict[str, Any]) -> None:
        pool_id = str(job.get("pool_id") or "")
        job_id = str(job.get("job_id") or "")
        if not pool_id or not job_id:
            return
        path = self._job_dir(pool_id) / f"{job_id}.json"
        public = dict(job)
        public.pop("api_key", None)
        path.write_text(json.dumps(public, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _load_job(self, job_id: str) -> dict[str, Any] | None:
        root = self.registry.root
        if not root.exists():
            return None
        for pool_dir in root.iterdir():
            path = pool_dir / "jobs" / f"{job_id}.json"
            if path.exists():
                return _read_job_record(path)
        return None

    def _load_progress(self, path: Path) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for payload in _read_jsonl_records(path):
            cid = str(payload.get("candidate_id") or "")
            candidate = payload.get("candidate")
            if cid and isinstance(candidate, dict):
                result[cid] = candidate
        return result

    def _load_consensus_progress(self, path: Path) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for payload in _read_jsonl_records(path):
            candidate_id = str(payload.get("candidate_id") or "")
            consensus = payload.get("result")
            if candidate_id and isinstance(consensus, dict):
                result[candidate_id] = consensus
        return result

    def _load_judgment_progress(self, path: Path) -> dict[tuple[str, str], dict[str, Any]]:
        result: dict[tuple[str, str], dict[str, Any]] = {}
        for payload in _read_jsonl_records(path):
            candidate_id = str(payload.get("candidate_id") or "")
            profile_id = str(payload.get("profile_id") or "")
            judgment = payload.get("judgment")
            if candidate_id and profile_id and isinstance(judgment, dict):
                result[(candidate_id, profile_id)] = judgment
        return result

    def _find_by_idempotency_key(self, pool_id: str, key: str) -> dict[str, Any] | None:
        for job in _JOBS.values():
            if job.get("pool_id") == pool_id and job.get("idempotency_key") == key:
                return job
        jobs_dir = self.registry.root / pool_id / "jobs"
        if not jobs_dir.is_dir():
            return None
        for path in jobs_dir.glob("*.json"):
            payload = _read_job_record(path)
            if payload is None:
                continue
            if payload.get("idempotency_key") == key:
                job_id = str(payload.get("job_id") or "")
                if job_id:
                    _JOBS[job_id] = payload
                return payload
        return None

    @staticmethod
    def _append_log(job: dict[str, Any], level: str, message: str) -> None:
        logs = job.setdefault("logs", [])
        logs.append({"ts": _utc_now(), "level": level, "message": redact_text(message)})
        if len(logs) > 500:
            del logs[:-500]


# module-level helpers for tests
def reset_jobs_for_tests() -> None:
    with _JOBS_LOCK:
        _JOBS.clear()
        global _RUNNING
        _RUNNING = 0
        _WORKER_THREADS.clear()
