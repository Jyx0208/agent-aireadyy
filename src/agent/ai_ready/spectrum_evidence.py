from __future__ import annotations

import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import Field

from agent.models import JsonModel


FRAGMENTATION_COLUMNS = [
    "fragmentation_method",
    "fragmentation_methods",
    "activation_method",
    "activation",
    "dissociation_method",
    "dissociation",
    "collision_type",
    "collision",
    "fragmentation",
]

_METHOD_PATTERNS = [
    ("EThcD", re.compile(r"\bethcd\b|electron\s+transfer/higher[-\s]*energy", re.IGNORECASE)),
    ("ETciD", re.compile(r"\betcid\b", re.IGNORECASE)),
    ("HCD", re.compile(r"\bhcd\b|higher[-\s]*energy\s+collisional", re.IGNORECASE)),
    ("CID", re.compile(r"\bcid\b|collision[-\s]*induced", re.IGNORECASE)),
    ("ETD", re.compile(r"\betd\b|electron\s+transfer\s+dissociation", re.IGNORECASE)),
    ("ECD", re.compile(r"\becd\b|electron\s+capture\s+dissociation", re.IGNORECASE)),
    ("UVPD", re.compile(r"\buvpd\b|ultraviolet\s+photodissociation", re.IGNORECASE)),
    ("PQD", re.compile(r"\bpqd\b|pulsed\s+q\s+dissociation", re.IGNORECASE)),
    ("SID", re.compile(r"\bsid\b|surface[-\s]*induced", re.IGNORECASE)),
]


class SpectrumEvidenceProfile(JsonModel):
    status: str
    paths_scanned: list[str] = Field(default_factory=list)
    files_scanned: int = 0
    spectra_scanned: int = 0
    rows_scanned: int = 0
    fragmentation_method_counts: dict[str, int] = Field(default_factory=dict)
    fragmentation_methods: list[str] = Field(default_factory=list)
    fragmentation_evidence_level: str = "unknown"
    warnings: list[str] = Field(default_factory=list)


def profile_spectrum_evidence(
    paths: list[str | Path],
    *,
    max_files: int = 4,
    max_spectra: int = 5000,
    max_rows: int = 5000,
    max_seconds: float = 5.0,
) -> SpectrumEvidenceProfile:
    """Conservatively summarize spectrum-level fragmentation evidence with hard scan caps."""
    start = time.monotonic()
    deadline = start + max(float(max_seconds), 0.1)
    counts: Counter[str] = Counter()
    warnings: list[str] = []
    scanned_paths: list[str] = []
    spectra_scanned = 0
    rows_scanned = 0

    for raw_path in [Path(path) for path in paths][: max(int(max_files), 0)]:
        if time.monotonic() >= deadline:
            warnings.append("spectrum_evidence_timeout")
            break
        if not raw_path.exists():
            warnings.append(f"spectrum_evidence_path_missing:{raw_path}")
            continue
        scanned_paths.append(str(raw_path))
        suffix = raw_path.suffix.casefold()
        try:
            if suffix == ".mgf":
                result = _profile_mgf(raw_path, max_spectra=max_spectra - spectra_scanned, deadline=deadline)
                counts.update(result["counts"])
                spectra_scanned += int(result["spectra_scanned"])
                warnings.extend(result["warnings"])
            elif suffix == ".parquet":
                result = _profile_table(raw_path, max_rows=max_rows - rows_scanned, deadline=deadline)
                counts.update(result["counts"])
                rows_scanned += int(result["rows_scanned"])
                warnings.extend(result["warnings"])
            elif suffix in {".tsv", ".txt", ".csv"}:
                result = _profile_table(raw_path, max_rows=max_rows - rows_scanned, deadline=deadline)
                counts.update(result["counts"])
                rows_scanned += int(result["rows_scanned"])
                warnings.extend(result["warnings"])
            else:
                warnings.append(f"unsupported_spectrum_evidence_extension:{suffix}")
        except Exception as exc:
            warnings.append(f"spectrum_evidence_unreadable:{raw_path.name}:{exc}")
        if spectra_scanned >= max_spectra or rows_scanned >= max_rows:
            warnings.append("spectrum_evidence_scan_truncated")
            break

    if len(paths) > max_files:
        warnings.append(f"spectrum_evidence_file_limit:{max_files}")
    methods = sorted(counts)
    if not scanned_paths:
        status = "not_found"
    elif time.monotonic() >= deadline:
        status = "partial"
    else:
        status = "completed"
    if len(methods) > 1:
        level = "mixed"
    elif len(methods) == 1:
        level = "spectrum"
    else:
        level = "unknown"
        if scanned_paths:
            warnings.append("fragmentation_method_not_confirmed_from_spectrum_evidence")
    return SpectrumEvidenceProfile(
        status=status,
        paths_scanned=scanned_paths,
        files_scanned=len(scanned_paths),
        spectra_scanned=spectra_scanned,
        rows_scanned=rows_scanned,
        fragmentation_method_counts=dict(sorted(counts.items())),
        fragmentation_methods=methods,
        fragmentation_evidence_level=level,
        warnings=sorted(set(warnings)),
    )


def detect_fragmentation_method(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for label, pattern in _METHOD_PATTERNS:
        if pattern.search(text):
            return label
    return ""


def detect_fragmentation_from_mapping(mapping: dict[str, Any]) -> str:
    for key, value in mapping.items():
        key_text = str(key or "").casefold()
        if any(token in key_text for token in ["activation", "fragment", "dissociation", "collision"]):
            method = detect_fragmentation_method(value)
            if method:
                return method
    for key in FRAGMENTATION_COLUMNS:
        if key in mapping:
            method = detect_fragmentation_method(mapping.get(key))
            if method:
                return method
    return ""


def _profile_mgf(path: Path, *, max_spectra: int, deadline: float) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    warnings: list[str] = []
    params: dict[str, str] = {}
    spectra_scanned = 0
    in_block = False
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw_line in handle:
            if time.monotonic() >= deadline:
                warnings.append("spectrum_evidence_timeout")
                break
            line = raw_line.strip()
            upper = line.upper()
            if upper == "BEGIN IONS":
                in_block = True
                params = {}
                continue
            if upper == "END IONS" and in_block:
                spectra_scanned += 1
                method = detect_fragmentation_from_mapping(params)
                if method:
                    counts[method] += 1
                in_block = False
                if spectra_scanned >= max_spectra:
                    warnings.append("spectrum_evidence_scan_truncated")
                    break
                continue
            if in_block and "=" in line:
                key, value = line.split("=", 1)
                params[key.strip().upper()] = value.strip()
    return {"counts": counts, "warnings": warnings, "spectra_scanned": spectra_scanned}


def _profile_table(path: Path, *, max_rows: int, deadline: float) -> dict[str, Any]:
    if max_rows <= 0:
        return {"counts": Counter(), "warnings": ["spectrum_evidence_scan_truncated"], "rows_scanned": 0}
    suffix = path.suffix.casefold()
    if suffix == ".parquet":
        frame = _read_parquet_fragmentation_columns(path)
    else:
        sep = "," if suffix == ".csv" else "\t"
        frame = pd.read_csv(path, sep=sep, dtype=str, nrows=max_rows, low_memory=False)
    columns = _fragmentation_columns_from_frame(frame)
    if not columns:
        return {"counts": Counter(), "warnings": [], "rows_scanned": min(len(frame), max_rows)}
    counts: Counter[str] = Counter()
    rows_scanned = 0
    for _, row in frame.loc[:, columns].head(max_rows).iterrows():
        if time.monotonic() >= deadline:
            return {
                "counts": counts,
                "warnings": ["spectrum_evidence_timeout"],
                "rows_scanned": rows_scanned,
            }
        rows_scanned += 1
        method = detect_fragmentation_from_mapping(row.to_dict())
        if method:
            counts[method] += 1
    warnings = ["spectrum_evidence_scan_truncated"] if len(frame) > max_rows else []
    return {"counts": counts, "warnings": warnings, "rows_scanned": rows_scanned}


def _read_parquet_fragmentation_columns(path: Path) -> pd.DataFrame:
    try:
        import pyarrow.parquet as pq

        schema = pq.read_schema(path)
        column_names = [str(name) for name in schema.names]
        columns = [name for name in column_names if _is_fragmentation_column(name)]
        if not columns:
            return pd.DataFrame(columns=column_names)
        return pd.read_parquet(path, columns=columns)
    except Exception:
        return pd.read_parquet(path)


def _fragmentation_columns_from_frame(frame: pd.DataFrame) -> list[str]:
    return [column for column in frame.columns if _is_fragmentation_column(str(column))]


def _is_fragmentation_column(column: str) -> bool:
    text = column.casefold()
    return any(token in text for token in ["activation", "fragment", "dissociation", "collision"])
