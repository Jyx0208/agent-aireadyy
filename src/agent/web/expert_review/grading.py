from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from agent.web.expert_review.pool_registry import blind_candidate_view


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def effective_grade(candidate: Mapping[str, Any]) -> int | None:
    """Latest human grade if present, else machine/top-level grade."""
    humans = candidate.get("human_grades")
    if isinstance(humans, list) and humans:
        last = humans[-1]
        if isinstance(last, Mapping) and last.get("grade") is not None:
            try:
                grade = int(last["grade"])
            except (TypeError, ValueError):
                grade = None
            if grade in {0, 1, 2, 3}:
                return grade
    raw = candidate.get("grade")
    if raw is None:
        return None
    try:
        grade = int(raw)
    except (TypeError, ValueError):
        return None
    return grade if grade in {0, 1, 2, 3} else None


def append_human_grade(
    candidate: Mapping[str, Any],
    *,
    grade: int | None,
    notes: str = "",
    reviewer_id: str = "",
    clear: bool = False,
) -> dict[str, Any]:
    """Return candidate with append-only human grade history.

    ``clear=True`` records an explicit nullification without deleting machine votes.
    """
    item = dict(candidate)
    history = [
        dict(entry)
        for entry in (item.get("human_grades") or [])
        if isinstance(entry, dict)
    ]
    if clear:
        history.append(
            {
                "grade": None,
                "notes": str(notes or ""),
                "reviewer_id": str(reviewer_id or ""),
                "source": "human_verified",
                "judgment_source": "human_verified",
                "rubric_version": "discovery-relevance-grade/v1",
                "ts": _utc_now(),
                "cleared": True,
            }
        )
        item["human_grades"] = history
        # Keep machine grade if present; only clear human effective grade surface.
        if item.get("machine_reviews"):
            votes = item.get("machine_reviews") or []
            grades = sorted(
                int(vote.get("grade"))
                for vote in votes
                if isinstance(vote, dict) and vote.get("grade") is not None
            )
            item["grade"] = grades[len(grades) // 2] if grades else None
        else:
            item["grade"] = None
        item["review_notes"] = str(notes or "")
        item["reviewer_id"] = str(reviewer_id or "")
        return item

    if grade is None or grade not in {0, 1, 2, 3}:
        raise ValueError("grade_must_be_0_to_3")
    history.append(
        {
            "grade": int(grade),
            "notes": str(notes or ""),
            "reviewer_id": str(reviewer_id or ""),
            "source": "human_verified",
            "judgment_source": "human_verified",
            "rubric_version": "discovery-relevance-grade/v1",
            "ts": _utc_now(),
        }
    )
    item["human_grades"] = history
    item["grade"] = int(grade)
    item["review_notes"] = str(notes or "")
    item["reviewer_id"] = str(reviewer_id or "")
    # Never drop machine history.
    return item


def _latest_active_human(candidate: Mapping[str, Any], reviewer_id: str = "") -> Mapping[str, Any] | None:
    humans = candidate.get("human_grades")
    if not isinstance(humans, list) or not humans:
        return None
    for entry in reversed(humans):
        if not isinstance(entry, Mapping):
            continue
        if reviewer_id and str(entry.get("reviewer_id") or "") != reviewer_id:
            continue
        if entry.get("cleared") or entry.get("grade") is None:
            return None
        return entry
    return None


def apply_human_grades_for_export(pool: Mapping[str, Any], *, reviewer_id: str = "") -> dict[str, Any]:
    """Build a compile-compatible pool containing only active human judgments."""
    payload = dict(pool)
    candidates: list[dict[str, Any]] = []
    human_count = 0
    for raw in payload.get("candidates") or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        latest_human = _latest_active_human(item, reviewer_id=reviewer_id)
        for field in (
            "machine_reviews",
            "machine_review_runs",
            "judgment_confidence",
            "confidence",
            "review_model",
            "review_method",
            "model_expert_judgments",
            "model_expert_consensus",
        ):
            item.pop(field, None)
        if latest_human is not None:
            human_count += 1
            item["grade"] = int(latest_human["grade"])
            item["reviewer_id"] = str(latest_human.get("reviewer_id") or "")
            item["review_notes"] = str(latest_human.get("notes") or "")
        else:
            item["grade"] = None
            item.pop("reviewer_id", None)
            item.pop("review_notes", None)
        item.pop("human_grades", None)
        item = blind_candidate_view(item, mode="developer")
        candidates.append(item)
    payload["candidates"] = candidates
    payload["judgment_source"] = (
        "human_verified"
        if human_count
        else str(pool.get("judgment_source") or "legacy_unverified")
    )
    payload["review_summary"] = {
        "graded_candidates": human_count,
        "ungraded_candidates": len(candidates) - human_count,
        "complete": bool(candidates) and human_count == len(candidates),
    }
    if "reviewed" not in str(payload.get("schema_version") or ""):
        payload["schema_version"] = "discovery-judgment-pool-reviewed/v2"
    return payload


def queue_bucket(candidate: Mapping[str, Any], *, mode: str) -> str:
    """Classify a candidate for priority queues."""
    grade = effective_grade(candidate)
    if grade is None:
        return "ungraded"
    if mode == "expert":
        return "graded"
    confidence = str(candidate.get("judgment_confidence") or candidate.get("confidence") or "")
    votes = candidate.get("machine_reviews") or []
    vote_grades = sorted(
        {
            int(vote.get("grade"))
            for vote in votes
            if isinstance(vote, dict) and vote.get("grade") is not None
        }
    )
    conflicts: list[str] = []
    for vote in votes:
        if isinstance(vote, dict):
            for item in vote.get("constraint_conflicts") or []:
                if str(item).strip():
                    conflicts.append(str(item))
    if conflicts:
        return "hard_constraint_conflicts"
    if len(vote_grades) >= 2 and (max(vote_grades) - min(vote_grades) >= 2):
        return "vote_disagreement"
    if confidence == "low":
        return "low_confidence"
    return "graded"


def merge_machine_reviews(
    existing_pool: Mapping[str, Any],
    machine_pool: Mapping[str, Any],
    *,
    job_id: str = "",
    profile_id: str = "",
    model: str = "",
) -> dict[str, Any]:
    """Merge machine-reviewed candidates without wiping humans or prior model runs."""
    existing_by_id = {
        str(item.get("candidate_id") or ""): dict(item)
        for item in (existing_pool.get("candidates") or [])
        if isinstance(item, dict) and str(item.get("candidate_id") or "")
    }
    order = [
        str(item.get("candidate_id") or "")
        for item in (existing_pool.get("candidates") or machine_pool.get("candidates") or [])
        if isinstance(item, dict) and str(item.get("candidate_id") or "")
    ]
    # preserve machine order extras
    for item in machine_pool.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("candidate_id") or "")
        if cid and cid not in order:
            order.append(cid)

    merged: list[dict[str, Any]] = []
    for cid in order:
        base = existing_by_id.get(cid, {})
        if base.get("machine_review_runs"):
            seen_run_keys: set[tuple[str, ...]] = set()
            deduplicated_runs: list[dict[str, Any]] = []
            for raw_run in reversed(base.get("machine_review_runs") or []):
                if not isinstance(raw_run, dict):
                    continue
                run = dict(raw_run)
                run_model = str(run.get("model") or "").strip().casefold()
                run_key = (
                    ("model", run_model)
                    if run_model
                    else (
                        "legacy",
                        str(run.get("job_id") or ""),
                        str(run.get("profile_id") or ""),
                    )
                )
                if run_key in seen_run_keys:
                    continue
                seen_run_keys.add(run_key)
                deduplicated_runs.append(run)
            base = dict(base)
            base["machine_review_runs"] = list(reversed(deduplicated_runs))
        machine = next(
            (
                dict(item)
                for item in (machine_pool.get("candidates") or [])
                if isinstance(item, dict) and str(item.get("candidate_id") or "") == cid
            ),
            None,
        )
        if machine is None:
            merged.append(base)
            continue
        item = dict(base)
        human_grades = item.get("human_grades")
        reviewer_id = item.get("reviewer_id")
        review_notes = item.get("review_notes")
        prior_runs = [
            dict(run)
            for run in (item.get("machine_review_runs") or [])
            if isinstance(run, dict)
        ]
        resolved_model = str(model or machine_pool.get("review_model") or machine.get("review_model") or "")
        run_record = {
            "job_id": str(job_id or ""),
            "profile_id": str(profile_id or ""),
            "model": resolved_model,
            "grade": machine.get("grade"),
            "confidence": machine.get("judgment_confidence") or machine.get("confidence"),
            "votes": [dict(vote) for vote in (machine.get("machine_reviews") or []) if isinstance(vote, dict)],
            "judgment_source": machine_pool.get("judgment_source"),
            "rubric_version": machine_pool.get("rubric_version"),
            "created_at": _utc_now(),
        }
        resolved_model_key = resolved_model.strip().casefold()
        if resolved_model_key:
            prior_runs = [
                run
                for run in prior_runs
                if str(run.get("model") or "").strip().casefold() != resolved_model_key
            ]
        else:
            run_key = (run_record["job_id"], run_record["profile_id"], run_record["model"])
            prior_runs = [
                run
                for run in prior_runs
                if (str(run.get("job_id") or ""), str(run.get("profile_id") or ""), str(run.get("model") or ""))
                != run_key
            ]
        if run_record["votes"] or run_record["grade"] is not None:
            prior_runs.append(run_record)
        item.update(machine)
        item["machine_review_runs"] = prior_runs
        if human_grades:
            item["human_grades"] = human_grades
            item["grade"] = effective_grade(item)
            if reviewer_id:
                item["reviewer_id"] = reviewer_id
            if review_notes is not None:
                item["review_notes"] = review_notes
        merged.append(item)

    payload = dict(existing_pool)
    for key, value in machine_pool.items():
        if key != "candidates" and key not in {"judgment_source", "review_summary"}:
            payload[key] = value
    payload["candidates"] = merged
    if any(_latest_active_human(item) is not None for item in merged):
        payload["judgment_source"] = existing_pool.get("judgment_source") or "human_verified"
    elif machine_pool.get("judgment_source"):
        payload["judgment_source"] = machine_pool.get("judgment_source")
    return payload


def merge_model_expert_results(
    existing_pool: Mapping[str, Any],
    results: Mapping[str, Any],
    *,
    job_id: str,
) -> dict[str, Any]:
    """Merge deterministic model-expert results without touching human history."""
    normalized_results = {
        str(candidate_id): _model_dump(result)
        for candidate_id, result in results.items()
        if str(candidate_id).strip()
    }
    candidates: list[dict[str, Any]] = []
    statuses: list[str] = []
    for raw in existing_pool.get("candidates") or []:
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        candidate_id = str(item.get("candidate_id") or "")
        result = normalized_results.get(candidate_id)
        if result is None:
            candidates.append(item)
            continue
        judgments = [
            _model_dump(judgment)
            for judgment in (result.get("judgments") or [])
            if isinstance(judgment, Mapping) or hasattr(judgment, "model_dump")
        ]
        prior = {
            str(judgment.get("judgment_id") or ""): dict(judgment)
            for judgment in (item.get("model_expert_judgments") or [])
            if isinstance(judgment, Mapping) and str(judgment.get("judgment_id") or "")
        }
        for judgment in judgments:
            judgment_id = str(judgment.get("judgment_id") or "")
            if judgment_id:
                prior[judgment_id] = judgment
        item["model_expert_judgments"] = list(prior.values())
        status = str(result.get("status") or "model_expert_provisional")
        statuses.append(status)
        consensus = {
            key: value
            for key, value in result.items()
            if key != "judgments"
        }
        consensus["job_id"] = str(job_id or "")
        consensus["judgment_ids"] = [
            str(judgment.get("judgment_id") or "")
            for judgment in judgments
            if str(judgment.get("judgment_id") or "")
        ]
        consensus["created_at"] = _utc_now()
        item["model_expert_consensus"] = consensus
        if _latest_active_human(item) is None:
            item["grade"] = result.get("consensus_grade")
            item["judgment_confidence"] = _consensus_confidence(judgments)
            item["review_notes"] = status
            item.pop("reviewer_id", None)
        else:
            item["grade"] = effective_grade(item)
        candidates.append(item)

    payload = dict(existing_pool)
    payload["candidates"] = candidates
    if any(_latest_active_human(candidate) is not None for candidate in candidates):
        payload["judgment_source"] = str(existing_pool.get("judgment_source") or "human_verified")
    elif "needs_adjudication" in statuses:
        payload["judgment_source"] = "needs_adjudication"
    elif "model_expert_provisional" in statuses:
        payload["judgment_source"] = "model_expert_provisional"
    elif statuses:
        payload["judgment_source"] = "model_expert_consensus"
    payload["review_summary"] = {
        "model_expert_candidates": len(statuses),
        "model_expert_consensus": statuses.count("model_expert_consensus"),
        "model_expert_provisional": statuses.count("model_expert_provisional"),
        "needs_adjudication": statuses.count("needs_adjudication"),
    }
    if "reviewed" not in str(payload.get("schema_version") or ""):
        payload["schema_version"] = "discovery-judgment-pool-reviewed/v2"
    return payload


def _model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    return dict(value) if isinstance(value, Mapping) else {}


def _consensus_confidence(judgments: list[dict[str, Any]]) -> str:
    confidences = [str(judgment.get("confidence") or "low") for judgment in judgments]
    if confidences and all(confidence == "high" for confidence in confidences):
        return "high"
    if confidences and all(confidence in {"high", "medium"} for confidence in confidences):
        return "medium"
    return "low"
