from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import Field

from agent.ai_ready.fragment_intensity_exporter import _load_peaklists
from agent.ai_ready.input_profile import _detect_columns, _has_modification_token, _unique_spectrum_count
from agent.ai_ready.rt_exporter import _clean_text, _optional_series
from agent.models import JsonModel
from agent.utils import write_json


LocatedRole = Literal["psm_table", "peptide_table", "pin_table", "peaklist_mgf"]


class LocatedAiReadyInput(JsonModel):
    path: str
    file_role: LocatedRole
    search_engine_guess: str | None = None
    source_file_guess: str | None = None
    row_count: int = 0
    columns: list[str] = Field(default_factory=list)
    has_rt: bool = False
    has_q_value: bool = False
    has_probability: bool = False
    has_target_decoy: bool = False
    has_modified_sequence: bool = False
    mgf_spectrum_count: int = 0
    warnings: list[str] = Field(default_factory=list)


class AiReadyInputLocationResult(JsonModel):
    status: str
    search_dir: str
    output_dir: str
    entries: list[LocatedAiReadyInput] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    json_path: str
    csv_path: str


def locate_ai_ready_inputs(
    *,
    search_dir: str | Path,
    output_dir: str | Path,
) -> AiReadyInputLocationResult:
    search_dir = Path(search_dir)
    if not search_dir.exists():
        raise ValueError(f"Search result directory does not exist: {search_dir}")
    if not search_dir.is_dir():
        raise ValueError(f"--search-dir must be a directory: {search_dir}")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    entries = [_inspect_file(path) for path in sorted(search_dir.rglob("*")) if _is_candidate_file(path)]
    entries = [entry for entry in entries if entry is not None]
    summary = _build_location_summary(entries)
    status = "blocked" if not entries else "completed"
    json_path = output_dir / "ai_ready_input_locations.json"
    csv_path = output_dir / "ai_ready_input_locations.csv"
    result = AiReadyInputLocationResult(
        status=status,
        search_dir=str(search_dir),
        output_dir=str(output_dir),
        entries=entries,
        summary=summary,
        json_path=str(json_path),
        csv_path=str(csv_path),
    )
    write_json(json_path, result.model_dump(mode="json"))
    _write_locations_csv(csv_path, entries)
    return result


def select_ai_ready_inputs(
    result: AiReadyInputLocationResult,
    *,
    task_type: str | None = None,
) -> tuple[list[Path], list[Path]]:
    tables = [Path(entry.path) for entry in result.entries if entry.file_role in {"psm_table", "peptide_table", "pin_table"}]
    peaklists = [Path(entry.path) for entry in result.entries if entry.file_role == "peaklist_mgf"]
    if task_type == "psm_scoring":
        psm_tables = [Path(entry.path) for entry in result.entries if entry.file_role in {"psm_table", "pin_table"}]
        return (psm_tables or tables), peaklists
    if task_type == "rt_prediction":
        rt_tables = [Path(entry.path) for entry in result.entries if entry.has_rt and entry.file_role in {"psm_table", "peptide_table"}]
        return (rt_tables or tables), peaklists
    if task_type in {"fragment_intensity_prediction", "denovo", "ptm_denovo", "chimeric_interpretation"}:
        psm_tables = [Path(entry.path) for entry in result.entries if entry.file_role == "psm_table"]
        return (psm_tables or tables), peaklists
    return tables, peaklists


def _is_candidate_file(path: Path) -> bool:
    if not path.is_file():
        return False
    name = path.name.casefold()
    stem = path.stem.casefold()
    if path.suffix.casefold() == ".mgf":
        return True
    if path.suffix.casefold() == ".pin":
        return True
    if path.suffix.casefold() == ".tsv":
        if "rawspectrum" in stem or "spectrum" in stem and "psm" not in stem:
            return False
        if "peptide_count" in stem or stem.endswith("_counts"):
            return False
        if "psm" in stem or "peptide" in stem or "search_result" in stem:
            return True
    return name in {
        "psm.tsv",
        "peptide.tsv",
        "combined_psm.tsv",
        "combined_peptide.tsv",
    }


def _inspect_file(path: Path) -> LocatedAiReadyInput | None:
    role = _role_for_path(path)
    if role is None:
        return None
    if role == "peaklist_mgf":
        return _inspect_mgf(path)
    return _inspect_table(path, role)


def _role_for_path(path: Path) -> LocatedRole | None:
    name = path.name.casefold()
    stem = path.stem.casefold()
    suffix = path.suffix.casefold()
    if suffix == ".mgf":
        return "peaklist_mgf"
    if suffix == ".pin":
        return "pin_table"
    if name in {"psm.tsv", "combined_psm.tsv"}:
        return "psm_table"
    if name in {"peptide.tsv", "combined_peptide.tsv"}:
        return "peptide_table"
    if suffix == ".tsv":
        if "rawspectrum" in stem or "spectrum" in stem and "psm" not in stem:
            return None
        if "peptide_count" in stem or stem.endswith("_counts"):
            return None
        if "psm" in stem or "search_result" in stem:
            return "psm_table"
        if "peptide" in stem:
            return "peptide_table"
    return None


def _inspect_mgf(path: Path) -> LocatedAiReadyInput:
    warnings: list[str] = []
    spectra = {}
    try:
        spectra, warnings = _load_peaklists([path])
    except Exception as exc:
        warnings = [f"mgf_read_failed:{exc}"]
    return LocatedAiReadyInput(
        path=str(path),
        file_role="peaklist_mgf",
        search_engine_guess=_guess_engine(path),
        source_file_guess=_source_guess_from_path(path),
        mgf_spectrum_count=_unique_spectrum_count(spectra),
        warnings=_dedupe(warnings),
    )


def _inspect_table(path: Path, role: LocatedRole) -> LocatedAiReadyInput:
    warnings: list[str] = []
    try:
        frame = pd.read_csv(path, sep="\t", dtype=str, low_memory=False)
    except Exception as exc:
        return LocatedAiReadyInput(
            path=str(path),
            file_role=role,
            search_engine_guess=_guess_engine(path),
            source_file_guess=_source_guess_from_path(path),
            warnings=[f"table_read_failed:{exc}"],
        )
    column_map = _detect_columns(frame.columns)
    source_file_guess = _first_value(frame, column_map.get("source_file")) or _source_guess_from_path(path)
    modified_column = column_map.get("modified_sequence")
    has_modified = bool(modified_column and any(_has_modification_token(value) for value in _optional_series(frame, modified_column)))
    if not column_map.get("peptide_sequence"):
        warnings.append("peptide_column_missing")
    if not column_map.get("spectrum_id") and role != "peptide_table":
        warnings.append("spectrum_id_column_missing")
    return LocatedAiReadyInput(
        path=str(path),
        file_role=role,
        search_engine_guess=_guess_engine(path),
        source_file_guess=source_file_guess,
        row_count=int(len(frame)),
        columns=[str(column) for column in frame.columns],
        has_rt=bool(column_map.get("retention_time")),
        has_q_value=bool(column_map.get("q_value")),
        has_probability=bool(column_map.get("psm_probability")),
        has_target_decoy=bool(column_map.get("target_decoy") or column_map.get("protein")),
        has_modified_sequence=has_modified,
        warnings=_dedupe(warnings),
    )


def _first_value(frame: pd.DataFrame, column: str | None) -> str | None:
    if not column:
        return None
    for value in _optional_series(frame, column):
        text = _clean_text(value)
        if text:
            return text
    return None


def _guess_engine(path: Path) -> str | None:
    text = str(path).casefold()
    if "fragpipe" in text or "msfragger" in text or "fragger" in text:
        return "fragpipe_msfragger"
    if "sage" in text:
        return "sage"
    if "msgf" in text or "ms-gf" in text:
        return "msgf"
    return None


def _source_guess_from_path(path: Path) -> str | None:
    stem = path.stem
    if stem in {"psm", "peptide", "combined_psm", "combined_peptide", "spectra"}:
        return None
    return stem


def _build_location_summary(entries: list[LocatedAiReadyInput]) -> dict[str, Any]:
    by_role: dict[str, int] = {}
    for entry in entries:
        by_role[entry.file_role] = by_role.get(entry.file_role, 0) + 1
    tables = [entry for entry in entries if entry.file_role != "peaklist_mgf"]
    peaklists = [entry for entry in entries if entry.file_role == "peaklist_mgf"]
    return {
        "located_files": len(entries),
        "role_counts": dict(sorted(by_role.items())),
        "search_result_count": len(tables),
        "peaklist_count": len(peaklists),
        "total_rows": sum(entry.row_count for entry in tables),
        "total_mgf_spectra": sum(entry.mgf_spectrum_count for entry in peaklists),
        "has_rt_table": any(entry.has_rt for entry in tables),
        "has_target_decoy_table": any(entry.has_target_decoy for entry in tables),
        "has_modified_sequence_table": any(entry.has_modified_sequence for entry in tables),
        "warnings": _dedupe([warning for entry in entries for warning in entry.warnings]),
    }


def _write_locations_csv(path: Path, entries: list[LocatedAiReadyInput]) -> None:
    fieldnames = [
        "path",
        "file_role",
        "search_engine_guess",
        "source_file_guess",
        "row_count",
        "columns",
        "has_rt",
        "has_q_value",
        "has_probability",
        "has_target_decoy",
        "has_modified_sequence",
        "mgf_spectrum_count",
        "warnings",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for entry in entries:
            payload = entry.model_dump(mode="json")
            writer.writerow(
                {
                    key: json.dumps(payload[key], ensure_ascii=False, sort_keys=True)
                    if isinstance(payload.get(key), (list, dict))
                    else payload.get(key, "")
                    for key in fieldnames
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
