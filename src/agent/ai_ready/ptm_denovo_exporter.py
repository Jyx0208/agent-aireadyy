from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import Field

from agent.ai_ready.fragment_intensity_exporter import _load_peaklists, _match_spectrum
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
    _optional_series,
    _series,
    _value_distribution,
)
from agent.models import JsonModel
from agent.utils import write_json
from agent.ai_ready.release_predicates import exporter_status_from_rows


PTM_DENOVO_SCHEMA_VERSION = "ptm_denovo_train_v0"
PTM_DENOVO_COLUMNS = [
    "project_accession",
    "source_file",
    "search_result_path",
    "peaklist_path",
    "spectrum_id",
    "peptide_sequence",
    "modified_sequence",
    "modification_tokens_json",
    "localization_confidence",
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
    "spectrum_mz_json",
    "spectrum_intensity_json",
    "label_source",
]
LOCALIZATION_COLUMNS = [
    "localization probability",
    "localization score",
    "ptm localization",
    "ptm localization probability",
    "site probability",
    "phospho probability",
    "phosphosite probability",
    "best localization probability",
    "ascore",
    "ptm score",
]


class PtmDenovoInput(JsonModel):
    path: str
    rows_in: int = 0
    rows_out: int = 0
    column_map: dict[str, str | None] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    filter_counts: dict[str, int] = Field(default_factory=dict)


class PtmDenovoExportResult(JsonModel):
    status: str
    schema_version: str = PTM_DENOVO_SCHEMA_VERSION
    output_parquet: str
    preview_csv: str
    report_json: str
    schema_json_path: str
    rows_in: int = 0
    rows_out: int = 0
    filter_counts: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    inputs: list[PtmDenovoInput] = Field(default_factory=list)


def export_ptm_denovo_ai_ready(
    search_results: list[str | Path],
    peaklists: list[str | Path],
    output_dir: str | Path,
    *,
    project_accession: str | None = None,
    source_file: str | None = None,
    task_build_plan: str | Path | None = None,
    q_value_threshold: float = 0.01,
    probability_threshold: float = 0.9,
    search_engine: str | None = None,
) -> PtmDenovoExportResult:
    if not search_results:
        raise ValueError("At least one --search-result is required.")
    if not peaklists:
        raise ValueError("At least one --peaklist MGF path is required.")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    spectra, peaklist_warnings = _load_peaklists([Path(path) for path in peaklists])
    metadata = _load_task_build_metadata(task_build_plan)

    frames: list[pd.DataFrame] = []
    input_reports: list[PtmDenovoInput] = []
    warnings: list[str] = list(peaklist_warnings)
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
            search_engine=search_engine,
        )
        input_reports.append(report)
        warnings.extend(report.warnings)
        total_filter_counts.update(report.filter_counts)
        if not frame.empty:
            frames.append(frame)

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=PTM_DENOVO_COLUMNS)
    combined = combined.loc[:, PTM_DENOVO_COLUMNS]
    output_parquet = output_dir / "ptm_denovo_train.parquet"
    preview_csv = output_dir / "ptm_denovo.preview.csv"
    report_json = output_dir / "ptm_denovo_export_report.json"
    schema_json = output_dir / "ptm_denovo_schema.json"
    combined.to_parquet(output_parquet, index=False)
    combined.head(100).to_csv(preview_csv, index=False)
    write_json(schema_json, _schema_payload())

    rows_in = sum(item.rows_in for item in input_reports)
    rows_out = int(len(combined))
    export_status = exporter_status_from_rows(rows_out)
    report = {
        "status": export_status,
        "schema_version": PTM_DENOVO_SCHEMA_VERSION,
        "rows_in": rows_in,
        "rows_out": rows_out,
        "rows_filtered": rows_in - rows_out,
        "filter_counts": dict(sorted(total_filter_counts.items())),
        "warnings": sorted(set(warnings)),
        "spectrum_count": _unique_spectrum_count(spectra),
        "charge_distribution": _value_distribution(combined.get("charge")),
        "search_result_count": len(input_reports),
        "inputs": [item.model_dump(mode="json") for item in input_reports],
        "outputs": {
            "ptm_denovo_train_parquet": str(output_parquet),
            "preview_csv": str(preview_csv),
            "schema_json": str(schema_json),
        },
    }
    write_json(report_json, report)
    return PtmDenovoExportResult(
        status=export_status,
        output_parquet=str(output_parquet),
        preview_csv=str(preview_csv),
        report_json=str(report_json),
        schema_json_path=str(schema_json),
        rows_in=rows_in,
        rows_out=rows_out,
        filter_counts=dict(sorted(total_filter_counts.items())),
        warnings=sorted(set(warnings)),
        inputs=input_reports,
    )


def _load_one_result(
    path: Path,
    *,
    spectra: dict[str, Any],
    metadata: dict[str, dict[str, Any]],
    project_accession: str | None,
    source_file: str | None,
    q_value_threshold: float,
    probability_threshold: float,
    search_engine: str | None,
) -> tuple[pd.DataFrame, PtmDenovoInput]:
    if not path.exists():
        raise ValueError(f"Search result does not exist: {path}")
    frame = pd.read_csv(path, sep="\t", dtype=str, low_memory=False)
    column_map = _detect_columns(frame.columns)
    missing = [
        field
        for field in ["peptide_sequence", "modified_sequence", "charge", "spectrum_id"]
        if column_map.get(field) is None
    ]
    warnings: list[str] = []
    if column_map.get("localization_confidence") is None:
        warnings.append("localization_confidence_missing")
    if missing:
        report = PtmDenovoInput(
            path=str(path),
            rows_in=int(len(frame)),
            rows_out=0,
            column_map=column_map,
            warnings=[f"missing_required_column:{field}" for field in missing] + warnings,
            filter_counts={"missing_required_columns": int(len(frame))},
        )
        return pd.DataFrame(columns=PTM_DENOVO_COLUMNS), report

    output = pd.DataFrame()
    output["peptide_sequence"] = _series(frame, column_map["peptide_sequence"]).map(_clean_sequence)
    output["modified_sequence"] = _series(frame, column_map["modified_sequence"]).map(_clean_text)
    output["charge"] = pd.to_numeric(_series(frame, column_map["charge"]), errors="coerce")
    output["spectrum_id"] = _series(frame, column_map["spectrum_id"]).map(_clean_text)
    output["q_value"] = pd.to_numeric(_optional_series(frame, column_map.get("q_value")), errors="coerce")
    output["psm_probability"] = pd.to_numeric(_optional_series(frame, column_map.get("psm_probability")), errors="coerce")
    output["localization_confidence"] = _optional_series(frame, column_map.get("localization_confidence")).map(_clean_text)

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
        source_metadata[str(source)][0].get("fragmentation_method")
        or source_metadata[str(source)][0].get("fragmentation_methods")
        or ""
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
    rows: list[dict[str, Any]] = []
    for row in output.to_dict(orient="records"):
        reason = _row_filter_reason(row, q_value_threshold=q_value_threshold, probability_threshold=probability_threshold)
        if reason:
            filter_counts[reason] += 1
            continue
        modification_tokens = _modification_tokens(row.get("modified_sequence"))
        if not modification_tokens:
            filter_counts["missing_modified_sequence"] += 1
            continue
        spectrum = _match_spectrum(str(row["spectrum_id"]), spectra)
        if spectrum is None:
            filter_counts["spectrum_not_matched"] += 1
            warnings.append("spectrum_not_matched")
            continue
        rows.append(
            {
                **row,
                "modification_tokens_json": json.dumps(modification_tokens, ensure_ascii=False),
                "peaklist_path": spectrum.path,
                "precursor_mz": spectrum.precursor_mz,
                "spectrum_mz_json": json.dumps(spectrum.mz, ensure_ascii=False),
                "spectrum_intensity_json": json.dumps(spectrum.intensity, ensure_ascii=False),
                "label_source": "high_confidence_modified_psm_tsv_plus_mgf",
            }
        )

    filtered = pd.DataFrame(rows, columns=PTM_DENOVO_COLUMNS)
    report = PtmDenovoInput(
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
        "localization_confidence": _find_column(available, LOCALIZATION_COLUMNS),
    }


def _row_filter_reason(row: dict[str, Any], *, q_value_threshold: float, probability_threshold: float) -> str | None:
    if not row.get("peptide_sequence"):
        return "missing_peptide_sequence"
    if not row.get("modified_sequence"):
        return "missing_modified_sequence"
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


def _modification_tokens(value: Any) -> list[str]:
    text = _clean_text(value)
    if not text:
        return []
    tokens: list[str] = []
    bracket_tokens = re.findall(r"\[[^\]]+\]|\([^)]+\)|\{[^}]+\}", text)
    tokens.extend(bracket_tokens)
    remainder = re.sub(r"\[[^\]]+\]|\([^)]+\)|\{[^}]+\}", " ", text)
    tokens.extend(re.findall(r"[+-]\d+(?:\.\d+)?", remainder))
    lower = text.casefold()
    for token in ["phospho", "acetyl", "glyco", "methyl", "oxidation", "ubiquitin", "carbamidomethyl"]:
        if token in lower:
            tokens.append(token)
    return _dedupe(tokens)


def _unique_spectrum_count(spectra: dict[str, Any]) -> int:
    unique = {(item.path, item.title, item.scan) for item in spectra.values()}
    return len(unique)


def _schema_payload() -> dict[str, Any]:
    return {
        "schema_version": PTM_DENOVO_SCHEMA_VERSION,
        "target_schema": "ptm_denovo_train.parquet",
        "columns": {
            "modified_sequence": "Modified peptide sequence label from a high-confidence PTM search result row.",
            "modification_tokens_json": "Detected modification tokens from the modified sequence string.",
            "localization_confidence": "PTM localization confidence if the source table provides it.",
            "spectrum_mz_json": "JSON array of MGF peak m/z values.",
            "spectrum_intensity_json": "JSON array of MGF peak intensities.",
            "label_source": "high_confidence_modified_psm_tsv_plus_mgf for v0 rows.",
        },
        "notes": [
            "v0 creates supervised modified spectrum-sequence pairs from existing PTM search results.",
            "v0 does not infer PTM localization; it preserves localization confidence when present.",
            "v0 reads MGF only and does not parse mzML directly.",
        ],
    }


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
