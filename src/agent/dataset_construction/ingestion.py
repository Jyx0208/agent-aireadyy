from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from agent.dataset_construction.models import DatasetCatalog, ObservationRecord


_SUMMARY_NAMES = ("mini_e2e_batch_summary.json", "batch_summary.json")


def _text(value: Any) -> str:
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value or "").strip()


def _first(row: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = _text(row.get(name))
        if value:
            return value
    return ""


def _stable_id(prefix: str, *parts: object) -> str:
    payload = "\x1f".join(_text(part).casefold() for part in parts)
    return f"{prefix}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    try:
        parsed = float(value) if value is not None and value != "" else None
        return parsed if parsed is not None and math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.replace("|", ";").split(";") if part.strip()]
    if isinstance(value, Iterable) and not isinstance(value, (bytes, Mapping)):
        return [_text(item) for item in value if _text(item)]
    return [_text(value)] if _text(value) else []


_TASK_ALIASES = {
    "rt": "rt_prediction",
    "retention_time": "rt_prediction",
    "fragment_intensity": "fragment_intensity_prediction",
    "fragment_intensity_prediction": "fragment_intensity_prediction",
}


def canonical_task_type(value: Any) -> str:
    text = _text(value).casefold()
    return _TASK_ALIASES.get(text, text)


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _label_evidence(task_type: str, row: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    peptide = _first(row, "peptide_sequence", "peptide", "sequence")
    modified = _first(row, "modified_sequence", "modified_peptide", "peptidoform")
    if task_type == "psm_scoring":
        label = _first(row, "target_decoy_label", "decoy_state", "is_decoy")
        return "target_decoy", {"target_decoy": label}
    if task_type == "rt_prediction":
        return "retention_time", {
            "retention_time": _optional_float(row.get("retention_time")),
            "unit": _first(row, "retention_time_unit", "rt_unit"),
            "unit_source": _first(row, "rt_unit_source"),
        }
    if task_type == "fragment_intensity_prediction":
        raw = _json_value(row.get("matched_ions_json"))
        if not raw:
            raw = _json_value(row.get("spectrum_intensity_json"))
        encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
        target_count = len(raw) if isinstance(raw, (list, dict)) else int(bool(_text(raw)))
        return "fragment_intensity", {
            "target_count": target_count,
            "target_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            if target_count
            else "",
        }
    if task_type == "ptm_denovo":
        tokens = _json_value(row.get("modification_tokens_json"))
        return "modified_peptide", {
            "peptide": peptide,
            "modified_peptide": modified,
            "modification_tokens": tokens if isinstance(tokens, list) else _strings(tokens),
            "localization_confidence": _optional_float(row.get("localization_confidence")),
        }
    return "peptide", {"peptide": peptide, "modified_peptide": modified or peptide}


def _find_summaries(batch_dir: Path) -> list[Path]:
    """Find an aggregate summary or all per-item summaries below a Batch root."""

    for name in _SUMMARY_NAMES:
        candidate = batch_dir / name
        if candidate.is_file():
            return [candidate]
    matches = sorted(batch_dir.glob("*batch*summary*.json"))
    if matches:
        return [matches[0]]
    recursive = sorted(
        {
            candidate.resolve()
            for name in _SUMMARY_NAMES
            for candidate in batch_dir.rglob(name)
            if candidate.is_file()
        }
    )
    if recursive:
        return recursive
    raise FileNotFoundError(f"No existing Batch summary found under {batch_dir}")


def _task_parquets(
    run: Mapping[str, Any],
    *,
    batch_dir: Path,
) -> Iterable[tuple[str, Path]]:
    task_files = run.get("task_files")
    if not isinstance(task_files, Mapping):
        return
    seen: set[Path] = set()
    for task_type, files in task_files.items():
        if not isinstance(files, Mapping):
            continue
        for value in files.values():
            path = Path(_text(value))
            if not path.is_absolute():
                path = batch_dir / path
            path = path.resolve()
            if path.suffix.lower() != ".parquet" or path in seen:
                continue
            seen.add(path)
            yield canonical_task_type(task_type) or "unknown", path


def _observations_from_parquet(
    *,
    run: Mapping[str, Any],
    task_type: str,
    artifact: Path,
) -> Iterable[ObservationRecord]:
    resolved = artifact.resolve()
    row_number = 0
    for batch in pq.ParquetFile(resolved).iter_batches(batch_size=65_536):
        for row in batch.to_pylist():
            row_number += 1
            project_id = _first(row, "project_accession", "project_id") or _first(
                run, "project_accession", "project_id"
            )
            source_file_id = _first(row, "source_file", "file_name", "raw_file_id") or _first(
                run, "source_file", "file_name", "raw_file_id"
            )
            sample_id = _first(row, "sample_id", "sample_name") or _first(
                run, "sample_id", "sample_name"
            )
            file_family_id = _first(row, "file_family_id", "raw_file_family_id") or _first(
                run, "file_family_id", "raw_file_family_id"
            ) or _stable_id("file-family", project_id, source_file_id)
            spectrum_id = _first(row, "spectrum_id", "usi", "native_id", "scan")
            label_type, label_payload = _label_evidence(task_type, row)
            observation_id = _stable_id(
                "observation",
                task_type,
                project_id,
                source_file_id,
                spectrum_id or row_number,
            )
            yield ObservationRecord(
                observation_id=observation_id,
                task_type=task_type,
                project_id=project_id,
                source_file_id=source_file_id,
                file_family_id=file_family_id,
                source_artifact_uri=str(resolved),
                source_row_number=row_number,
                spectrum_id=spectrum_id,
                sample_id=sample_id,
                subject_id=_first(run, "subject_id", "individual_id"),
                technical_replicate_id=_first(run, "technical_replicate_id"),
                fraction_id=_first(run, "fraction_id"),
                tmt_plex_id=_first(run, "tmt_plex_id", "plex_id"),
                lab_id=_first(row, "lab_id", "submitter_lab", "laboratory") or _first(run, "lab_id", "submitter_lab", "laboratory"),
                instrument_id=_first(row, "instrument_id", "instrument_family", "instrument") or _first(run, "instrument_id", "instrument_family", "instrument"),
                organism_id=_first(row, "organism_taxon_id", "organism_id", "canonical_species", "species") or _first(run, "organism_id", "species", "canonical_species"),
                acquisition_id=_first(row, "acquisition_id", "acquisition_mode", "fragmentation_method") or _first(run, "acquisition_id", "acquisition_mode"),
                gradient_id=_first(row, "gradient_id", "lc_gradient_minutes", "lc_gradient") or _first(run, "gradient_id", "lc_gradient"),
                search_workflow_id=_first(row, "search_workflow_id", "workflow_id", "search_engine") or _first(run, "search_workflow_id", "workflow_id"),
                peptide=_first(row, "peptide_sequence", "peptide", "sequence"),
                modified_peptide=_first(row, "modified_sequence", "modified_peptide", "peptidoform"),
                protein_ids=_strings(row.get("protein_accession") or row.get("protein_ids")),
                protein_family_ids=_strings(row.get("protein_family_ids")),
                modification_classes=_strings(row.get("modification_classes") or row.get("ptm_type")),
                charge=_optional_int(row.get("charge")),
                q_value=_optional_float(row.get("q_value")),
                psm_probability=_optional_float(row.get("psm_probability")),
                label_type=label_type,
                label_payload=label_payload,
                label_source=_first(row, "label_source") or "source_artifact_row",
            )


def ingest_existing_batch(
    batch_dir: str | Path,
    *,
    task_types: Iterable[str] | None = None,
) -> DatasetCatalog:
    """Read existing Batch artifacts into a row-level catalog without modifying them."""

    source = Path(batch_dir).resolve()
    observations: list[ObservationRecord] = []
    warnings: list[str] = []
    selected_tasks = (
        {canonical_task_type(value) for value in task_types}
        if task_types is not None
        else None
    )
    seen_artifacts: set[Path] = set()
    for summary_path in _find_summaries(source):
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        runs = summary.get("run_results") or summary.get("runs") or []
        if not isinstance(runs, list):
            raise ValueError(
                f"Batch summary must contain a list of run_results: {summary_path}"
            )
        for run in runs:
            if not isinstance(run, Mapping):
                warnings.append(f"ignored_non_object_run:{summary_path}")
                continue
            for task_type, artifact in _task_parquets(
                run,
                batch_dir=summary_path.parent,
            ):
                if selected_tasks is not None and task_type not in selected_tasks:
                    continue
                if artifact in seen_artifacts:
                    warnings.append(f"duplicate_parquet_ignored:{artifact}")
                    continue
                seen_artifacts.add(artifact)
                if not artifact.is_file():
                    warnings.append(f"missing_parquet:{artifact}")
                    continue
                observations.extend(
                    _observations_from_parquet(
                        run=run,
                        task_type=task_type,
                        artifact=artifact,
                    )
                )
    unique: dict[str, ObservationRecord] = {}
    for row in observations:
        previous = unique.get(row.observation_id)
        if previous is None:
            unique[row.observation_id] = row
            continue
        label = (row.task_type, row.peptide, row.modified_peptide, row.label_payload)
        previous_label = (
            previous.task_type,
            previous.peptide,
            previous.modified_peptide,
            previous.label_payload,
        )
        if label != previous_label:
            raise ValueError(
                "conflicting_duplicate_observation:"
                f"{row.observation_id}:{previous.source_artifact_uri}:{row.source_artifact_uri}"
            )
        source_artifacts = sorted(
            {
                previous.source_artifact_uri,
                row.source_artifact_uri,
                *_strings(previous.metadata.get("duplicate_source_artifacts")),
            }
        )
        unique[row.observation_id] = previous.model_copy(
            update={
                "q_value": min(
                    value
                    for value in (previous.q_value, row.q_value)
                    if value is not None
                )
                if previous.q_value is not None or row.q_value is not None
                else None,
                "metadata": {
                    **previous.metadata,
                    "duplicate_source_artifacts": source_artifacts,
                },
            }
        )
        warnings.append(f"duplicate_observation_merged:{row.observation_id}")
    return DatasetCatalog(
        source_batch_dir=str(source),
        observations=list(unique.values()),
        warnings=warnings,
    )
