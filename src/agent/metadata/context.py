from __future__ import annotations

import io
import re
from collections.abc import Iterable
from pathlib import PurePath
from typing import Any

import pandas as pd

from agent.input.normalizer import normalize_input
from agent.models import MetadataValue, ProjectContext
from agent.pride.client import PrideClient


def _normalize_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _normalize_name(value: str) -> str:
    return normalize_input(value).normalized_name if value else value


def _sdrf_candidate_columns(columns: Iterable[str]) -> list[str]:
    result = []
    for column in columns:
        normalized = _normalize_key(column)
        if "data file" in normalized or "file name" in normalized or "raw file" in normalized:
            result.append(column)
    return result


def load_sdrf_rows(table_text: str) -> list[dict[str, Any]]:
    frame = pd.read_csv(io.StringIO(table_text), sep="\t", dtype=str).fillna("")
    return frame.to_dict(orient="records")


def select_sdrf_rows_for_file(rows: list[dict[str, Any]], file_name: str) -> list[dict[str, Any]]:
    if not rows:
        return []
    normalized_target = _normalize_name(file_name)
    target_stem = PurePath(file_name).stem.lower()
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
    project_files = client.list_project_files(project_accession)
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
            value=[entry.get("name") for entry in project.get("organisms", [])],
            source="pride.organisms",
            source_level="project",
            completeness=1.0 if project.get("organisms") else 0.0,
        ),
        "instruments": MetadataValue(
            value=[entry.get("name") for entry in project.get("instruments", [])],
            source="pride.instruments",
            source_level="project",
            completeness=1.0 if project.get("instruments") else 0.0,
        ),
        "experimentTypes": MetadataValue(
            value=[entry.get("name") for entry in project.get("experimentTypes", [])],
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
        project_accession=project_accession,
        file_name=file_name,
        metadata=metadata,
        sdrf_rows=sdrf_rows,
        project_files=project_files,
    )
