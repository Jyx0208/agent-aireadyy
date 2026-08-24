from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable
from pathlib import PurePath
from typing import Any

import pandas as pd

from agent.input.normalizer import normalize_input
from agent.models import MetadataValue, ProjectContext
from agent.pride.client import PrideClient, list_project_files_paginated_with_state


SDRF_CANONICAL_COLUMNS: dict[str, tuple[str, ...]] = {
    "cell_line": ("cell line",),
    "organism": ("organism",),
    "disease": ("disease",),
    "treatment": ("treatment", "compound", "drug", "dose"),
    "control": ("control",),
    "assay": ("assay", "data acquisition method", "technology type"),
    "fraction": ("fraction",),
}


def _normalize_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _normalize_name(value: str) -> str:
    return normalize_input(value).normalized_name if value else value


def _entry_names(entries: Iterable[dict[str, Any]]) -> list[str]:
    names = []
    for entry in entries:
        name = entry.get("name")
        if name:
            names.append(str(name))
    return names


def _sdrf_candidate_columns(columns: Iterable[str]) -> list[str]:
    result = []
    for column in columns:
        normalized = _normalize_key(column)
        if "data file" in normalized or "file name" in normalized or "raw file" in normalized:
            result.append(column)
    return result


def _detect_sdrf_delimiter(table_text: str) -> str:
    sample = table_text[:65536]
    try:
        return csv.Sniffer().sniff(sample, delimiters="\t,;").delimiter
    except csv.Error:
        header = next((line for line in table_text.splitlines() if line.strip()), "")
        counts = {delimiter: header.count(delimiter) for delimiter in ("\t", ",", ";")}
        delimiter, count = max(counts.items(), key=lambda item: item[1])
        return delimiter if count else "\t"


def load_sdrf_rows(table_text: str) -> list[dict[str, Any]]:
    frame = pd.read_csv(
        io.StringIO(table_text),
        sep=_detect_sdrf_delimiter(table_text),
        dtype=str,
    ).fillna("")
    return frame.to_dict(orient="records")


def build_sdrf_file_index(
    rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Index SDRF file columns once instead of rescanning all rows per file."""
    if not rows:
        return {}
    index: dict[str, list[dict[str, Any]]] = {}
    columns = _sdrf_candidate_columns(rows[0].keys())
    for row in rows:
        seen_keys: set[str] = set()
        for column in columns:
            value = str(row.get(column, "") or "")
            normalized_value = _normalize_name(value)
            value_stem = PurePath(value).stem.lower()
            keys = [
                f"name:{normalized_value}" if normalized_value else "",
                f"stem:{value_stem}" if value_stem else "",
            ]
            for key in keys:
                if not key or key in seen_keys:
                    continue
                index.setdefault(key, []).append(row)
                seen_keys.add(key)
    return index


def select_sdrf_rows_for_file(
    rows: list[dict[str, Any]],
    file_name: str,
    *,
    file_index: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    if not rows:
        return []
    normalized_target = _normalize_name(file_name)
    target_stem = PurePath(file_name).stem.lower()
    if file_index is not None:
        matches: list[dict[str, Any]] = []
        seen_rows: set[int] = set()
        for key in (
            f"name:{normalized_target}" if normalized_target else "",
            f"stem:{target_stem}" if target_stem else "",
        ):
            for row in file_index.get(key, []):
                row_id = id(row)
                if row_id in seen_rows:
                    continue
                seen_rows.add(row_id)
                matches.append(row)
        return matches
    matches: list[dict[str, Any]] = []
    columns = _sdrf_candidate_columns(rows[0].keys())
    for row in rows:
        for column in columns:
            value = row.get(column, "")
            normalized_value = _normalize_name(value)
            if normalized_value == normalized_target:
                matches.append(row)
                break
            if PurePath(value).stem.lower() == target_stem and target_stem:
                matches.append(row)
                break
    return matches


def extract_sdrf_assay_values(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Return values from explicit assay columns while preserving their source column."""
    if not rows:
        return []
    assay_columns = [
        column
        for column in rows[0]
        if "assay" in _normalize_key(column) or "technology type" in _normalize_key(column)
    ]
    return [
        (column, value)
        for row in rows
        for column in assay_columns
        if (value := " ".join(str(row.get(column) or "").split()).strip())
    ]


def summarize_sdrf_rows(
    rows: list[dict[str, Any]],
    file_names: list[str],
    *,
    source_url: str | None,
    content_sha256: str | None,
    status: str = "available",
    errors: list[str] | None = None,
    max_values_per_field: int = 12,
    max_examples: int = 8,
    file_index: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Build a bounded, Agent-safe SDRF projection without returning raw rows."""
    columns = list(rows[0].keys()) if rows else []
    normalized_columns = {column: _normalize_key(column) for column in columns}
    canonical_columns = {
        field: [
            column
            for column, normalized in normalized_columns.items()
            if any(pattern in normalized for pattern in patterns)
        ]
        for field, patterns in SDRF_CANONICAL_COLUMNS.items()
    }
    canonical_fields: dict[str, list[str]] = {}
    conflicts: list[str] = []
    for field, matching_columns in canonical_columns.items():
        values: list[str] = []
        seen_values: set[str] = set()
        for row in rows:
            for column in matching_columns:
                value = " ".join(str(row.get(column) or "").split()).strip()
                normalized_value = value.casefold()
                if not value or normalized_value in seen_values:
                    continue
                values.append(value[:240])
                seen_values.add(normalized_value)
        if len(values) > max_values_per_field:
            conflicts.append(
                f"{field}: {len(values)} distinct values; showing first {max_values_per_field}"
            )
        if values:
            canonical_fields[field] = values[:max_values_per_field]

    match_status_counts = {"matched": 0, "no_file_match": 0, "no_sdrf": 0}
    examples: list[dict[str, Any]] = []
    for file_name in file_names:
        matched_rows = (
            select_sdrf_rows_for_file(rows, file_name, file_index=file_index)
            if rows
            else []
        )
        match_status = "matched" if matched_rows else ("no_file_match" if rows else "no_sdrf")
        match_status_counts[match_status] += 1
        if len(examples) < max_examples:
            examples.append(
                {
                    "file_name": file_name[:240],
                    "status": match_status,
                    "matched_row_count": len(matched_rows),
                }
            )
        if matched_rows:
            for field, matching_columns in canonical_columns.items():
                matched_values = {
                    " ".join(str(row.get(column) or "").split()).strip().casefold()
                    for row in matched_rows
                    for column in matching_columns
                    if str(row.get(column) or "").strip()
                }
                if len(matched_values) > 1 and len(conflicts) < 12:
                    conflicts.append(f"{file_name[:120]}: conflicting {field} values")

    return {
        "status": status,
        "source_url": source_url,
        "content_sha256": content_sha256,
        "row_count": len(rows),
        "match_status_counts": match_status_counts,
        "canonical_fields": canonical_fields,
        "file_match_examples": examples,
        "missing_columns": [field for field, matches in canonical_columns.items() if not matches],
        "conflicts": conflicts[:12],
        "errors": [" ".join(str(error).split())[:500] for error in (errors or [])[:8]],
    }


def detect_sdrf_file(project_files: list[dict[str, Any]]) -> dict[str, Any] | None:
    for file_record in project_files:
        file_name = file_record.get("fileName", "").lower()
        if file_name.endswith(".sdrf.tsv") or "sdrf" in file_name:
            return file_record
    return None


def build_project_context(
    client: PrideClient,
    project_accession: str,
    file_name: str,
) -> ProjectContext:
    project = client.get_project(project_accession)
    project_files = list_project_files_paginated_with_state(
        client,
        project_accession,
        mode="exhaustive",
    ).records
    metadata = {
        "title": MetadataValue(
            value=project.get("title"),
            source="pride.title",
            source_level="project",
            completeness=1.0 if project.get("title") else 0.0,
        ),
        "projectDescription": MetadataValue(
            value=project.get("projectDescription"),
            source="pride.projectDescription",
            source_level="project",
            completeness=1.0 if project.get("projectDescription") else 0.0,
        ),
        "sampleProcessingProtocol": MetadataValue(
            value=project.get("sampleProcessingProtocol"),
            source="pride.sampleProcessingProtocol",
            source_level="project",
            completeness=1.0 if project.get("sampleProcessingProtocol") else 0.0,
        ),
        "dataProcessingProtocol": MetadataValue(
            value=project.get("dataProcessingProtocol"),
            source="pride.dataProcessingProtocol",
            source_level="project",
            completeness=1.0 if project.get("dataProcessingProtocol") else 0.0,
        ),
        "organisms": MetadataValue(
            value=_entry_names(project.get("organisms", [])),
            source="pride.organisms",
            source_level="project",
            completeness=1.0 if project.get("organisms") else 0.0,
        ),
        "instruments": MetadataValue(
            value=_entry_names(project.get("instruments", [])),
            source="pride.instruments",
            source_level="project",
            completeness=1.0 if project.get("instruments") else 0.0,
        ),
        "experimentTypes": MetadataValue(
            value=_entry_names(project.get("experimentTypes", [])),
            source="pride.experimentTypes",
            source_level="project",
            completeness=1.0 if project.get("experimentTypes") else 0.0,
        ),
        "keywords": MetadataValue(
            value=project.get("keywords", []),
            source="pride.keywords",
            source_level="project",
            completeness=1.0 if project.get("keywords") else 0.0,
        ),
    }

    sdrf_rows: list[dict[str, Any]] = []
    sdrf_file = detect_sdrf_file(project_files)
    if sdrf_file:
        download_url = client.first_download_url(sdrf_file)
        if download_url:
            sdrf_rows = select_sdrf_rows_for_file(load_sdrf_rows(client.download_text(download_url)), file_name)

    return ProjectContext(
        repository="pride",
        project_accession=project_accession,
        px_accession=project_accession,
        file_name=file_name,
        metadata=metadata,
        sdrf_rows=sdrf_rows,
        project_files=project_files,
        evidence_documents=[
            {
                "source": "pride.project",
                "text": " ".join(
                    str(value or "")
                    for value in (
                        project.get("title"),
                        project.get("projectDescription"),
                        project.get("sampleProcessingProtocol"),
                        project.get("dataProcessingProtocol"),
                    )
                ),
            }
        ],
        raw_project_metadata=project,
    )


def build_project_context_for_known_file(
    client: PrideClient,
    project_accession: str,
    file_name: str,
    *,
    file_size_bytes: int | None = None,
    download_url: str | None = None,
) -> ProjectContext:
    """Build a PRIDE context for an already-local file without listing all files.

    This is intentionally narrow: it still uses project-level metadata, but it
    avoids the expensive/fragile PRIDE file-list endpoint for cached local files
    supplied by discovery handoff or manual HPC sync.
    """
    project = client.get_project(project_accession)
    project_files = [
        {
            "fileName": file_name,
            "fileSizeBytes": file_size_bytes,
            "publicFileLocations": [{"value": download_url}] if download_url else [],
            "fileCategory": {"value": "SEARCH"} if file_name.lower().endswith((".tsv", ".pin")) else {"value": "RAW"},
        }
    ]
    metadata = {
        "title": MetadataValue(
            value=project.get("title"),
            source="pride.title",
            source_level="project",
            completeness=1.0 if project.get("title") else 0.0,
        ),
        "projectDescription": MetadataValue(
            value=project.get("projectDescription"),
            source="pride.projectDescription",
            source_level="project",
            completeness=1.0 if project.get("projectDescription") else 0.0,
        ),
        "sampleProcessingProtocol": MetadataValue(
            value=project.get("sampleProcessingProtocol"),
            source="pride.sampleProcessingProtocol",
            source_level="project",
            completeness=1.0 if project.get("sampleProcessingProtocol") else 0.0,
        ),
        "dataProcessingProtocol": MetadataValue(
            value=project.get("dataProcessingProtocol"),
            source="pride.dataProcessingProtocol",
            source_level="project",
            completeness=1.0 if project.get("dataProcessingProtocol") else 0.0,
        ),
        "organisms": MetadataValue(
            value=_entry_names(project.get("organisms", [])),
            source="pride.organisms",
            source_level="project",
            completeness=1.0 if project.get("organisms") else 0.0,
        ),
        "instruments": MetadataValue(
            value=_entry_names(project.get("instruments", [])),
            source="pride.instruments",
            source_level="project",
            completeness=1.0 if project.get("instruments") else 0.0,
        ),
        "experimentTypes": MetadataValue(
            value=_entry_names(project.get("experimentTypes", [])),
            source="pride.experimentTypes",
            source_level="project",
            completeness=1.0 if project.get("experimentTypes") else 0.0,
        ),
        "keywords": MetadataValue(
            value=project.get("keywords", []),
            source="pride.keywords",
            source_level="project",
            completeness=1.0 if project.get("keywords") else 0.0,
        ),
    }

    return ProjectContext(
        repository="pride",
        project_accession=project_accession,
        px_accession=project_accession,
        file_name=file_name,
        metadata=metadata,
        sdrf_rows=[],
        project_files=project_files,
        evidence_documents=[
            {
                "source": "pride.project",
                "text": " ".join(
                    str(value or "")
                    for value in (
                        project.get("title"),
                        project.get("projectDescription"),
                        project.get("sampleProcessingProtocol"),
                        project.get("dataProcessingProtocol"),
                    )
                ),
            },
            {
                "source": "local.known_file",
                "text": f"Using local cached source file {file_name}; PRIDE project file list was not queried.",
            },
        ],
        raw_project_metadata=project,
    )
