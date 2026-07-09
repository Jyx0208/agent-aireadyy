from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import Field

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


PSM_SCORING_SCHEMA_VERSION = "psm_scoring_train_v0"
PSM_SCORING_COLUMNS = [
    "project_accession",
    "source_file",
    "search_result_path",
    "spectrum_id",
    "peptide_sequence",
    "modified_sequence",
    "charge",
    "is_decoy",
    "target_decoy_label",
    "q_value",
    "psm_probability",
    "search_engine",
    "score_features_json",
    "species",
    "canonical_species",
    "organism_taxon_id",
    "instrument_family",
    "ptm_type",
    "modification_scope",
    "labeling_strategy",
    "label_source",
]

TARGET_DECOY_COLUMNS = [
    "target decoy",
    "target/decoy",
    "label",
    "decoy",
    "is decoy",
    "reverse",
]
PROTEIN_COLUMNS = ["protein", "proteins", "protein id", "mapped proteins", "protein accession"]
CORE_COLUMN_KEYS = {
    "peptide_sequence",
    "modified_sequence",
    "charge",
    "spectrum_id",
    "source_file",
    "q_value",
    "psm_probability",
    "search_engine",
    "target_decoy",
    "protein",
}


class PsmScoringInput(JsonModel):
    path: str
    rows_in: int = 0
    rows_out: int = 0
    column_map: dict[str, str | None] = Field(default_factory=dict)
    score_columns: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    filter_counts: dict[str, int] = Field(default_factory=dict)


class PsmScoringExportResult(JsonModel):
    status: str
    schema_version: str = PSM_SCORING_SCHEMA_VERSION
    output_parquet: str
    preview_csv: str
    report_json: str
    schema_json_path: str
    rows_in: int = 0
    rows_out: int = 0
    target_count: int = 0
    decoy_count: int = 0
    filter_counts: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    inputs: list[PsmScoringInput] = Field(default_factory=list)


def export_psm_scoring_ai_ready(
    search_results: list[str | Path],
    output_dir: str | Path,
    *,
    project_accession: str | None = None,
    source_file: str | None = None,
    task_build_plan: str | Path | None = None,
    require_target_decoy: bool = True,
    search_engine: str | None = None,
) -> PsmScoringExportResult:
    if not search_results:
        raise ValueError("At least one --search-result is required.")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = _load_task_build_metadata(task_build_plan)

    frames: list[pd.DataFrame] = []
    input_reports: list[PsmScoringInput] = []
    warnings: list[str] = []
    total_filter_counts: Counter[str] = Counter()
    for search_result in search_results:
        frame, report = _load_one_result(
            Path(search_result),
            metadata=metadata,
            project_accession=project_accession,
            source_file=source_file,
            require_target_decoy=require_target_decoy,
            search_engine=search_engine,
        )
        input_reports.append(report)
        warnings.extend(report.warnings)
        total_filter_counts.update(report.filter_counts)
        if not frame.empty:
            frames.append(frame)

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=PSM_SCORING_COLUMNS)
    combined = combined.loc[:, PSM_SCORING_COLUMNS]
    output_parquet = output_dir / "psm_scoring_train.parquet"
    preview_csv = output_dir / "psm_scoring.preview.csv"
    report_json = output_dir / "psm_scoring_export_report.json"
    schema_json = output_dir / "psm_scoring_schema.json"

    combined.to_parquet(output_parquet, index=False)
    combined.head(100).to_csv(preview_csv, index=False)
    write_json(schema_json, _schema_payload())

    rows_in = sum(item.rows_in for item in input_reports)
    rows_out = int(len(combined))
    decoy_count = int(combined["is_decoy"].fillna(False).sum()) if rows_out else 0
    target_count = rows_out - decoy_count
    report = {
        "status": "completed",
        "schema_version": PSM_SCORING_SCHEMA_VERSION,
        "rows_in": rows_in,
        "rows_out": rows_out,
        "rows_filtered": rows_in - rows_out,
        "target_count": target_count,
        "decoy_count": decoy_count,
        "filter_counts": dict(sorted(total_filter_counts.items())),
        "warnings": sorted(set(warnings)),
        "charge_distribution": _value_distribution(combined.get("charge")),
        "score_columns": sorted(set().union(*(set(item.score_columns) for item in input_reports))) if input_reports else [],
        "inputs": [item.model_dump(mode="json") for item in input_reports],
        "outputs": {
            "psm_scoring_train_parquet": str(output_parquet),
            "preview_csv": str(preview_csv),
            "schema_json": str(schema_json),
        },
    }
    write_json(report_json, report)
    return PsmScoringExportResult(
        status="completed",
        output_parquet=str(output_parquet),
        preview_csv=str(preview_csv),
        report_json=str(report_json),
        schema_json_path=str(schema_json),
        rows_in=rows_in,
        rows_out=rows_out,
        target_count=target_count,
        decoy_count=decoy_count,
        filter_counts=dict(sorted(total_filter_counts.items())),
        warnings=sorted(set(warnings)),
        inputs=input_reports,
    )


def _load_one_result(
    path: Path,
    *,
    metadata: dict[str, dict[str, Any]],
    project_accession: str | None,
    source_file: str | None,
    require_target_decoy: bool,
    search_engine: str | None,
) -> tuple[pd.DataFrame, PsmScoringInput]:
    if not path.exists():
        raise ValueError(f"Search result does not exist: {path}")
    frame = pd.read_csv(path, sep="\t", dtype=str, low_memory=False)
    column_map = _detect_columns(frame.columns)
    missing = [field for field in ["peptide_sequence", "charge", "spectrum_id"] if column_map.get(field) is None]
    if column_map.get("target_decoy") is None and column_map.get("protein") is None:
        if require_target_decoy:
            raise ValueError(f"target_decoy_label_missing: {path}")
        missing.append("target_decoy")
    if missing:
        report = PsmScoringInput(
            path=str(path),
            rows_in=int(len(frame)),
            rows_out=0,
            column_map=column_map,
            warnings=[f"missing_required_column:{field}" for field in missing],
            filter_counts={"missing_required_columns": int(len(frame))},
        )
        return pd.DataFrame(columns=PSM_SCORING_COLUMNS), report

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
    output["ptm_type"] = [source_metadata[str(source)][0].get("ptm_type") or "" for source in output["source_file"]]
    output["modification_scope"] = [
        source_metadata[str(source)][0].get("modification_scope") or "" for source in output["source_file"]
    ]
    output["labeling_strategy"] = [
        source_metadata[str(source)][0].get("labeling_strategy") or "" for source in output["source_file"]
    ]
    decoy_series = _decoy_series(frame, column_map)
    output["is_decoy"] = decoy_series
    output["target_decoy_label"] = decoy_series.map(lambda value: 0 if bool(value) else 1)

    score_columns = _score_columns(frame, column_map)
    output["score_features_json"] = [
        json.dumps(_score_payload(frame.iloc[index], score_columns), ensure_ascii=False)
        for index in range(len(frame))
    ]
    output["label_source"] = "target_decoy_search_result_tsv"

    keep = pd.Series(True, index=output.index)
    filter_counts: Counter[str] = Counter()
    for column, reason in [
        ("peptide_sequence", "missing_peptide_sequence"),
        ("charge", "missing_charge"),
        ("spectrum_id", "missing_spectrum_id"),
    ]:
        mask = output[column].isna() | (output[column].astype(str).str.strip() == "")
        filter_counts[reason] = int(mask.sum())
        keep &= ~mask
    filtered = output.loc[keep].copy()
    filtered["charge"] = filtered["charge"].astype("Int64")
    report = PsmScoringInput(
        path=str(path),
        rows_in=int(len(frame)),
        rows_out=int(len(filtered)),
        column_map=column_map,
        score_columns=score_columns,
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
        "target_decoy": _find_column(available, TARGET_DECOY_COLUMNS),
        "protein": _find_column(available, PROTEIN_COLUMNS),
    }


def _decoy_series(frame: pd.DataFrame, column_map: dict[str, str | None]) -> pd.Series:
    target_decoy_column = column_map.get("target_decoy")
    if target_decoy_column:
        return frame[target_decoy_column].fillna("").map(_is_decoy_value)
    protein_column = column_map.get("protein")
    if protein_column:
        return frame[protein_column].fillna("").map(_is_decoy_protein)
    return pd.Series([pd.NA] * len(frame), index=frame.index)


def _is_decoy_value(value: Any) -> bool:
    text = _clean_text(value).casefold()
    if text in {"true", "t", "yes", "decoy", "reverse", "rev", "-1"}:
        return True
    if text in {"0", "false", "f", "no", "target", "forward", "1"}:
        return False
    return "decoy" in text or text.startswith("rev_") or "reverse" in text


def _is_decoy_protein(value: Any) -> bool:
    text = _clean_text(value).casefold()
    return "decoy" in text or "rev_" in text or "reverse" in text


def _score_columns(frame: pd.DataFrame, column_map: dict[str, str | None]) -> list[str]:
    core = {value for value in column_map.values() if value}
    score_columns: list[str] = []
    for column in frame.columns:
        if column in core:
            continue
        normalized = column.casefold()
        if any(token in normalized for token in ["score", "hyperscore", "expect", "e-value", "xcorr", "delta", "mass", "ppm", "ion", "rank"]):
            numeric = pd.to_numeric(frame[column], errors="coerce")
            if numeric.notna().any():
                score_columns.append(column)
    return score_columns


def _score_payload(row: pd.Series, score_columns: list[str]) -> dict[str, float | str]:
    payload: dict[str, float | str] = {}
    for column in score_columns:
        value = row.get(column)
        numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
        payload[column] = float(numeric) if not pd.isna(numeric) else _clean_text(value)
    return payload


def _clean_sequence(value: Any) -> str:
    text = _clean_text(value).upper()
    return "".join(char for char in text if char.isalpha())


def _schema_payload() -> dict[str, Any]:
    return {
        "schema_version": PSM_SCORING_SCHEMA_VERSION,
        "target_schema": "psm_scoring_train.parquet",
        "columns": {
            "target_decoy_label": "1 for target PSM, 0 for decoy PSM.",
            "is_decoy": "Boolean decoy indicator inferred from target/decoy or protein columns.",
            "score_features_json": "JSON object containing numeric search score features retained from input TSV.",
        },
        "notes": [
            "v0 requires target/decoy labels or decoy protein accessions.",
            "v0 consumes existing search result TSV/PIN-style tables and does not generate target-decoy databases.",
        ],
    }
