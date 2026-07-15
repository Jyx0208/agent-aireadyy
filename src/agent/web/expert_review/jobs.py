from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agent.discovery.blind_judging import judge_blinded_pool
from agent.web.expert_review.grading import merge_machine_reviews
from agent.web.expert_review.openai_judge import OpenAISdkJudge, redact_text
from agent.web.expert_review.pool_registry import ExpertPoolRegistry


_JOBS_LOCK = threading.RLock()
_JOBS: dict[str, dict[str, Any]] = {}
_RUNNING = 0
_MAX_RUNNING = 2
_WORKER_THREADS: dict[str, threading.Thread] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _public_job(job: dict[str, Any], *, detail: bool = False) -> dict[str, Any]:
    payload = {
        "job_id": job.get("job_id"),
        "pool_id": job.get("pool_id"),
        "profile_id": job.get("profile_id"),
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
        resolve_profile: Callable[[str], dict[str, str]],
        max_running: int = _MAX_RUNNING,
        judge_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.registry = registry
        self.resolve_profile = resolve_profile
        self.max_running = max_running
        self.judge_factory = judge_factory or (
            lambda **kwargs: OpenAISdkJudge(**kwargs)
        )

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
                        try:
                            payload = json.loads(path.read_text(encoding="utf-8"))
                        except (OSError, UnicodeError, json.JSONDecodeError):
                            continue
                        if isinstance(payload, dict) and payload.get("job_id"):
                            jid = str(payload["job_id"])
                            if jid not in _JOBS:
                                _JOBS[jid] = payload
                                jobs.append(payload)
            if pool_id:
                jobs = [job for job in jobs if job.get("pool_id") == pool_id]
            jobs.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
            return [_public_job(job) for job in jobs]

    def get_job(self, job_id: str, *, detail: bool = False) -> dict[str, Any] | None:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job is None:
                job = self._load_job(job_id)
                if job is None:
                    return None
                _JOBS[job_id] = job
            return _public_job(job, detail=detail)

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
            "workers": max(1, int(workers)),
            "output_reviewed_path": None,
            "api_key": secrets["api_key"],
            "timeout": secrets.get("timeout") or "120",
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
            if job.get("status") in {"completed", "failed", "cancelled"}:
                return _public_job(job)
            job["cancel_requested"] = True
            self._append_log(job, "warning", "Cancel requested.")
            self._persist_job(job)
            return _public_job(job)

    def retry_failed(self, job_id: str) -> dict[str, Any] | None:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id) or self._load_job(job_id)
            if job is None:
                return None
            failed_ids = list(job.get("failed_ids") or [])
            if not failed_ids:
                self._append_log(job, "info", "No failed candidates to retry.")
                self._persist_job(job)
                return _public_job(job)
            for cid in failed_ids:
                job.setdefault("items", {})[cid] = "pending"
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
            if job.get("cancel_requested"):
                self._finish(job_id, status="cancelled", error=None)
                return
            pool_id = str(job["pool_id"])
            document = self.registry.load_pool_document(pool_id, prefer_reviewed=True)
            if document is None:
                self._finish(job_id, status="failed", error="pool_not_found")
                return
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

            judge = self.judge_factory(
                api_key=str(job.get("api_key") or ""),
                base_url=str(job.get("base_url") or ""),
                model=str(job.get("model") or ""),
                timeout=float(job.get("timeout") or 120),
            )

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
                    on_review=on_review,
                )
            except Exception as exc:
                message = redact_text(str(exc))
                if "job_cancelled" in message or (job.get("cancel_requested")):
                    # merge partial progress
                    partial = self._load_progress(progress_path)
                    if partial:
                        partial_pool = {
                            **document,
                            "candidates": [
                                partial.get(str(c.get("candidate_id")), c)
                                if str(c.get("candidate_id")) in partial
                                else c
                                for c in (document.get("candidates") or [])
                                if isinstance(c, dict)
                            ],
                        }
                        # simpler: rebuild from existing map
                        candidates = []
                        by_id = {
                            str(c.get("candidate_id") or ""): c
                            for c in (document.get("candidates") or [])
                            if isinstance(c, dict)
                        }
                        by_id.update(partial)
                        for c in document.get("candidates") or []:
                            if isinstance(c, dict):
                                candidates.append(by_id.get(str(c.get("candidate_id")), c))
                        merged = merge_machine_reviews(document, {**document, "candidates": candidates})
                        self._write_reviewed(pool_id, job_id, merged)
                    self._finish(job_id, status="cancelled", error=None)
                    return
                self._finish(job_id, status="failed", error=message)
                return

            if job.get("cancel_requested"):
                self._finish(job_id, status="cancelled", error=None)
                return

            existing_doc = self.registry.load_pool_document(pool_id, prefer_reviewed=True) or document
            merged = merge_machine_reviews(existing_doc, reviewed)
            out_path = self._write_reviewed(pool_id, job_id, merged)
            with _JOBS_LOCK:
                current = _JOBS.get(job_id)
                if current is not None:
                    current["output_reviewed_path"] = str(out_path)
                    self._persist_job(current)
            self._finish(job_id, status="completed", error=None)
        finally:
            with _JOBS_LOCK:
                global _RUNNING
                _RUNNING = max(0, _RUNNING - 1)
            self._kick()

    def _write_reviewed(self, pool_id: str, job_id: str, pool: dict[str, Any]) -> Path:
        pool_dir = self.registry.root / pool_id
        pool_dir.mkdir(parents=True, exist_ok=True)
        path = pool_dir / "pool.reviewed.json"
        path.write_text(json.dumps(pool, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # refresh registry stats
        record = self.registry.get_pool(pool_id) or {
            "pool_id": pool_id,
            "label": pool_id,
            "created_at": _utc_now(),
            "paths": {},
            "tags": [],
        }
        record["updated_at"] = _utc_now()
        record.setdefault("paths", {})["reviewed"] = "pool.reviewed.json"
        record["stats"] = self.registry._compute_stats(pool)
        record["judgment_source"] = str(pool.get("judgment_source") or "")
        self.registry._write_record(pool_id, record)
        # also snapshot job output
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
            # strip secret before final persist public copy
            self._append_log(job, "info" if status == "completed" else "error", f"Job {status}.")
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
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    return None
                return payload if isinstance(payload, dict) else None
        return None

    def _load_progress(self, path: Path) -> dict[str, dict[str, Any]]:
        if not path.exists():
            return {}
        result: dict[str, dict[str, Any]] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            cid = str(payload.get("candidate_id") or "")
            candidate = payload.get("candidate")
            if cid and isinstance(candidate, dict):
                result[cid] = candidate
        return result

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
