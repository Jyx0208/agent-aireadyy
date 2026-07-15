from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


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
                "ts": _utc_now(),
                "cleared": True,
            }
        )
        item["human_grades"] = history
        # Keep machine grade if present; only clear human effective grade surface.
        if item.get("machine_reviews"):
            # Recompute top-level grade from machine if machine exists.
            machine_grade = item.get("grade")
            if machine_grade is None:
                votes = item.get("machine_reviews") or []
                grades = [int(v.get("grade")) for v in votes if isinstance(v, dict) and v.get("grade") is not None]
                if grades:
                    grades_sorted = sorted(grades)
                    machine_grade = grades_sorted[len(grades_sorted) // 2]
            item["grade"] = machine_grade
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
            "ts": _utc_now(),
        }
    )
    item["human_grades"] = history
    item["grade"] = int(grade)
    item["review_notes"] = str(notes or "")
    item["reviewer_id"] = str(reviewer_id or "")
    # Never drop machine history.
    return item


def apply_human_grades_for_export(pool: Mapping[str, Any]) -> dict[str, Any]:
    """Build compile-compatible reviewed pool using effective grades."""
    payload = dict(pool)
    candidates: list[dict[str, Any]] = []
    human_count = 0
    for raw in payload.get("candidates") or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        grade = effective_grade(item)
        item["grade"] = grade
        if item.get("human_grades"):
            human_count += 1
            last = item["human_grades"][-1] if item["human_grades"] else {}
            if isinstance(last, dict):
                item["reviewer_id"] = str(last.get("reviewer_id") or item.get("reviewer_id") or "")
                item["review_notes"] = str(last.get("notes") or item.get("review_notes") or "")
        candidates.append(item)
    payload["candidates"] = candidates
    if human_count:
        payload["judgment_source"] = "human_verified"
    elif not payload.get("judgment_source"):
        payload["judgment_source"] = "provisional_same_family"
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
) -> dict[str, Any]:
    """Merge machine-reviewed candidates without wiping human grades."""
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
        # preserve human history and notes
        human_grades = item.get("human_grades")
        reviewer_id = item.get("reviewer_id")
        review_notes = item.get("review_notes")
        item.update(machine)
        if human_grades:
            item["human_grades"] = human_grades
            # effective grade remains human
            item["grade"] = effective_grade(item)
            if reviewer_id:
                item["reviewer_id"] = reviewer_id
            if review_notes is not None:
                item["review_notes"] = review_notes
        merged.append(item)

    payload = dict(existing_pool)
    for key, value in machine_pool.items():
        if key != "candidates":
            payload[key] = value
    payload["candidates"] = merged
    # if any human grades exist keep human_verified as optional export choice later
    if any(item.get("human_grades") for item in merged):
        # keep machine source at pool level only when no humans — else leave prior
        pass
    elif machine_pool.get("judgment_source"):
        payload["judgment_source"] = machine_pool.get("judgment_source")
    return payload
