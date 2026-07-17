from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any


_ZH_PER_PROJECT_RE = re.compile(
    r"(?:每个|每一|单个)(?:项目|数据集).*?(?:至少|不少于|最低)\s*(\d+)\s*(?:个|份|条)?\s*(文件|样本)",
    re.IGNORECASE,
)
_EN_PER_PROJECT_RE = re.compile(
    r"(?:each|per)\s+(?:project|dataset).*?(?:at\s+least|minimum(?:\s+of)?)\s*(\d+)\s*(files?|samples?)",
    re.IGNORECASE,
)
_PORTFOLIO_MAX_RE = re.compile(
    r"越多越好|尽量多|尽可能多|尽量搜全|越全越好|as\s+many\s+as\s+possible|"
    r"maximi[sz]e\s+(?:the\s+)?(?:total\s+)?(?:projects?|datasets?|samples?)|"
    r"more\s+(?:usable\s+)?(?:projects?|datasets?)\s+(?:is|are)\s+better",
    re.IGNORECASE,
)


def calibration_task_identity(
    prompt: str,
    visible_constraints: Mapping[str, Any] | None,
    task_semantics: Mapping[str, Any] | None,
) -> str:
    payload = {
        "prompt": " ".join(str(prompt or "").casefold().split()),
        "visible_constraints": dict(visible_constraints or {}),
        "task_semantics": dict(task_semantics or {}),
        "rubric_version": "discovery-relevance-grade/v1",
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


def interpret_review_task(
    prompt: str,
    constraints: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Make quantity scope explicit so experts do not invent per-project minima."""
    text = str(prompt or "").strip()
    supplied = constraints if isinstance(constraints, Mapping) else {}
    explicit_scope = str(supplied.get("quantity_scope") or "").strip().lower()
    minimum_value = None
    minimum_unit = None

    supplied_files = supplied.get("per_project_min_files")
    supplied_samples = supplied.get("per_project_min_samples")
    if supplied_files not in (None, ""):
        minimum_value, minimum_unit = int(supplied_files), "files"
    elif supplied_samples not in (None, ""):
        minimum_value, minimum_unit = int(supplied_samples), "samples"
    else:
        matched = _ZH_PER_PROJECT_RE.search(text) or _EN_PER_PROJECT_RE.search(text)
        if matched:
            minimum_value = int(matched.group(1))
            raw_unit = matched.group(2).lower()
            minimum_unit = "samples" if raw_unit.startswith("样本") or raw_unit.startswith("sample") else "files"

    if minimum_value is not None or explicit_scope == "per_project":
        quantity_scope = "per_project"
    elif explicit_scope == "portfolio" or _PORTFOLIO_MAX_RE.search(text):
        quantity_scope = "portfolio"
    else:
        quantity_scope = "unspecified"

    portfolio_preference = (
        "maximize_total_usable_items"
        if quantity_scope == "portfolio" and (_PORTFOLIO_MAX_RE.search(text) or explicit_scope == "portfolio")
        else None
    )
    per_project_minimum = (
        {"value": minimum_value, "unit": minimum_unit}
        if minimum_value is not None and minimum_unit is not None
        else None
    )
    penalize_small_project = quantity_scope == "per_project" and per_project_minimum is not None
    return {
        "schema_version": "review-task-semantics/v1",
        "quantity_scope": quantity_scope,
        "portfolio_size_preference": portfolio_preference,
        "per_project_minimum": per_project_minimum,
        "penalize_small_project": penalize_small_project,
        "quantity_rule": (
            "Maximize total usable files or samples across the final portfolio. Do not lower one usable candidate's grade solely because it is small."
            if quantity_scope == "portfolio"
            else "Apply the explicit per-project minimum when judging candidate fitness."
            if penalize_small_project
            else "No per-project quantity minimum was specified; file count is descriptive evidence only."
        ),
    }
