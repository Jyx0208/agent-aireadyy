from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any


_METRIC_ALIASES = {
    "sequence_accuracy": {
        "sequence_accuracy",
        "seq_accuracy",
        "seq_acc",
        "peptide_accuracy",
        "peptide_acc",
        "peptide_match_rate",
        "exact_match",
        "exact_match_rate",
    },
    "peptide_recall": {"peptide_recall", "pep_recall", "peptide recall", "peptide-level recall", "pep_r"},
    "peptide_precision": {"peptide_precision", "pep_precision", "peptide precision", "peptide-level precision", "pep_p"},
    "amino_acid_recall": {"amino_acid_recall", "aa_recall", "aa recall", "aa_r"},
    "amino_acid_precision": {"amino_acid_precision", "aa_precision", "aa precision", "aa_p"},
    "f1": {"f1", "f1_score", "f-score", "fscore"},
    "accuracy": {"accuracy", "acc"},
    "auc": {"auc", "auroc", "roc_auc"},
    "cosine_similarity": {"cosine_similarity", "cosine", "spectral_angle", "sa"},
    "pearson": {"pearson", "pearsonr", "pearson_r"},
    "spearman": {"spearman", "spearmanr", "spearman_r"},
    "mae": {"mae", "mean_absolute_error"},
    "rmse": {"rmse", "root_mean_squared_error"},
    "loss": {"loss", "val_loss", "validation_loss"},
}

_PREFERRED_BY_TASK = {
    "denovo": ["sequence_accuracy", "peptide_recall", "amino_acid_recall", "f1", "accuracy"],
    "ptm_denovo": ["site_localization_accuracy", "sequence_accuracy", "peptide_recall", "f1", "accuracy"],
    "psm_scoring": ["auc", "f1", "accuracy"],
    "fragment_intensity_prediction": ["cosine_similarity", "pearson", "spearman"],
    "rt_prediction": ["pearson", "spearman", "mae", "rmse"],
}


def load_model_metrics_file(path: str | Path, *, adapter: str = "auto", task_type: str = "") -> dict[str, Any]:
    """Load common external model evaluation outputs into a metrics dict.

    This is intentionally lightweight: it does not run a model. It normalizes
    already-produced JSON/CSV/TSV/log metrics so model-loop can diagnose
    failure modes and plan data expansion from real model evidence.
    """
    path = Path(path)
    suffix = path.suffix.casefold()
    if suffix == ".json":
        metrics = _load_json_metrics(path)
        source_format = "json"
    elif suffix in {".csv", ".tsv"}:
        metrics = _load_table_metrics(path, delimiter="\t" if suffix == ".tsv" else ",")
        source_format = suffix.lstrip(".")
    else:
        metrics = _load_text_metrics(path)
        source_format = "text"
    normalized = _normalize_metric_keys(metrics)
    if "primary_metric" not in normalized:
        inferred = _infer_primary_metric(normalized, task_type=task_type)
        if inferred:
            normalized["primary_metric"] = inferred
    normalized.setdefault("higher_is_better", _higher_is_better(str(normalized.get("primary_metric") or "")))
    normalized["metric_source_path"] = str(path)
    normalized["metric_source_format"] = source_format
    normalized["metric_adapter_template"] = _normalize_adapter_name(adapter)
    return normalized


def _load_json_metrics(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        if isinstance(payload.get("metrics"), dict):
            merged = dict(payload["metrics"])
            for key in ["primary_metric", "higher_is_better", "thresholds", "slices", "train", "val", "test"]:
                if key in payload and key not in merged:
                    merged[key] = payload[key]
            return merged
        return payload
    if isinstance(payload, list):
        return _rows_to_metrics([row for row in payload if isinstance(row, dict)])
    return {}


def _load_table_metrics(path: Path, *, delimiter: str) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter=delimiter))
    return _rows_to_metrics(rows)


def _rows_to_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    lower_columns = {key.casefold(): key for key in rows[0].keys()}
    metric_col = _first_column(lower_columns, ["metric", "name", "key", "measure"])
    value_col = _first_column(lower_columns, ["value", "score", "mean", "metric_value"])
    split_col = _first_column(lower_columns, ["split", "slice", "subset", "group"])
    metrics: dict[str, Any] = {}
    if metric_col and value_col:
        for row in rows:
            metric = _canonical_metric_name(str(row.get(metric_col) or ""))
            value = _coerce_float(row.get(value_col))
            if not metric or value is None:
                continue
            value = _maybe_fraction(metric, value)
            split = str(row.get(split_col) or "").strip() if split_col else ""
            if split and split.casefold() not in {"all", "overall", "metrics"}:
                metrics.setdefault("slices", {}).setdefault(_safe_key(split), {})[metric] = value
            else:
                metrics[metric] = value
        return metrics
    if len(rows) == 1:
        for key, value in rows[0].items():
            parsed = _coerce_float(value)
            if parsed is None:
                continue
            metric = _canonical_metric_name(key)
            metrics[metric] = _maybe_fraction(metric, parsed)
    return metrics


def _load_text_metrics(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    metrics: dict[str, Any] = {}
    pattern = re.compile(r"(?im)^\s*([A-Za-z][A-Za-z0-9_ ./%+\-]{1,80})\s*(?:=|:|\t)\s*(-?\d+(?:\.\d+)?)\s*%?\s*$")
    for match in pattern.finditer(text):
        raw_key = match.group(1).strip()
        raw_value = match.group(2)
        value = _coerce_float(raw_value)
        if value is None:
            continue
        metric = _canonical_metric_name(raw_key)
        if not metric:
            continue
        metrics[metric] = _maybe_fraction(metric, value, percent_hint="%" in match.group(0))
    return metrics


def _normalize_metric_keys(metrics: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in metrics.items():
        if key in {"primary_metric", "higher_is_better", "thresholds"}:
            normalized[key] = value
            continue
        if key == "slices" and isinstance(value, dict):
            normalized["slices"] = {
                _safe_key(slice_name): _normalize_metric_keys(slice_metrics)
                for slice_name, slice_metrics in value.items()
                if isinstance(slice_metrics, dict)
            }
            continue
        if isinstance(value, dict):
            normalized[_safe_key(str(key))] = _normalize_metric_keys(value)
            continue
        metric = _canonical_metric_name(str(key))
        parsed = _coerce_float(value)
        normalized[metric] = _maybe_fraction(metric, parsed) if parsed is not None else value
    return normalized


def _canonical_metric_name(raw: str) -> str:
    cleaned = _safe_key(raw)
    for canonical, aliases in _METRIC_ALIASES.items():
        if cleaned == _safe_key(canonical) or cleaned in {_safe_key(alias) for alias in aliases}:
            return canonical
    return cleaned


def _infer_primary_metric(metrics: dict[str, Any], *, task_type: str) -> str | None:
    for metric in _PREFERRED_BY_TASK.get(task_type, []):
        if metric in metrics or _metric_in_slices(metrics, metric):
            return metric
    for metric in ["sequence_accuracy", "accuracy", "f1", "auc", "cosine_similarity", "pearson", "mae", "loss"]:
        if metric in metrics:
            return metric
    for key, value in metrics.items():
        if isinstance(value, (int, float)) and key not in {"total_rows", "adapter_selected_count"}:
            return key
    return None


def _metric_in_slices(metrics: dict[str, Any], metric: str) -> bool:
    slices = metrics.get("slices")
    if not isinstance(slices, dict):
        return False
    return any(isinstance(value, dict) and metric in value for value in slices.values())


def _higher_is_better(metric: str) -> bool:
    lowered = metric.casefold()
    return not any(marker in lowered for marker in ["loss", "error", "mae", "rmse", "mse"])


def _first_column(columns: dict[str, str], candidates: list[str]) -> str | None:
    for candidate in candidates:
        if candidate.casefold() in columns:
            return columns[candidate.casefold()]
    return None


def _safe_key(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", str(value).strip().casefold()).strip("_")
    return cleaned or "metric"


def _normalize_adapter_name(adapter: str) -> str:
    value = str(adapter or "auto").strip().casefold().replace("-", "_")
    aliases = {
        "xuanjinovo": "xuanjinovo_eval",
        "xuanjinovo_template": "xuanjinovo_eval",
        "massnet": "massnet_eval",
        "massnet_dda": "massnet_eval",
        "casanovo": "casanovo_eval",
    }
    return aliases.get(value, value or "auto")


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


def _maybe_fraction(metric: str, value: float | None, *, percent_hint: bool = False) -> float | None:
    if value is None:
        return None
    bounded_metric = any(
        marker in metric.casefold()
        for marker in ["accuracy", "precision", "recall", "f1", "auc", "cosine", "pearson", "spearman"]
    )
    if bounded_metric and (percent_hint or value > 1.0) and value <= 100.0:
        return round(value / 100.0, 8)
    return value
