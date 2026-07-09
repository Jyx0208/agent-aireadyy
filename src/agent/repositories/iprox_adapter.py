from __future__ import annotations

import json
import os
import re
import threading
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any, Callable, ClassVar, Iterable
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from xml.etree import ElementTree

from agent.assets.downloader import download_file_asset
from agent.input.normalizer import InputTask, normalize_input
from agent.metadata.canonical import CanonicalFile, CanonicalMetadataValue, CanonicalProject
from agent.models import FileAsset, MetadataValue, ProjectCandidate, ProjectContext, ProjectResolution
from agent.pride.resolver import resolve_primary_project
from agent.repositories.matching import (
    canonical_file_to_asset,
    canonical_files_to_project_file_records,
    classify_asset_type,
    match_canonical_file,
    score_file_match,
)


_IPROX_INDEX_ENV = "AGENT_IPROX_INDEX_XLSX"
_IPROX_JSONL_INDEX_DIR_ENV = "AGENT_IPROX_INDEX_DIR"
_IPROX_METADATA_CACHE_ENV = "AGENT_IPROX_METADATA_CACHE_DIR"
_IPROX_ACCESSION_RE = re.compile(r"\bIPX\d{6,}\b", re.IGNORECASE)
_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_DOWNLOAD_CHUNK_SIZE = 1024 * 1024
_IPROX_PUBLIC_BASE = "https://www.iprox.cn"
_IPROX_PUBLIC_DOWNLOAD_BASE = "https://download.iprox.cn"


def default_iprox_index_path() -> Path:
    configured = os.getenv(_IPROX_INDEX_ENV)
    if configured:
        return Path(configured)
    roots = [Path.cwd(), Path(__file__).resolve().parents[3]]
    for root in roots:
        matches = sorted(root.glob("iProx*.xlsx"))
        if matches:
            return matches[0]
    return roots[0] / "iprox.xlsx"


def default_iprox_index_dir() -> Path:
    configured = os.getenv(_IPROX_JSONL_INDEX_DIR_ENV)
    if configured:
        return Path(configured)
    roots = [Path.cwd(), Path(__file__).resolve().parents[3]]
    for root in roots:
        candidate = root / "data" / "iprox_index"
        if candidate.exists():
            return candidate
    return roots[0] / "data" / "iprox_index"


def default_iprox_file_index_path() -> Path:
    return default_iprox_index_dir() / "iprox_file_index.jsonl"


def default_iprox_metadata_cache_dir() -> Path:
    configured = os.getenv(_IPROX_METADATA_CACHE_ENV)
    if configured:
        return Path(configured)
    return Path.cwd() / ".agent_cache" / "iprox_metadata"


def extract_iprox_accessions(value: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for match in _IPROX_ACCESSION_RE.finditer(str(value or "")):
        accession = match.group(0).upper()
        if accession not in seen:
            seen.add(accession)
            out.append(accession)
    return out


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _column_index(reference: str | None) -> int:
    letters = re.match(r"([A-Z]+)", str(reference or "").upper())
    if not letters:
        return 0
    index = 0
    for char in letters.group(1):
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        handle = archive.open("xl/sharedStrings.xml")
    except KeyError:
        return []
    with handle:
        root = ElementTree.parse(handle).getroot()
    values: list[str] = []
    for item in root:
        if _local_name(item.tag) != "si":
            continue
        values.append("".join(node.text or "" for node in item.iter() if _local_name(node.tag) == "t"))
    return values


def _first_sheet_path(archive: zipfile.ZipFile) -> str:
    try:
        with archive.open("xl/workbook.xml") as handle:
            workbook = ElementTree.parse(handle).getroot()
    except KeyError:
        return "xl/worksheets/sheet1.xml"

    relationship_id = ""
    for item in workbook.iter():
        if _local_name(item.tag) == "sheet":
            relationship_id = item.attrib.get(_REL_NS) or item.attrib.get("r:id") or ""
            break
    if not relationship_id:
        return "xl/worksheets/sheet1.xml"

    with archive.open("xl/_rels/workbook.xml.rels") as handle:
        rels = ElementTree.parse(handle).getroot()
    for rel in rels:
        if rel.attrib.get("Id") != relationship_id:
            continue
        target = str(rel.attrib.get("Target") or "worksheets/sheet1.xml").replace("\\", "/")
        if target.startswith("/"):
            return target.lstrip("/")
        if target.startswith("xl/"):
            return target
        return f"xl/{target}"
    return "xl/worksheets/sheet1.xml"


def _cell_value(cell: ElementTree.Element, shared: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iter() if _local_name(node.tag) == "t")
    raw = ""
    for child in cell:
        if _local_name(child.tag) == "v":
            raw = child.text or ""
            break
    if cell_type == "s" and raw:
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return ""
    return raw


def iter_xlsx_rows(path: str | Path) -> Iterable[dict[str, str]]:
    path = Path(path)
    with zipfile.ZipFile(path) as archive:
        shared = _shared_strings(archive)
        sheet_path = _first_sheet_path(archive)
        header: list[str] | None = None
        with archive.open(sheet_path) as handle:
            for event, row in ElementTree.iterparse(handle, events=("end",)):
                if _local_name(row.tag) != "row":
                    continue
                values_by_index: dict[int, str] = {}
                for cell in row:
                    if _local_name(cell.tag) != "c":
                        continue
                    values_by_index[_column_index(cell.attrib.get("r"))] = _cell_value(cell, shared)
                if values_by_index:
                    max_index = max(values_by_index)
                    values = [values_by_index.get(index, "") for index in range(max_index + 1)]
                else:
                    values = []
                if header is None:
                    header = [str(value).strip() for value in values]
                elif any(str(value).strip() for value in values):
                    yield {name: values[index] if index < len(values) else "" for index, name in enumerate(header) if name}
                row.clear()


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _to_int(value: Any) -> int | None:
    text = str(value or "").replace(",", "").strip()
    return int(text) if text.isdigit() else None


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _dedupe_text(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        key = text.lower()
        if text and key not in seen and key != "null":
            seen.add(key)
            out.append(text)
    return out


def _url_transfer_method(url: str | None) -> str:
    if not url:
        return "unknown"
    if url.startswith("ftp://"):
        return "ftp"
    if url.startswith(("http://", "https://")):
        return "https"
    return "unknown"


def _quote_download_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.scheme:
        return url
    path = quote(unquote(parts.path), safe="/%:@")
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def _normalize_public_iprox_url(url: str | None) -> str | None:
    if not url:
        return None
    text = str(url).strip()
    if text.startswith("http://download.iprox.org/"):
        text = "https://download.iprox.cn/" + text[len("http://download.iprox.org/") :]
    elif text.startswith("https://download.iprox.org/"):
        text = "https://download.iprox.cn/" + text[len("https://download.iprox.org/") :]
    return _quote_download_url(text)


@dataclass(frozen=True)
class IproxFileRecord:
    project_id: str
    file_name: str
    file_path: str | None = None
    org_file_path: str | None = None
    download_url: str | None = None
    file_size: int | None = None
    checksum: str | None = None
    file_type: str | None = None
    is_dia: bool = False
    is_dda: bool = False
    is_raw: bool = False
    raw: dict[str, str] | None = None

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "IproxFileRecord":
        return cls(
            project_id=str(row.get("project_id") or "").strip().upper(),
            file_name=str(row.get("file_name") or "").strip(),
            file_path=_first_text(row.get("file_path")),
            org_file_path=_first_text(row.get("org_file_path")),
            download_url=_normalize_public_iprox_url(_first_text(row.get("down_url"))),
            file_size=_to_int(row.get("file_size")),
            checksum=_first_text(row.get("checksum")),
            file_type=_first_text(row.get("file_type")),
            is_dia=_truthy(row.get("is_dia")),
            is_dda=_truthy(row.get("is_dda")),
            is_raw=_truthy(row.get("is_raw")),
            raw=dict(row),
        )


@dataclass(frozen=True)
class IproxProjectMetadata:
    title: str | None = None
    description: str | None = None
    organisms: tuple[str, ...] = ()
    instruments: tuple[str, ...] = ()
    experiment_types: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    publication_date: str | None = None


def parse_iprox_project_xml(xml_text: str | bytes) -> IproxProjectMetadata:
    if isinstance(xml_text, bytes):
        xml_text = xml_text.decode("utf-8", errors="replace")
    root = ElementTree.fromstring(xml_text)

    def descendants(parent: ElementTree.Element, name: str) -> list[ElementTree.Element]:
        return [item for item in parent.iter() if _local_name(item.tag) == name]

    summaries = descendants(root, "SubdatasetSummary") or descendants(root, "DatasetSummary")
    summary = summaries[0] if summaries else None
    title = summary.attrib.get("title") if summary is not None else None
    publication_date = summary.attrib.get("announceDate") if summary is not None else None
    description = None
    if summary is not None:
        for item in summary:
            if _local_name(item.tag) == "Description":
                description = (item.text or "").strip() or None
                break

    organisms: list[str] = []
    for species_list in descendants(root, "SpeciesList"):
        for param in descendants(species_list, "cvParam"):
            if "scientific name" in str(param.attrib.get("name") or "").lower():
                organisms.append(param.attrib.get("value") or "")

    instruments: list[str] = []
    for instrument_list in descendants(root, "InstrumentList"):
        for param in descendants(instrument_list, "cvParam"):
            name = str(param.attrib.get("name") or "").strip()
            value = str(param.attrib.get("value") or "").strip()
            candidate = value if value and value.lower() != "null" else name
            if candidate and candidate.lower() not in {"instrument", "instrument model"}:
                instruments.append(candidate)

    keywords: list[str] = []
    for keyword_list in descendants(root, "KeywordList"):
        for param in descendants(keyword_list, "cvParam"):
            value = str(param.attrib.get("value") or "").strip()
            name = str(param.attrib.get("name") or "").strip()
            if value:
                keywords.append(value)
            elif name and "keyword" not in name.lower():
                keywords.append(name)

    evidence_text = " ".join([title or "", description or "", *keywords])
    experiment_types: list[str] = []
    if re.search(r"\bDDA\b|data[- ]dependent", evidence_text, re.IGNORECASE):
        experiment_types.append("DDA")
    if re.search(r"\bDIA\b|SWATH|data[- ]independent", evidence_text, re.IGNORECASE):
        experiment_types.append("DIA")

    return IproxProjectMetadata(
        title=title,
        description=description,
        organisms=tuple(_dedupe_text(organisms)),
        instruments=tuple(_dedupe_text(instruments)),
        experiment_types=tuple(_dedupe_text(experiment_types)),
        keywords=tuple(_dedupe_text(keywords)),
        publication_date=publication_date,
    )


def _read_json_payload(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("{"):
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {"data": payload}
    match = re.search(r"\((\{.*\})\)\s*;?\s*$", text, flags=re.DOTALL)
    if match:
        payload = json.loads(match.group(1))
        return payload if isinstance(payload, dict) else {"data": payload}
    raise ValueError("Unsupported iProX JSON/JSONP response.")


def _fetch_text(url: str, timeout: int = 45) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _project_ids_for_year(year: int, *, base_url: str = _IPROX_PUBLIC_BASE) -> list[str]:
    url = f"{base_url.rstrip('/')}/projectFileList/getProjectDataFileByYear.jsonp?date={int(year)}"
    payload = _read_json_payload(_fetch_text(url))
    data = payload.get("data") or []
    return [str(item).strip().upper() for item in data if str(item).strip()]


def _xml_url_for_project(project_id: str, *, download_base: str = _IPROX_PUBLIC_DOWNLOAD_BASE) -> str:
    project_id = str(project_id or "").strip().upper()
    return f"{download_base.rstrip('/')}/{project_id}/PX_{project_id}.xml"


def _iprox_accessions_from_url(url: str | None) -> list[str]:
    if not url:
        return []
    return extract_iprox_accessions(unquote(urlsplit(str(url)).path))


def _suffix_file_type(file_name: str) -> str | None:
    lower = file_name.casefold()
    for suffix in (".raw", ".mzml", ".mzxml", ".mgf", ".wiff", ".d", ".tsv", ".txt", ".csv", ".xml", ".mzid"):
        if lower.endswith(suffix):
            return suffix.lstrip(".")
    return None


def _parse_file_records_from_project_xml(project_id: str, xml_text: str | bytes) -> list[dict[str, Any]]:
    if isinstance(xml_text, bytes):
        xml_text = xml_text.decode("utf-8", errors="replace")
    root = ElementTree.fromstring(xml_text)
    records: list[dict[str, Any]] = []
    for node in root.iter():
        if _local_name(node.tag) != "DatasetFile":
            continue
        file_name = str(node.attrib.get("name") or "").strip()
        if not file_name:
            continue
        url = None
        for param in node.iter():
            if _local_name(param.tag) != "cvParam":
                continue
            name = str(param.attrib.get("name") or "").casefold()
            value = str(param.attrib.get("value") or "").strip()
            if value and ("uri" in name or value.startswith(("http://", "https://", "ftp://"))):
                url = _normalize_public_iprox_url(value)
                break
        file_type = _suffix_file_type(file_name)
        record_project_ids = [str(project_id or "").strip().upper()]
        for accession in _iprox_accessions_from_url(url):
            if accession not in record_project_ids:
                record_project_ids.append(accession)
        for record_project_id in record_project_ids:
            records.append(
                {
                    "project_id": record_project_id,
                    "parent_project_id": project_id if record_project_id != project_id else "",
                    "file_name": file_name,
                    "file_path": url,
                    "org_file_path": url,
                    "down_url": url,
                    "file_size": "",
                    "checksum": "",
                    "file_type": file_type or "",
                    "is_dia": "1" if file_type == "mzml" and "dia" in file_name.casefold() else "0",
                    "is_dda": "1" if file_type in {"raw", "mzml", "mzxml", "mgf"} else "0",
                    "is_raw": "1" if file_type in {"raw", "mzml", "mzxml", "wiff", "d", "mgf"} else "0",
                }
            )
    return records


def refresh_public_iprox_index(
    *,
    years: Iterable[int] = (),
    project_ids: Iterable[str] | None = None,
    output_dir: str | Path,
    base_url: str = _IPROX_PUBLIC_BASE,
    download_base: str = _IPROX_PUBLIC_DOWNLOAD_BASE,
    max_projects: int | None = None,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    project_index_path = output / "iprox_project_index.jsonl"
    file_index_path = output / "iprox_file_index.jsonl"
    summary_path = output / "iprox_index_summary.json"
    xml_dir = output / "project_xml"
    xml_dir.mkdir(parents=True, exist_ok=True)

    project_rows: list[dict[str, Any]] = []
    file_rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    seen_projects: set[str] = set()

    requested_projects = _dedupe_text(str(item).strip().upper() for item in (project_ids or []) if str(item).strip())
    year_project_pairs: list[tuple[int | None, str]] = [(None, project_id) for project_id in requested_projects]
    for year in years:
        try:
            year_project_pairs.extend((int(year), project_id) for project_id in _project_ids_for_year(int(year), base_url=base_url))
        except Exception as exc:
            failures.append({"stage": "list_projects", "year": int(year), "error": str(exc)})
            continue
    if not year_project_pairs:
        failures.append({"stage": "list_projects", "error": "no_years_or_projects_requested"})

    for year, project_id in year_project_pairs:
        if project_id in seen_projects:
            continue
        if max_projects is not None and len(seen_projects) >= max_projects:
            break
        seen_projects.add(project_id)
        xml_url = _xml_url_for_project(project_id, download_base=download_base)
        project_row = {
            "repository": "iprox",
            "project_id": project_id,
            "native_accession": project_id,
            "px_accession": "",
            "year": int(year) if year is not None else "",
            "xml_url": xml_url,
        }
        try:
            xml_text = _fetch_text(xml_url)
            (xml_dir / f"{project_id}.xml").write_text(xml_text, encoding="utf-8")
            metadata = parse_iprox_project_xml(xml_text)
            project_row.update(
                {
                    "title": metadata.title or "",
                    "description": metadata.description or "",
                    "organisms": list(metadata.organisms),
                    "instruments": list(metadata.instruments),
                    "experiment_types": list(metadata.experiment_types),
                    "keywords": list(metadata.keywords),
                    "publication_date": metadata.publication_date or "",
                }
            )
            file_rows.extend(_parse_file_records_from_project_xml(project_id, xml_text))
            file_rows.append(
                {
                    "project_id": project_id,
                    "file_name": f"PX_{project_id}.xml",
                    "file_path": xml_url,
                    "org_file_path": xml_url,
                    "down_url": xml_url,
                    "file_size": "",
                    "checksum": "",
                    "file_type": "xml",
                    "is_dia": "0",
                    "is_dda": "0",
                    "is_raw": "0",
                }
            )
        except Exception as exc:
            project_row["xml_status"] = "unavailable"
            failures.append({"stage": "download_project_xml", "project_id": project_id, "url": xml_url, "error": str(exc)})
        project_rows.append(project_row)
        if max_projects is not None and len(seen_projects) >= max_projects:
            break

    with project_index_path.open("w", encoding="utf-8") as handle:
        for row in project_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    with file_index_path.open("w", encoding="utf-8") as handle:
        for row in file_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = {
        "status": "ready" if file_rows else "blocked",
        "years": [int(year) for year in years],
        "requested_projects": requested_projects,
        "project_count": len(project_rows),
        "file_count": len(file_rows),
        "project_index": str(project_index_path),
        "file_index": str(file_index_path),
        "failures": failures,
        "next_step": "set_AGENT_IPROX_INDEX_DIR_or_pass_index_path" if file_rows else "retry_refresh_iprox_index_or_set_agent_iprox_index_xlsx",
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


class IproxXlsxIndex:
    _CACHE: ClassVar[
        dict[tuple[str, int, int], tuple[list[IproxFileRecord], dict[str, list[IproxFileRecord]], dict[str, list[IproxFileRecord]]]]
    ] = {}
    _CACHE_LOCK: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_iprox_index_path()
        self._loaded = False
        self._records: list[IproxFileRecord] = []
        self._records_by_project: dict[str, list[IproxFileRecord]] = {}
        self._records_by_name: dict[str, list[IproxFileRecord]] = {}

    def load(self) -> None:
        if self._loaded:
            return
        if not self.path.exists():
            raise FileNotFoundError(f"iProX mapping workbook not found: {self.path}")
        stat = self.path.stat()
        cache_key = (str(self.path.resolve()), stat.st_mtime_ns, stat.st_size)
        with self._CACHE_LOCK:
            cached = self._CACHE.get(cache_key)
        if cached is not None:
            self._records, self._records_by_project, self._records_by_name = cached
            self._loaded = True
            return

        records: list[IproxFileRecord] = []
        by_project: dict[str, list[IproxFileRecord]] = {}
        by_name: dict[str, list[IproxFileRecord]] = {}
        for row in iter_xlsx_rows(self.path):
            record = IproxFileRecord.from_row(row)
            if not record.project_id or not record.file_name:
                continue
            records.append(record)
            by_project.setdefault(record.project_id, []).append(record)
            by_name.setdefault(normalize_input(record.file_name).normalized_name, []).append(record)
        self._records = records
        self._records_by_project = by_project
        self._records_by_name = by_name
        self._loaded = True
        with self._CACHE_LOCK:
            self._CACHE[cache_key] = (records, by_project, by_name)

    def records_for_project(self, accession: str) -> list[IproxFileRecord]:
        self.load()
        return list(self._records_by_project.get(str(accession or "").strip().upper(), []))

    def all_records(self) -> list[IproxFileRecord]:
        self.load()
        return list(self._records)

    def find_file_records(self, file_name: str) -> list[IproxFileRecord]:
        self.load()
        task = normalize_input(file_name)
        exact = list(self._records_by_name.get(task.normalized_name, []))
        if exact:
            return exact
        matches: list[IproxFileRecord] = []
        for record in self._records:
            if score_file_match(task, record.file_name) is not None:
                matches.append(record)
        return matches


class IproxJsonlIndex:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_iprox_file_index_path()
        self._loaded = False
        self._records: list[IproxFileRecord] = []
        self._records_by_project: dict[str, list[IproxFileRecord]] = {}
        self._records_by_name: dict[str, list[IproxFileRecord]] = {}

    def load(self) -> None:
        if self._loaded:
            return
        if not self.path.exists():
            raise FileNotFoundError(f"iProX public JSONL index not found: {self.path}")
        records: list[IproxFileRecord] = []
        by_project: dict[str, list[IproxFileRecord]] = {}
        by_name: dict[str, list[IproxFileRecord]] = {}
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                record = IproxFileRecord.from_row({key: "" if value is None else str(value) for key, value in row.items()})
                if not record.project_id or not record.file_name:
                    continue
                records.append(record)
                by_project.setdefault(record.project_id, []).append(record)
                by_name.setdefault(normalize_input(record.file_name).normalized_name, []).append(record)
        self._records = records
        self._records_by_project = by_project
        self._records_by_name = by_name
        self._loaded = True

    def records_for_project(self, accession: str) -> list[IproxFileRecord]:
        self.load()
        return list(self._records_by_project.get(str(accession or "").strip().upper(), []))

    def all_records(self) -> list[IproxFileRecord]:
        self.load()
        return list(self._records)

    def find_file_records(self, file_name: str) -> list[IproxFileRecord]:
        self.load()
        task = normalize_input(file_name)
        exact = list(self._records_by_name.get(task.normalized_name, []))
        if exact:
            return exact
        return [record for record in self._records if score_file_match(task, record.file_name) is not None]


def _make_iprox_index(index_path: str | Path | None = None) -> IproxXlsxIndex | IproxJsonlIndex:
    if index_path is not None:
        path = Path(index_path)
        if path.is_dir():
            return IproxJsonlIndex(path / "iprox_file_index.jsonl")
        if path.suffix.lower() == ".jsonl":
            return IproxJsonlIndex(path)
        return IproxXlsxIndex(path)
    jsonl_path = default_iprox_file_index_path()
    if jsonl_path.exists():
        return IproxJsonlIndex(jsonl_path)
    return IproxXlsxIndex(default_iprox_index_path())


class IproxAdapter:
    name = "iprox"

    def __init__(
        self,
        index_path: str | Path | None = None,
        index: IproxXlsxIndex | IproxJsonlIndex | None = None,
        metadata_cache_dir: str | Path | None = None,
    ) -> None:
        self.index = index or _make_iprox_index(index_path)
        self.metadata_cache_dir = Path(metadata_cache_dir) if metadata_cache_dir is not None else default_iprox_metadata_cache_dir()

    def can_handle_accession(self, value: str) -> bool:
        return bool(extract_iprox_accessions(value))

    def resolve_project(self, raw_input: str) -> ProjectResolution:
        task = normalize_input(raw_input)
        try:
            accessions = extract_iprox_accessions(raw_input)
            if accessions:
                for accession in reversed(accessions):
                    records = self.index.records_for_project(accession)
                    if records:
                        return self._resolution_from_records(task, records, explicit_accession=accession)
                return ProjectResolution.empty().model_copy(
                    update={
                        "resolution_reason": f"iProX accession was found, but no rows matched it in {self.index.path}.",
                    }
                )
            records = self.index.find_file_records(task.file_name)
        except FileNotFoundError as exc:
            return ProjectResolution.empty().model_copy(update={"resolution_reason": str(exc)})
        return self._resolution_from_records(task, records, explicit_accession=None)

    def _resolution_from_records(
        self,
        task: InputTask,
        records: list[IproxFileRecord],
        explicit_accession: str | None,
    ) -> ProjectResolution:
        candidates: list[ProjectCandidate] = []
        for project_id, project_records in self._group_by_project(records).items():
            project = self.get_project(project_id)
            matched = self.match_file(task, [self.map_file(record, project) for record in project_records])
            if matched is not None:
                score, match_type = score_file_match(task, matched.file_name) or (100, "exact")
                matched_file = matched.file_name
            elif explicit_accession:
                score, match_type = 100, "accession"
                matched_file = task.file_name
            else:
                continue
            evidence = [f"iProX index file-to-project mapping: {self.index.path}"]
            if explicit_accession:
                evidence.append("iProX accession input")
            candidates.append(
                ProjectCandidate(
                    repository=self.name,
                    project_accession=project.primary_accession,
                    native_accession=project.native_accession,
                    matched_file=matched_file,
                    match_type="accession_path" if explicit_accession and matched is not None else match_type,
                    match_score=score,
                    evidence=evidence,
                    metadata_consistency=self._metadata_consistency(project),
                )
            )
        return resolve_primary_project(candidates) if candidates else ProjectResolution.empty()

    def get_project(self, accession: str) -> CanonicalProject:
        accession = str(accession or "").strip().upper()
        try:
            records = self.index.records_for_project(accession)
        except FileNotFoundError:
            records = []
        xml_metadata = self._load_project_metadata(accession, records)
        experiment_types = _dedupe_text([*self._experiment_types(records), *xml_metadata.experiment_types])
        return CanonicalProject(
            repository=self.name,
            primary_accession=accession,
            native_accession=accession,
            title=xml_metadata.title or (f"iProX project {accession}" if accession else "iProX project"),
            description=xml_metadata.description,
            organisms=[CanonicalMetadataValue(value=item, source="iprox.project_xml") for item in xml_metadata.organisms],
            instruments=[CanonicalMetadataValue(value=item, source="iprox.project_xml") for item in xml_metadata.instruments],
            experiment_types=[CanonicalMetadataValue(value=item, source="iprox.xlsx") for item in experiment_types],
            keywords=_dedupe_text(["iProX", *xml_metadata.keywords]),
            publication_date=xml_metadata.publication_date,
            raw_metadata={
                "repository": "iprox",
                "project_id": accession,
                "index_path": str(self.index.path),
                "file_count": len(records),
                "project_metadata": {
                    "title": xml_metadata.title,
                    "description": xml_metadata.description,
                    "organisms": list(xml_metadata.organisms),
                    "instruments": list(xml_metadata.instruments),
                    "experiment_types": list(xml_metadata.experiment_types),
                    "keywords": list(xml_metadata.keywords),
                    "publication_date": xml_metadata.publication_date,
                },
            },
        )

    def search_projects(self, query: str, limit: int = 30) -> list[CanonicalProject]:
        query_text = str(query or "").casefold()
        query_tokens = [token for token in re.split(r"[^a-zA-Z0-9]+", query_text) if len(token) >= 3]
        try:
            records = self.index.all_records()
        except FileNotFoundError:
            raise
        scores: dict[str, int] = {}
        for record in records:
            haystack = " ".join(
                str(value or "")
                for value in [
                    record.project_id,
                    record.file_name,
                    record.file_path,
                    record.org_file_path,
                    record.file_type,
                    "dda" if record.is_dda else "",
                    "dia" if record.is_dia else "",
                    "raw" if record.is_raw else "",
                ]
            ).casefold()
            score = 0
            if record.project_id.casefold() in query_text:
                score += 100
            score += sum(1 for token in query_tokens if token in haystack)
            if score > 0:
                scores[record.project_id] = max(scores.get(record.project_id, 0), score)
        ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
        return [self.get_project(project_id) for project_id, _score in ordered]

    def list_project_files(self, project: CanonicalProject) -> list[CanonicalFile]:
        try:
            records = self.index.records_for_project(project.primary_accession)
        except FileNotFoundError:
            records = []
        return self._dedupe_files([self.map_file(record, project) for record in records])

    def match_file(self, task: InputTask, files: list[CanonicalFile]) -> CanonicalFile | None:
        return match_canonical_file(task, files)

    def build_project_context(self, resolution: ProjectResolution, file_name: str) -> ProjectContext:
        if resolution.primary_project is None:
            raise ValueError("Cannot build iProX context without a primary project.")
        project = self.get_project(resolution.primary_project.project_accession)
        files = self.list_project_files(project)
        metadata = {
            "title": MetadataValue(value=project.title, source="iprox.title", source_level="project", completeness=1.0 if project.title else 0.0),
            "projectDescription": MetadataValue(
                value=project.description,
                source="iprox.project_xml" if project.description else "iprox.xlsx",
                source_level="project",
                completeness=1.0 if project.description else 0.0,
            ),
            "sampleProcessingProtocol": MetadataValue(value=None, source="iprox.xlsx", source_level="project", completeness=0.0),
            "dataProcessingProtocol": MetadataValue(value=None, source="iprox.xlsx", source_level="project", completeness=0.0),
            "organisms": MetadataValue(
                value=[item.value for item in project.organisms],
                source="iprox.project_xml",
                source_level="project",
                completeness=1.0 if project.organisms else 0.0,
            ),
            "instruments": MetadataValue(
                value=[item.value for item in project.instruments],
                source="iprox.project_xml",
                source_level="project",
                completeness=1.0 if project.instruments else 0.0,
            ),
            "experimentTypes": MetadataValue(
                value=[item.value for item in project.experiment_types],
                source="iprox.xlsx",
                source_level="project",
                completeness=1.0 if project.experiment_types else 0.0,
            ),
            "keywords": MetadataValue(value=project.keywords, source="iprox.xlsx", source_level="project", completeness=1.0),
        }
        project_xml = project.raw_metadata.get("project_metadata") if isinstance(project.raw_metadata, dict) else None
        evidence_documents: list[dict[str, Any]] = []
        if isinstance(project_xml, dict) and any(
            project_xml.get(key) for key in ("title", "description", "organisms", "instruments", "keywords")
        ):
            evidence_documents.append(
                {
                    "source": "iprox.project_xml",
                    "text": (
                        f"title={project_xml.get('title') or ''}; description={project_xml.get('description') or ''}; "
                        f"organisms={project_xml.get('organisms') or []}; instruments={project_xml.get('instruments') or []}; "
                        f"keywords={project_xml.get('keywords') or []}"
                    ),
                }
            )
        evidence_documents.extend(
            [
                {"source": "iprox.xlsx", "text": f"mapping={self.index.path}; project_id={project.primary_accession}"},
                {"source": "iprox.file_paths", "text": "; ".join(file.logical_path or file.file_name for file in files[:200])},
            ]
        )
        return ProjectContext(
            repository=self.name,
            project_accession=project.primary_accession,
            native_accession=project.native_accession,
            file_name=file_name,
            metadata=metadata,
            project_files=canonical_files_to_project_file_records(files),
            evidence_documents=evidence_documents,
            raw_project_metadata=project.raw_metadata,
        )

    def resolve_file_asset(self, task: InputTask, context: ProjectContext, work_dir: str | Path) -> FileAsset:
        project = CanonicalProject(
            repository=self.name,
            primary_accession=context.project_accession,
            native_accession=context.native_accession,
        )
        files = [self._context_record_to_file(record, project) for record in context.project_files]
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
        return download_file_asset(self, asset.model_copy(update={"local_path": target_path}), report=report)

    def download_to_path(self, url: str, target_path: str | Path, report: Callable | None = None) -> Path:
        target_path = Path(target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        safe_url = _quote_download_url(url)
        downloaded = 0
        total = 0
        started = monotonic()
        with urllib.request.urlopen(safe_url, timeout=60) as response, target_path.open("wb") as handle:
            try:
                total = int(response.headers.get("Content-Length", "0") or "0")
            except (AttributeError, TypeError, ValueError):
                total = 0
            while True:
                chunk = response.read(_DOWNLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                if report:
                    elapsed = max(monotonic() - started, 0.001)
                    speed_bps = downloaded / elapsed
                    eta_seconds = ((total - downloaded) / speed_bps) if total > 0 and speed_bps > 0 else None
                    report(
                        {
                            "kind": "download_progress",
                            "label": target_path.name,
                            "downloaded": downloaded,
                            "total": total,
                            "speed_bps": speed_bps,
                            "eta_seconds": eta_seconds,
                            "complete": False,
                        }
                    )
        if report:
            elapsed = max(monotonic() - started, 0.001)
            speed_bps = downloaded / elapsed if downloaded > 0 else 0.0
            report(
                {
                    "kind": "download_progress",
                    "label": target_path.name,
                    "downloaded": downloaded,
                    "total": total,
                    "speed_bps": speed_bps,
                    "eta_seconds": 0,
                    "complete": True,
                }
            )
            report(f"Download complete: {target_path}")
        return target_path

    def map_file(self, record: IproxFileRecord, project: CanonicalProject) -> CanonicalFile:
        logical_path = _first_text(record.org_file_path, record.file_path)
        asset_type = classify_asset_type(record.file_name)
        category = "raw" if record.is_raw or asset_type in {"raw", "mzml", "mzxml", "tims", "wiff", "mgf", "mzid"} else "metadata"
        file_format = _first_text(record.file_type, asset_type if asset_type != "unknown" else None)
        primary_url = _normalize_public_iprox_url(record.download_url)
        download_urls = [primary_url] if primary_url else []
        return CanonicalFile(
            repository=self.name,
            project_accession=project.primary_accession,
            file_name=record.file_name,
            logical_path=logical_path,
            file_category=category,
            file_format=file_format,
            size_bytes=record.file_size,
            checksum=record.checksum,
            download_urls=download_urls,
            transfer_method=_url_transfer_method(primary_url),
            raw_record=record.raw or {},
        )

    def _context_record_to_file(self, record: dict[str, Any], project: CanonicalProject) -> CanonicalFile:
        locations = record.get("publicFileLocations")
        download_urls: list[str] = []
        if isinstance(locations, list):
            for location in locations:
                if isinstance(location, dict) and location.get("value"):
                    download_urls.append(str(location["value"]))
        raw = record.get("rawRecord") if isinstance(record.get("rawRecord"), dict) else {}
        return CanonicalFile(
            repository=self.name,
            project_accession=project.primary_accession,
            file_name=str(record.get("fileName") or ""),
            logical_path=_first_text(record.get("logicalPath")),
            file_category=(record.get("fileCategory") or {}).get("value") if isinstance(record.get("fileCategory"), dict) else None,
            file_format=_first_text(raw.get("file_type"), raw.get("fileFormat")),
            size_bytes=_to_int(record.get("fileSizeBytes")),
            checksum=_first_text(raw.get("checksum")),
            download_urls=download_urls,
            transfer_method=str(record.get("transferMethod") or _url_transfer_method(download_urls[0] if download_urls else None)),
            raw_record=raw,
        )

    @staticmethod
    def _group_by_project(records: Iterable[IproxFileRecord]) -> dict[str, list[IproxFileRecord]]:
        grouped: dict[str, list[IproxFileRecord]] = {}
        for record in records:
            grouped.setdefault(record.project_id, []).append(record)
        return grouped

    @staticmethod
    def _experiment_types(records: list[IproxFileRecord]) -> list[str]:
        values: list[str] = []
        if any(record.is_dda for record in records):
            values.append("DDA")
        if any(record.is_dia for record in records):
            values.append("DIA")
        return values

    def _load_project_metadata(self, accession: str, records: list[IproxFileRecord]) -> IproxProjectMetadata:
        cache_path = self.metadata_cache_dir / f"{accession}.xml"
        if not cache_path.exists():
            xml_record = self._metadata_xml_record(accession, records)
            candidate_urls = []
            if xml_record is not None and xml_record.download_url:
                candidate_urls.append(xml_record.download_url)
            candidate_urls.extend(self._inferred_metadata_xml_urls(accession, records))
            for candidate_url in candidate_urls:
                try:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    with urllib.request.urlopen(_quote_download_url(candidate_url), timeout=30) as response:
                        cache_path.write_bytes(response.read())
                    break
                except Exception:
                    continue
        try:
            if cache_path.exists():
                return parse_iprox_project_xml(cache_path.read_bytes())
        except Exception:
            return IproxProjectMetadata()
        return IproxProjectMetadata()

    @staticmethod
    def _metadata_xml_record(accession: str, records: list[IproxFileRecord]) -> IproxFileRecord | None:
        exact_name = f"{accession.lower()}.xml"
        xml_records = [record for record in records if record.file_name.lower().endswith(".xml")]
        for record in xml_records:
            if record.file_name.lower() == exact_name:
                return record
        return xml_records[0] if xml_records else None

    @staticmethod
    def _inferred_metadata_xml_urls(accession: str, records: list[IproxFileRecord]) -> list[str]:
        accession = str(accession or "").strip().upper()
        urls: list[str] = []
        for record in records:
            accessions = _iprox_accessions_from_url(record.download_url)
            if len(accessions) >= 2 and accessions[-1] == accession:
                parent = accessions[-2]
                urls.append(f"{_IPROX_PUBLIC_DOWNLOAD_BASE}/{parent}/{accession}/{accession}.xml")
        return _dedupe_text(urls)

    @staticmethod
    def _dedupe_files(files: list[CanonicalFile]) -> list[CanonicalFile]:
        seen: set[tuple[str, str | None]] = set()
        out: list[CanonicalFile] = []
        for file in files:
            key = (file.file_name.lower(), file.logical_path)
            if key in seen:
                continue
            seen.add(key)
            out.append(file)
        return out

    @staticmethod
    def _metadata_consistency(project: CanonicalProject) -> float:
        checks = [bool(project.experiment_types), bool(project.keywords), bool(project.organisms), bool(project.instruments)]
        return sum(checks) / len(checks)
