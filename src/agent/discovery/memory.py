from __future__ import annotations

import csv
import json
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from agent.discovery.models import DatasetManifest, DatasetRequest
from agent.input.normalizer import safe_output_stem
from agent.models import JsonModel


ReviewDecision = Literal["keep", "reject", "needs_review"]
ReviewReason = Literal[
    "correct",
    "wrong_ptm",
    "wrong_acquisition",
    "wrong_species",
    "result_file",
    "missing_url",
    "duplicate",
    "sdrf_mismatch",
    "project_level_overused",
    "unknown_acceptable",
    "unclear",
    "other",
]

VALID_REVIEW_DECISIONS = {"keep", "reject", "needs_review"}
VALID_REVIEW_REASONS = {
    "correct",
    "wrong_ptm",
    "wrong_acquisition",
    "wrong_species",
    "result_file",
    "missing_url",
    "duplicate",
    "sdrf_mismatch",
    "project_level_overused",
    "unknown_acceptable",
    "unclear",
    "other",
}

DECISION_PRIOR_WEIGHTS = {
    "keep": 0.03,
    "reject": -0.04,
    "needs_review": -0.015,
}


class DiscoveryRunRecord(JsonModel):
    run_id: str
    created_at: str
    request: dict[str, Any]
    queries: list[str] = Field(default_factory=list)
    output_dir: str
    manifest_path: str
    summary: dict[str, Any] = Field(default_factory=dict)


class DiscoveryReviewDecision(JsonModel):
    review_id: str
    run_id: str
    created_at: str
    repository: str = "pride"
    project_accession: str
    file_name: str
    decision: ReviewDecision
    reason: ReviewReason
    note: str = ""


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def generate_discovery_run_id(request: DatasetRequest, *, created_at: datetime | None = None) -> str:
    timestamp = (created_at or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    species = "-".join(request.species) if request.species else "species"
    stem = f"{timestamp}_{request.repository}_{request.goal}_{request.ptm_type}_{species}_{request.acquisition_mode}"
    return safe_output_stem(stem)


def _append_jsonl(path: Path, records: list[JsonModel]) -> None:
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record.model_dump(mode="json"), ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            records.append(json.loads(text))
    return records


def load_dataset_manifest(path: str | Path) -> DatasetManifest:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return DatasetManifest.model_validate(payload)


def _clamp_prior(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


def _note_fields(note: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in str(note or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            fields[key] = value
    return fields


def _planned_repositories_from_note(note: str) -> list[str]:
    fields = _note_fields(note)
    raw = fields.get("planned_repositories", "")
    values = raw.replace(";", ",").split(",")
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = value.strip().lower().replace("-", "_")
        if not text:
            continue
        if text in {"px", "proteomexchange"}:
            text = "pride"
        elif text in {"msv", "gnps", "massive_ucsd"}:
            text = "massive"
        elif text == "ipx":
            text = "iprox"
        if text not in seen:
            seen.add(text)
            cleaned.append(text)
    return cleaned


def _decision_prior_weight(decision: "DiscoveryReviewDecision") -> float:
    weight = DECISION_PRIOR_WEIGHTS.get(decision.decision, 0.0)
    fields = _note_fields(decision.note)
    curation_type = fields.get("curation_type", "")
    if decision.decision == "keep":
        if curation_type in {"review_high_value_blocked", "review_model_informed_discovery_request"}:
            weight += 0.02
        elif curation_type in {"confirm_ptm_semantics", "confirm_species_policy"}:
            weight += 0.01
    elif decision.decision == "reject":
        if curation_type in {"exclude_low_value_high_risk", "check_leakage_risk"}:
            weight -= 0.03
        elif curation_type in {"confirm_ptm_semantics", "confirm_species_policy"}:
            weight -= 0.02
    elif decision.decision == "needs_review":
        if curation_type in {"review_metadata_missing", "review_high_value_blocked"}:
            weight -= 0.01
    return weight


def memory_prior_for_project(
    decisions: list["DiscoveryReviewDecision"],
    project_accession: str,
) -> float:
    total = 0.0
    for decision in decisions:
        if decision.project_accession != project_accession:
            continue
        total += _decision_prior_weight(decision)
    return round(_clamp_prior(total, 0.08), 3)


def memory_prior_for_file(
    decisions: list["DiscoveryReviewDecision"],
    project_accession: str,
    file_name: str,
) -> float:
    total = memory_prior_for_project(decisions, project_accession)
    for decision in decisions:
        if decision.project_accession == project_accession and decision.file_name == file_name:
            total += _decision_prior_weight(decision) * 2
    return round(_clamp_prior(total, 0.12), 3)


def memory_feedback_for_candidate(
    decisions: list["DiscoveryReviewDecision"],
    project_accession: str,
    file_name: str | None = None,
) -> dict[str, Any]:
    """Summarize human/curation feedback for a project or file candidate.

    Discovery scoring uses a numeric prior for ranking, while downstream
    data-value reasoning needs a more auditable explanation: what was reviewed,
    which action was preferred, and whether the decision came from model-informed
    multi-repository planning.
    """

    matched = [
        decision
        for decision in decisions
        if decision.project_accession == project_accession
        and (file_name is None or decision.file_name == file_name)
    ]
    if not matched:
        return {}

    decision_counts = Counter(decision.decision for decision in matched)
    reason_counts = Counter(decision.reason for decision in matched)
    curation_type_counts: Counter[str] = Counter()
    planned_repositories: set[str] = set()
    repository_strategy_counts: Counter[str] = Counter()
    for decision in matched:
        fields = _note_fields(decision.note)
        if fields.get("curation_type"):
            curation_type_counts[fields["curation_type"]] += 1
        if fields.get("repository_strategy"):
            repository_strategy_counts[fields["repository_strategy"]] += 1
        planned_repositories.update(_planned_repositories_from_note(decision.note))

    latest = matched[-1]
    recommended_action = _memory_recommended_action(matched)
    evidence = [
        f"{latest.decision}:{latest.reason}",
        *[f"curation_type:{key}" for key in sorted(curation_type_counts)],
    ]
    if planned_repositories:
        evidence.append("planned_repositories:" + ",".join(sorted(planned_repositories)))

    return {
        "schema_version": "discovery-memory-feedback/v1",
        "scope": "file" if file_name is not None else "project",
        "review_count": len(matched),
        "decision_counts": dict(sorted(decision_counts.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
        "curation_type_counts": dict(sorted(curation_type_counts.items())),
        "latest_decision": latest.decision,
        "latest_reason": latest.reason,
        "recommended_action": recommended_action,
        "score_adjustment": memory_prior_for_file(decisions, project_accession, file_name) if file_name is not None else memory_prior_for_project(decisions, project_accession),
        "repository_strategy": repository_strategy_counts.most_common(1)[0][0] if repository_strategy_counts else "",
        "planned_repositories": sorted(planned_repositories),
        "evidence": evidence,
    }


def _memory_recommended_action(decisions: list["DiscoveryReviewDecision"]) -> str:
    file_level = decisions[-3:]
    counts = Counter(decision.decision for decision in file_level)
    if counts.get("reject", 0) >= max(1, counts.get("keep", 0) + counts.get("needs_review", 0)):
        return "skip"
    if counts.get("keep", 0) >= max(1, counts.get("reject", 0) + counts.get("needs_review", 0)):
        return "process"
    if counts.get("needs_review", 0) or counts:
        return "review"
    return ""


def build_run_record(
    *,
    run_id: str,
    manifest: DatasetManifest,
    output_dir: str | Path,
    manifest_path: str | Path,
) -> DiscoveryRunRecord:
    return DiscoveryRunRecord(
        run_id=run_id,
        created_at=now_utc_iso(),
        request=manifest.request.model_dump(mode="json"),
        queries=[str(query) for query in manifest.summary.get("queries", []) or []],
        output_dir=str(output_dir),
        manifest_path=str(manifest_path),
        summary=manifest.summary,
    )


def decisions_from_review_csv(
    *,
    review_csv: str | Path,
    manifest: DatasetManifest,
) -> list[DiscoveryReviewDecision]:
    run_id = manifest.run_id or str(manifest.summary.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("Manifest has no run_id; cannot import review decisions.")

    files_by_key = {(file.project_accession, file.file_name): file for file in manifest.files}
    decisions: list[DiscoveryReviewDecision] = []
    with Path(review_csv).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"project_accession", "file_name", "decision", "reason"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Review CSV is missing required columns: {', '.join(sorted(missing))}")
        for index, row in enumerate(reader, start=2):
            project_accession = str(row.get("project_accession") or "").strip()
            file_name = str(row.get("file_name") or "").strip()
            decision = str(row.get("decision") or "").strip()
            reason = str(row.get("reason") or "").strip()
            note = str(row.get("note") or "").strip()
            if not project_accession or not file_name:
                raise ValueError(f"Review CSV row {index} is missing project_accession or file_name.")
            if decision not in VALID_REVIEW_DECISIONS:
                raise ValueError(f"Review CSV row {index} has invalid decision: {decision!r}")
            if reason not in VALID_REVIEW_REASONS:
                raise ValueError(f"Review CSV row {index} has invalid reason: {reason!r}")
            file = files_by_key.get((project_accession, file_name))
            decisions.append(
                DiscoveryReviewDecision(
                    review_id=uuid.uuid4().hex,
                    run_id=run_id,
                    created_at=now_utc_iso(),
                    repository=file.repository if file is not None else manifest.request.repository,
                    project_accession=project_accession,
                    file_name=file_name,
                    decision=decision,  # type: ignore[arg-type]
                    reason=reason,  # type: ignore[arg-type]
                    note=note,
                )
            )
    return decisions


class DiscoveryMemory:
    def __init__(self, root_dir: str | Path = Path("runs") / "discovery_memory") -> None:
        self.root_dir = Path(root_dir)

    @property
    def discovery_runs_path(self) -> Path:
        return self.root_dir / "discovery_runs.jsonl"

    @property
    def review_decisions_path(self) -> Path:
        return self.root_dir / "review_decisions.jsonl"

    def append_run(self, record: DiscoveryRunRecord) -> None:
        _append_jsonl(self.discovery_runs_path, [record])

    def append_review_decisions(self, decisions: list[DiscoveryReviewDecision]) -> None:
        _append_jsonl(self.review_decisions_path, decisions)

    def load_runs(self) -> list[DiscoveryRunRecord]:
        return [DiscoveryRunRecord.model_validate(record) for record in _read_jsonl(self.discovery_runs_path)]

    def load_review_decisions(self) -> list[DiscoveryReviewDecision]:
        return [
            DiscoveryReviewDecision.model_validate(record)
            for record in _read_jsonl(self.review_decisions_path)
        ]

    def summary(self) -> dict[str, Any]:
        runs = self.load_runs()
        decisions = self.load_review_decisions()
        decision_counts = Counter(decision.decision for decision in decisions)
        reject_reason_counts = Counter(
            decision.reason for decision in decisions if decision.decision == "reject"
        )
        reviewed_projects = {decision.project_accession for decision in decisions}
        reviewed_files = {(decision.project_accession, decision.file_name) for decision in decisions}
        return {
            "memory_dir": str(self.root_dir),
            "discovery_run_count": len(runs),
            "review_decision_count": len(decisions),
            "decision_counts": dict(sorted(decision_counts.items())),
            "reject_reason_counts": dict(sorted(reject_reason_counts.items())),
            "reviewed_project_count": len(reviewed_projects),
            "reviewed_file_count": len(reviewed_files),
            "latest_run_id": runs[-1].run_id if runs else None,
        }
