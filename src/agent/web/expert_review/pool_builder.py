from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from typing import Any

from agent.web.expert_review.pool_registry import strip_pool_for_mode


_VISIBLE_PROJECT_FIELDS = (
    "project_title",
    "project_description",
    "species",
    "acquisition_mode",
    "labeling_strategy",
    "instrument_families",
    "fragmentation_methods",
    "immunopeptide_scope",
    "hla_class",
    "immunopeptide_enrichment_methods",
    "validity_status",
    "evidence_completeness",
)
_VISIBLE_CONSTRAINT_FIELDS = {
    "repository",
    "species",
    "species_policy",
    "acquisition_mode",
    "labeling_strategy",
    "ptm_types",
    "task_type",
}


def _normalized_accession(value: Any) -> str:
    return str(value or "").strip().upper()


def _slug_hash(value: str, *, length: int = 12) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def blind_file_bundle(files: list[Mapping[str, Any]]) -> dict[str, Any]:
    role_counts = Counter(str(item.get("file_role") or "unknown") for item in files)
    type_counts = Counter(str(item.get("file_type") or "unknown") for item in files)
    readiness_counts = Counter(str(item.get("task_readiness_status") or "unknown") for item in files)
    missing = sorted(
        {
            str(requirement)
            for item in files
            for requirement in (item.get("missing_task_requirements") or [])
            if str(requirement).strip()
        }
    )
    raw_count = role_counts["raw_acquisition"] + role_counts["converted_peaklist"]
    return {
        "selected_file_count": len(files),
        "file_role_counts": dict(role_counts),
        "file_type_counts": dict(type_counts),
        "task_readiness_counts": dict(readiness_counts),
        "missing_task_requirements": missing,
        "paired_raw_and_results": bool(raw_count and role_counts["search_result"]),
    }


def build_blinded_pool_from_discovery(
    record: Mapping[str, Any],
    *,
    prompt: str,
    build_id: str,
    visible_constraints: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    prompt = str(prompt or "").strip()
    build_id = str(build_id or "").strip()
    if not prompt:
        raise ValueError("prompt_required")
    if not build_id:
        raise ValueError("build_id_required")

    scenario_id = f"prompt-{_slug_hash(build_id, length=10)}"
    variant_id = "prompt"
    files_by_accession: dict[str, list[Mapping[str, Any]]] = {}
    for item in record.get("files") or []:
        if not isinstance(item, Mapping):
            continue
        accession = _normalized_accession(item.get("project_accession"))
        if accession:
            files_by_accession.setdefault(accession, []).append(item)

    candidates_by_accession: dict[str, dict[str, Any]] = {}
    for project in record.get("projects") or []:
        if not isinstance(project, Mapping):
            continue
        accession = _normalized_accession(project.get("project_accession"))
        if not accession:
            continue
        candidate = {
            "candidate_id": "candidate_" + _slug_hash(f"{build_id}:{scenario_id}:{variant_id}:{accession}"),
            "scenario_id": scenario_id,
            "variant_id": variant_id,
            "visible_prompt": prompt,
            **{field: project.get(field) for field in _VISIBLE_PROJECT_FIELDS},
            **blind_file_bundle(files_by_accession.get(accession, [])),
            "grade": None,
            "review_notes": "",
            "reviewer_id": "",
        }
        current = candidates_by_accession.get(accession)
        if current is None or len(json.dumps(candidate, ensure_ascii=False, default=str)) > len(
            json.dumps(current, ensure_ascii=False, default=str)
        ):
            candidates_by_accession[accession] = candidate

    if not candidates_by_accession:
        raise ValueError("discovery_result_has_no_candidates")

    constraints = {
        str(key): value
        for key, value in (visible_constraints or {}).items()
        if str(key) in _VISIBLE_CONSTRAINT_FIELDS and value not in (None, "", [], {})
    }
    task_key = f"{scenario_id}:{variant_id}"
    pool = {
        "schema_version": "discovery-judgment-pool-blinded/v2",
        "instructions": {
            "grade_3": "Directly satisfies the task and important explicit constraints.",
            "grade_2": "Strongly relevant and usable, with a minor scope or evidence gap.",
            "grade_1": "Related topic but not a suitable answer to the task.",
            "grade_0": "Off-topic or contradicts an explicit hard constraint.",
            "review_rule": "Judge visible repository metadata only. Candidate origin is intentionally hidden.",
        },
        "tasks": {
            task_key: {
                "scenario_id": scenario_id,
                "variant_id": variant_id,
                "visible_prompt": prompt,
                "visible_constraints": constraints,
            }
        },
        "candidates": [candidates_by_accession[key] for key in sorted(candidates_by_accession)],
    }
    pool = strip_pool_for_mode(pool, mode="expert")
    private_key = {
        "schema_version": "discovery-judgment-key/v1",
        "build_id": build_id,
        "candidates": [
            {
                "candidate_id": candidates_by_accession[accession]["candidate_id"],
                "scenario_id": scenario_id,
                "variant_id": variant_id,
                "project_accession": accession,
            }
            for accession in sorted(candidates_by_accession)
        ],
    }
    return pool, private_key
