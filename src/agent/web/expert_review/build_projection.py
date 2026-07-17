from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def attach_review_progress(
    builds: Sequence[Mapping[str, Any]],
    jobs: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Project safe review-job progress onto build resources for one UI poll."""
    review_ids = {
        str(build.get("review_job_id") or "").strip()
        for build in builds
        if str(build.get("review_job_id") or "").strip()
    }
    jobs_by_id = {
        str(job.get("job_id") or "").strip(): job
        for job in jobs
        if str(job.get("job_id") or "").strip() in review_ids
    }
    enriched: list[dict[str, Any]] = []
    for build in builds:
        payload = dict(build)
        review_job = jobs_by_id.get(str(build.get("review_job_id") or "").strip())
        if review_job is not None:
            log_tail = review_job.get("log_tail")
            log_tail = log_tail if isinstance(log_tail, list) else []
            progress = review_job.get("progress")
            payload["review_progress"] = {
                "status": review_job.get("status"),
                "progress": dict(progress) if isinstance(progress, Mapping) else {},
                "error": review_job.get("error"),
                "log_tail": [dict(item) for item in log_tail if isinstance(item, Mapping)][-8:],
            }
        enriched.append(payload)
    return enriched
