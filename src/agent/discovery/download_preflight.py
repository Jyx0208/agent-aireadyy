from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.pride.client import PrideClient
from agent.utils import write_json


DEFAULT_PREFLIGHT_QUERIES = [
    "phosphoproteomics",
    "phosphorylation",
    "phospho",
    "IMAC",
    "TiO2",
]

ACQUISITION_EXTENSIONS = (".mzml", ".mzml.gz", ".raw", ".raw.zip", ".mzxml", ".wiff", ".d")
PREFERRED_ACQUISITION_EXTENSIONS = (".mzml", ".mzml.gz", ".mzxml")
PEAKLIST_EXTENSIONS = (".mgf",)
SEARCH_TABLE_SUFFIXES = (
    "psm.tsv",
    "peptide.tsv",
    "combined_psm.tsv",
    "combined_peptide.tsv",
    ".pin",
)
SEARCH_RESULT_EXTENSIONS = (".mzid",)


def _accession(project: dict[str, Any]) -> str:
    return str(project.get("accession") or project.get("projectAccession") or "").strip()


def _title(project: dict[str, Any]) -> str:
    return str(project.get("title") or project.get("projectTitle") or "").strip()


def _size_bytes(record: dict[str, Any]) -> int | None:
    value = record.get("fileSizeBytes") or record.get("fileSizeInBytes") or record.get("fileSize")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _file_name(record: dict[str, Any]) -> str:
    return str(record.get("fileName") or record.get("name") or "").strip()


def classify_pride_file_name(file_name: str) -> str:
    lowered = file_name.casefold()
    if lowered.endswith(PREFERRED_ACQUISITION_EXTENSIONS):
        return "preferred_acquisition"
    if lowered.endswith(ACQUISITION_EXTENSIONS):
        return "acquisition"
    if lowered.endswith(PEAKLIST_EXTENSIONS):
        return "peaklist_mgf"
    if any(lowered.endswith(suffix) for suffix in SEARCH_TABLE_SUFFIXES):
        return "search_result_table"
    if lowered.endswith(SEARCH_RESULT_EXTENSIONS):
        return "search_result_mzid"
    if lowered.endswith((".sdrf.tsv", "sdrf.tsv", ".fasta", ".fa")):
        return "metadata"
    if lowered.endswith((".tsv", ".csv", ".xlsx", ".txt", ".pdf", ".zip", ".dat", ".dta", ".sne")):
        return "other_result_or_archive"
    return "unknown"


def _dedupe_projects(projects: list[dict[str, Any]], limit: int, excluded: set[str]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    selected: list[dict[str, Any]] = []
    for project in projects:
        accession = _accession(project)
        if not accession or accession in seen or accession in excluded:
            continue
        seen.add(accession)
        selected.append(project)
        if len(selected) >= limit:
            break
    return selected


def _candidate_row(
    *,
    project: dict[str, Any],
    file_record: dict[str, Any],
    file_role: str,
    max_file_bytes: int,
) -> dict[str, Any]:
    accession = _accession(project)
    name = _file_name(file_record)
    size = _size_bytes(file_record)
    download_url = PrideClient.first_download_url(file_record)
    return {
        "repository": "pride",
        "project_accession": accession,
        "project_title": _title(project),
        "file_name": name,
        "file_role": file_role,
        "expected_size_bytes": size,
        "expected_size_mb": round(size / (1024 * 1024), 3) if size is not None else None,
        "under_max_file_mb": bool(size is not None and size <= max_file_bytes),
        "download_url": download_url,
        "has_download_url": bool(download_url),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "repository",
        "project_accession",
        "project_title",
        "file_name",
        "file_role",
        "expected_size_bytes",
        "expected_size_mb",
        "under_max_file_mb",
        "download_url",
        "has_download_url",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _project_summary(project: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    role_counts = Counter(str(row["file_role"]) for row in rows)
    small_acquisition = [
        row
        for row in rows
        if row["file_role"] in {"preferred_acquisition", "acquisition"} and row["under_max_file_mb"]
    ]
    preferred_small_acquisition = [
        row for row in rows if row["file_role"] == "preferred_acquisition" and row["under_max_file_mb"]
    ]
    search_tables = [row for row in rows if row["file_role"] == "search_result_table"]
    peaklists = [row for row in rows if row["file_role"] == "peaklist_mgf"]
    return {
        "repository": "pride",
        "project_accession": _accession(project),
        "project_title": _title(project),
        "files_seen": len(rows),
        "role_counts": dict(role_counts),
        "small_acquisition_count": len(small_acquisition),
        "preferred_small_acquisition_count": len(preferred_small_acquisition),
        "search_result_table_count": len(search_tables),
        "peaklist_mgf_count": len(peaklists),
        "direct_export_pair_possible": bool(search_tables and peaklists),
        "recommended_for_download_preflight": bool(preferred_small_acquisition or small_acquisition or (search_tables and peaklists)),
    }


def preflight_pride_download_candidates(
    *,
    output_dir: str | Path,
    queries: list[str] | None = None,
    max_projects: int = 12,
    max_files_per_project: int = 80,
    max_file_mb: int = 500,
    exclude_projects: list[str] | None = None,
    client: PrideClient | None = None,
) -> dict[str, Any]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    queries = queries or DEFAULT_PREFLIGHT_QUERIES
    excluded = set(exclude_projects or ["PXD000900"])
    max_file_bytes = max_file_mb * 1024 * 1024
    owns_client = client is None
    pride = client or PrideClient(timeout=30)
    failures: list[dict[str, str]] = []
    project_records: list[dict[str, Any]] = []

    try:
        for query in queries:
            try:
                project_records.extend(pride.search_projects(query, page_size=max_projects))
            except Exception as exc:  # pragma: no cover - network boundary
                failures.append({"stage": "search_projects", "query": query, "error": str(exc)})
            project_records = _dedupe_projects(project_records, max_projects, excluded)
            if len(project_records) >= max_projects:
                break

        all_rows: list[dict[str, Any]] = []
        project_summaries: list[dict[str, Any]] = []
        for project_hit in project_records:
            accession = _accession(project_hit)
            try:
                project = pride.get_project(accession)
            except Exception as exc:  # pragma: no cover - network boundary
                failures.append({"stage": "get_project", "project": accession, "error": str(exc)})
                project = project_hit
            try:
                files = pride.list_project_files(accession, max_files=max_files_per_project)
            except Exception as exc:  # pragma: no cover - network boundary
                failures.append({"stage": "list_project_files", "project": accession, "error": str(exc)})
                continue

            project_rows: list[dict[str, Any]] = []
            for file_record in files:
                name = _file_name(file_record)
                role = classify_pride_file_name(name)
                if role in {"unknown", "other_result_or_archive"}:
                    continue
                row = _candidate_row(
                    project=project,
                    file_record=file_record,
                    file_role=role,
                    max_file_bytes=max_file_bytes,
                )
                project_rows.append(row)
                all_rows.append(row)
            project_summaries.append(_project_summary(project, project_rows))

        small_download_candidates = [
            row
            for row in all_rows
            if row["file_role"] in {"preferred_acquisition", "acquisition"}
            and row["under_max_file_mb"]
            and row["has_download_url"]
        ]
        direct_export_candidates = [
            summary for summary in project_summaries if summary["direct_export_pair_possible"]
        ]
        role_counts = Counter(str(row["file_role"]) for row in all_rows)
        payload = {
            "status": "completed" if project_records else "blocked",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "queries": queries,
            "excluded_projects": sorted(excluded),
            "max_projects": max_projects,
            "max_files_per_project": max_files_per_project,
            "max_file_mb": max_file_mb,
            "projects_seen": len(project_records),
            "candidate_files_seen": len(all_rows),
            "small_download_candidates": len(small_download_candidates),
            "direct_export_candidate_projects": len(direct_export_candidates),
            "role_counts": dict(role_counts),
            "failures": failures,
            "projects": project_summaries,
            "files": all_rows,
            "recommended_next_step": (
                "review small_download_candidates before any download"
                if small_download_candidates
                else "no small acquisition candidates found; try broader or different queries"
            ),
        }
        write_json(output_dir / "pride_download_preflight.json", payload)
        _write_csv(output_dir / "pride_download_preflight_files.csv", all_rows)
        _write_csv(output_dir / "pride_download_preflight_small_download_candidates.csv", small_download_candidates)
        write_json(output_dir / "pride_download_preflight_projects.json", project_summaries)
        return {
            **payload,
            "output_dir": str(output_dir),
            "files": {
                "preflight_json": str(output_dir / "pride_download_preflight.json"),
                "files_csv": str(output_dir / "pride_download_preflight_files.csv"),
                "small_download_candidates_csv": str(output_dir / "pride_download_preflight_small_download_candidates.csv"),
                "projects_json": str(output_dir / "pride_download_preflight_projects.json"),
            },
        }
    finally:
        if owns_client:
            pride.close()
