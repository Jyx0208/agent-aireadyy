from __future__ import annotations

import csv
import hashlib
import json
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from agent.discovery.memory import (
    VALID_REVIEW_DECISIONS,
    VALID_REVIEW_REASONS,
    DiscoveryReviewDecision,
    now_utc_iso,
)
from agent.discovery.models import DatasetManifest, DiscoveredFile, DiscoveryEvidence
from agent.utils import write_json


ReviewSelection = Literal["usable", "valid", "all", "review"]

REVIEW_SHEET_COLUMNS = [
    "run_id",
    "repository",
    "project_accession",
    "project_title",
    "file_name",
    "download_url",
    "file_type",
    "file_role",
    "file_role_reasons",
    "sdrf_match_status",
    "evidence_level",
    "file_level_evidence_count",
    "project_level_evidence_count",
    "evidence_warnings",
    "species",
    "acquisition_mode",
    "ptm_type",
    "trust_score",
    "validity_status",
    "validity_reasons",
    "instrument_families",
    "fragmentation_methods",
    "lc_gradient_minutes",
    "evidence",
    "review_decision",
    "review_reason",
    "review_note",
]

REPORT_COLUMNS = ["metric", "value"]
FALSE_POSITIVE_COLUMNS = ["reason", "count"]
DATA_VALUE_EVAL_ROWS = [
    "strategy",
    "selected_count",
    "composite_proxy_score",
    "mean_data_value_score",
    "mean_task_ai_readiness_score",
    "estimated_label_yield",
    "diversity_coverage_score",
    "mean_cost_efficiency",
    "mean_risk_penalty",
    "usable_rate",
    "total_expected_size_mb",
    "coverage",
    "top_files",
]


@dataclass(frozen=True)
class ValidationReview:
    file: DiscoveredFile
    decision: str
    reason: str
    note: str = ""


def _join_values(values: list[str]) -> str:
    return ";".join(str(value) for value in values if str(value).strip())


def _evidence_payload(evidence: list[DiscoveryEvidence]) -> str:
    return json.dumps([item.model_dump(mode="json") for item in evidence], ensure_ascii=False)


def _review_sheet_row(file: DiscoveredFile, run_id: str | None) -> dict[str, Any]:
    return {
        "run_id": run_id or "",
        "repository": file.repository,
        "project_accession": file.project_accession,
        "project_title": file.project_title or "",
        "file_name": file.file_name,
        "download_url": file.download_url or "",
        "file_type": file.file_type,
        "file_role": file.file_role,
        "file_role_reasons": _join_values(file.file_role_reasons),
        "sdrf_match_status": file.sdrf_match_status,
        "evidence_level": file.evidence_level,
        "file_level_evidence_count": file.file_level_evidence_count,
        "project_level_evidence_count": file.project_level_evidence_count,
        "evidence_warnings": _join_values(file.evidence_warnings),
        "species": _join_values(file.species),
        "acquisition_mode": file.acquisition_mode or "",
        "ptm_type": file.ptm_type or "",
        "trust_score": file.trust_score,
        "validity_status": file.validity_status,
        "validity_reasons": _join_values(file.validity_reasons),
        "instrument_families": _join_values(file.instrument_families),
        "fragmentation_methods": _join_values(file.fragmentation_methods),
        "lc_gradient_minutes": file.lc_gradient_minutes if file.lc_gradient_minutes is not None else "",
        "evidence": _evidence_payload(file.evidence),
        "review_decision": file.review_decision or "",
        "review_reason": file.review_reason or "",
        "review_note": file.review_note or "",
    }


def _sorted_files(files: list[DiscoveredFile]) -> list[DiscoveredFile]:
    return sorted(
        files,
        key=lambda file: (
            -file.trust_score,
            -file.file_score,
            file.project_accession,
            file.file_name.casefold(),
        ),
    )


def select_review_files(
    manifest: DatasetManifest,
    *,
    selection: ReviewSelection = "usable",
    max_files: int = 50,
) -> list[DiscoveredFile]:
    if max_files < 1:
        raise ValueError("max_files must be >= 1.")
    if selection == "usable":
        files = [file for file in manifest.files if file.validity_status in {"valid", "weak_keep"}]
    elif selection == "valid":
        files = [file for file in manifest.files if file.validity_status == "valid"]
    elif selection == "review":
        files = [file for file in manifest.files if file.validity_status == "needs_review"]
    elif selection == "all":
        files = list(manifest.files)
    else:
        raise ValueError(f"Unsupported review selection: {selection!r}")
    return _sorted_files(files)[:max_files]


def write_review_sheet(
    manifest: DatasetManifest,
    output_csv: str | Path,
    *,
    selection: ReviewSelection = "usable",
    max_files: int = 50,
) -> Path:
    output_path = Path(output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    selected_files = select_review_files(manifest, selection=selection, max_files=max_files)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_SHEET_COLUMNS)
        writer.writeheader()
        for file in selected_files:
            writer.writerow(_review_sheet_row(file, manifest.run_id))
    return output_path


def _row_value(row: dict[str, str], *names: str) -> str:
    for name in names:
        value = str(row.get(name) or "").strip()
        if value:
            return value
    return ""


def load_validation_reviews(
    *,
    review_csv: str | Path,
    manifest: DatasetManifest,
) -> list[ValidationReview]:
    files_by_key = {(file.project_accession, file.file_name): file for file in manifest.files}
    reviews: list[ValidationReview] = []
    with Path(review_csv).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"project_accession", "file_name"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Review CSV is missing required columns: {', '.join(sorted(missing))}")
        for index, row in enumerate(reader, start=2):
            project_accession = _row_value(row, "project_accession")
            file_name = _row_value(row, "file_name")
            decision = _row_value(row, "review_decision", "decision")
            reason = _row_value(row, "review_reason", "reason")
            note = _row_value(row, "review_note", "note")
            if not project_accession or not file_name:
                raise ValueError(f"Review CSV row {index} is missing project_accession or file_name.")
            if not decision:
                continue
            if decision not in VALID_REVIEW_DECISIONS:
                raise ValueError(f"Review CSV row {index} has invalid decision: {decision!r}")
            if reason not in VALID_REVIEW_REASONS:
                raise ValueError(f"Review CSV row {index} has invalid reason: {reason!r}")
            file = files_by_key.get((project_accession, file_name))
            if file is None:
                raise ValueError(f"Review CSV row {index} does not match a file in this manifest.")
            reviews.append(ValidationReview(file=file, decision=decision, reason=reason, note=note))
    return reviews


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _unknown_fields(file: DiscoveredFile) -> list[str]:
    missing: list[str] = []
    if not file.species:
        missing.append("species")
    if not file.acquisition_mode:
        missing.append("acquisition_mode")
    if not file.ptm_type:
        missing.append("ptm_type")
    if not file.download_url:
        missing.append("download_url")
    if not file.instrument_families:
        missing.append("instrument_families")
    if not file.fragmentation_methods:
        missing.append("fragmentation_methods")
    if file.lc_gradient_minutes is None and not file.lc_gradient:
        missing.append("lc_gradient")
    return missing


def build_validation_report(manifest: DatasetManifest, reviews: list[ValidationReview]) -> dict[str, Any]:
    reviewed_files = len(reviews)
    decision_counts = Counter(review.decision for review in reviews)
    reject_reason_counts = Counter(
        review.reason for review in reviews if review.decision == "reject"
    )
    validity_by_decision: dict[str, Counter[str]] = defaultdict(Counter)
    unknown_counter: Counter[str] = Counter()
    valid_reviewed = 0
    valid_kept = 0
    usable_reviewed = 0
    usable_kept = 0
    sdrf_related_issue_count = 0
    project_level_overused_count = 0

    for review in reviews:
        file = review.file
        validity_by_decision[file.validity_status][review.decision] += 1
        unknown_counter.update(_unknown_fields(file))
        if file.validity_status == "valid":
            valid_reviewed += 1
            if review.decision == "keep":
                valid_kept += 1
        if file.validity_status in {"valid", "weak_keep"}:
            usable_reviewed += 1
            if review.decision == "keep":
                usable_kept += 1
        if review.reason == "sdrf_mismatch":
            sdrf_related_issue_count += 1
        if review.reason == "project_level_overused":
            project_level_overused_count += 1

    return {
        "run_id": manifest.run_id,
        "reviewed_files": reviewed_files,
        "keep_count": decision_counts.get("keep", 0),
        "reject_count": decision_counts.get("reject", 0),
        "needs_review_count": decision_counts.get("needs_review", 0),
        "keep_rate": _rate(decision_counts.get("keep", 0), reviewed_files),
        "reject_rate": _rate(decision_counts.get("reject", 0), reviewed_files),
        "needs_review_rate": _rate(decision_counts.get("needs_review", 0), reviewed_files),
        "valid_keep_rate": _rate(valid_kept, valid_reviewed),
        "usable_keep_rate": _rate(usable_kept, usable_reviewed),
        "false_positive_reason_counts": dict(sorted(reject_reason_counts.items())),
        "validity_status_by_review_decision": {
            status: dict(sorted(counts.items()))
            for status, counts in sorted(validity_by_decision.items())
        },
        "unknown_field_counts": dict(sorted(unknown_counter.items())),
        "sdrf_related_issue_count": sdrf_related_issue_count,
        "project_level_overused_count": project_level_overused_count,
    }


def _flatten_metric_rows(report: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for key, value in report.items():
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
        else:
            rendered = "" if value is None else str(value)
        rows.append({"metric": key, "value": rendered})
    return rows


def write_validation_report(
    *,
    manifest: DatasetManifest,
    reviews: list[ValidationReview],
    output_dir: str | Path,
) -> dict[str, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    report = build_validation_report(manifest, reviews)
    paths = {
        "discovery_validation_report_json": output_path / "discovery_validation_report.json",
        "discovery_validation_report_csv": output_path / "discovery_validation_report.csv",
        "false_positive_reasons_csv": output_path / "false_positive_reasons.csv",
    }
    write_json(paths["discovery_validation_report_json"], report)
    with paths["discovery_validation_report_csv"].open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(_flatten_metric_rows(report))
    with paths["false_positive_reasons_csv"].open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FALSE_POSITIVE_COLUMNS)
        writer.writeheader()
        for reason, count in sorted(report["false_positive_reason_counts"].items()):
            writer.writerow({"reason": reason, "count": count})
    return paths


def evaluate_data_value_selection(
    *,
    manifest: DatasetManifest,
    output_dir: str | Path,
    max_files: int | None = None,
    random_seed: int = 17,
) -> dict[str, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    limit = max_files or max(1, min(manifest.request.max_files, len(manifest.files) or 1))
    rows = _data_value_strategy_rows(manifest, limit=limit, random_seed=random_seed)
    best_baseline = max(
        (row for row in rows if row["strategy"] != "agent_data_value"),
        key=lambda row: float(row["composite_proxy_score"]),
        default=None,
    )
    agent = next((row for row in rows if row["strategy"] == "agent_data_value"), None)
    summary = {
        "run_id": manifest.run_id,
        "goal": manifest.request.goal,
        "task_type": _request_task_type(manifest),
        "max_files": limit,
        "strategies": rows,
        "best_baseline_strategy": best_baseline.get("strategy") if best_baseline else "",
        "agent_composite_proxy_score": agent.get("composite_proxy_score") if agent else 0.0,
        "best_baseline_composite_proxy_score": best_baseline.get("composite_proxy_score") if best_baseline else 0.0,
        "agent_minus_best_baseline": round(
            float(agent.get("composite_proxy_score") if agent else 0.0)
            - float(best_baseline.get("composite_proxy_score") if best_baseline else 0.0),
            6,
        ),
        "interpretation": _data_value_eval_interpretation(agent, best_baseline),
    }
    paths = {
        "data_value_strategy_eval_json": output_path / "data_value_strategy_eval.json",
        "data_value_strategy_eval_csv": output_path / "data_value_strategy_eval.csv",
        "data_value_strategy_eval_md": output_path / "data_value_strategy_eval.md",
    }
    write_json(paths["data_value_strategy_eval_json"], summary)
    with paths["data_value_strategy_eval_csv"].open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DATA_VALUE_EVAL_ROWS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(row.get(key), ensure_ascii=False, sort_keys=True)
                    if isinstance(row.get(key), (dict, list))
                    else row.get(key, "")
                    for key in DATA_VALUE_EVAL_ROWS
                }
            )
    paths["data_value_strategy_eval_md"].write_text(_markdown_data_value_eval(summary), encoding="utf-8")
    return paths


def _data_value_strategy_rows(
    manifest: DatasetManifest,
    *,
    limit: int,
    random_seed: int,
) -> list[dict[str, Any]]:
    files = list(manifest.files)
    strategies = {
        "agent_data_value": _select_agent_data_value(files, limit),
        "random_baseline": _select_random_baseline(files, limit, seed=random_seed),
        "repository_keyword_baseline": _select_keyword_baseline(files, manifest, limit),
        "manual_rule_baseline": _select_manual_rule_baseline(files, limit),
    }
    return [_strategy_metrics(name, selected, manifest=manifest) for name, selected in strategies.items()]


def _select_agent_data_value(files: list[DiscoveredFile], limit: int) -> list[DiscoveredFile]:
    return sorted(
        files,
        key=lambda file: (
            -float(file.data_value_score or 0.0),
            -float(file.task_ai_readiness_score or 0.0),
            -float(file.trust_score or 0.0),
            file.project_accession,
            file.file_name.casefold(),
        ),
    )[:limit]


def _select_random_baseline(files: list[DiscoveredFile], limit: int, *, seed: int) -> list[DiscoveredFile]:
    return sorted(files, key=lambda file: _stable_random_key(file, seed))[:limit]


def _stable_random_key(file: DiscoveredFile, seed: int) -> str:
    text = f"{seed}|{file.repository}|{file.project_accession}|{file.file_name}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _select_keyword_baseline(files: list[DiscoveredFile], manifest: DatasetManifest, limit: int) -> list[DiscoveredFile]:
    request_terms = _request_keywords(manifest)
    return sorted(
        files,
        key=lambda file: (
            -_keyword_match_score(file, request_terms),
            -float(file.trust_score or 0.0),
            -float(file.file_score or 0.0),
            file.project_accession,
            file.file_name.casefold(),
        ),
    )[:limit]


def _request_keywords(manifest: DatasetManifest) -> list[str]:
    request = manifest.request
    values: list[str] = [
        request.goal,
        request.ptm_type,
        request.acquisition_mode,
        request.labeling_strategy,
        request.modification_scope or "",
        *request.species,
        *request.canonical_species,
    ]
    return [str(value).casefold() for value in values if str(value).strip()]


def _keyword_match_score(file: DiscoveredFile, terms: list[str]) -> float:
    text = " ".join(
        [
            file.project_accession,
            file.project_title or "",
            file.file_name,
            file.file_type,
            file.file_role,
            file.acquisition_mode or "",
            file.ptm_type or "",
            file.modification_scope or "",
            file.labeling_strategy or "",
            " ".join(file.species + file.canonical_species),
            " ".join(file.instrument_families + file.fragmentation_methods + file.ptm_evidence_terms + file.ptm_enrichment_methods),
            " ".join(item.text for item in file.evidence),
        ]
    ).casefold()
    if not terms:
        return 0.0
    return sum(1.0 for term in terms if term and term in text) / len(terms)


def _select_manual_rule_baseline(files: list[DiscoveredFile], limit: int) -> list[DiscoveredFile]:
    usable = {"valid": 2, "weak_keep": 1, "needs_review": 0, "exclude": -1}
    role_rank = {"converted_peaklist": 3, "raw_acquisition": 2, "search_result": 1}
    return sorted(
        files,
        key=lambda file: (
            -usable.get(file.validity_status, 0),
            -role_rank.get(file.file_role, 0),
            _size_rank(file),
            -float(file.trust_score or 0.0),
            file.project_accession,
            file.file_name.casefold(),
        ),
    )[:limit]


def _size_rank(file: DiscoveredFile) -> float:
    if file.expected_size_bytes is None or file.expected_size_bytes <= 0:
        return 512.0
    return file.expected_size_bytes / (1024 * 1024)


def _strategy_metrics(name: str, selected: list[DiscoveredFile], *, manifest: DatasetManifest) -> dict[str, Any]:
    selected_count = len(selected)
    readiness = _mean([float(file.task_ai_readiness_score or 0.0) for file in selected])
    data_value = _mean([float(file.data_value_score or 0.0) for file in selected])
    estimated_label_yield = sum(float((file.data_value_components or {}).get("estimated_label_yield") or 0.0) for file in selected)
    diversity = _diversity_coverage_score(selected)
    cost = _mean([float((file.data_value_components or {}).get("cost_efficiency") or 0.0) for file in selected])
    risk = _mean([float((file.data_value_components or {}).get("risk_penalty") or 0.0) for file in selected])
    usable = sum(1 for file in selected if file.validity_status in {"valid", "weak_keep"})
    size_mb = round(sum(float(file.expected_size_bytes or 0.0) for file in selected) / (1024 * 1024), 3)
    composite = round(0.30 * data_value + 0.25 * readiness + 0.20 * diversity + 0.15 * cost - 0.20 * risk, 6)
    return {
        "strategy": name,
        "selected_count": selected_count,
        "mean_data_value_score": round(data_value, 6),
        "mean_task_ai_readiness_score": round(readiness, 6),
        "estimated_label_yield": round(estimated_label_yield, 6),
        "diversity_coverage_score": round(diversity, 6),
        "mean_cost_efficiency": round(cost, 6),
        "mean_risk_penalty": round(risk, 6),
        "usable_rate": _rate(usable, selected_count),
        "total_expected_size_mb": size_mb,
        "composite_proxy_score": composite,
        "coverage": _coverage_counts(selected),
        "top_files": [
            {
                "repository": file.repository,
                "project_accession": file.project_accession,
                "file_name": file.file_name,
                "data_value_score": file.data_value_score,
                "task_ai_readiness_score": file.task_ai_readiness_score,
                "validity_status": file.validity_status,
            }
            for file in selected[:20]
        ],
        "task_type": _request_task_type(manifest),
    }


def _coverage_counts(files: list[DiscoveredFile]) -> dict[str, int]:
    dimensions = {
        "repository": [file.repository for file in files],
        "project": [file.project_accession for file in files],
        "species": [value for file in files for value in file.canonical_species or file.species],
        "instrument": [value for file in files for value in file.instrument_families],
        "fragmentation": [value for file in files for value in file.fragmentation_methods],
        "ptm": [file.ptm_type or "" for file in files],
        "labeling": [file.labeling_strategy or "" for file in files],
    }
    return {key: len({str(value).casefold() for value in values if str(value).strip()}) for key, values in dimensions.items()}


def _diversity_coverage_score(files: list[DiscoveredFile]) -> float:
    if not files:
        return 0.0
    coverage = _coverage_counts(files)
    capped = {
        "repository": min(1.0, coverage["repository"] / 3),
        "project": min(1.0, coverage["project"] / max(1, min(5, len(files)))),
        "species": min(1.0, coverage["species"] / 3),
        "instrument": min(1.0, coverage["instrument"] / 3),
        "fragmentation": min(1.0, coverage["fragmentation"] / 3),
        "ptm": min(1.0, coverage["ptm"] / 3),
        "labeling": min(1.0, coverage["labeling"] / 3),
    }
    return _mean(list(capped.values()))


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _request_task_type(manifest: DatasetManifest) -> str:
    task_types = {file.task_type for file in manifest.files if file.task_type}
    if len(task_types) == 1:
        return next(iter(task_types)) or ""
    return str(manifest.request.goal or "")


def _data_value_eval_interpretation(agent: dict[str, Any] | None, baseline: dict[str, Any] | None) -> str:
    if not agent:
        return "agent_strategy_missing"
    if not baseline:
        return "no_baseline_available"
    delta = float(agent.get("composite_proxy_score") or 0.0) - float(baseline.get("composite_proxy_score") or 0.0)
    if delta >= 0.05:
        return "agent_data_value_selection_outperforms_proxy_baselines"
    if delta >= -0.02:
        return "agent_data_value_selection_matches_proxy_baselines"
    return "agent_data_value_selection_underperforms_best_proxy_baseline"


def _markdown_data_value_eval(summary: dict[str, Any]) -> str:
    lines = [
        "# Data Value Selection Evaluation",
        "",
        f"- Run ID: `{summary.get('run_id') or ''}`",
        f"- Goal/task: `{summary.get('task_type') or summary.get('goal') or ''}`",
        f"- Max files: {summary.get('max_files')}",
        f"- Best baseline: `{summary.get('best_baseline_strategy')}`",
        f"- Agent minus best baseline: `{summary.get('agent_minus_best_baseline')}`",
        f"- Interpretation: `{summary.get('interpretation')}`",
        "",
        "## Strategy Scores",
        "",
    ]
    for row in summary.get("strategies") or []:
        lines.append(
            f"- `{row.get('strategy')}` composite={row.get('composite_proxy_score')} "
            f"value={row.get('mean_data_value_score')} readiness={row.get('mean_task_ai_readiness_score')} "
            f"diversity={row.get('diversity_coverage_score')} risk={row.get('mean_risk_penalty')} "
            f"size_mb={row.get('total_expected_size_mb')}"
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This is an offline proxy evaluation over discovery metadata, not a model training result.",
            "- Use `run-dataset-model-loop` or an external training adapter for true held-out model metrics.",
        ]
    )
    return "\n".join(lines) + "\n"


def validation_reviews_to_memory_decisions(
    *,
    manifest: DatasetManifest,
    reviews: list[ValidationReview],
) -> list[DiscoveryReviewDecision]:
    run_id = manifest.run_id or str(manifest.summary.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("Manifest has no run_id; cannot save validation reviews to memory.")
    return [
        DiscoveryReviewDecision(
            review_id=uuid.uuid4().hex,
            run_id=run_id,
            created_at=now_utc_iso(),
            repository=review.file.repository,
            project_accession=review.file.project_accession,
            file_name=review.file.file_name,
            decision=review.decision,  # type: ignore[arg-type]
            reason=review.reason,  # type: ignore[arg-type]
            note=review.note,
        )
        for review in reviews
    ]
