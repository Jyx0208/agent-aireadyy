from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from agent.models import JsonModel


ConstraintStrength = Literal["hard", "soft", "open"]
ConstraintScope = Literal[
    "project",
    "assay",
    "file",
    "spectrum",
    "sample",
    "portfolio",
]
ConstraintStatus = Literal["pass", "partial", "fail", "unknown", "not_applicable"]

_UNKNOWN_VALUE_SENTINELS = frozenset(
    {
        "unknown",
        "unknown ptm",
        "not known",
        "n/a",
        "na",
        "not available",
        "unavailable",
        "none",
        "null",
        "not specified",
        "unspecified",
        "not reported",
        "not set",
        "missing",
    }
)


def is_substantive_constraint_value(value: Any) -> bool:
    """Return whether a value contains evidence rather than an unknown marker."""

    if value is None:
        return False
    if isinstance(value, str):
        normalized = re.sub(
            r"[\s_-]+",
            " ",
            value.strip().casefold(),
        ).strip()
        return bool(normalized) and normalized not in _UNKNOWN_VALUE_SENTINELS
    if isinstance(value, dict):
        return bool(value) and any(
            is_substantive_constraint_value(item) for item in value.values()
        )
    if isinstance(value, (list, tuple, set)):
        return bool(value) and any(
            is_substantive_constraint_value(item) for item in value
        )
    return True


class ScientificConstraint(JsonModel):
    """One user-owned scientific requirement that must survive into discovery.

    First-class strategy fields remain convenient UI shortcuts.  This model is
    the extension seam for requirements the product has never seen before
    (sample source, disease, HLA typing, cohort design, licence, and so on).
    The discovery Agent may reason about the value, but may not silently drop or
    reinterpret it.
    """

    id: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    label: str = Field(min_length=1, max_length=240)
    dimension: str = Field(min_length=1, max_length=120)
    operator: str = Field(default="matches", min_length=1, max_length=64)
    value: Any = None
    strength: ConstraintStrength = "soft"
    scope: ConstraintScope = "project"
    evidence_required: bool = True
    rationale: str = Field(default="", max_length=500)
    source: Literal["user", "accepted_recommendation", "inferred"] = "user"

    @field_validator("id", "label", "dimension", "operator", "rationale")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return " ".join(str(value or "").split()).strip()

    @model_validator(mode="after")
    def validate_value(self) -> "ScientificConstraint":
        try:
            encoded = json.dumps(self.value, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("constraint value must be JSON-serializable") from exc
        if len(encoded) > 4000:
            raise ValueError("constraint value is too large")
        return self


class ConstraintAssessment(JsonModel):
    """Auditable project-level decision for one ScientificConstraint."""

    constraint_id: str = Field(min_length=1, max_length=96)
    status: ConstraintStatus
    reason: str = Field(min_length=1, max_length=1000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=30)
    observed_value: Any = None

    @field_validator("constraint_id", "reason")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return " ".join(str(value or "").split()).strip()

    @field_validator("evidence_refs")
    @classmethod
    def normalize_refs(cls, values: list[str]) -> list[str]:
        return list(
            dict.fromkeys(
                " ".join(str(value or "").split()).strip()[:240]
                for value in values
                if str(value or "").strip()
            )
        )

    @model_validator(mode="after")
    def validate_evidence(self) -> "ConstraintAssessment":
        if self.status in {"pass", "partial", "fail"} and not self.evidence_refs:
            raise ValueError("decided constraint assessments require evidence_refs")
        return self


def may_be_hard(source: str | None) -> bool:
    """Hard strength is only allowed for user or accepted_recommendation provenance."""

    normalized = str(source or "").strip().casefold()
    return normalized in {"user", "accepted_recommendation"}


def constraint_may_be_hard(constraint: ScientificConstraint) -> bool:
    """Whether a constraint is eligible to act as a hard scientific gate."""

    return constraint.strength == "hard" and may_be_hard(constraint.source)


class ConstraintNormalizeResult(JsonModel):
    """Outcome of normalizing scientific constraints without silent loss.

    accepted are valid constraints. rejected retains raw payloads that failed
    validation so callers can fail-closed or surface diagnostics.
    """

    accepted: list[ScientificConstraint] = Field(default_factory=list)
    rejected: list[dict[str, Any]] = Field(default_factory=list)
    open_notes: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.rejected


def normalize_scientific_constraints_result(
    value: Any,
    *,
    max_items: int = 100,
) -> ConstraintNormalizeResult:
    """Normalize constraints and record every rejected item (no silent drop)."""

    if value is None:
        return ConstraintNormalizeResult()
    if not isinstance(value, list):
        return ConstraintNormalizeResult(
            rejected=[
                {
                    "raw": value,
                    "error_code": "not_a_list",
                    "message": "scientific_constraints must be a list",
                }
            ],
            open_notes=["scientific_constraints payload was not a list"],
        )

    accepted: list[ScientificConstraint] = []
    rejected: list[dict[str, Any]] = []
    by_id: dict[str, int] = {}
    for index, raw in enumerate(value[:max_items]):
        if isinstance(raw, ScientificConstraint):
            constraint = raw
        elif isinstance(raw, dict):
            item = dict(raw)
            # Missing provenance is inferred (soft), never invent user.
            if not str(item.get("source") or "").strip():
                item["source"] = "inferred"
            source = str(item.get("source") or "").strip().casefold()
            item["source"] = {
                "accepted_preference": "accepted_recommendation",
                "system_default": "inferred",
                "default": "inferred",
            }.get(
                source,
                source if source in {"user", "accepted_recommendation", "inferred"} else "inferred",
            )
            if item.get("strength") == "hard" and not may_be_hard(item.get("source")):
                item["strength"] = "soft"
            try:
                constraint = ScientificConstraint.model_validate(item)
            except Exception as exc:
                rejected.append(
                    {
                        "raw": raw,
                        "error_code": "validation_error",
                        "message": str(exc),
                        "index": index,
                    }
                )
                continue
        else:
            rejected.append(
                {
                    "raw": raw,
                    "error_code": "not_an_object",
                    "message": "constraint item must be an object",
                    "index": index,
                }
            )
            continue
        if constraint.strength == "hard" and not may_be_hard(constraint.source):
            constraint = constraint.model_copy(update={"strength": "soft"})
        key = constraint.id.casefold()
        if key in by_id:
            accepted[by_id[key]] = constraint
        else:
            by_id[key] = len(accepted)
            accepted.append(constraint)
    return ConstraintNormalizeResult(accepted=accepted, rejected=rejected)


def normalize_scientific_constraints(value: Any) -> list[ScientificConstraint]:
    """Return accepted constraints only.

    Prefer normalize_scientific_constraints_result for fail-closed ingress so
    rejected items remain visible to callers.
    """

    return list(normalize_scientific_constraints_result(value).accepted)


def constraint_slug(label: str, *, fallback: str = "constraint") -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(label or "").strip()).strip("-.")
    return (normalized or fallback)[:96]


def normalize_constraint_bindings_result(
    value: Any,
    *,
    max_items: int = 100,
) -> ConstraintNormalizeResult:
    """Normalize compact bindings without silently dropping invalid items."""

    if value is None:
        return ConstraintNormalizeResult()
    if not isinstance(value, list):
        return ConstraintNormalizeResult(
            rejected=[
                {
                    "raw": value,
                    "error_code": "not_a_list",
                    "message": "constraint bindings must be a list",
                }
            ],
            open_notes=["constraint bindings payload was not a list"],
        )

    accepted: list[ScientificConstraint] = []
    rejected: list[dict[str, Any]] = []
    by_id: dict[str, int] = {}
    for index, raw in enumerate(value[:max_items], start=1):
        if isinstance(raw, ScientificConstraint):
            constraint = raw
        elif isinstance(raw, dict):
            item = dict(raw)
            dimension = " ".join(str(item.get("dimension") or "").split()).strip()
            if not dimension:
                rejected.append(
                    {
                        "raw": raw,
                        "error_code": "missing_dimension",
                        "message": "binding requires dimension",
                        "index": index - 1,
                    }
                )
                continue
            item.setdefault(
                "id",
                constraint_slug(
                    f"binding.{dimension}.{index}",
                    fallback=f"binding-{index}",
                ),
            )
            item.setdefault("label", dimension.replace("_", " "))
            if "scope" not in item and item.get("evidence_scope") is not None:
                item["scope"] = item.pop("evidence_scope")
            source = str(item.get("source") or "").strip().casefold()
            # Missing/unknown provenance is soft (inferred), never invent user.
            if not source:
                source = "inferred"
            item["source"] = {
                "accepted_preference": "accepted_recommendation",
                "system_default": "inferred",
                "default": "inferred",
            }.get(
                source,
                source if source in {"user", "accepted_recommendation", "inferred"} else "inferred",
            )
            # Inferred provenance cannot carry hard strength.
            if item.get("strength") == "hard" and not may_be_hard(item.get("source")):
                item["strength"] = "soft"
            try:
                constraint = ScientificConstraint.model_validate(item)
            except Exception as exc:
                rejected.append(
                    {
                        "raw": raw,
                        "error_code": "validation_error",
                        "message": str(exc),
                        "index": index - 1,
                    }
                )
                continue
        else:
            rejected.append(
                {
                    "raw": raw,
                    "error_code": "not_an_object",
                    "message": "binding item must be an object",
                    "index": index - 1,
                }
            )
            continue
        key = constraint.id.casefold()
        if key in by_id:
            accepted[by_id[key]] = constraint
        else:
            by_id[key] = len(accepted)
            accepted.append(constraint)
    return ConstraintNormalizeResult(accepted=accepted, rejected=rejected)


def normalize_constraint_bindings(value: Any) -> list[ScientificConstraint]:
    """Normalize first-class strategy bindings into the existing constraint model.

    This is a compatibility adapter, not a second constraint hierarchy.  Legacy
    ``ScientificConstraint`` payloads keep their explicit ids/labels, while the
    compact binding shape used by strategy/publication contracts receives stable
    defaults and maps ``evidence_scope`` onto ``scope``.

    Prefer normalize_constraint_bindings_result when rejection audit is required.
    """

    return list(normalize_constraint_bindings_result(value).accepted)


def evaluate_constraint_value(
    constraint: ScientificConstraint,
    observed_value: Any,
) -> bool | None:
    """Evaluate the public operator contract without guessing domain semantics.

    ``None`` means that the operator/value shape is not machine-verifiable. A
    hard constraint must never be treated as passed in that state.
    """

    operator = re.sub(r"[\s-]+", "_", str(constraint.operator or "").strip().casefold())
    source_operator = operator
    aliases = {
        ">=": "gte",
        "ge": "gte",
        "at_least": "gte",
        ">": "gt",
        "<=": "lte",
        "le": "lte",
        "at_most": "lte",
        "<": "lt",
        "=": "eq",
        "==": "eq",
        "equals": "eq",
        "is": "eq",
        "!=": "neq",
        "not_equals": "neq",
        "exclude": "not_contains",
        "excludes": "not_contains",
        "does_not_contain": "not_contains",
        "not_matches": "not_contains",
        "exclude_if_matches": "not_contains",
        "not_in": "not_in",
    }
    operator = aliases.get(operator, operator)

    if operator in {"exists", "nonempty", "present"}:
        return is_substantive_constraint_value(observed_value)

    expected = constraint.value
    if source_operator == "exclude_if_matches":
        def _strip_exclusion_directive(value: Any) -> Any:
            if not isinstance(value, str):
                return value
            stripped = re.sub(
                r"^(?:exclude(?:d)?|without|omit|remove|no|排除|不要|不含|剔除)\s*[:：]?\s*",
                "",
                value.strip(),
                flags=re.IGNORECASE,
            )
            return stripped or value

        if isinstance(expected, list):
            expected = [_strip_exclusion_directive(item) for item in expected]
        else:
            expected = _strip_exclusion_directive(expected)
    if observed_value is None or expected is None:
        return None

    def _fully_substantive(value: Any) -> bool:
        if isinstance(value, dict):
            return bool(value) and all(
                _fully_substantive(item) for item in value.values()
            )
        if isinstance(value, (list, tuple, set)):
            return bool(value) and all(_fully_substantive(item) for item in value)
        return is_substantive_constraint_value(value)

    # Absence is not affirmative evidence for an exclusion.  In particular,
    # ``not_contains``-style operators must not turn an unknown/empty observed
    # value into a passing hard constraint merely because there is no text to
    # search.  Returning None keeps the result explicitly unverifiable.
    if not is_substantive_constraint_value(observed_value):
        return None
    if (
        constraint.evidence_required
        and operator in {"neq", "not_equal", "not_contains", "not_in"}
        and not _fully_substantive(observed_value)
    ):
        return None

    if operator in {"gte", "gt", "lte", "lt"}:
        if isinstance(observed_value, bool) or isinstance(expected, bool):
            return None
        try:
            observed_number = float(observed_value)
            expected_number = float(expected)
        except (TypeError, ValueError):
            return None
        if operator == "gte":
            return observed_number >= expected_number
        if operator == "gt":
            return observed_number > expected_number
        if operator == "lte":
            return observed_number <= expected_number
        return observed_number < expected_number

    def _atoms(value: Any) -> list[Any]:
        if isinstance(value, dict):
            return list(value.values())
        if isinstance(value, (list, tuple, set)):
            return list(value)
        return [value]

    def _normal(value: Any) -> str:
        return " ".join(str(value or "").split()).casefold()

    observed_atoms = _atoms(observed_value)
    expected_atoms = _atoms(expected)
    observed_text = [_normal(value) for value in observed_atoms]
    expected_text = [_normal(value) for value in expected_atoms]
    if not observed_text or not expected_text:
        return None

    if operator in {"eq", "exact", "matches"}:
        return set(observed_text) == set(expected_text) if (
            len(observed_text) > 1 or len(expected_text) > 1
        ) else observed_text[0] == expected_text[0]
    if operator in {"neq", "not_equal"}:
        return not evaluate_constraint_value(
            constraint.model_copy(update={"operator": "eq"}), observed_value
        )
    if operator == "contains":
        return all(
            any(expected_item in observed_item for observed_item in observed_text)
            for expected_item in expected_text
        )
    if operator == "not_contains":
        return all(
            all(expected_item not in observed_item for observed_item in observed_text)
            for expected_item in expected_text
        )
    if operator == "in":
        return all(item in expected_text for item in observed_text)
    if operator == "not_in":
        return all(item not in expected_text for item in observed_text)
    return None
