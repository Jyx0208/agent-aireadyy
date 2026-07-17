from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


MINIMUM_LABELS = 30
FEATURE_NAMES = (
    "heuristic_relevance",
    "evidence_completeness",
    "instrument_metadata",
    "fragmentation_metadata",
    "domain_scope_metadata",
    "species_metadata",
    "acquisition_metadata",
    "validity",
    "semantic_metadata_confidence",
)

_STORE_LOCK = threading.RLock()
_ACTIVE_CACHE: dict[str, tuple[int, dict[str, Any] | None]] = {}


def default_calibration_path() -> Path:
    configured = str(os.getenv("AGENT_DISCOVERY_CALIBRATION_PATH") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[3] / "runs" / "discovery_scoring" / "calibration.active.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clamp01(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number > 1.0 and number <= 100.0:
        number /= 100.0
    return max(0.0, min(1.0, number))


def _known_list(value: Any) -> bool:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return False
    return any(str(item or "").strip().casefold() not in {"", "unknown", "not reported"} for item in value)


def project_calibration_features(project: Mapping[str, Any] | Any) -> dict[str, float]:
    if hasattr(project, "model_dump"):
        project = project.model_dump(mode="python")
    raw = dict(project) if isinstance(project, Mapping) else {}
    stored_features = raw.get("calibration_features") if isinstance(raw.get("calibration_features"), Mapping) else {}
    validity = str(raw.get("validity_status") or "").casefold()
    return {
        "heuristic_relevance": _clamp01(stored_features.get("heuristic_relevance", raw.get("project_score"))),
        "evidence_completeness": _clamp01(raw.get("evidence_completeness")),
        "instrument_metadata": 1.0 if _known_list(raw.get("instrument_families")) else 0.0,
        "fragmentation_metadata": 1.0 if _known_list(raw.get("fragmentation_methods")) else 0.0,
        "domain_scope_metadata": 1.0 if str(raw.get("immunopeptide_scope") or raw.get("modification_scope") or raw.get("ptm_type") or "").strip() else 0.0,
        "species_metadata": 1.0 if _known_list(raw.get("species")) else 0.0,
        "acquisition_metadata": 1.0 if str(raw.get("acquisition_mode") or "").strip().casefold() not in {"", "unknown", "not reported"} else 0.0,
        "validity": {"valid": 1.0, "weak_keep": 0.7, "needs_review": 0.35, "exclude": 0.0}.get(validity, 0.5),
        "semantic_metadata_confidence": max(
            _clamp01(raw.get("semantic_metadata_confidence")),
            _clamp01(raw.get("immunopeptide_metadata_confidence")),
        ),
    }


def _latest_human_grade(candidate: Mapping[str, Any]) -> int | None:
    history = candidate.get("human_grades")
    if not isinstance(history, list):
        return None
    for entry in reversed(history):
        if not isinstance(entry, Mapping):
            continue
        if entry.get("cleared"):
            return None
        if str(entry.get("judgment_source") or entry.get("source") or "") != "human_verified":
            continue
        try:
            grade = int(entry.get("grade"))
        except (TypeError, ValueError):
            return None
        return grade if grade in {0, 1, 2, 3} else None
    return None


def _resolved_label(candidate: Mapping[str, Any]) -> tuple[int, str] | None:
    human_grade = _latest_human_grade(candidate)
    if human_grade is not None:
        return human_grade, "human_verified"
    consensus = candidate.get("model_expert_consensus")
    if not isinstance(consensus, Mapping) or str(consensus.get("status") or "") != "model_expert_consensus":
        return None
    try:
        grade = int(consensus.get("consensus_grade"))
    except (TypeError, ValueError):
        return None
    return (grade, "model_expert_consensus") if grade in {0, 1, 2, 3} else None


def _label_rank(candidate: Mapping[str, Any], source: str) -> tuple[int, str, str]:
    timestamp = ""
    if source == "human_verified":
        for entry in reversed(candidate.get("human_grades") or []):
            if isinstance(entry, Mapping) and str(entry.get("judgment_source") or entry.get("source") or "") == "human_verified":
                timestamp = str(entry.get("ts") or "")
                break
    else:
        consensus = candidate.get("model_expert_consensus")
        if isinstance(consensus, Mapping):
            timestamp = str(consensus.get("created_at") or "")
    return (2 if source == "human_verified" else 1, timestamp, str(candidate.get("candidate_id") or ""))


def _mae(rows: list[tuple[list[float], float]], weights: list[float]) -> float:
    if not rows:
        return 0.0
    return sum(abs(sum(weight * value for weight, value in zip(weights, features)) - target) for features, target in rows) / len(rows)


def _project_simplex(values: list[float]) -> list[float]:
    positive = [max(0.0, value) for value in values]
    total = sum(positive)
    if total <= 0:
        return [1.0 / len(values)] * len(values)
    return [value / total for value in positive]


def fit_scoring_calibration(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows: list[tuple[list[float], float]] = []
    digest_rows: list[dict[str, Any]] = []
    source_counts: Counter[str] = Counter()
    grade_counts: Counter[int] = Counter()
    resolved_by_project: dict[str, tuple[tuple[int, str, str], Mapping[str, Any], int, str]] = {}
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        resolved = _resolved_label(candidate)
        if resolved is None:
            continue
        grade, source = resolved
        project_id = str(candidate.get("calibration_project_id") or candidate.get("candidate_id") or "")
        rank = _label_rank(candidate, source)
        current = resolved_by_project.get(project_id)
        if current is None or rank > current[0]:
            resolved_by_project[project_id] = (rank, candidate, grade, source)

    for project_id, (_rank, candidate, grade, source) in sorted(resolved_by_project.items()):
        features = project_calibration_features(candidate)
        rows.append(([features[name] for name in FEATURE_NAMES], grade / 3.0))
        digest_rows.append(
            {
                "project_id": project_id,
                "grade": grade,
                "source": source,
                "features": features,
            }
        )
        source_counts[source] += 1
        grade_counts[grade] += 1

    warnings: list[str] = []
    if len(rows) < MINIMUM_LABELS:
        warnings.append("minimum_30_labels_required")
    if len(grade_counts) < 2:
        warnings.append("at_least_two_grade_levels_required")

    equal = [1.0 / len(FEATURE_NAMES)] * len(FEATURE_NAMES)
    equal_mae = _mae(rows, equal)
    best = equal
    best_mae = equal_mae
    if rows and len(grade_counts) >= 2:
        weights = list(equal)
        learning_rate = 0.08
        prior_strength = 0.035
        for _ in range(600):
            gradient = [0.0] * len(weights)
            for features, target in rows:
                error = sum(weight * value for weight, value in zip(weights, features)) - target
                for index, value in enumerate(features):
                    gradient[index] += 2.0 * error * value / len(rows)
            for index in range(len(weights)):
                gradient[index] += prior_strength * (weights[index] - equal[index])
            weights = _project_simplex([weight - learning_rate * grad for weight, grad in zip(weights, gradient)])
        fitted_mae = _mae(rows, weights)
        if fitted_mae <= best_mae:
            best, best_mae = weights, fitted_mae

    eligible = not warnings
    preview_id = hashlib.sha256(
        json.dumps(
            sorted(digest_rows, key=lambda item: item["project_id"]),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:20]
    return {
        "schema_version": "discovery-scoring-calibration/v1",
        "eligible": eligible,
        "sample_count": len(rows),
        "preview_id": preview_id,
        "minimum_sample_count": MINIMUM_LABELS,
        "grade_distribution": {str(key): grade_counts.get(key, 0) for key in range(4)},
        "label_sources": dict(sorted(source_counts.items())),
        "weights": {name: round(weight, 8) for name, weight in zip(FEATURE_NAMES, best)},
        "metrics": {
            "mae": round(best_mae, 6),
            "equal_weight_mae": round(equal_mae, 6),
        },
        "warnings": warnings,
        "excluded_features": ["selected_file_count", "file_count"],
        "quantity_policy": "Portfolio-level quantity preferences never reduce an individual project's suitability score.",
        "truth_notice": "Model consensus is a calibration label, not an independently verified gold standard.",
        "created_at": _utc_now(),
    }


class DiscoveryCalibrationStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_calibration_path()

    def load_active(self) -> dict[str, Any] | None:
        with _STORE_LOCK:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                return None
            return payload if isinstance(payload, dict) and payload.get("version_id") else None

    def activate(self, report: Mapping[str, Any]) -> dict[str, Any]:
        if not report.get("eligible"):
            raise ValueError("calibration_not_eligible")
        weights = report.get("weights")
        if not isinstance(weights, Mapping) or not weights:
            raise ValueError("calibration_weights_required")
        active = {
            **dict(report),
            "version_id": f"cal-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}",
            "activated_at": _utc_now(),
        }
        with _STORE_LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(json.dumps(active, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.path)
            _ACTIVE_CACHE.pop(str(self.path.resolve()), None)
        return active


def load_active_calibration(path: str | Path | None = None) -> dict[str, Any] | None:
    resolved = Path(path) if path is not None else default_calibration_path()
    key = str(resolved.resolve())
    try:
        modified = resolved.stat().st_mtime_ns
    except OSError:
        modified = -1
    cached = _ACTIVE_CACHE.get(key)
    if cached is not None and cached[0] == modified:
        return cached[1]
    active = DiscoveryCalibrationStore(resolved).load_active()
    _ACTIVE_CACHE[key] = (modified, active)
    return active


def score_project_with_calibration(project: Mapping[str, Any] | Any, calibration: Mapping[str, Any]) -> dict[str, Any]:
    features = project_calibration_features(project)
    weights = calibration.get("weights") if isinstance(calibration, Mapping) else None
    if not isinstance(weights, Mapping):
        raise ValueError("calibration_weights_required")
    contributions = {
        name: features[name] * max(0.0, float(weights.get(name) or 0.0))
        for name in FEATURE_NAMES
    }
    score = max(0.0, min(100.0, sum(contributions.values()) * 100.0))
    return {
        "score": round(score, 2),
        "features": features,
        "components": {name: round(value * 100.0, 3) for name, value in contributions.items()},
        "version_id": str(calibration.get("version_id") or "preview"),
    }
