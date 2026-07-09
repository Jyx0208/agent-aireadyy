from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from agent.discovery.ontology import normalize_labeling_strategy, normalize_ptm_type
from agent.discovery.task_readiness import normalize_task_type
from agent.utils import write_json


def model_request_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        raw_records = payload.get("requests")
        if isinstance(raw_records, list):
            return [item for item in raw_records if isinstance(item, dict)]
        if payload.get("request_id") or payload.get("query") or payload.get("constraints"):
            return [payload]
    return []


def discovery_payload_from_model_request(
    request: dict[str, Any],
    *,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    overrides = overrides or {}
    constraints = request.get("constraints") if isinstance(request.get("constraints"), dict) else {}
    query = _query_from_model_request(request, overrides)
    task_type = normalize_task_type(
        _first_clean_value(overrides.get("task_type"), request.get("task_type"), constraints.get("task_type"))
    )
    species_policy = _clean_text(
        overrides.get("species_policy") or constraints.get("species_policy") or request.get("species_policy") or "open"
    ).lower()
    if species_policy not in {"open", "include_only", "exclude"}:
        species_policy = "open"
    species = _species_from_model_request(request, constraints)
    ptm_type = _ptm_from_model_request(request, constraints)
    acquisition = _clean_text(
        overrides.get("acquisition_mode") or constraints.get("acquisition") or constraints.get("acquisition_mode") or "DDA"
    ).lower()
    if acquisition not in {"dda", "dia", "prm", "srm", "mrm"}:
        acquisition = "dda"
    labeling_strategy = normalize_labeling_strategy(
        overrides.get("labeling_strategy")
        or constraints.get("labeling_strategy")
        or request.get("labeling_strategy")
        or "label_free"
    )
    repository = _clean_repository(overrides.get("repository") or request.get("repository"), default="")
    if not repository:
        repository = _repository_from_model_request(request)

    planned_repositories = _planned_repositories(
        repository=repository,
        repositories=request.get("repositories"),
    )
    payload: dict[str, Any] = {
        "source": "remote",
        "repository": repository,
        "repository_strategy": "multi_repository" if len(planned_repositories) > 1 else "single_repository",
        "planned_repositories": planned_repositories,
        "goal": "ptm",
        "ptm_type": ptm_type,
        "modification_scope": ptm_type,
        "species": species,
        "species_policy": species_policy,
        "labeling_strategy": labeling_strategy,
        "acquisition_mode": acquisition,
        "task_type": task_type or "",
        "max_projects": _bounded_int(overrides.get("max_projects"), default=5, minimum=1, maximum=100),
        "max_files": _bounded_int(overrides.get("max_files"), default=50, minimum=1, maximum=2000),
        "max_candidate_projects": _bounded_int(overrides.get("max_candidate_projects"), default=50, minimum=1, maximum=300),
        "max_files_per_project": _bounded_int(overrides.get("max_files_per_project"), default=20, minimum=1, maximum=100),
        "agentic": True,
        "agentic_rounds": _bounded_int(overrides.get("agentic_rounds"), default=1, minimum=1, maximum=2),
        "diversity_strategy": _clean_text(overrides.get("diversity_strategy") or "high") or "high",
        "prompt": query,
        "model_informed_request_id": _clean_text(request.get("request_id")),
        "model_informed_request_dimension": _clean_text(request.get("dimension")),
        "model_informed_request_target": _clean_text(request.get("target")),
        "model_informed_request_reason": _clean_text(request.get("reason")),
        "model_informed_request_priority": _clean_text(request.get("priority") or "medium"),
        "requires_user_confirmation": bool(request.get("requires_user_confirmation", True)),
    }
    repositories = request.get("repositories")
    if isinstance(repositories, list):
        payload["repositories"] = [
            repository
            for repository in (_clean_repository(item, default="") for item in repositories)
            if repository
        ]
    return payload


def model_informed_discovery_payloads(
    discovery_requests: dict[str, Any],
    *,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    records = model_request_records(discovery_requests)
    rows = [
        {
            "request_id": _clean_text(record.get("request_id")),
            "requires_user_confirmation": bool(record.get("requires_user_confirmation", True)),
            "payload": discovery_payload_from_model_request(record, overrides=overrides),
        }
        for record in records
    ]
    return {
        "schema_version": "model-informed-discovery-payloads/v1",
        "status": "ready" if rows else "no_action_needed",
        "request_count": len(records),
        "payload_count": len(rows),
        "payloads": rows,
    }


def write_model_informed_discovery_payloads(
    *,
    output_json: str | Path,
    output_csv: str | Path,
    output_md: str | Path,
    discovery_requests: dict[str, Any],
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = model_informed_discovery_payloads(discovery_requests, overrides=overrides)
    write_json(output_json, payload)
    _write_payloads_csv(output_csv, payload)
    Path(output_md).write_text(markdown_model_informed_discovery_payloads(payload), encoding="utf-8")
    return payload


def model_informed_discovery_payload_queue(payloads: dict[str, Any]) -> dict[str, Any]:
    rows = payloads.get("payloads") if isinstance(payloads.get("payloads"), list) else []
    items = [_queue_item(row, index=index) for index, row in enumerate(rows, start=1) if isinstance(row, dict)]
    return {
        "schema_version": "model-informed-discovery-payload-queue/v1",
        "status": "ready" if items else "no_action_needed",
        "item_count": len(items),
        "ready_count": sum(1 for item in items if item.get("queue_status") == "ready_for_user_confirmation"),
        "review_count": sum(1 for item in items if item.get("queue_status") == "needs_review"),
        "blocked_count": sum(1 for item in items if item.get("queue_status") == "blocked"),
        "items": sorted(items, key=lambda item: (-float(item.get("priority_score") or 0), str(item.get("request_id") or ""))),
    }


def model_informed_repository_plan(payloads: dict[str, Any], queue: dict[str, Any] | None = None) -> dict[str, Any]:
    payload_rows = payloads.get("payloads") if isinstance(payloads.get("payloads"), list) else []
    queue_rows = (queue or {}).get("items") if isinstance((queue or {}).get("items"), list) else []
    planned: list[str] = []
    strategies: dict[str, int] = {}
    request_count = 0

    for row in payload_rows:
        if not isinstance(row, dict):
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if not payload:
            continue
        request_count += 1
        strategy = _clean_text(payload.get("repository_strategy")) or _repository_strategy_from_payload(payload)
        strategies[strategy] = strategies.get(strategy, 0) + 1
        planned.extend(_planned_repositories_from_payload(payload))

    for row in queue_rows:
        if not isinstance(row, dict):
            continue
        strategy = _clean_text(row.get("repository_strategy"))
        if strategy:
            strategies[strategy] = strategies.get(strategy, 0) + 1
        planned.extend(_planned_repositories_from_payload(row))

    planned = _dedupe(planned)
    strategy = "multi_repository" if len(planned) > 1 else ("single_repository" if planned else "")
    return {
        "schema_version": "model-informed-repository-plan/v1",
        "status": "ready" if planned else "not_available",
        "request_count": _safe_int(payloads.get("request_count"), request_count or len(payload_rows)),
        "payload_count": _safe_int(payloads.get("payload_count"), len(payload_rows)),
        "queue_item_count": _safe_int((queue or {}).get("item_count"), len(queue_rows)),
        "repository_strategy": strategy,
        "repository_strategy_counts": dict(sorted(strategies.items())),
        "planned_repositories": planned,
        "repositories_display": ", ".join(planned),
    }


def write_model_informed_discovery_payload_queue(
    *,
    output_json: str | Path,
    output_csv: str | Path,
    output_md: str | Path,
    payloads: dict[str, Any],
) -> dict[str, Any]:
    queue = model_informed_discovery_payload_queue(payloads)
    write_json(output_json, queue)
    _write_payload_queue_csv(output_csv, queue)
    Path(output_md).write_text(markdown_model_informed_discovery_payload_queue(queue), encoding="utf-8")
    return queue


def model_informed_curation_queue(
    discovery_requests: dict[str, Any],
    *,
    payload_queue: dict[str, Any] | None = None,
) -> dict[str, Any]:
    requests = model_request_records(discovery_requests)
    payload_items = {
        _clean_text(item.get("request_id")): item
        for item in ((payload_queue or {}).get("items") or [])
        if isinstance(item, dict) and _clean_text(item.get("request_id"))
    }
    rows = [_curation_item_from_request(request, payload_items.get(_clean_text(request.get("request_id")))) for request in requests]
    rows.sort(key=lambda row: (-float(row.get("priority_score") or 0), str(row.get("request_id") or "")))
    return {
        "schema_version": "model-informed-curation-queue/v1",
        "status": "ready" if rows else "no_action_needed",
        "row_count": len(rows),
        "action_counts": _counts([str(row.get("action") or "unknown") for row in rows]),
        "curation_type_counts": _counts([str(row.get("curation_type") or "unknown") for row in rows]),
        "rows": rows,
    }


def write_model_informed_curation_queue(
    *,
    output_json: str | Path,
    output_csv: str | Path,
    output_md: str | Path,
    discovery_requests: dict[str, Any],
    payload_queue: dict[str, Any] | None = None,
) -> dict[str, Any]:
    queue = model_informed_curation_queue(discovery_requests, payload_queue=payload_queue)
    write_json(output_json, queue)
    _write_model_curation_csv(output_csv, queue)
    Path(output_md).write_text(markdown_model_informed_curation_queue(queue), encoding="utf-8")
    return queue


def markdown_model_informed_discovery_payloads(payload: dict[str, Any]) -> str:
    rows = payload.get("payloads") if isinstance(payload.get("payloads"), list) else []
    lines = [
        "# Model-informed discovery payloads",
        "",
        f"- Status: `{payload.get('status') or 'unknown'}`",
        f"- Payloads: {len(rows)}",
        "",
    ]
    if not rows:
        lines.append("No model-informed discovery payloads were generated.")
        return "\n".join(lines) + "\n"
    lines.append("| Request | Repository | Planned repositories | Task | PTM | Species policy | Query |")
    lines.append("|---|---|---|---|---|---|---|")
    for row in rows:
        if not isinstance(row, dict):
            continue
        discovery_payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        lines.append(
            "| "
            + " | ".join(
                _md_cell(value)
                for value in [
                    row.get("request_id"),
                    discovery_payload.get("repository"),
                    ", ".join(map(str, discovery_payload.get("planned_repositories") or [])),
                    discovery_payload.get("task_type"),
                    discovery_payload.get("ptm_type"),
                    discovery_payload.get("species_policy"),
                    discovery_payload.get("prompt"),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def markdown_model_informed_discovery_payload_queue(queue: dict[str, Any]) -> str:
    rows = queue.get("items") if isinstance(queue.get("items"), list) else []
    lines = [
        "# Model-informed discovery payload queue",
        "",
        f"- Status: `{queue.get('status') or 'unknown'}`",
        f"- Items: {len(rows)}",
        f"- Ready for user confirmation: {queue.get('ready_count', 0)}",
        f"- Needs review: {queue.get('review_count', 0)}",
        f"- Blocked: {queue.get('blocked_count', 0)}",
        "",
    ]
    if not rows:
        lines.append("No model-informed discovery payload queue item was generated.")
        return "\n".join(lines) + "\n"
    lines.append("| Request | Status | Action | Priority | Reason |")
    lines.append("|---|---|---|---:|---|")
    for row in rows:
        if not isinstance(row, dict):
            continue
        lines.append(
            "| "
            + " | ".join(
                _md_cell(value)
                for value in [
                    row.get("request_id"),
                    row.get("queue_status"),
                    row.get("recommended_action"),
                    row.get("priority_score"),
                    "; ".join(map(str, row.get("reasons") or [])),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def markdown_model_informed_curation_queue(queue: dict[str, Any]) -> str:
    rows = queue.get("rows") if isinstance(queue.get("rows"), list) else []
    lines = [
        "# Model-informed curation queue",
        "",
        f"- Status: `{queue.get('status') or 'unknown'}`",
        f"- Items: {len(rows)}",
        "",
    ]
    if not rows:
        lines.append("No model-informed curation item was generated.")
        return "\n".join(lines) + "\n"
    lines.append("| Request | Action | Priority | Target | Planned repositories | Payload status | Reason |")
    lines.append("|---|---|---:|---|---|---|---|")
    for row in rows:
        if not isinstance(row, dict):
            continue
        lines.append(
            "| "
            + " | ".join(
                _md_cell(value)
                for value in [
                    row.get("request_id"),
                    row.get("action"),
                    row.get("priority_score"),
                    f"{row.get('dimension') or ''}:{row.get('target') or ''}",
                    ", ".join(map(str, row.get("planned_repositories") or [])),
                    row.get("payload_queue_status"),
                    row.get("reason"),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _write_payloads_csv(path: str | Path, payload: dict[str, Any]) -> None:
    rows = payload.get("payloads") if isinstance(payload.get("payloads"), list) else []
    columns = [
        "request_id",
        "repository",
        "repository_strategy",
        "planned_repositories",
        "task_type",
        "ptm_type",
        "species_policy",
        "species",
        "labeling_strategy",
        "acquisition_mode",
        "max_projects",
        "max_files",
        "prompt",
        "requires_user_confirmation",
    ]
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            if not isinstance(row, dict):
                continue
            discovery_payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            writer.writerow(
                {
                    "request_id": row.get("request_id") or discovery_payload.get("model_informed_request_id") or "",
                    "repository": discovery_payload.get("repository") or "",
                    "repository_strategy": discovery_payload.get("repository_strategy") or "",
                    "planned_repositories": ";".join(map(str, discovery_payload.get("planned_repositories") or [])),
                    "task_type": discovery_payload.get("task_type") or "",
                    "ptm_type": discovery_payload.get("ptm_type") or "",
                    "species_policy": discovery_payload.get("species_policy") or "",
                    "species": ";".join(map(str, discovery_payload.get("species") or [])),
                    "labeling_strategy": discovery_payload.get("labeling_strategy") or "",
                    "acquisition_mode": discovery_payload.get("acquisition_mode") or "",
                    "max_projects": discovery_payload.get("max_projects") or "",
                    "max_files": discovery_payload.get("max_files") or "",
                    "prompt": discovery_payload.get("prompt") or "",
                    "requires_user_confirmation": row.get("requires_user_confirmation", True),
                }
            )


def _write_payload_queue_csv(path: str | Path, queue: dict[str, Any]) -> None:
    rows = queue.get("items") if isinstance(queue.get("items"), list) else []
    columns = [
        "request_id",
        "queue_status",
        "recommended_action",
        "priority_score",
        "repository",
        "repository_strategy",
        "planned_repositories",
        "task_type",
        "ptm_type",
        "species_policy",
        "requires_user_confirmation",
        "reasons",
        "warnings",
        "blockers",
        "prompt",
    ]
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            if not isinstance(row, dict):
                continue
            writer.writerow(
                {
                    "request_id": row.get("request_id") or "",
                    "queue_status": row.get("queue_status") or "",
                    "recommended_action": row.get("recommended_action") or "",
                    "priority_score": row.get("priority_score") or "",
                    "repository": row.get("repository") or "",
                    "repository_strategy": row.get("repository_strategy") or "",
                    "planned_repositories": ";".join(map(str, row.get("planned_repositories") or [])),
                    "task_type": row.get("task_type") or "",
                    "ptm_type": row.get("ptm_type") or "",
                    "species_policy": row.get("species_policy") or "",
                    "requires_user_confirmation": row.get("requires_user_confirmation"),
                    "reasons": ";".join(map(str, row.get("reasons") or [])),
                    "warnings": ";".join(map(str, row.get("warnings") or [])),
                    "blockers": ";".join(map(str, row.get("blockers") or [])),
                    "prompt": row.get("prompt") or "",
                }
            )


def _write_model_curation_csv(path: str | Path, queue: dict[str, Any]) -> None:
    rows = queue.get("rows") if isinstance(queue.get("rows"), list) else []
    columns = [
        "curation_id",
        "curation_type",
        "action",
        "priority_score",
        "reason",
        "request_id",
        "dimension",
        "target",
        "query",
        "repository",
        "repository_strategy",
        "repositories",
        "planned_repositories",
        "task_type",
        "payload_queue_status",
        "payload_recommended_action",
        "requires_user_confirmation",
        "warnings",
        "blockers",
        "suggested_cli",
    ]
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            if not isinstance(row, dict):
                continue
            writer.writerow(
                {
                    **{column: row.get(column) or "" for column in columns},
                    "repositories": ";".join(map(str, row.get("repositories") or [])),
                    "planned_repositories": ";".join(map(str, row.get("planned_repositories") or [])),
                    "warnings": ";".join(map(str, row.get("warnings") or [])),
                    "blockers": ";".join(map(str, row.get("blockers") or [])),
                }
            )


def _curation_item_from_request(request: dict[str, Any], payload_item: dict[str, Any] | None) -> dict[str, Any]:
    request_id = _clean_text(request.get("request_id")) or "model_gap_request"
    priority = _clean_text(request.get("priority") or "medium").lower()
    payload_status = _clean_text((payload_item or {}).get("queue_status"))
    payload_action = _clean_text((payload_item or {}).get("recommended_action"))
    blockers = [str(item) for item in ((payload_item or {}).get("blockers") or []) if str(item).strip()]
    warnings = [str(item) for item in ((payload_item or {}).get("warnings") or []) if str(item).strip()]
    if payload_status == "blocked" or blockers:
        action = "fix_request_before_discovery"
    elif payload_status == "needs_review" or warnings:
        action = "review_before_discovery"
    else:
        action = "confirm_and_run_discovery"
    constraints = request.get("constraints") if isinstance(request.get("constraints"), dict) else {}
    planned_repositories = _planned_repositories(
        repository=_clean_text((payload_item or {}).get("repository") or request.get("repository") or "auto"),
        repositories=(payload_item or {}).get("planned_repositories")
        or (payload_item or {}).get("repositories")
        or request.get("repositories"),
    )
    return {
        "curation_id": f"model_curation:{request_id}",
        "curation_type": "review_model_informed_discovery_request",
        "action": action,
        "priority_score": (payload_item or {}).get("priority_score") or _queue_priority_score(
            priority=priority,
            dimension=_clean_text(request.get("dimension")).lower(),
            blockers=blockers,
            warnings=warnings,
        ),
        "reason": "model_failure_mode_requires_data_expansion",
        "selection": "model_informed_expansion",
        "request_id": request_id,
        "dimension": request.get("dimension") or "",
        "target": request.get("target") or "",
        "query": request.get("query") or "",
        "repository": request.get("repository") or "auto",
        "repository_strategy": "multi_repository" if len(planned_repositories) > 1 else "single_repository",
        "repositories": request.get("repositories") if isinstance(request.get("repositories"), list) else [],
        "planned_repositories": planned_repositories,
        "task_type": request.get("task_type") or "",
        "requires_user_confirmation": bool(request.get("requires_user_confirmation", True)),
        "species_policy": constraints.get("species_policy") or "",
        "max_file_size_mb": constraints.get("max_file_size_mb") or "",
        "payload_queue_status": payload_status,
        "payload_recommended_action": payload_action,
        "warnings": warnings,
        "blockers": blockers,
        "suggested_cli": request.get("suggested_cli") or "",
    }


def _queue_item(row: dict[str, Any], *, index: int) -> dict[str, Any]:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    request_id = _clean_text(row.get("request_id") or payload.get("model_informed_request_id") or f"payload_{index:03d}")
    reasons: list[str] = []
    warnings: list[str] = []
    blockers: list[str] = []
    priority = _clean_text(payload.get("model_informed_request_priority") or "medium").lower()
    dimension = _clean_text(payload.get("model_informed_request_dimension")).lower()
    prompt = _clean_text(payload.get("prompt"))
    repository = _clean_text(payload.get("repository") or "auto")
    planned_repositories = _planned_repositories(
        repository=repository,
        repositories=payload.get("planned_repositories") or payload.get("repositories"),
    )
    task_type = _clean_text(payload.get("task_type"))
    acquisition = _clean_text(payload.get("acquisition_mode") or "dda").lower()
    ptm_type = _clean_text(payload.get("ptm_type"))
    requires_confirmation = bool(row.get("requires_user_confirmation", payload.get("requires_user_confirmation", True)))

    if not prompt:
        blockers.append("missing_discovery_query")
    if not task_type:
        warnings.append("missing_task_type")
    if acquisition != "dda":
        blockers.append("non_dda_acquisition_not_supported")
    if repository not in {"pride", "massive", "iprox", "auto"}:
        blockers.append("unsupported_repository")
    if ptm_type == "unknown_ptm":
        warnings.append("broad_ptm_scope_review_recommended")
    if requires_confirmation:
        reasons.append("requires_user_confirmation")
    if dimension:
        reasons.append(f"model_gap_dimension:{dimension}")

    if blockers:
        queue_status = "blocked"
        recommended_action = "fix_request_before_discovery"
    elif warnings or requires_confirmation:
        queue_status = "needs_review" if warnings else "ready_for_user_confirmation"
        recommended_action = "review_and_run_discovery"
    else:
        queue_status = "ready_for_user_confirmation"
        recommended_action = "run_discovery_after_user_confirmation"

    return {
        "request_id": request_id,
        "queue_status": queue_status,
        "recommended_action": recommended_action,
        "priority_score": _queue_priority_score(priority=priority, dimension=dimension, blockers=blockers, warnings=warnings),
        "repository": repository,
        "repository_strategy": "multi_repository" if len(planned_repositories) > 1 else "single_repository",
        "planned_repositories": planned_repositories,
        "task_type": task_type,
        "ptm_type": ptm_type,
        "species_policy": payload.get("species_policy") or "open",
        "requires_user_confirmation": requires_confirmation,
        "prompt": prompt,
        "reasons": reasons or ["model_informed_discovery_request"],
        "warnings": warnings,
        "blockers": blockers,
        "payload": payload,
    }


def _queue_priority_score(*, priority: str, dimension: str, blockers: list[str], warnings: list[str]) -> float:
    base = {"urgent": 0.95, "high": 0.85, "medium": 0.65, "low": 0.4}.get(priority, 0.65)
    if dimension in {"ptm", "instrument", "organism", "diversity"}:
        base += 0.08
    elif dimension in {"label_yield", "model_quality", "spectrum_quality"}:
        base += 0.06
    if blockers:
        base -= 0.35
    if warnings:
        base -= 0.08
    return round(max(0.0, min(1.0, base)), 3)


def _counts(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _query_from_model_request(request: dict[str, Any], payload: dict[str, Any]) -> str:
    query = _clean_text(request.get("query") or payload.get("query") or payload.get("prompt"))
    if query:
        return query
    pieces = [
        _clean_text(request.get("task_type")),
        _clean_text(request.get("target")),
        _clean_text(request.get("dimension")),
        _clean_text(request.get("reason")),
    ]
    return " ".join(piece for piece in pieces if piece).strip()


def _ptm_from_model_request(request: dict[str, Any], constraints: dict[str, Any]) -> str:
    raw = _first_clean_value(
        constraints.get("modification_scope"),
        constraints.get("ptm_type"),
        constraints.get("ptm"),
        request.get("ptm_type"),
        request.get("modification_scope"),
    )
    if not raw and _clean_text(request.get("dimension")).lower() in {"ptm", "modification", "modification_scope"}:
        raw = _clean_text(request.get("target"))
    if raw in {"any_ptm", "modified_peptides", "modified_peptide"}:
        return "unknown_ptm"
    return normalize_ptm_type(raw or "unknown_ptm")


def _species_from_model_request(request: dict[str, Any], constraints: dict[str, Any]) -> list[str]:
    raw = (
        constraints.get("species")
        or constraints.get("organism")
        or constraints.get("organism_preference")
        or request.get("species")
        or request.get("organism")
    )
    if isinstance(raw, list):
        values = [_clean_text(item) for item in raw]
    else:
        values = [item.strip() for item in str(raw or "").replace("\n", ",").replace(";", ",").split(",")]
    species = [item for item in values if item]
    return species or ["human"]


def _repository_from_model_request(request: dict[str, Any]) -> str:
    repository = _clean_text(request.get("repository"))
    if repository:
        return _clean_repository(repository, default="auto")
    repositories = request.get("repositories")
    if isinstance(repositories, list):
        cleaned = [_clean_repository(item, default="") for item in repositories]
        cleaned = [item for item in cleaned if item]
        if len(set(cleaned)) == 1:
            return cleaned[0]
        if cleaned:
            return "auto"
    return "auto"


def _planned_repositories(*, repository: str, repositories: Any) -> list[str]:
    if isinstance(repositories, list):
        cleaned = [_clean_repository(item, default="") for item in repositories]
        cleaned = [item for item in cleaned if item]
    else:
        cleaned = []
    if cleaned:
        return _dedupe(cleaned)
    repository = _clean_repository(repository, default="auto")
    if repository == "auto":
        return ["pride", "massive", "iprox"]
    return [repository]


def _planned_repositories_from_payload(payload: dict[str, Any]) -> list[str]:
    planned = payload.get("planned_repositories")
    if isinstance(planned, list):
        cleaned = [_clean_repository(item, default="") for item in planned]
        cleaned = [item for item in cleaned if item]
        if cleaned:
            return _dedupe(cleaned)
    repositories = payload.get("repositories")
    if isinstance(repositories, list):
        cleaned = [_clean_repository(item, default="") for item in repositories]
        cleaned = [item for item in cleaned if item]
        if cleaned:
            return _dedupe(cleaned)
    repository = _clean_repository(payload.get("repository"), default="auto")
    if repository == "auto":
        return ["pride", "massive", "iprox"]
    return [repository] if repository else []


def _repository_strategy_from_payload(payload: dict[str, Any]) -> str:
    repositories = _planned_repositories_from_payload(payload)
    return "multi_repository" if len(repositories) > 1 else "single_repository"


def _clean_repository(value: Any, default: str = "pride") -> str:
    repository = _clean_text(value).lower().replace("-", "_")
    if repository in {"auto", "all"}:
        return "auto"
    if repository in {"pride", "px", "proteomexchange"}:
        return "pride"
    if repository in {"massive", "massive_ucsd", "msv", "gnps"}:
        return "massive"
    if repository in {"iprox", "ipx"}:
        return "iprox"
    return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _first_clean_value(*values: Any) -> str:
    for value in values:
        if isinstance(value, list):
            for item in value:
                text = _clean_text(item)
                if text:
                    return text
            continue
        text = _clean_text(value)
        if text:
            return text
    return ""


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _md_cell(value: Any) -> str:
    text = _clean_text(value).replace("|", "\\|")
    return text or "-"
