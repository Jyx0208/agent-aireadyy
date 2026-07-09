from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import Field

from agent.ai_ready.fragment_intensity_exporter import _load_peaklists, _match_spectrum
from agent.ai_ready.psm_scoring_exporter import PROTEIN_COLUMNS, TARGET_DECOY_COLUMNS
from agent.ai_ready.rt_exporter import (
    CHARGE_COLUMNS,
    MODIFIED_COLUMNS,
    PEPTIDE_COLUMNS,
    PROBABILITY_COLUMNS,
    Q_VALUE_COLUMNS,
    RT_COLUMNS,
    SOURCE_FILE_COLUMNS,
    SPECTRUM_COLUMNS,
    _clean_text,
    _find_column,
    _optional_series,
)
from agent.discovery.task_readiness import normalize_task_type
from agent.models import JsonModel
from agent.utils import write_json


InputStatus = Literal["ready", "weak_ready", "blocked"]

TASKS_REQUIRING_PEAKLIST = {
    "fragment_intensity_prediction",
    "denovo",
    "ptm_denovo",
    "chimeric_interpretation",
}
SUPPORTED_PROFILE_TASKS = {
    "rt_prediction",
    "fragment_intensity_prediction",
    "psm_scoring",
    "denovo",
    "ptm_denovo",
    "chimeric_interpretation",
}


class AiReadyInputTaskProfile(JsonModel):
    task_type: str
    input_status: InputStatus
    rows_in: int = 0
    search_result_paths: list[str] = Field(default_factory=list)
    peaklist_paths: list[str] = Field(default_factory=list)
    detected_columns: dict[str, str | None] = Field(default_factory=dict)
    missing_columns: list[str] = Field(default_factory=list)
    present_fields: dict[str, bool] = Field(default_factory=dict)
    mgf_spectrum_count: int = 0
    matched_spectrum_count: int = 0
    unmatched_spectrum_count: int = 0
    modification_token_rows: int = 0
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AiReadyInputProfileResult(JsonModel):
    status: str
    output_dir: str
    search_result_paths: list[str] = Field(default_factory=list)
    peaklist_paths: list[str] = Field(default_factory=list)
    rows_in: int = 0
    tsv_columns: dict[str, list[str]] = Field(default_factory=dict)
    task_profiles: list[AiReadyInputTaskProfile] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    json_path: str
    csv_path: str


def profile_ai_ready_inputs(
    *,
    search_results: list[str | Path],
    peaklists: list[str | Path] | None,
    task_types: list[str],
    output_dir: str | Path,
) -> AiReadyInputProfileResult:
    if not search_results:
        raise ValueError("At least one --search-result is required.")
    normalized_tasks = _normalize_tasks(task_types)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    frames: list[tuple[Path, pd.DataFrame, dict[str, str | None]]] = []
    tsv_columns: dict[str, list[str]] = {}
    total_rows = 0
    for path_value in search_results:
        path = Path(path_value)
        if not path.exists():
            raise ValueError(f"Search result does not exist: {path}")
        frame = pd.read_csv(path, sep="\t", dtype=str, low_memory=False)
        column_map = _detect_columns(frame.columns)
        frames.append((path, frame, column_map))
        tsv_columns[str(path)] = [str(column) for column in frame.columns]
        total_rows += int(len(frame))

    spectra = {}
    peaklist_warnings: list[str] = []
    peaklist_paths = [Path(path) for path in (peaklists or [])]
    if peaklist_paths:
        spectra, peaklist_warnings = _load_peaklists(peaklist_paths)
    mgf_spectrum_count = _unique_spectrum_count(spectra)

    task_profiles = [
        _profile_task(
            task_type=task_type,
            frames=frames,
            spectra=spectra,
            mgf_spectrum_count=mgf_spectrum_count,
            peaklist_paths=peaklist_paths,
            peaklist_warnings=peaklist_warnings,
        )
        for task_type in normalized_tasks
    ]
    status = "blocked" if all(item.input_status == "blocked" for item in task_profiles) else "completed"
    json_path = output_dir / "ai_ready_input_profile.json"
    csv_path = output_dir / "ai_ready_input_profile.csv"
    result = AiReadyInputProfileResult(
        status=status,
        output_dir=str(output_dir),
        search_result_paths=[str(Path(path)) for path in search_results],
        peaklist_paths=[str(path) for path in peaklist_paths],
        rows_in=total_rows,
        tsv_columns=tsv_columns,
        task_profiles=task_profiles,
        warnings=sorted(set(peaklist_warnings)),
        json_path=str(json_path),
        csv_path=str(csv_path),
    )
    write_json(json_path, result.model_dump(mode="json"))
    _write_profile_csv(csv_path, task_profiles)
    return result


def _profile_task(
    *,
    task_type: str,
    frames: list[tuple[Path, pd.DataFrame, dict[str, str | None]]],
    spectra: dict[str, Any],
    mgf_spectrum_count: int,
    peaklist_paths: list[Path],
    peaklist_warnings: list[str],
) -> AiReadyInputTaskProfile:
    merged_columns = _merge_column_maps([column_map for _, _, column_map in frames])
    required_columns = _required_columns(task_type)
    missing_columns = [column for column in required_columns if not merged_columns.get(column)]
    blockers: list[str] = []
    warnings: list[str] = list(peaklist_warnings)
    if missing_columns:
        blockers.append("missing_search_result_columns")

    needs_peaklist = task_type in TASKS_REQUIRING_PEAKLIST
    if needs_peaklist and not peaklist_paths:
        blockers.append("needs_peaklist")

    matched_spectra = 0
    unmatched_spectra = 0
    if needs_peaklist and peaklist_paths and merged_columns.get("spectrum_id"):
        matched_spectra, unmatched_spectra = _count_spectrum_matches(frames, spectra)
        if matched_spectra == 0:
            blockers.append("spectrum_not_matched")
        elif unmatched_spectra:
            warnings.append("spectrum_not_matched")

    if task_type == "psm_scoring" and not (merged_columns.get("target_decoy") or merged_columns.get("protein")):
        blockers.append("needs_target_decoy_labels")

    modification_token_rows = _count_modification_token_rows(frames, merged_columns.get("modified_sequence"))
    if task_type == "ptm_denovo" and not modification_token_rows:
        blockers.append("needs_modified_sequence_labels")

    if not (merged_columns.get("q_value") or merged_columns.get("psm_probability")):
        warnings.append("confidence_column_missing")
    if task_type == "rt_prediction" and not merged_columns.get("retention_time"):
        blockers.append("missing_search_result_columns")

    blockers = _dedupe(blockers)
    warnings = _dedupe(warnings)
    if blockers:
        input_status: InputStatus = "blocked"
    elif warnings:
        input_status = "weak_ready"
    else:
        input_status = "ready"

    return AiReadyInputTaskProfile(
        task_type=task_type,
        input_status=input_status,
        rows_in=sum(int(len(frame)) for _, frame, _ in frames),
        search_result_paths=[str(path) for path, _, _ in frames],
        peaklist_paths=[str(path) for path in peaklist_paths],
        detected_columns=merged_columns,
        missing_columns=missing_columns,
        present_fields={key: bool(value) for key, value in merged_columns.items()},
        mgf_spectrum_count=mgf_spectrum_count,
        matched_spectrum_count=matched_spectra,
        unmatched_spectrum_count=unmatched_spectra,
        modification_token_rows=modification_token_rows,
        blockers=blockers,
        warnings=warnings,
    )


def _normalize_tasks(task_types: list[str]) -> list[str]:
    values = task_types or ["rt_prediction", "fragment_intensity_prediction", "psm_scoring", "denovo"]
    result: list[str] = []
    for value in values:
        task_type = normalize_task_type(value)
        if task_type not in SUPPORTED_PROFILE_TASKS:
            raise ValueError(f"Input profiler does not support task type: {value}")
        if task_type not in result:
            result.append(task_type)
    return result


def _detect_columns(columns: pd.Index) -> dict[str, str | None]:
    available = list(columns)
    return {
        "peptide_sequence": _find_column(available, PEPTIDE_COLUMNS),
        "modified_sequence": _find_column(available, MODIFIED_COLUMNS),
        "charge": _find_column(available, CHARGE_COLUMNS),
        "spectrum_id": _find_column(available, SPECTRUM_COLUMNS),
        "source_file": _find_column(available, SOURCE_FILE_COLUMNS),
        "retention_time": _find_column(available, RT_COLUMNS),
        "q_value": _find_column(available, Q_VALUE_COLUMNS),
        "psm_probability": _find_column(available, PROBABILITY_COLUMNS),
        "target_decoy": _find_column(available, TARGET_DECOY_COLUMNS),
        "protein": _find_column(available, PROTEIN_COLUMNS),
    }


def _required_columns(task_type: str) -> list[str]:
    if task_type == "rt_prediction":
        return ["peptide_sequence", "charge", "retention_time"]
    if task_type == "psm_scoring":
        return ["peptide_sequence", "charge", "spectrum_id"]
    if task_type == "ptm_denovo":
        return ["peptide_sequence", "modified_sequence", "charge", "spectrum_id"]
    return ["peptide_sequence", "charge", "spectrum_id"]


def _merge_column_maps(column_maps: list[dict[str, str | None]]) -> dict[str, str | None]:
    keys = sorted(set().union(*(item.keys() for item in column_maps)))
    result: dict[str, str | None] = {}
    for key in keys:
        result[key] = next((item.get(key) for item in column_maps if item.get(key)), None)
    return result


def _count_spectrum_matches(
    frames: list[tuple[Path, pd.DataFrame, dict[str, str | None]]],
    spectra: dict[str, Any],
) -> tuple[int, int]:
    matched = 0
    unmatched = 0
    for _, frame, column_map in frames:
        spectrum_column = column_map.get("spectrum_id")
        if not spectrum_column:
            continue
        for value in _optional_series(frame, spectrum_column):
            spectrum_id = _clean_text(value)
            if not spectrum_id:
                continue
            if _match_spectrum(spectrum_id, spectra) is None:
                unmatched += 1
            else:
                matched += 1
    return matched, unmatched


def _count_modification_token_rows(
    frames: list[tuple[Path, pd.DataFrame, dict[str, str | None]]],
    modified_column: str | None,
) -> int:
    if not modified_column:
        return 0
    count = 0
    for _, frame, column_map in frames:
        column = column_map.get("modified_sequence")
        if not column:
            continue
        for value in _optional_series(frame, column):
            if _has_modification_token(value):
                count += 1
    return count


def _has_modification_token(value: Any) -> bool:
    text = _clean_text(value)
    if not text:
        return False
    lower = text.casefold()
    if any(token in lower for token in ["phosph", "acetyl", "glyco", "methyl", "oxid", "mod"]):
        return True
    return any(char in text for char in "[](){}+@*")


def _unique_spectrum_count(spectra: dict[str, Any]) -> int:
    unique = {(item.path, item.title, item.scan) for item in spectra.values()}
    return len(unique)


def _write_profile_csv(path: Path, rows: list[AiReadyInputTaskProfile]) -> None:
    fieldnames = [
        "task_type",
        "input_status",
        "rows_in",
        "missing_columns",
        "mgf_spectrum_count",
        "matched_spectrum_count",
        "unmatched_spectrum_count",
        "modification_token_rows",
        "blockers",
        "warnings",
        "detected_columns",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "task_type": row.task_type,
                    "input_status": row.input_status,
                    "rows_in": row.rows_in,
                    "missing_columns": ";".join(row.missing_columns),
                    "mgf_spectrum_count": row.mgf_spectrum_count,
                    "matched_spectrum_count": row.matched_spectrum_count,
                    "unmatched_spectrum_count": row.unmatched_spectrum_count,
                    "modification_token_rows": row.modification_token_rows,
                    "blockers": ";".join(row.blockers),
                    "warnings": ";".join(row.warnings),
                    "detected_columns": json.dumps(row.detected_columns, ensure_ascii=False),
                }
            )


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result
