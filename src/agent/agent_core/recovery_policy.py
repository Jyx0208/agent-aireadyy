from __future__ import annotations

from typing import Any

from pydantic import Field

from agent.models import JsonModel


AUTO_RECOVERY_ACTIONS = {
    "reduce_threads",
    "retry_download",
    "invalidate_cache_and_redownload",
    "retry_conversion_with_fallback",
    "retry_transient_failure",
}

BIOLOGICAL_ACTIONS = {
    "change_species",
    "change_species_database",
    "change_database_organism",
    "change_acquisition_mode",
    "change_enzyme",
    "change_labeling_strategy",
    "remove_project_modification",
}


class RecoveryDecision(JsonModel):
    decision: str
    allowed_action: str | None = None
    requires_human: bool = True
    parameters: dict[str, Any] = Field(default_factory=dict)
    next_manual_actions: list[str] = Field(default_factory=list)
    safety_checks: list[dict[str, Any]] = Field(default_factory=list)


def _thread_reduction(current_threads: int | None) -> int:
    if current_threads is None or current_threads <= 1:
        return 1
    return max(1, current_threads // 2)


def recommend_recovery(
    *,
    category: str,
    current_threads: int | None = None,
    requested_action: str | None = None,
) -> RecoveryDecision:
    if requested_action in BIOLOGICAL_ACTIONS:
        return RecoveryDecision(
            decision="manual_required",
            allowed_action="mark_review_required",
            requires_human=True,
            next_manual_actions=[
                "Review required: requested recovery would change biological interpretation.",
            ],
            safety_checks=[
                {
                    "name": "biological_boundary",
                    "passed": False,
                    "detail": f"{requested_action} is not in the autonomous recovery allowlist.",
                }
            ],
        )

    if category in {"insufficient_memory", "fragpipe_oom"}:
        return RecoveryDecision(
            decision="retry_scheduled",
            allowed_action="reduce_threads",
            requires_human=False,
            parameters={"thread_num": _thread_reduction(current_threads)},
            safety_checks=[
                {
                    "name": "computational_only",
                    "passed": True,
                    "detail": "Reducing thread count does not change biological interpretation.",
                }
            ],
        )

    if category in {"download_failure", "network", "timeout", "rate_limited", "remote_service"}:
        return RecoveryDecision(
            decision="retry_scheduled",
            allowed_action="retry_download",
            requires_human=False,
            safety_checks=[
                {
                    "name": "transient_failure",
                    "passed": True,
                    "detail": "Transient remote failure can be retried within bounded limits.",
                }
            ],
        )

    if category in {"conversion_failure", "mzml_empty_or_corrupt"}:
        return RecoveryDecision(
            decision="retry_scheduled",
            allowed_action="retry_conversion_with_fallback",
            requires_human=False,
            safety_checks=[
                {
                    "name": "converter_allowlist",
                    "passed": True,
                    "detail": "Retry uses the configured converter fallback only.",
                }
            ],
        )

    return RecoveryDecision(
        decision="manual_required",
        allowed_action="mark_review_required",
        requires_human=True,
        next_manual_actions=[
            "Inspect failure evidence and decide whether a biologically safe retry or parameter review is appropriate.",
        ],
        safety_checks=[
            {
                "name": "known_safe_action",
                "passed": False,
                "detail": f"No automatic recovery action is allowlisted for category {category}.",
            }
        ],
    )
