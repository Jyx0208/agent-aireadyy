from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable

import httpx

from agent.assets.downloader import download_file_asset
from agent.input.normalizer import InputTask, normalize_input
from agent.metadata.canonical import CanonicalFile, CanonicalMetadataValue, CanonicalProject
from agent.models import FileAsset, MetadataValue, ProjectCandidate, ProjectContext, ProjectResolution
from agent.pride.resolver import resolve_primary_project
from agent.repositories.index import RepositoryIndex
from agent.repositories.matching import canonical_file_to_asset, canonical_files_to_project_file_records, match_canonical_file


def _cache_index_path() -> Path:
    configured = os.environ.get("AGENT_REPOSITORY_INDEX_DIR")
    root = Path(configured) if configured else Path.cwd() / ".agent_cache" / "indexes"
    return root / "iprox.sqlite"


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


def _metadata_values(value: Any, source: str) -> list[CanonicalMetadataValue]:
    return [CanonicalMetadataValue(value=item, source=source) for item in _list_text(value)]


class IproxClient:
    def __init__(self, base_url: str = "https://www.iprox.cn", timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=httpx.Timeout(timeout, read=max(timeout, 120.0)), follow_redirects=True)

    def get_dataset(self, accession: str) -> dict[str, Any]:
        response = self._client.get(f"{self.base_url}/proxi/datasets/{accession}")
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and not self._empty_dataset_payload(payload):
            return payload
        response = self._client.get(
            f"{self.base_url}/proxi/datasets",
            params={"resultType": "full", "accession": accession},
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list) and payload:
            first = payload[0]
            return first if isinstance(first, dict) else {"value": first}
        return payload if isinstance(payload, dict) else {"records": payload}

    def list_project_ids_by_date(self, granularity: str, value: str) -> list[str]:
        endpoints = {
            "day": "getProjectDataFileByDay",
            "month": "getProjectDataFileByMonth",
            "year": "getProjectDataFileByYear",
        }
        endpoint = endpoints.get(granularity)
        if endpoint is None:
            raise ValueError("iProX date granularity must be day, month, or year.")
        response = self._client.get(f"{self.base_url}/projectFileList/{endpoint}.jsonp", params={"date": value})
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else []
        return [str(item).strip() for item in data if str(item).strip()] if isinstance(data, list) else []

    @staticmethod
    def _empty_dataset_payload(payload: dict[str, Any]) -> bool:
        return not any(value not in (None, "", [], {}) for value in payload.values())

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


class IproxAdapter:
    name = "iprox"

    def __init__(
        self,
        client: IproxClient | None = None,
        index: RepositoryIndex | None = None,
        aspera_username: str | None = None,
    ):
        self.client = client or IproxClient()
        self.index = index or RepositoryIndex(_cache_index_path())
        self.aspera_username = aspera_username or os.environ.get("IPROX_ASPERA_USERNAME")

    def can_handle_accession(self, value: str) -> bool:
        return value.upper().startswith(("IPX", "PXD"))

    def resolve_project(self, raw_input: str) -> ProjectResolution:
        task = normalize_input(raw_input)
        if self.can_handle_accession(task.file_name):
            project = self.get_project(task.file_name)
            candidate = ProjectCandidate(
                repository=self.name,
                project_accession=project.primary_accession,
                native_accession=project.native_accession,
                px_accession=project.px_accession,
                matched_file=task.file_name,
                match_type="accession",
                match_score=100,
                evidence=["iProX accession input"],
                metadata_consistency=self._metadata_consistency(project),
            )
            return resolve_primary_project([candidate])

        files = self.index.find_files_by_name(self.name, task.file_name)
        candidates = [
            ProjectCandidate(
                repository=self.name,
                project_accession=file.project_accession,
                native_accession=file.project_accession,
                matched_file=file.file_name,
                match_type="exact",
                match_score=100,
                evidence=["iProX local index exact file match"],
                metadata_consistency=0.2,
            )
            for file in files
        ]
        return resolve_primary_project(candidates)

    def get_project(self, accession: str) -> CanonicalProject:
        indexed = self.index.get_project(self.name, accession)
        if indexed is not None:
            return indexed
        raw = self.client.get_dataset(accession)
        return self.map_project(raw, accession)

    def list_project_files(self, project: CanonicalProject) -> list[CanonicalFile]:
        indexed = self.index.list_files(self.name, project.primary_accession)
        if indexed:
            return indexed
        return [self.map_file(record, project) for record in self._raw_file_records(project.raw_metadata)]

    def sync_index_by_date(
        self,
        granularity: str,
        value: str,
        *,
        limit: int | None = None,
        report: Callable[[str], None] | None = None,
    ) -> dict[str, int]:
        project_ids = self.client.list_project_ids_by_date(granularity, value)
        if limit is not None:
            project_ids = project_ids[: max(0, int(limit))]
        projects = 0
        files = 0
        xml_placeholders = 0
        for accession in project_ids:
            project = self.get_project(accession)
            if not project.primary_accession:
                project = project.model_copy(update={"primary_accession": accession, "native_accession": accession})
            project_files = self.list_project_files(project)
            if not project_files:
                project_files = [self._project_xml_placeholder(project)]
                xml_placeholders += 1
            self.index.upsert_project(project)
            self.index.replace_files(self.name, project.primary_accession, project_files)
            projects += 1
            files += len(project_files)
            if report:
                report(f"Indexed iProX project {project.primary_accession}: {len(project_files)} files")
        return {"projects": projects, "files": files, "xml_placeholders": xml_placeholders}

    def sync_index_from_xml_files(
        self,
        paths: list[str | Path],
        *,
        report: Callable[[str], None] | None = None,
    ) -> dict[str, int]:
        projects = 0
        files = 0
        for path in paths:
            project, project_files = self.project_and_files_from_px_xml(Path(path).read_text(encoding="utf-8", errors="replace"))
            self.index.upsert_project(project)
            self.index.replace_files(self.name, project.primary_accession, project_files)
            projects += 1
            files += len(project_files)
            if report:
                report(f"Indexed iProX XML {path}: {project.primary_accession}, {len(project_files)} files")
        return {"projects": projects, "files": files}

    def project_and_files_from_px_xml(self, text: str) -> tuple[CanonicalProject, list[CanonicalFile]]:
        root = ET.fromstring(text)

        def local(tag: str) -> str:
            return tag.rsplit("}", 1)[-1]

        def elements(name: str):
            return [element for element in root.iter() if local(element.tag) == name]

        root_id = root.attrib.get("id")
        native_accession: str | None = None
        px_accession: str | None = root_id if root_id and root_id.upper().startswith("PXD") else None
        for identifier in elements("DatasetIdentifier"):
            repository = (identifier.attrib.get("repository") or "").lower()
            accession = identifier.attrib.get("accession") or identifier.attrib.get("id")
            if not accession:
                continue
            if repository == "iprox" or accession.upper().startswith("IPX"):
                native_accession = accession
            elif accession.upper().startswith("PXD"):
                px_accession = accession
        primary = native_accession or px_accession or root_id or "unknown"
        title = self._first_xml_text(root, {"title"})
        species = self._xml_cv_values(root, parent_names={"speciesList"}, prefer_value=True)
        instruments = self._xml_cv_values(root, parent_names={"instrumentList"}, prefer_value=False)
        project = CanonicalProject(
            repository=self.name,
            primary_accession=primary,
            native_accession=native_accession,
            px_accession=px_accession,
            title=title,
            organisms=[CanonicalMetadataValue(value=value, source="iprox.px_xml.species") for value in species],
            instruments=[CanonicalMetadataValue(value=value, source="iprox.px_xml.instrument") for value in instruments],
            raw_metadata={"source": "px_xml", "native_accession": native_accession, "px_accession": px_accession},
        )
        files: list[CanonicalFile] = []
        for dataset_file in elements("DatasetFile"):
            name = dataset_file.attrib.get("name") or dataset_file.attrib.get("id") or ""
            url = ""
            for child in list(dataset_file.iter()):
                if local(child.tag) != "cvParam":
                    continue
                value = child.attrib.get("value") or ""
                if value:
                    url = value
                    break
            logical_path = self._logical_path_from_url_or_name(url, name)
            transfer = "unknown"
            if url.startswith("aspera://"):
                transfer = "aspera"
                url = url.removeprefix("aspera://")
            elif url.startswith(("http://", "https://")):
                transfer = "https"
            elif "@" in url and ":" in url:
                transfer = "aspera"
            files.append(
                CanonicalFile(
                    repository=self.name,
                    project_accession=project.primary_accession,
                    file_name=name or Path(logical_path).name,
                    logical_path=logical_path,
                    download_urls=[url] if url else [],
                    transfer_method=transfer,
                    raw_record={"name": name, "url": url, "source": "px_xml"},
                )
            )
        return project, files

    def match_file(self, task: InputTask, files: list[CanonicalFile]) -> CanonicalFile | None:
        return match_canonical_file(task, files)

    def build_project_context(self, resolution: ProjectResolution, file_name: str) -> ProjectContext:
        if resolution.primary_project is None:
            raise ValueError("Cannot build iProX context without a primary project.")
        project = self.get_project(resolution.primary_project.project_accession)
        files = self.list_project_files(project)
        metadata = {
            "title": MetadataValue(value=project.title, source="iprox.title", source_level="project", completeness=1.0 if project.title else 0.0),
            "projectDescription": MetadataValue(value=project.description, source="iprox.description", source_level="project", completeness=1.0 if project.description else 0.0),
            "sampleProcessingProtocol": MetadataValue(value=project.sample_processing_protocol.value if project.sample_processing_protocol else None, source="iprox.sample_processing_protocol", source_level="project", completeness=1.0 if project.sample_processing_protocol else 0.0),
            "dataProcessingProtocol": MetadataValue(value=project.data_processing_protocol.value if project.data_processing_protocol else None, source="iprox.data_processing_protocol", source_level="project", completeness=1.0 if project.data_processing_protocol else 0.0),
            "organisms": MetadataValue(value=[item.value for item in project.organisms], source="iprox.organisms", source_level="project", completeness=1.0 if project.organisms else 0.0),
            "instruments": MetadataValue(value=[item.value for item in project.instruments], source="iprox.instruments", source_level="project", completeness=1.0 if project.instruments else 0.0),
            "experimentTypes": MetadataValue(value=[item.value for item in project.experiment_types], source="iprox.experiment_types", source_level="project", completeness=1.0 if project.experiment_types else 0.0),
            "keywords": MetadataValue(value=project.keywords, source="iprox.keywords", source_level="project", completeness=1.0 if project.keywords else 0.0),
        }
        evidence = [
            {"source": "iprox.proxi_or_index", "text": f"title={project.title or ''}; description={project.description or ''}"},
            {"source": "iprox.file_paths", "text": "; ".join(file.logical_path or file.file_name for file in files[:200])},
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
        if asset.transfer_method == "aspera":
            raise ValueError(self.aspera_command(asset, target_path.parent))
        return download_file_asset(self.client, asset.model_copy(update={"local_path": target_path}), report=report)

    def download_to_path(self, url: str, target_path: str | Path, report: Callable | None = None) -> Path:
        return self.client.download_to_path(url, target_path, report=report)

    def aspera_command(self, asset: FileAsset, output_dir: str | Path) -> str:
        project = asset.native_project_accession or asset.project_accession or ""
        logical = asset.logical_path or asset.matched_project_file or asset.original_file_name
        user = self.aspera_username or "<iprox_username>"
        return (
            "iProX file requires Aspera download. Run: "
            f"ascp -d -QT -l1000m -P 33001 --file-manifest=text -k 2 -o Overwrite=diff "
            f"{user}@download.iprox.org:/data/iprox/{project}/{logical} {Path(output_dir)}"
        )

    def _project_xml_placeholder(self, project: CanonicalProject) -> CanonicalFile:
        accession = project.native_accession or project.primary_accession
        file_name = f"PX_{accession}.xml"
        user = self.aspera_username or "<iprox_username>"
        aspera_url = f"{user}@download.iprox.org:/data/iprox/{accession}/{file_name}"
        return CanonicalFile(
            repository=self.name,
            project_accession=project.primary_accession,
            file_name=file_name,
            logical_path=file_name,
            file_category="metadata",
            file_format="xml",
            download_urls=[aspera_url],
            transfer_method="aspera",
            raw_record={
                "source": "iprox.project_xml_placeholder",
                "note": "PROXI did not provide dataFiles; download this PX XML via Aspera and import it with sync-repository-index --xml-dir.",
            },
        )

    def map_project(self, raw: dict[str, Any], fallback_accession: str = "") -> CanonicalProject:
        native_accession = _first_text(raw.get("accession"), raw.get("project_id"), raw.get("projectId"), fallback_accession if fallback_accession.upper().startswith("IPX") else None)
        px_accession = _first_text(raw.get("px_accession"), raw.get("proteomeXchangeAccession"), raw.get("pxid"), fallback_accession if fallback_accession.upper().startswith("PXD") else None)
        primary = native_accession or px_accession or fallback_accession
        sample_protocol = _first_text(raw.get("sample_processing_protocol"), raw.get("sampleProtocol"))
        data_protocol = _first_text(raw.get("data_processing_protocol"), raw.get("dataProtocol"), raw.get("informaticsProtocol"))
        return CanonicalProject(
            repository=self.name,
            primary_accession=primary,
            native_accession=native_accession,
            px_accession=px_accession,
            title=_first_text(raw.get("title"), raw.get("project_title"), raw.get("projectTitle")),
            description=_first_text(raw.get("description"), raw.get("project_description"), raw.get("summary")),
            organisms=_metadata_values(_first_text(raw.get("species"), raw.get("organism")), "iprox.organism"),
            instruments=_metadata_values(_first_text(raw.get("instrument"), raw.get("instruments")), "iprox.instrument"),
            experiment_types=_metadata_values(_first_text(raw.get("experiment_type"), raw.get("submission_type")), "iprox.experiment_type"),
            keywords=_list_text(raw.get("keywords") or raw.get("projectTag")),
            sample_processing_protocol=CanonicalMetadataValue(value=sample_protocol, source="iprox.sample_processing_protocol") if sample_protocol else None,
            data_processing_protocol=CanonicalMetadataValue(value=data_protocol, source="iprox.data_processing_protocol") if data_protocol else None,
            submission_date=_first_text(raw.get("submission_date"), raw.get("submitDate")),
            publication_date=_first_text(raw.get("publication_date"), raw.get("release_date"), raw.get("publicStartDate")),
            raw_metadata=raw,
        )

    def map_file(self, raw: dict[str, Any], project: CanonicalProject) -> CanonicalFile:
        logical_path = _first_text(raw.get("logicalPath"), raw.get("path"), raw.get("filePath"), raw.get("file"))
        file_name = _first_text(raw.get("fileName"), raw.get("filename"), raw.get("name"))
        if not file_name and logical_path:
            file_name = logical_path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        public_locations = raw.get("publicFileLocations")
        public_url = None
        if isinstance(public_locations, list) and public_locations:
            first_location = public_locations[0]
            public_url = first_location.get("value") if isinstance(first_location, dict) else str(first_location)
        transfer_hint = _first_text(raw.get("transferMethod"), raw.get("transfer_method"))
        url = _first_text(raw.get("download_url"), raw.get("http_url"), raw.get("url"), public_url)
        aspera_path = _first_text(raw.get("aspera_path"), raw.get("aspera"))
        transfer = transfer_hint if transfer_hint in {"https", "ftp", "aspera"} else "unknown"
        if transfer == "unknown" and url and url.startswith(("http://", "https://")):
            transfer = "https"
        if not url and aspera_path:
            url = aspera_path
            transfer = "aspera"
        elif not url and project.native_accession:
            file_path = logical_path or file_name or ""
            url = f"{self.aspera_username or '<iprox_username>'}@download.iprox.org:/data/iprox/{project.native_accession}/{file_path}"
            transfer = "aspera"
        return CanonicalFile(
            repository=self.name,
            project_accession=project.primary_accession,
            file_name=file_name or logical_path or "",
            logical_path=logical_path,
            file_category=_first_text(raw.get("fileCategory"), raw.get("file_type"), raw.get("type")),
            file_format=_first_text(raw.get("file_format"), raw.get("format")),
            size_bytes=self._to_int(_first_text(raw.get("size_bytes"), raw.get("fileSize"), raw.get("size"))),
            checksum=_first_text(raw.get("checksum"), raw.get("md5")),
            download_urls=[url] if url else [],
            transfer_method=transfer,
            raw_record=raw,
        )

    def _raw_file_records(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        for key in ("files", "fileList", "data_files", "dataFile", "dataFiles"):
            value = raw.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    def _metadata_consistency(self, project: CanonicalProject) -> float:
        checks = [bool(project.organisms), bool(project.instruments), bool(project.description), bool(project.sample_processing_protocol), bool(project.data_processing_protocol)]
        return sum(checks) / len(checks)

    @staticmethod
    def _to_int(value: str | None) -> int | None:
        if not value:
            return None
        match = re.search(r"\d+", value.replace(",", ""))
        return int(match.group(0)) if match else None

    def _first_xml_text(self, root: ET.Element, names: set[str]) -> str | None:
        for element in root.iter():
            local = element.tag.rsplit("}", 1)[-1]
            if local in names and element.text and element.text.strip():
                return element.text.strip()
        return None

    def _xml_cv_values(self, root: ET.Element, parent_names: set[str], prefer_value: bool) -> list[str]:
        values: list[str] = []
        for parent in root.iter():
            local_parent = parent.tag.rsplit("}", 1)[-1]
            if local_parent not in parent_names:
                continue
            for child in parent.iter():
                local_child = child.tag.rsplit("}", 1)[-1]
                if local_child != "cvParam":
                    continue
                value = child.attrib.get("value") if prefer_value else None
                text = value or child.attrib.get("name")
                if text and text.strip():
                    values.append(text.strip())
        seen: set[str] = set()
        out: list[str] = []
        for value in values:
            key = value.lower()
            if key not in seen:
                seen.add(key)
                out.append(value)
        return out

    def _logical_path_from_url_or_name(self, url: str, name: str) -> str:
        cleaned = url.removeprefix("aspera://")
        marker = "/data/iprox/"
        if marker in cleaned:
            tail = cleaned.split(marker, 1)[1]
            parts = tail.split("/", 1)
            if len(parts) == 2:
                return parts[1]
        if cleaned.startswith(("http://", "https://")) and "/" in cleaned:
            return cleaned.rsplit("/", 1)[-1]
        return name
