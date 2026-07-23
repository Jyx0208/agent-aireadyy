from __future__ import annotations

from collections.abc import Mapping, Set
from typing import Any

from agent.discovery.task_profiles import (
    CriticalAgendaItem,
    OPTIONAL_TASK_TYPES,
    TASK_PROFILES,
    common_critical_agenda,
    training_critical_agenda,
)


_BROWSE_ONLY_TASK_TYPES = {
    "browse_only",
    "browse",
    "data_only",
    "find_data",
    "find_data_only",
    "none",
    "null",
    "unknown",
    "undetermined",
    "unspecified",
    "n_a",
    "na",
    "any",
    "general",
}


def build_critical_decision_agenda(
    intent_snapshot: Mapping[str, Any],
    gap_report: Mapping[str, Any] | None = None,
    resolved_fields: Set[str] | None = None,
) -> list[CriticalAgendaItem]:
    """Build a deterministic, task-profile-driven decision agenda.

    The result is a priority/readiness guard, not a questionnaire.  This pure
    function never writes strategy state, and ``gap_report`` remains accepted
    for the dialogue seam even though item triggers are self-describing data.
    """

    del gap_report
    resolved = set(resolved_fields or ())
    definitions = _agenda_definitions(intent_snapshot.get("task_type"))
    active = [
        item.model_copy(deep=True)
        for item in definitions
        if all(
            _trigger_matches(trigger, item, intent_snapshot, resolved)
            for trigger in item.trigger_conditions
        )
    ]
    return sorted(active, key=lambda item: (-item.priority, item.id))


def next_critical_decision(
    intent_snapshot: Mapping[str, Any],
    gap_report: Mapping[str, Any] | None = None,
    resolved_fields: Set[str] | None = None,
) -> CriticalAgendaItem | None:
    """Return only the highest-priority unresolved item for a dynamic turn."""

    agenda = build_critical_decision_agenda(
        intent_snapshot,
        gap_report=gap_report,
        resolved_fields=resolved_fields,
    )
    return agenda[0] if agenda else None


def agenda_for_manager(
    intent_snapshot: Mapping[str, Any],
    gap_report: Mapping[str, Any] | None = None,
    resolved_fields: Set[str] | None = None,
) -> list[dict[str, Any]]:
    """Serialize agenda data for the existing Dialogue Manager contract."""

    return [
        {
            **item.model_dump(mode="json"),
            "critical": item.blocks_build_ready,
        }
        for item in build_critical_decision_agenda(
            intent_snapshot,
            gap_report=gap_report,
            resolved_fields=resolved_fields,
        )
    ]


def _agenda_definitions(raw_task_type: Any) -> list[CriticalAgendaItem]:
    task_type = _clean_text(raw_task_type).lower().replace("-", "_").replace(" ", "_")
    if task_type in TASK_PROFILES:
        return TASK_PROFILES[task_type].critical_agenda
    if task_type == "other":
        return training_critical_agenda()
    if not task_type or task_type in _BROWSE_ONLY_TASK_TYPES or task_type in OPTIONAL_TASK_TYPES:
        return common_critical_agenda()
    return common_critical_agenda()


def _trigger_matches(
    trigger: Any,
    item: CriticalAgendaItem,
    snapshot: Mapping[str, Any],
    resolved_fields: set[str],
) -> bool:
    if trigger.operator == "decision_variables_unresolved":
        return any(
            not _decision_variable_resolved(variable, snapshot, resolved_fields)
            for variable in item.decision_variables
        )

    field = trigger.field or ""
    value = snapshot.get(field)
    if trigger.operator == "missing":
        if field in resolved_fields:
            return False
        return _is_missing(value)
    normalized = _clean_text(value).lower()
    expected = {entry.lower() for entry in trigger.values}
    if trigger.operator == "missing_or_values":
        if field in resolved_fields:
            return False
        return _is_missing(value) or normalized in expected
    if trigger.operator == "not_values":
        return normalized not in expected
    raise ValueError(f"Unsupported agenda trigger operator: {trigger.operator}")


def _decision_variable_resolved(
    variable: str,
    snapshot: Mapping[str, Any],
    resolved_fields: set[str],
) -> bool:
    if variable in resolved_fields or not _is_missing(snapshot.get(variable)):
        return True
    constraints = snapshot.get("scientific_constraints")
    if not isinstance(constraints, list):
        return False
    for constraint in constraints:
        if not isinstance(constraint, Mapping):
            continue
        dimension = _clean_text(constraint.get("dimension") or constraint.get("id"))
        if dimension.lower() == variable.lower():
            return True
    return False


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return not value
    return False


def _clean_text(value: Any) -> str:
    return str(value or "").strip()
