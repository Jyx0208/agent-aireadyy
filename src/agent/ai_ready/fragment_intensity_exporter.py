from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import Field

from agent.ai_ready.spectrum_evidence import detect_fragmentation_from_mapping, profile_spectrum_evidence
from agent.ai_ready.rt_exporter import (
    CHARGE_COLUMNS,
    MODIFIED_COLUMNS,
    PEPTIDE_COLUMNS,
    PROBABILITY_COLUMNS,
    Q_VALUE_COLUMNS,
    SEARCH_ENGINE_COLUMNS,
    SOURCE_FILE_COLUMNS,
    SPECTRUM_COLUMNS,
    _clean_text,
    _find_column,
    _first_nonempty,
    _guess_search_engine,
    _load_task_build_metadata,
    _metadata_by_source,
    _numeric_distribution,
    _optional_series,
    _series,
)
from agent.models import JsonModel
from agent.utils import write_json


FRAGMENT_INTENSITY_SCHEMA_VERSION = "fragment_intensity_train_v0"
FRAGMENT_INTENSITY_COLUMNS = [
    "project_accession",
    "source_file",
    "search_result_path",
    "peaklist_path",
    "spectrum_id",
    "peptide_sequence",
    "modified_sequence",
    "charge",
    "precursor_mz",
    "q_value",
    "psm_probability",
    "search_engine",
    "species",
    "canonical_species",
    "organism_taxon_id",
    "instrument_family",
    "fragmentation_method",
    "ptm_type",
    "modification_scope",
    "labeling_strategy",
    "matched_ions_json",
    "spectrum_mz_json",
    "spectrum_intensity_json",
    "label_source",
]

AA_MASS = {
    "A": 71.037114,
    "R": 156.101111,
    "N": 114.042927,
    "D": 115.026943,
    "C": 103.009185,
    "E": 129.042593,
    "Q": 128.058578,
    "G": 57.021464,
    "H": 137.058912,
    "I": 113.084064,
    "L": 113.084064,
    "K": 128.094963,
    "M": 131.040485,
    "F": 147.068414,
    "P": 97.052764,
    "S": 87.032028,
    "T": 101.047679,
    "W": 186.079313,
    "Y": 163.063329,
    "V": 99.068414,
}
PROTON = 1.007276
H2O = 18.010565


@dataclass
class MgfSpectrum:
    path: str
    title: str
    scan: str
    precursor_mz: float | None
    charge: int | None
    fragmentation_method: str
    mz: list[float]
    intensity: list[float]


class FragmentIntensityInput(JsonModel):
    path: str
    rows_in: int = 0
    rows_out: int = 0
    column_map: dict[str, str | None] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    filter_counts: dict[str, int] = Field(default_factory=dict)


class FragmentIntensityExportResult(JsonModel):
    status: str
    schema_version: str = FRAGMENT_INTENSITY_SCHEMA_VERSION
    output_parquet: str
    preview_csv: str
    report_json: str
    schema_json_path: str
    rows_in: int = 0
    rows_out: int = 0
    filter_counts: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    inputs: list[FragmentIntensityInput] = Field(default_factory=list)
    spectrum_evidence: dict[str, Any] = Field(default_factory=dict)


def export_fragment_intensity_ai_ready(
    search_results: list[str | Path],
    peaklists: list[str | Path],
    output_dir: str | Path,
    *,
    project_accession: str | None = None,
    source_file: str | None = None,
    task_build_plan: str | Path | None = None,
    q_value_threshold: float = 0.01,
    probability_threshold: float = 0.9,
    fragment_tolerance_da: float = 0.5,
    search_engine: str | None = None,
) -> FragmentIntensityExportResult:
    if not search_results:
        raise ValueError("At least one --search-result is required.")
    if not peaklists:
        raise ValueError("At least one --peaklist MGF path is required.")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    peaklist_paths = [Path(path) for path in peaklists]
    spectrum_evidence = profile_spectrum_evidence(peaklist_paths)
    spectra, peaklist_warnings = _load_peaklists(peaklist_paths)
    metadata = _load_task_build_metadata(task_build_plan)

    frames: list[pd.DataFrame] = []
    input_reports: list[FragmentIntensityInput] = []
    warnings: list[str] = list(peaklist_warnings) + list(spectrum_evidence.warnings)
    total_filter_counts: Counter[str] = Counter()
    for search_result in search_results:
        frame, report = _load_one_result(
            Path(search_result),
            spectra=spectra,
            metadata=metadata,
            project_accession=project_accession,
            source_file=source_file,
            q_value_threshold=q_value_threshold,
            probability_threshold=probability_threshold,
            fragment_tolerance_da=fragment_tolerance_da,
            search_engine=search_engine,
        )
        input_reports.append(report)
        warnings.extend(report.warnings)
        total_filter_counts.update(report.filter_counts)
        if not frame.empty:
            frames.append(frame)

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=FRAGMENT_INTENSITY_COLUMNS)
    combined = combined.loc[:, FRAGMENT_INTENSITY_COLUMNS]

    output_parquet = output_dir / "fragment_intensity_train.parquet"
    preview_csv = output_dir / "fragment_intensity.preview.csv"
    report_json = output_dir / "fragment_intensity_export_report.json"
    schema_json = output_dir / "fragment_intensity_schema.json"
    combined.to_parquet(output_parquet, index=False)
    combined.head(100).to_csv(preview_csv, index=False)
    write_json(schema_json, _schema_payload())

    rows_in = sum(item.rows_in for item in input_reports)
    rows_out = int(len(combined))
    report = {
        "status": "completed",
        "schema_version": FRAGMENT_INTENSITY_SCHEMA_VERSION,
        "rows_in": rows_in,
        "rows_out": rows_out,
        "rows_filtered": rows_in - rows_out,
        "filter_counts": dict(sorted(total_filter_counts.items())),
        "warnings": sorted(set(warnings)),
        "spectrum_evidence": spectrum_evidence.model_dump(mode="json"),
        "spectrum_count": len(spectra),
        "matched_ion_count": _matched_ion_count(combined),
        "charge_distribution": _value_distribution(combined.get("charge")),
        "matched_ions_per_row": _matched_ions_per_row_distribution(combined),
        "inputs": [item.model_dump(mode="json") for item in input_reports],
        "outputs": {
            "fragment_intensity_train_parquet": str(output_parquet),
            "preview_csv": str(preview_csv),
            "schema_json": str(schema_json),
        },
    }
    write_json(report_json, report)
    return FragmentIntensityExportResult(
        status="completed",
        output_parquet=str(output_parquet),
        preview_csv=str(preview_csv),
        report_json=str(report_json),
        schema_json_path=str(schema_json),
        rows_in=rows_in,
        rows_out=rows_out,
        filter_counts=dict(sorted(total_filter_counts.items())),
        warnings=sorted(set(warnings)),
        inputs=input_reports,
        spectrum_evidence=spectrum_evidence.model_dump(mode="json"),
    )


def _load_one_result(
    path: Path,
    *,
    spectra: dict[str, MgfSpectrum],
    metadata: dict[str, dict[str, Any]],
    project_accession: str | None,
    source_file: str | None,
    q_value_threshold: float,
    probability_threshold: float,
    fragment_tolerance_da: float,
    search_engine: str | None,
) -> tuple[pd.DataFrame, FragmentIntensityInput]:
    if not path.exists():
        raise ValueError(f"Search result does not exist: {path}")
    frame = pd.read_csv(path, sep="\t", dtype=str, low_memory=False)
    column_map = _detect_columns(frame.columns)
    missing = [field for field in ["peptide_sequence", "charge", "spectrum_id"] if column_map.get(field) is None]
    if missing:
        report = FragmentIntensityInput(
            path=str(path),
            rows_in=int(len(frame)),
            rows_out=0,
            column_map=column_map,
            warnings=[f"missing_required_column:{field}" for field in missing],
            filter_counts={"missing_required_columns": int(len(frame))},
        )
        return pd.DataFrame(columns=FRAGMENT_INTENSITY_COLUMNS), report

    output = pd.DataFrame()
    output["peptide_sequence"] = _series(frame, column_map["peptide_sequence"]).map(_clean_sequence)
    modified_column = column_map.get("modified_sequence")
    output["modified_sequence"] = _series(frame, modified_column).map(_clean_text) if modified_column else output["peptide_sequence"]
    output["charge"] = pd.to_numeric(_series(frame, column_map["charge"]), errors="coerce")
    output["spectrum_id"] = _series(frame, column_map["spectrum_id"]).map(_clean_text)
    output["q_value"] = pd.to_numeric(_optional_series(frame, column_map.get("q_value")), errors="coerce")
    output["psm_probability"] = pd.to_numeric(_optional_series(frame, column_map.get("psm_probability")), errors="coerce")

    source_series = _optional_series(frame, column_map.get("source_file")).map(_clean_text)
    if source_file:
        output["source_file"] = source_file
    elif column_map.get("source_file") is not None:
        output["source_file"] = source_series.replace("", path.stem)
    else:
        output["source_file"] = path.stem
    source_metadata = _metadata_by_source(metadata, output["source_file"], path.stem)
    output["project_accession"] = [
        project_accession or source_metadata[str(source)][0].get("project_accession") or ""
        for source in output["source_file"]
    ]
    output["search_result_path"] = str(path)
    output["search_engine"] = search_engine or _first_nonempty(_optional_series(frame, column_map.get("search_engine"))) or _guess_search_engine(path)
    output["species"] = [source_metadata[str(source)][0].get("species") or "" for source in output["source_file"]]
    output["canonical_species"] = [
        source_metadata[str(source)][0].get("canonical_species") or "" for source in output["source_file"]
    ]
    output["organism_taxon_id"] = [
        source_metadata[str(source)][0].get("organism_taxon_id") or "" for source in output["source_file"]
    ]
    output["instrument_family"] = [
        source_metadata[str(source)][0].get("instrument_family") or "" for source in output["source_file"]
    ]
    output["fragmentation_method"] = [
        source_metadata[str(source)][0].get("fragmentation_method") or source_metadata[str(source)][0].get("fragmentation_methods") or ""
        for source in output["source_file"]
    ]
    output["ptm_type"] = [source_metadata[str(source)][0].get("ptm_type") or "" for source in output["source_file"]]
    output["modification_scope"] = [
        source_metadata[str(source)][0].get("modification_scope") or "" for source in output["source_file"]
    ]
    output["labeling_strategy"] = [
        source_metadata[str(source)][0].get("labeling_strategy") or "" for source in output["source_file"]
    ]

    filter_counts: Counter[str] = Counter()
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    for row in output.to_dict(orient="records"):
        reason = _row_filter_reason(row, q_value_threshold=q_value_threshold, probability_threshold=probability_threshold)
        if reason:
            filter_counts[reason] += 1
            continue
        if _has_unsafe_modification(str(row["peptide_sequence"]), str(row["modified_sequence"])):
            filter_counts["unsupported_modified_peptide"] += 1
            warnings.append("modified_peptide_not_annotated")
            continue
        spectrum = _match_spectrum(str(row["spectrum_id"]), spectra)
        if spectrum is None:
            filter_counts["spectrum_not_matched"] += 1
            warnings.append("spectrum_not_matched")
            continue
        if not str(row.get("fragmentation_method") or "").strip() and spectrum.fragmentation_method:
            row["fragmentation_method"] = spectrum.fragmentation_method
        matched = _match_theoretical_ions(str(row["peptide_sequence"]), spectrum, tolerance_da=fragment_tolerance_da)
        if not matched:
            filter_counts["no_matched_fragments"] += 1
            warnings.append("no_matched_fragments")
            continue
        rows.append(
            {
                **row,
                "peaklist_path": _spectrum_path_hint(spectrum, peaklists_from_index=spectra),
                "precursor_mz": spectrum.precursor_mz,
                "matched_ions_json": json.dumps(matched, ensure_ascii=False),
                "spectrum_mz_json": json.dumps(spectrum.mz, ensure_ascii=False),
                "spectrum_intensity_json": json.dumps(spectrum.intensity, ensure_ascii=False),
                "label_source": "psm_tsv_plus_mgf_b_y_annotation",
            }
        )

    filtered = pd.DataFrame(rows, columns=FRAGMENT_INTENSITY_COLUMNS)
    report = FragmentIntensityInput(
        path=str(path),
        rows_in=int(len(frame)),
        rows_out=int(len(filtered)),
        column_map=column_map,
        warnings=sorted(set(warnings)),
        filter_counts={key: value for key, value in sorted(filter_counts.items()) if value},
    )
    return filtered, report


def _detect_columns(columns: pd.Index) -> dict[str, str | None]:
    available = list(columns)
    return {
        "peptide_sequence": _find_column(available, PEPTIDE_COLUMNS),
        "modified_sequence": _find_column(available, MODIFIED_COLUMNS),
        "charge": _find_column(available, CHARGE_COLUMNS),
        "spectrum_id": _find_column(available, SPECTRUM_COLUMNS),
        "source_file": _find_column(available, SOURCE_FILE_COLUMNS),
        "q_value": _find_column(available, Q_VALUE_COLUMNS),
        "psm_probability": _find_column(available, PROBABILITY_COLUMNS),
        "search_engine": _find_column(available, SEARCH_ENGINE_COLUMNS),
    }


def _load_peaklists(paths: list[Path]) -> tuple[dict[str, MgfSpectrum], list[str]]:
    spectra: dict[str, MgfSpectrum] = {}
    warnings: list[str] = []
    for path in paths:
        if not path.exists():
            raise ValueError(f"Peaklist does not exist: {path}")
        if path.suffix.casefold() != ".mgf":
            warnings.append(f"unsupported_peaklist_extension:{path.suffix}")
            continue
        for spectrum in _parse_mgf(path):
            for key in _spectrum_keys(spectrum):
                spectra.setdefault(key, spectrum)
    if not spectra:
        warnings.append("no_mgf_spectra_loaded")
    return spectra, warnings


def _parse_mgf(path: Path) -> list[MgfSpectrum]:
    spectra: list[MgfSpectrum] = []
    params: dict[str, str] = {}
    mz: list[float] = []
    intensity: list[float] = []
    in_block = False
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        upper = line.upper()
        if upper == "BEGIN IONS":
            in_block = True
            params = {}
            mz = []
            intensity = []
            continue
        if upper == "END IONS" and in_block:
            spectra.append(
                MgfSpectrum(
                    path=str(path),
                    title=params.get("TITLE", ""),
                    scan=params.get("SCANS", ""),
                    precursor_mz=_first_float(params.get("PEPMASS", "")),
                    charge=_first_int(params.get("CHARGE", "")),
                    fragmentation_method=detect_fragmentation_from_mapping(params),
                    mz=mz,
                    intensity=intensity,
                )
            )
            in_block = False
            continue
        if not in_block:
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            params[key.strip().upper()] = value.strip()
            continue
        parts = line.split()
        if len(parts) >= 2:
            try:
                mz.append(float(parts[0]))
                intensity.append(float(parts[1]))
            except ValueError:
                continue
    return spectra


def _spectrum_keys(spectrum: MgfSpectrum) -> list[str]:
    keys = [spectrum.title, spectrum.scan]
    keys.extend(re.findall(r"scan=?\s*(\d+)", spectrum.title, flags=re.IGNORECASE))
    keys.extend(re.findall(r"scan[:=](\d+)", spectrum.title, flags=re.IGNORECASE))
    keys.extend(_dot_scan_keys(spectrum.title))
    return [_key(item) for item in keys if item]


def _match_spectrum(spectrum_id: str, spectra: dict[str, MgfSpectrum]) -> MgfSpectrum | None:
    candidates = [spectrum_id, Path(spectrum_id).stem]
    candidates.extend(re.findall(r"scan=?\s*(\d+)", spectrum_id, flags=re.IGNORECASE))
    candidates.extend(re.findall(r"scan[:=](\d+)", spectrum_id, flags=re.IGNORECASE))
    candidates.extend(_dot_scan_keys(spectrum_id))
    for candidate in candidates:
        key = _key(candidate)
        if key in spectra:
            return spectra[key]
    return None


def _dot_scan_keys(value: str) -> list[str]:
    text = str(value or "")
    keys: list[str] = []
    for match in re.finditer(r"\.(\d{3,})\.(?:\d{3,})(?:\.|$)", text):
        raw_scan = match.group(1)
        keys.append(raw_scan)
        try:
            keys.append(str(int(raw_scan)))
        except ValueError:
            pass
    return keys


def _key(value: str) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _row_filter_reason(row: dict[str, Any], *, q_value_threshold: float, probability_threshold: float) -> str | None:
    if not row.get("peptide_sequence"):
        return "missing_peptide_sequence"
    if pd.isna(row.get("charge")):
        return "missing_charge"
    if not row.get("spectrum_id"):
        return "missing_spectrum_id"
    q_value = row.get("q_value")
    if not pd.isna(q_value) and float(q_value) > q_value_threshold:
        return "q_value_above_threshold"
    probability = row.get("psm_probability")
    if not pd.isna(probability) and float(probability) < probability_threshold:
        return "probability_below_threshold"
    return None


def _clean_sequence(value: Any) -> str:
    text = _clean_text(value).upper()
    return "".join(char for char in text if char.isalpha())


def _has_unsafe_modification(peptide_sequence: str, modified_sequence: str) -> bool:
    if not modified_sequence:
        return False
    cleaned_modified = _clean_sequence(modified_sequence)
    if cleaned_modified != peptide_sequence:
        return True
    return bool(re.search(r"[\[\]\(\)\+\-\d]", modified_sequence))


def _match_theoretical_ions(peptide: str, spectrum: MgfSpectrum, *, tolerance_da: float) -> list[dict[str, float | str]]:
    if len(peptide) < 2 or any(aa not in AA_MASS for aa in peptide):
        return []
    ions: list[tuple[str, float]] = []
    prefix = 0.0
    total = sum(AA_MASS[aa] for aa in peptide)
    for index, aa in enumerate(peptide[:-1], start=1):
        prefix += AA_MASS[aa]
        suffix = total - prefix
        ions.append((f"b{index}", prefix + PROTON))
        ions.append((f"y{len(peptide) - index}", suffix + H2O + PROTON))
    matched: list[dict[str, float | str]] = []
    for label, theoretical_mz in ions:
        best_index = _nearest_peak(theoretical_mz, spectrum.mz, tolerance_da=tolerance_da)
        if best_index is None:
            continue
        matched.append(
            {
                "ion": label,
                "theoretical_mz": round(theoretical_mz, 6),
                "observed_mz": spectrum.mz[best_index],
                "intensity": spectrum.intensity[best_index],
            }
        )
    return matched


def _nearest_peak(target_mz: float, mz_values: list[float], *, tolerance_da: float) -> int | None:
    best_index: int | None = None
    best_error = tolerance_da
    for index, value in enumerate(mz_values):
        error = abs(value - target_mz)
        if error <= best_error:
            best_index = index
            best_error = error
    return best_index


def _first_float(value: str) -> float | None:
    for token in str(value or "").replace(",", " ").split():
        try:
            return float(token)
        except ValueError:
            continue
    return None


def _first_int(value: str) -> int | None:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else None


def _spectrum_path_hint(spectrum: MgfSpectrum, *, peaklists_from_index: dict[str, MgfSpectrum]) -> str:
    return spectrum.path


def _matched_ion_count(frame: pd.DataFrame) -> int:
    total = 0
    for value in frame.get("matched_ions_json", []):
        try:
            total += len(json.loads(value))
        except (TypeError, json.JSONDecodeError):
            continue
    return total


def _matched_ions_per_row_distribution(frame: pd.DataFrame) -> dict[str, float | int | None]:
    counts: list[int] = []
    for value in frame.get("matched_ions_json", []):
        try:
            counts.append(len(json.loads(value)))
        except (TypeError, json.JSONDecodeError):
            continue
    return _numeric_distribution(pd.Series(counts))


def _value_distribution(series: pd.Series | None) -> dict[str, int]:
    if series is None:
        return {}
    return {str(key): int(value) for key, value in sorted(Counter(series.dropna().astype(str)).items())}


def _schema_payload() -> dict[str, Any]:
    return {
        "schema_version": FRAGMENT_INTENSITY_SCHEMA_VERSION,
        "target_schema": "fragment_intensity_train.parquet",
        "columns": {
            "peptide_sequence": "Unmodified peptide sequence used for theoretical b/y ion annotation.",
            "matched_ions_json": "JSON list of matched b/y ions with theoretical m/z, observed m/z, and intensity.",
            "spectrum_mz_json": "JSON array of MGF peak m/z values.",
            "spectrum_intensity_json": "JSON array of MGF peak intensities.",
            "label_source": "psm_tsv_plus_mgf_b_y_annotation for v0 rows.",
        },
        "notes": [
            "v0 reads MGF only and does not parse mzML directly.",
            "Modified peptide strings are not annotated unless they are equivalent to the unmodified sequence.",
        ],
    }
