from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import httpx

from agent.assets.downloader import download_file_asset
from agent.input.normalizer import InputTask, normalize_input
from agent.metadata.canonical import CanonicalFile, CanonicalMetadataValue, CanonicalProject
from agent.models import FileAsset, MetadataValue, ProjectCandidate, ProjectContext, ProjectResolution
from agent.pride.resolver import resolve_primary_project
from agent.repositories.matching import (
    MASSIVE_COLLECTION_PRIORITY,
    classify_asset_type,
    canonical_file_to_asset,
    canonical_files_to_project_file_records,
    match_canonical_file,
)


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _list_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        return [str(item) for item in value.values() if str(item).strip()]
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _cv_values(value: Any, *, name_contains: str | None = None, prefer_name_without_value: bool = False) -> list[str]:
    values: list[str] = []
    if value is None:
        return values
    if isinstance(value, dict):
        name = str(value.get("name") or value.get("label") or "").strip()
        raw_value = value.get("value")
        name_ok = not name_contains or name_contains.lower() in name.lower()
        if name_ok and raw_value not in (None, "", "null"):
            values.append(str(raw_value).strip())
        elif name_ok and prefer_name_without_value and name and name.lower() not in {"null", "instrument model"}:
            values.append(name)
        for child in value.values():
            if isinstance(child, list | tuple | dict):
                values.extend(_cv_values(child, name_contains=name_contains, prefer_name_without_value=prefer_name_without_value))
        return values
    if isinstance(value, list | tuple | set):
        for child in value:
            values.extend(_cv_values(child, name_contains=name_contains, prefer_name_without_value=prefer_name_without_value))
        return values
    text = str(value).strip()
    return [text] if text and text.lower() != "null" and name_contains is None else []


def _dedupe_text(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = re.sub(r"\s+", " ", str(value)).strip()
        key = text.lower()
        if text and key not in seen and key != "null":
            seen.add(key)
            out.append(text)
    return out


def _plain_text_values(*values: Any) -> list[str]:
    out: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            text = value.strip()
            if text and text.lower() != "null":
                out.append(text)
            continue
        if isinstance(value, list | tuple | set):
            out.extend(_plain_text_values(*value))
    return out


def _metadata_values(value: Any, source: str) -> list[CanonicalMetadataValue]:
    return [CanonicalMetadataValue(value=item, source=source) for item in _list_text(value)]


class MassiveClient:
    def __init__(
        self,
        base_url: str = "https://massive.ucsd.edu/ProteoSAFe",
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=httpx.Timeout(timeout, read=max(timeout, 120.0)), follow_redirects=True)

    def get_dataset(self, accession: str) -> dict[str, Any]:
        response = self._client.get(f"{self.base_url}/proxi/v0.1/datasets/{accession}")
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {"records": payload}

    def query_datasets(self, accession: str) -> dict[str, Any]:
        query = quote(f'{{"title_input":"{accession}"}}')
        response = self._client.get(f"{self.base_url}/QueryDatasets?pageSize=30&offset=0&query={query}")
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            rows = payload.get("row_data") or payload.get("data") or payload.get("datasets")
            if isinstance(rows, list) and rows:
                first = rows[0]
                return first if isinstance(first, dict) else {"value": first}
            return payload
        return {"records": payload}

    def list_dataset_files_from_csv(self, accession: str, csv_url: str | None = None) -> list[dict[str, Any]]:
        # MassIVE does not expose a single stable file-list API in its public docs.
        # This hook supports deployments that point at a precomputed MassIVE file index CSV.
        if not csv_url:
            return []
        response = self._client.get(csv_url)
        response.raise_for_status()
        rows = csv.DictReader(io.StringIO(response.text))
        out: list[dict[str, Any]] = []
        for row in rows:
            dataset = _first_text(row.get("dataset"), row.get("accession"), row.get("dataset_id"))
            if dataset and dataset.upper() != accession.upper():
                continue
            out.append(row)
        return out

    def _datasetcache_csv(self, sql: str) -> list[dict[str, Any]]:
        url = "https://datasetcache.gnps2.org/datasette/database.csv?sql=" + quote(sql)
        response = self._client.get(url)
        response.raise_for_status()
        text = response.text.lstrip("\ufeff\r\n")
        if not text.strip():
            return []
        return [dict(row) for row in csv.DictReader(io.StringIO(text))]

    def list_dataset_files_from_cache(self, accession: str, limit: int = 5000) -> list[dict[str, Any]]:
        safe_accession = accession.replace("'", "''")
        sql = f"select * from filename where dataset='{safe_accession}' limit {int(limit)}"
        return self._datasetcache_csv(sql)

    def find_files_by_name_from_cache(self, file_name: str, limit: int = 200) -> list[dict[str, Any]]:
        safe_name = file_name.replace("'", "''").lower()
        sql = (
            "select * from filename where "
            f"lower(filepath)= '{safe_name}' or "
            f"lower(filepath) like '%/{safe_name}' or "
            f"lower(filepath) like '%/{safe_name}/%' "
            f"limit {int(limit)}"
        )
        return self._datasetcache_csv(sql)

    def download_to_path(self, url: str, target_path: str | Path, report: Callable | None = None) -> Path:
        target_path = Path(target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with self._client.stream("GET", url) as response:
            response.raise_for_status()
            with target_path.open("wb") as handle:
                for chunk in response.iter_bytes():
                    if chunk:
                        handle.write(chunk)
        if report:
            report(f"Download complete: {target_path}")
        return target_path


class MassiveAdapter:
    name = "massive"

    def __init__(self, client: MassiveClient | None = None, file_index_csv_url: str | None = None):
        self.client = client or MassiveClient()
        self.file_index_csv_url = file_index_csv_url

    def can_handle_accession(self, value: str) -> bool:
        return value.upper().startswith(("MSV", "RMSV", "PXD"))

    def resolve_project(self, raw_input: str) -> ProjectResolution:
        task = normalize_input(raw_input)
        if self.can_handle_accession(task.file_name):
            project = self.get_project(task.file_name)
            matched_file = task.file_name
            match_type = "accession"
            evidence = ["MassIVE accession input"]
        else:
            raw_files = self.client.find_files_by_name_from_cache(task.file_name)
            files_by_project: dict[str, list[dict[str, Any]]] = {}
            for record in raw_files:
                dataset = _first_text(record.get("dataset"), record.get("accession"), record.get("dataset_id"))
                if dataset:
                    files_by_project.setdefault(dataset, []).append(record)
            candidates: list[ProjectCandidate] = []
            for dataset, records in files_by_project.items():
                project = self.get_project(dataset)
                files = self._dedupe_files([self.map_file(record, project) for record in records])
                matched = self.match_file(task, files)
                if matched is None:
                    continue
                candidates.append(
                    ProjectCandidate(
                        repository=self.name,
                        project_accession=project.primary_accession,
                        native_accession=project.native_accession,
                        px_accession=project.px_accession,
                        matched_file=matched.file_name,
                        match_type="exact",
                        match_score=100,
                        evidence=["MassIVE datasetcache exact file match"],
                        metadata_consistency=self._metadata_consistency(project),
                    )
                )
            return resolve_primary_project(candidates) if candidates else ProjectResolution.empty()
        candidate = ProjectCandidate(
            repository=self.name,
            project_accession=project.primary_accession,
            native_accession=project.native_accession,
            px_accession=project.px_accession,
            matched_file=matched_file,
            match_type=match_type,
            match_score=100,
            evidence=evidence,
            metadata_consistency=self._metadata_consistency(project),
        )
        return resolve_primary_project([candidate])

    def get_project(self, accession: str) -> CanonicalProject:
        try:
            raw = self.client.get_dataset(accession)
        except Exception:
            raw = self.client.query_datasets(accession)
        return self.map_project(raw, accession)

    def list_project_files(self, project: CanonicalProject) -> list[CanonicalFile]:
        raw_files = self._raw_file_records(project.raw_metadata)
        if not raw_files and hasattr(self.client, "list_dataset_files_from_csv"):
            raw_files = self.client.list_dataset_files_from_csv(project.primary_accession, self.file_index_csv_url)
        if not raw_files and hasattr(self.client, "list_dataset_files_from_cache"):
            raw_files = self.client.list_dataset_files_from_cache(project.primary_accession)
        return self._dedupe_files([self.map_file(record, project) for record in raw_files])

    def match_file(self, task: InputTask, files: list[CanonicalFile]) -> CanonicalFile | None:
        return match_canonical_file(task, files)

    def build_project_context(self, resolution: ProjectResolution, file_name: str) -> ProjectContext:
        if resolution.primary_project is None:
            raise ValueError("Cannot build MassIVE context without a primary project.")
        project = self.get_project(resolution.primary_project.project_accession)
        files = self.list_project_files(project)
        metadata = {
            "title": MetadataValue(value=project.title, source="massive.title", source_level="project", completeness=1.0 if project.title else 0.0),
            "projectDescription": MetadataValue(value=project.description, source="massive.description", source_level="project", completeness=1.0 if project.description else 0.0),
            "sampleProcessingProtocol": MetadataValue(value=None, source="massive", source_level="project", completeness=0.0),
            "dataProcessingProtocol": MetadataValue(value=project.data_processing_protocol.value if project.data_processing_protocol else None, source="massive.data_processing_protocol", source_level="project", completeness=1.0 if project.data_processing_protocol else 0.0),
            "organisms": MetadataValue(value=[item.value for item in project.organisms], source="massive.organisms", source_level="project", completeness=1.0 if project.organisms else 0.0),
            "instruments": MetadataValue(value=[item.value for item in project.instruments], source="massive.instruments", source_level="project", completeness=1.0 if project.instruments else 0.0),
            "experimentTypes": MetadataValue(value=[item.value for item in project.experiment_types], source="massive.experiment_types", source_level="project", completeness=1.0 if project.experiment_types else 0.0),
            "keywords": MetadataValue(value=project.keywords, source="massive.keywords", source_level="project", completeness=1.0 if project.keywords else 0.0),
        }
        evidence = [
            {"source": "massive.proxi_or_querydatasets", "text": f"title={project.title or ''}; description={project.description or ''}"},
            {"source": "massive.file_paths", "text": "; ".join(file.logical_path or file.file_name for file in files[:200])},
        ]
        return ProjectContext(
            repository=self.name,
            project_accession=project.primary_accession,
            native_accession=project.native_accession,
            px_accession=project.px_accession,
            file_name=file_name,
            metadata=metadata,
            project_files=canonical_files_to_project_file_records(files),
            evidence_documents=evidence,
            raw_project_metadata=project.raw_metadata,
        )

    def resolve_file_asset(self, task: InputTask, context: ProjectContext, work_dir: str | Path) -> FileAsset:
        project = CanonicalProject(
            repository=self.name,
            primary_accession=context.project_accession,
            native_accession=context.native_accession,
            px_accession=context.px_accession,
        )
        files = [self.map_file(record, project) for record in context.project_files]
        matched = self.match_file(task, files)
        if matched is None:
            return FileAsset(
                repository=self.name,
                original_file_name=task.file_name,
                resolved_asset_type="unknown",
                project_accession=context.project_accession,
                native_project_accession=context.native_accession,
                asset_confidence=0.0,
                match_type="unresolved",
            )
        return canonical_file_to_asset(task, project, matched, work_dir)

    def download_file(self, asset: FileAsset, target_path: Path, report: Callable | None = None) -> Path:
        return download_file_asset(self.client, asset.model_copy(update={"local_path": target_path}), report=report)

    def download_to_path(self, url: str, target_path: str | Path, report: Callable | None = None) -> Path:
        return self.client.download_to_path(url, target_path, report=report)

    def map_project(self, raw: dict[str, Any], fallback_accession: str = "") -> CanonicalProject:
        massive_ids = _cv_values(raw.get("accession"), name_contains="MassIVE dataset identifier")
        ftp_urls = _cv_values(raw.get("datasetLink"), name_contains="Dataset FTP location")
        raw = dict(raw)
        if ftp_urls and not raw.get("ftp_url"):
            raw["ftp_url"] = ftp_urls[0]
        native_accession = _first_text(
            massive_ids[0] if massive_ids else None,
            raw.get("accession"),
            raw.get("dataset"),
            raw.get("dataset_id"),
            raw.get("MassIVE accession"),
            fallback_accession if fallback_accession.upper().startswith(("MSV", "RMSV")) else None,
        )
        px_accession = _first_text(
            raw.get("px_accession"),
            raw.get("proteomeXchangeAccession"),
            raw.get("ProteomeXchange ID"),
            raw.get("pxd"),
            fallback_accession if fallback_accession.upper().startswith("PXD") else None,
        )
        primary = native_accession or px_accession or fallback_accession
        description = _first_text(raw.get("description"), raw.get("dataset.comments"), raw.get("comment"), raw.get("abstract"))
        if not description:
            description = _first_text(raw.get("summary"))
        processing = _first_text(raw.get("data_processing_protocol"), raw.get("methods"), raw.get("protocol"))
        instruments = _dedupe_text(_cv_values(raw.get("instruments"), prefer_name_without_value=True)) or _plain_text_values(raw.get("instrument"), raw.get("instruments"))
        organisms = (
            _dedupe_text(_cv_values(raw.get("species"), name_contains="scientific name"))
            or _dedupe_text(_cv_values(raw.get("species"), name_contains="common name"))
            or _dedupe_text(_cv_values(raw.get("organism")))
            or _plain_text_values(raw.get("species"), raw.get("organism"))
        )
        keywords = _dedupe_text(_cv_values(raw.get("keywords"))) or _list_text(raw.get("keywords"))
        return CanonicalProject(
            repository=self.name,
            primary_accession=primary,
            native_accession=native_accession,
            px_accession=px_accession,
            title=_first_text(raw.get("title"), raw.get("desc"), raw.get("dataset.title")),
            description=description,
            organisms=[CanonicalMetadataValue(value=item, source="massive.organism") for item in organisms],
            instruments=[CanonicalMetadataValue(value=item, source="massive.instrument") for item in instruments],
            experiment_types=_metadata_values(_first_text(raw.get("experiment_type"), raw.get("modality")), "massive.experiment_type"),
            keywords=keywords,
            data_processing_protocol=CanonicalMetadataValue(value=processing, source="massive.data_processing_protocol") if processing else None,
            submission_date=_first_text(raw.get("created"), raw.get("submission_date")),
            publication_date=_first_text(raw.get("publication_date"), raw.get("release_date")),
            raw_metadata=raw,
        )

    def map_file(self, raw: dict[str, Any], project: CanonicalProject) -> CanonicalFile:
        logical_path = _first_text(raw.get("logicalPath"), raw.get("filepath"), raw.get("path"), raw.get("file"))
        logical_path = self._collapse_datasetcache_path(logical_path)
        file_name = _first_text(raw.get("fileName"), raw.get("filename"), raw.get("name"))
        if not file_name and logical_path:
            file_name = PurePathCompat.name(logical_path)
        raw_category = raw.get("fileCategory")
        if isinstance(raw_category, dict):
            raw_category = _first_text(raw_category.get("value"), raw_category.get("name"))
        category = _first_text(raw.get("collection"), raw_category, raw.get("file_type"), raw.get("type"))
        if not category and logical_path:
            category = logical_path.split("/", 1)[0]
        asset_type = classify_asset_type(file_name or logical_path or "")
        if category and category.lower() not in MASSIVE_COLLECTION_PRIORITY and asset_type != "unknown":
            category = asset_type
        public_locations = raw.get("publicFileLocations")
        public_url = None
        if isinstance(public_locations, list) and public_locations:
            first_location = public_locations[0]
            public_url = first_location.get("value") if isinstance(first_location, dict) else str(first_location)
        url = _first_text(raw.get("download_url"), raw.get("url"), raw.get("ftp_url"), public_url)
        base_ftp = _first_text(project.raw_metadata.get("ftp_url"), project.raw_metadata.get("ftpDownloadURL"), project.raw_metadata.get("dataset_ftp_url"))
        if not url and base_ftp and logical_path:
            url = f"{base_ftp.rstrip('/')}/{logical_path.lstrip('/')}"
        if not url and logical_path and project.native_accession:
            url = f"ftp://massive.ucsd.edu/{project.native_accession}/{logical_path.lstrip('/')}"
        transfer = "unknown"
        if url:
            transfer = "ftp" if url.startswith("ftp://") else "https" if url.startswith(("http://", "https://")) else "unknown"
        return CanonicalFile(
            repository=self.name,
            project_accession=project.primary_accession,
            file_name=file_name or logical_path or "",
            logical_path=logical_path,
            file_category=category,
            file_format=_first_text(raw.get("file_format"), raw.get("format")),
            size_bytes=self._to_int(_first_text(raw.get("size_bytes"), raw.get("fileSizeBytes"), raw.get("filesize"), raw.get("size"))),
            checksum=_first_text(raw.get("checksum"), raw.get("md5")),
            download_urls=[url] if url else [],
            transfer_method=transfer,
            raw_record=raw,
        )

    def _collapse_datasetcache_path(self, logical_path: str | None) -> str | None:
        if not logical_path:
            return logical_path
        parts = logical_path.replace("\\", "/").split("/")
        for index, part in enumerate(parts):
            if classify_asset_type(part) != "unknown":
                return "/".join(parts[: index + 1])
        return logical_path

    def _dedupe_files(self, files: list[CanonicalFile]) -> list[CanonicalFile]:
        seen: set[tuple[str, str | None]] = set()
        out: list[CanonicalFile] = []
        for file in files:
            key = (file.file_name.lower(), file.logical_path)
            if key in seen:
                continue
            seen.add(key)
            out.append(file)
        return out

    def _raw_file_records(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        for key in ("files", "fileList", "dataset_files", "all_files"):
            value = raw.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    def _metadata_consistency(self, project: CanonicalProject) -> float:
        checks = [bool(project.organisms), bool(project.instruments), bool(project.description), bool(project.data_processing_protocol), bool(project.keywords)]
        return sum(checks) / len(checks)

    @staticmethod
    def _to_int(value: str | None) -> int | None:
        if not value:
            return None
        match = re.search(r"\d+", value.replace(",", ""))
        return int(match.group(0)) if match else None


class PurePathCompat:
    @staticmethod
    def name(path: str) -> str:
        return path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
