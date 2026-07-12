from __future__ import annotations

import re
from datetime import UTC, datetime

from agent.control_plane.models import DynamicBudgetLimits, DynamicBudgetUsage, RoundMetrics
from agent.discovery.models import DatasetManifest, DatasetRequest


def evaluate_round_metrics(
    current: DatasetManifest,
    previous: DatasetManifest | None,
    *,
    request: DatasetRequest,
    queries: list[str],
    prior_queries: list[str],
    usage: DynamicBudgetUsage,
    limits: DynamicBudgetLimits,
    round_index: int,
) -> RoundMetrics:
    current_counts = _manifest_counts(current)
    previous_counts = _manifest_counts(previous)
    usable = current_counts["usable_files"]
    selected = current_counts["selected_files"]
    sufficiency_floor = max(1, min(int(request.max_files), 10))
    candidate_shortfall = _clamp(1.0 - usable / sufficiency_floor)
    quality_gap = _clamp(1.0 - usable / max(1, selected))
    unknown_total = sum(int(value or 0) for value in (current.summary.get("unknown_counts") or {}).values())
    metadata_gap = _clamp(unknown_total / max(1, selected * 4))
    diversity = current.summary.get("instrument_family_distribution") or {}
    diversity_gap = _clamp(1.0 - len(diversity) / 2.0) if selected >= 2 else 1.0
    novelty = _query_novelty(queries, prior_queries)
    new_usable = max(0, usable - previous_counts["usable_files"])
    last_yield = _clamp(new_usable / max(1, len(queries)))
    pressure = max(
        usage.query_units / limits.max_query_units,
        usage.repository_requests / limits.max_repository_requests,
        elapsed_seconds_since(usage.started_at) / limits.max_elapsed_seconds,
    )
    return RoundMetrics(
        round_index=round_index,
        candidate_shortfall=candidate_shortfall,
        quality_gap=quality_gap,
        metadata_gap=metadata_gap,
        diversity_gap=diversity_gap,
        strategy_novelty=novelty,
        last_round_yield=last_yield,
        query_repetition=_clamp(1.0 - novelty),
        budget_pressure=_clamp(pressure),
        counts=current_counts,
        deltas={key: current_counts[key] - previous_counts[key] for key in current_counts},
    )


def elapsed_seconds_since(started_at: str) -> float:
    started = datetime.fromisoformat(started_at)
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - started.astimezone(UTC)).total_seconds())


def _clamp(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


def _manifest_counts(manifest: DatasetManifest | None) -> dict[str, int]:
    if manifest is None:
        return {"selected_files": 0, "usable_files": 0, "valid_files": 0, "review_files": 0}
    files = list(manifest.files)
    return {
        "selected_files": int(manifest.summary.get("selected_files") or len(files)),
        "usable_files": sum(item.validity_status in {"valid", "weak_keep"} for item in files),
        "valid_files": sum(item.validity_status == "valid" for item in files),
        "review_files": sum(item.validity_status == "needs_review" for item in files),
    }


def _query_tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(value).casefold()))


def _query_novelty(queries: list[str], prior_queries: list[str]) -> float:
    if not prior_queries:
        return 1.0
    maximum_similarity = 0.0
    for query in queries:
        current = _query_tokens(query)
        for prior in prior_queries:
            previous = _query_tokens(prior)
            union = current | previous
            similarity = len(current & previous) / len(union) if union else 1.0
            maximum_similarity = max(maximum_similarity, similarity)
    return _clamp(1.0 - maximum_similarity)
