from __future__ import annotations

from pathlib import Path
import zipfile
from xml.sax.saxutils import escape

import pytest

from agent.input.normalizer import normalize_input
from agent.metadata.canonical import CanonicalFile, CanonicalProject
from agent.models import FileAsset, ProjectCandidate, ProjectContext, ProjectResolution
from agent.orchestrator.pipeline import AgentService
from agent.repositories.index import RepositoryIndex
from agent.repositories.massive_adapter import MassiveAdapter
from agent.repositories.matching import canonical_files_to_project_file_records
from agent.repositories.pride_adapter import PrideAdapter
from agent.repositories.registry import RepositoryRegistry


IPROX_HEADERS = [
    "id",
    "project_id",
    "file_name",
    "file_path",
    "file_size",
    "statue",
    "checksum",
    "org_file_path",
    "down_url",
    "is_dia",
    "is_dda",
    "file_type",
    "is_raw",
]


def _write_iprox_xlsx(path: Path, rows: list[list[str]]) -> Path:
    values: list[str] = []
    indexes: dict[str, int] = {}

    def shared_index(value: str) -> int:
        if value not in indexes:
            indexes[value] = len(values)
            values.append(value)
        return indexes[value]

    table = [IPROX_HEADERS, *rows]
    row_xml: list[str] = []
    for row_index, row in enumerate(table, start=1):
        cells: list[str] = []
        for col_index, value in enumerate(row, start=1):
            col = ""
            cursor = col_index
            while cursor:
                cursor, rem = divmod(cursor - 1, 26)
                col = chr(ord("A") + rem) + col
            cells.append(f'<c r="{col}{row_index}" t="s"><v>{shared_index(value)}</v></c>')
        row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    shared_xml = "".join(f"<si><t>{escape(value)}</t></si>" for value in values)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
</Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>
</workbook>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            f"""<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(values)}" uniqueCount="{len(values)}">{shared_xml}</sst>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f"""<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>{''.join(row_xml)}</sheetData>
</worksheet>""",
        )
    return path


def _sample_iprox_xlsx(tmp_path: Path) -> Path:
    return _write_iprox_xlsx(
        tmp_path / "iprox.xlsx",
        [
            [
                "1",
                "IPX0015463001",
                "OsGF14f interacting proteins.raw",
                "/IPX0015463000/IPX0015463001/OsGF14f interacting proteins.raw",
                "780276859",
                "1",
                "c467",
                "raw/OsGF14f interacting proteins.raw",
                "https://download.iprox.cn/IPX0015463000/IPX0015463001/OsGF14f interacting proteins.raw",
                "0",
                "1",
                "raw",
                "1",
            ],
            [
                "2",
                "IPX0015463001",
                "IPX0015463001.xml",
                "/IPX0015463000/IPX0015463001/IPX0015463001.xml",
                "123",
                "1",
                "abc",
                "metadata/IPX0015463001.xml",
                "https://download.iprox.cn/IPX0015463000/IPX0015463001/IPX0015463001.xml",
                "0",
                "0",
                "xml",
                "0",
            ],
        ],
    )


def test_cli_exposes_iprox_support():
    cli_text = Path("src/agent/cli.py").read_text(encoding="utf-8")

    assert "sync-repository-index" not in cli_text
    assert "iprox" in cli_text.lower()


def test_default_registry_supports_pride_massive_and_iprox():
    registry = RepositoryRegistry()

    assert [adapter.name for adapter in registry.adapters] == ["pride", "massive", "iprox"]
    assert RepositoryRegistry.supported_names() == ("pride", "massive", "iprox")
    assert registry.get("iprox").name == "iprox"
    assert registry.choose("auto", "https://download.iprox.cn/IPX0015463000/IPX0015463001/sample.raw").name == "iprox"


def _repository_resolution(repository: str, accession: str, confidence: float, match_score: int = 100) -> ProjectResolution:
    return ProjectResolution(
        primary_project=ProjectCandidate(
            repository=repository,
            project_accession=accession,
            native_accession=accession,
            matched_file="shared.raw",
            match_type="exact",
            match_score=match_score,
            metadata_consistency=confidence,
        ),
        resolution_confidence=confidence,
        resolution_reason=f"{repository} matched shared.raw",
        needs_review=False,
    )


def test_agent_auto_repository_queries_all_adapters_and_selects_highest_confidence():
    calls: list[str] = []

    class Adapter:
        def __init__(self, name: str, resolution: ProjectResolution):
            self.name = name
            self._resolution = resolution

        def can_handle_accession(self, value: str) -> bool:
            return False

        def resolve_project(self, raw_input: str) -> ProjectResolution:
            calls.append(self.name)
            return self._resolution

    registry = RepositoryRegistry(
        adapters=[
            Adapter("pride", _repository_resolution("pride", "PXD_LOW", 0.55)),
            Adapter("massive", ProjectResolution.empty()),
            Adapter("iprox", _repository_resolution("iprox", "IPX_HIGH", 0.96)),
        ]
    )
    service = AgentService(repository_registry=registry, llm_reasoner=object())

    resolution = service.resolve_project_from_repository("shared.raw", repository="auto")

    assert calls == ["pride", "massive", "iprox"]
    assert resolution.primary_project is not None
    assert resolution.primary_project.repository == "iprox"
    assert resolution.primary_project.project_accession == "IPX_HIGH"
    assert resolution.alternative_projects[0].repository == "pride"
    assert "highest resolution confidence" in resolution.resolution_reason


def test_auto_repository_marks_equal_confidence_cross_repository_matches_for_review():
    class Adapter:
        def __init__(self, name: str, resolution: ProjectResolution):
            self.name = name
            self._resolution = resolution

        def can_handle_accession(self, value: str) -> bool:
            return False

        def resolve_project(self, raw_input: str) -> ProjectResolution:
            return self._resolution

    registry = RepositoryRegistry(
        adapters=[
            Adapter("pride", _repository_resolution("pride", "PXD_TIE", 1.0)),
            Adapter("iprox", _repository_resolution("iprox", "IPX_TIE", 1.0)),
        ]
    )

    resolution = registry.resolve_project("auto", "shared.raw")

    assert resolution.primary_project is not None
    assert resolution.needs_review is True
    assert len(resolution.alternative_projects) == 1
    assert "same confidence" in resolution.resolution_reason


def test_iprox_xlsx_index_streams_project_rows(tmp_path: Path):
    from agent.repositories.iprox_adapter import IproxXlsxIndex

    index = IproxXlsxIndex(_sample_iprox_xlsx(tmp_path))

    records = index.records_for_project("IPX0015463001")

    assert len(records) == 2
    assert records[0].project_id == "IPX0015463001"
    assert records[0].file_name == "OsGF14f interacting proteins.raw"
    assert records[0].download_url == "https://download.iprox.cn/IPX0015463000/IPX0015463001/OsGF14f interacting proteins.raw"
    assert records[0].is_raw is True


def test_iprox_adapter_resolves_file_name_from_spreadsheet(tmp_path: Path):
    from agent.repositories.iprox_adapter import IproxAdapter

    adapter = IproxAdapter(index_path=_sample_iprox_xlsx(tmp_path))

    resolution = adapter.resolve_project("OsGF14f interacting proteins.raw")
    context = adapter.build_project_context(resolution, "OsGF14f interacting proteins.raw")
    asset = adapter.resolve_file_asset(normalize_input("OsGF14f interacting proteins.raw"), context, tmp_path)

    assert resolution.primary_project is not None
    assert resolution.primary_project.repository == "iprox"
    assert resolution.primary_project.project_accession == "IPX0015463001"
    assert resolution.primary_project.matched_file == "OsGF14f interacting proteins.raw"
    assert resolution.primary_project.match_type == "exact"
    assert context.repository == "iprox"
    assert context.metadata["experimentTypes"].value == ["DDA"]
    assert context.project_files[0]["fileName"] == "OsGF14f interacting proteins.raw"
    assert asset.repository == "iprox"
    assert asset.matched_project_file == "OsGF14f interacting proteins.raw"
    assert asset.download_url == "https://download.iprox.cn/IPX0015463000/IPX0015463001/OsGF14f interacting proteins.raw"
    assert asset.transfer_method == "https"


def test_iprox_adapter_merges_project_xml_metadata_into_context(tmp_path: Path):
    from agent.repositories.iprox_adapter import IproxAdapter

    metadata_cache = tmp_path / "iprox-metadata"
    metadata_cache.mkdir()
    (metadata_cache / "IPX0015463001.xml").write_text(
        """<iProXDataset id="IPX0015463000" formatVersion="2.0">
  <DatasetSummary announceDate="2020-01-02" title="Instrument-rich iProX project">
    <Description>Yeast DDA proteomics data.</Description>
  </DatasetSummary>
  <SpeciesList>
    <Species><cvParam name="taxonomy: scientific name" value="Saccharomyces cerevisiae"/></Species>
  </SpeciesList>
  <InstrumentList>
    <Instrument id="Instrument_1"><cvParam name="Q Exactive"/></Instrument>
  </InstrumentList>
  <KeywordList>
    <cvParam name="submitter keyword" value="Q Exactive, DDA"/>
  </KeywordList>
</iProXDataset>""",
        encoding="utf-8",
    )
    adapter = IproxAdapter(index_path=_sample_iprox_xlsx(tmp_path), metadata_cache_dir=metadata_cache)

    resolution = adapter.resolve_project("OsGF14f interacting proteins.raw")
    context = adapter.build_project_context(resolution, "OsGF14f interacting proteins.raw")

    assert context.metadata["title"].value == "Instrument-rich iProX project"
    assert context.metadata["projectDescription"].value == "Yeast DDA proteomics data."
    assert context.metadata["organisms"].value == ["Saccharomyces cerevisiae"]
    assert context.metadata["instruments"].value == ["Q Exactive"]
    assert "Q Exactive" in context.evidence_documents[0]["text"]


def test_iprox_download_to_path_reports_chunk_progress(monkeypatch, tmp_path: Path):
    from agent.repositories import iprox_adapter
    from agent.repositories.iprox_adapter import IproxAdapter

    payload = b"a" * 4096 + b"b" * 2048

    class FakeResponse:
        headers = {"Content-Length": str(len(payload))}

        def __init__(self) -> None:
            self.offset = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size: int = -1) -> bytes:
            if self.offset >= len(payload):
                return b""
            if size is None or size < 0:
                size = len(payload) - self.offset
            chunk = payload[self.offset : self.offset + min(size, 2048)]
            self.offset += len(chunk)
            return chunk

    monkeypatch.setattr(iprox_adapter.urllib.request, "urlopen", lambda *args, **kwargs: FakeResponse())
    events: list[object] = []

    output = IproxAdapter(index_path=_sample_iprox_xlsx(tmp_path)).download_to_path(
        "https://download.iprox.cn/IPX001/sample raw.raw",
        tmp_path / "sample.raw",
        report=events.append,
    )

    assert output.read_bytes() == payload
    progress_events = [event for event in events if isinstance(event, dict) and event.get("kind") == "download_progress"]
    assert progress_events
    assert progress_events[0]["downloaded"] == 2048
    assert progress_events[0]["total"] == len(payload)
    assert progress_events[-1]["complete"] is True
    assert progress_events[-1]["downloaded"] == len(payload)


def test_registry_rejects_duplicate_adapter_names():
    class Adapter:
        name = "pride"

    with pytest.raises(ValueError, match="Duplicate"):
        RepositoryRegistry(adapters=[Adapter(), Adapter()])


def test_pride_adapter_maps_project_and_download_file():
    project = {
        "accession": "PXD000001",
        "title": "Project title",
        "projectDescription": "description",
        "organisms": [{"name": "Homo sapiens"}],
        "instruments": [{"name": "Orbitrap"}],
        "experimentTypes": [{"name": "Shotgun proteomics"}],
        "keywords": ["DDA"],
    }
    file_record = {
        "fileName": "sample.raw",
        "fileSizeBytes": 123,
        "publicFileLocations": [{"value": "ftp://ftp.pride.ebi.ac.uk/pride/data/archive/sample.raw"}],
    }
    adapter = PrideAdapter(client=None)

    canonical_project = adapter.map_project(project)
    canonical_file = adapter.map_file(file_record, canonical_project)

    assert canonical_project.repository == "pride"
    assert canonical_project.primary_accession == "PXD000001"
    assert canonical_project.px_accession == "PXD000001"
    assert canonical_file.download_urls == ["https://ftp.pride.ebi.ac.uk/pride/data/archive/sample.raw"]
    assert canonical_file.transfer_method == "https"


def test_massive_adapter_maps_native_and_px_accessions_and_prioritizes_raw(tmp_path: Path):
    adapter = MassiveAdapter(client=None)
    project = adapter.map_project(
        {
            "accession": "MSV000000001",
            "proteomeXchangeAccession": "PXD000001",
            "title": "MassIVE dataset",
            "species": "Homo sapiens",
            "files": [
                {"filepath": "metadata/sample.raw", "collection": "metadata", "ftp_url": "ftp://massive/sample.raw"},
                {"filepath": "raw/sample.raw", "collection": "raw", "ftp_url": "ftp://massive/raw/sample.raw"},
            ],
        },
        "MSV000000001",
    )
    files = adapter.list_project_files(project)
    matched = adapter.match_file(normalize_input("sample.raw"), files)
    asset = adapter.resolve_file_asset(
        normalize_input("sample.raw"),
        ProjectContext(
            repository="massive",
            project_accession="MSV000000001",
            native_accession="MSV000000001",
            px_accession="PXD000001",
            file_name="sample.raw",
            project_files=canonical_files_to_project_file_records(files),
        ),
        tmp_path,
    )

    assert project.repository == "massive"
    assert project.native_accession == "MSV000000001"
    assert project.px_accession == "PXD000001"
    assert matched is not None
    assert matched.file_category == "raw"
    assert asset.repository == "massive"
    assert asset.transfer_method == "https"
    assert asset.local_path == tmp_path / "assets" / "downloads" / "sample.raw"


def test_massive_adapter_resolves_file_name_from_dataset_cache_and_builds_ftp_url(tmp_path: Path):
    class FakeMassiveClient:
        def get_dataset(self, accession: str):
            assert accession == "MSV000068064"
            return {
                "accession": [{"name": "MassIVE dataset identifier", "value": "MSV000068064"}],
                "datasetLink": [{"name": "Dataset FTP location", "value": "ftp://massive.ucsd.edu/v01/MSV000068064"}],
                "title": "MassIVE raw dataset",
            }

        def query_datasets(self, accession: str):
            raise AssertionError("PROXI dataset lookup should succeed")

        def find_files_by_name_from_cache(self, file_name: str, limit: int = 200):
            assert file_name == "srm_74_3.raw"
            return [
                {
                    "dataset": "MSV000068064",
                    "filepath": "datasets/68064/srm_74_3.raw/_HEADER.TXT",
                    "collection": "raw",
                    "size": "1234",
                }
            ]

        def list_dataset_files_from_cache(self, accession: str, limit: int = 5000):
            assert accession == "MSV000068064"
            return [
                {
                    "dataset": "MSV000068064",
                    "filepath": "datasets/68064/srm_74_3.raw/_HEADER.TXT",
                    "collection": "raw",
                    "size": "1234",
                }
            ]

    adapter = MassiveAdapter(client=FakeMassiveClient())

    resolution = adapter.resolve_project("srm_74_3.raw")
    context = adapter.build_project_context(resolution, "srm_74_3.raw")
    asset = adapter.resolve_file_asset(normalize_input("srm_74_3.raw"), context, tmp_path)

    assert resolution.primary_project is not None
    assert resolution.primary_project.repository == "massive"
    assert resolution.primary_project.project_accession == "MSV000068064"
    assert resolution.primary_project.match_type == "exact"
    assert context.repository == "massive"
    assert context.project_files[0]["logicalPath"] == "datasets/68064/srm_74_3.raw"
    assert asset.matched_project_file == "srm_74_3.raw"
    assert asset.logical_path == "datasets/68064/srm_74_3.raw"
    assert asset.download_url == "https://massive.ucsd.edu/ProteoSAFe/DownloadResultFile?file=f.MSV000068064/datasets/68064/srm_74_3.raw"
    assert asset.download_urls[1] == "ftp://massive.ucsd.edu/v01/MSV000068064/datasets/68064/srm_74_3.raw"
    assert asset.transfer_method == "https"


def test_massive_client_filename_search_uses_fast_cache_fallbacks():
    from agent.repositories.massive_adapter import MassiveClient

    class FakeMassiveClient(MassiveClient):
        def __init__(self):
            self.calls: list[str] = []

        def _datasetcache_csv(self, sql: str):
            self.calls.append(sql)
            if "collection=" in sql:
                return [{"dataset": "MSV000001", "filepath": "raw/sample.raw", "collection": "sample.raw"}]
            raise AssertionError(f"Unexpected slow query before indexed fallback: {sql}")

    client = FakeMassiveClient()

    rows = client.find_files_by_name_from_cache("sample.raw")

    assert rows == [{"dataset": "MSV000001", "filepath": "raw/sample.raw", "collection": "sample.raw"}]
    assert client.calls
    assert all("lower(" not in sql.lower() for sql in client.calls)


def test_massive_adapter_keeps_datasetcache_match_when_project_metadata_is_unavailable(tmp_path: Path):
    class FakeMassiveClient:
        def get_dataset(self, accession: str):
            raise RuntimeError("MassIVE PROXI unavailable")

        def query_datasets(self, accession: str):
            raise RuntimeError("MassIVE QueryDatasets unavailable")

        def find_files_by_name_from_cache(self, file_name: str, limit: int = 200):
            return [
                {
                    "dataset": "MSV000099999",
                    "filepath": "raw/sample.raw",
                    "collection": "raw",
                    "size": "1234",
                }
            ]

        def list_dataset_files_from_cache(self, accession: str, limit: int = 5000):
            return [
                {
                    "dataset": accession,
                    "filepath": "raw/sample.raw",
                    "collection": "raw",
                    "size": "1234",
                }
            ]

    adapter = MassiveAdapter(client=FakeMassiveClient())

    resolution = adapter.resolve_project("sample.raw")
    context = adapter.build_project_context(resolution, "sample.raw")
    asset = adapter.resolve_file_asset(normalize_input("sample.raw"), context, tmp_path)

    assert resolution.primary_project is not None
    assert resolution.primary_project.project_accession == "MSV000099999"
    assert context.project_accession == "MSV000099999"
    assert asset.matched_project_file == "sample.raw"
    assert asset.download_url == "https://massive.ucsd.edu/ProteoSAFe/DownloadResultFile?file=f.MSV000099999/raw/sample.raw"
    assert asset.download_urls[1] == "ftp://massive.ucsd.edu/MSV000099999/raw/sample.raw"


def test_massive_adapter_resolves_f_msv_path_without_datasetcache_hit(tmp_path: Path):
    class FakeMassiveClient:
        def get_dataset(self, accession: str):
            assert accession == "MSV000101852"
            return {
                "accession": [{"name": "MassIVE dataset identifier", "value": "MSV000101852"}],
                "datasetLink": [{"name": "Dataset FTP location", "value": "ftp://massive.ucsd.edu/v01/MSV000101852"}],
                "title": "MassIVE explicit-path dataset",
                "species": [[{"name": "taxonomy: scientific name", "value": "Homo sapiens"}]],
                "instruments": [{"name": "Orbitrap Exploris 480"}],
            }

        def query_datasets(self, accession: str):
            raise AssertionError("PROXI dataset lookup should succeed")

        def find_files_by_name_from_cache(self, file_name: str, limit: int = 200):
            raise AssertionError("Explicit MSV path should not need global filename search")

        def list_dataset_files_from_cache(self, accession: str, limit: int = 5000):
            return []

    adapter = MassiveAdapter(client=FakeMassiveClient())
    raw_input = "f.MSV000101852/raw/20240920_Ex480_Evo_CDS_2-Biotin_KO3.raw"

    resolution = adapter.resolve_project(raw_input)
    context = adapter.build_project_context(resolution, normalize_input(raw_input).file_name)
    asset = adapter.resolve_file_asset(normalize_input(raw_input), context, tmp_path)

    assert resolution.primary_project is not None
    assert resolution.primary_project.project_accession == "MSV000101852"
    assert resolution.primary_project.matched_file == "20240920_Ex480_Evo_CDS_2-Biotin_KO3.raw"
    assert context.project_files[0]["logicalPath"] == "raw/20240920_Ex480_Evo_CDS_2-Biotin_KO3.raw"
    assert asset.matched_project_file == "20240920_Ex480_Evo_CDS_2-Biotin_KO3.raw"
    assert asset.logical_path == "raw/20240920_Ex480_Evo_CDS_2-Biotin_KO3.raw"
    assert asset.download_url == "https://massive.ucsd.edu/ProteoSAFe/DownloadResultFile?file=f.MSV000101852/raw/20240920_Ex480_Evo_CDS_2-Biotin_KO3.raw"
    assert asset.download_urls[1] == "ftp://massive.ucsd.edu/v01/MSV000101852/raw/20240920_Ex480_Evo_CDS_2-Biotin_KO3.raw"
    assert asset.transfer_method == "https"


def test_massive_adapter_prefers_https_downloadresultfile_over_ftp_for_datasetcache_files():
    adapter = MassiveAdapter(client=None)
    project = adapter.map_project(
        {
            "accession": [{"name": "MassIVE dataset identifier", "value": "MSV000101852"}],
            "datasetLink": [{"name": "Dataset FTP location", "value": "ftp://massive.ucsd.edu/v13/MSV000101852"}],
            "title": "MassIVE dataset",
        },
        "MSV000101852",
    )

    file = adapter.map_file({"filepath": "raw/sample.raw", "collection": "raw"}, project)

    assert file.download_urls == [
        "https://massive.ucsd.edu/ProteoSAFe/DownloadResultFile?file=f.MSV000101852/raw/sample.raw",
        "ftp://massive.ucsd.edu/v13/MSV000101852/raw/sample.raw",
    ]
    assert file.transfer_method == "https"


def test_massive_client_downloads_ftp_urls_with_urllib(tmp_path: Path, monkeypatch):
    from agent.repositories import massive_adapter
    from agent.repositories.massive_adapter import MassiveClient

    calls: list[tuple[str, Path]] = []

    def fake_urlretrieve(url: str, filename: str | Path):
        target = Path(filename)
        calls.append((url, target))
        target.write_bytes(b"raw-bytes")
        return str(target), {}

    monkeypatch.setattr(massive_adapter.urllib.request, "urlretrieve", fake_urlretrieve)
    target = tmp_path / "sample.raw"
    logs: list[str] = []

    downloaded = MassiveClient().download_to_path("ftp://massive.ucsd.edu/v13/MSV000101852/raw/sample.raw", target, report=logs.append)

    assert downloaded == target
    assert target.read_bytes() == b"raw-bytes"
    assert calls == [("ftp://massive.ucsd.edu/v13/MSV000101852/raw/sample.raw", target)]
    assert logs


def test_massive_adapter_maps_proxi_cv_metadata_to_canonical_project():
    adapter = MassiveAdapter(client=None)

    project = adapter.map_project(
        {
            "accession": [{"name": "MassIVE dataset identifier", "value": "MSV000081607"}],
            "datasetLink": [
                {"name": "MassIVE dataset URI", "value": "https://massive.ucsd.edu/ProteoSAFe/QueryMSV?id=MSV000081607"},
                {"name": "Dataset FTP location", "value": "ftp://massive.ucsd.edu/v01/MSV000081607"},
            ],
            "instruments": [{"name": "Q Exactive"}],
            "species": [[{"name": "taxonomy: scientific name", "value": "Homo sapiens"}]],
            "keywords": [{"name": "submitter keyword", "value": "digestion"}],
            "summary": "Confetti project",
            "title": "Confetti",
        },
        "PXD000900",
    )

    assert project.primary_accession == "MSV000081607"
    assert project.native_accession == "MSV000081607"
    assert project.px_accession == "PXD000900"
    assert project.raw_metadata["ftp_url"] == "ftp://massive.ucsd.edu/v01/MSV000081607"
    assert [item.value for item in project.instruments] == ["Q Exactive"]
    assert [item.value for item in project.organisms] == ["Homo sapiens"]


def test_massive_adapter_ignores_null_cv_metadata_and_classifies_datasetcache_raw_files():
    adapter = MassiveAdapter(client=None)

    project = adapter.map_project(
        {
            "accession": [{"name": "MassIVE dataset identifier", "value": "MSV000068064"}],
            "datasetLink": [{"name": "Dataset FTP location", "value": "ftp://massive.ucsd.edu/v01/MSV000068064"}],
            "instruments": [{"name": "instrument model", "value": "null"}],
            "species": [[{"name": "taxonomy: common name", "value": "null"}]],
            "title": "Hidden metadata dataset",
        },
        "MSV000068064",
    )
    file = adapter.map_file(
        {
            "dataset": "MSV000068064",
            "filepath": "datasets/68064/srm_74_3.raw/_EXPMENT.INF",
            "collection": "68064",
            "size": "3256",
        },
        project,
    )

    assert project.organisms == []
    assert project.instruments == []
    assert file.file_category == "raw"
    assert file.download_urls == [
        "https://massive.ucsd.edu/ProteoSAFe/DownloadResultFile?file=f.MSV000068064/datasets/68064/srm_74_3.raw",
        "ftp://massive.ucsd.edu/v01/MSV000068064/datasets/68064/srm_74_3.raw",
    ]


def test_repository_index_round_trips_files(tmp_path: Path):
    index = RepositoryIndex(tmp_path / "repo.sqlite")
    project = CanonicalProject(repository="massive", primary_accession="MSV0001", native_accession="MSV0001")
    files = [
        CanonicalFile(
            repository="massive",
            project_accession="MSV0001",
            file_name="a.raw",
            logical_path="raw/a.raw",
            file_category="raw",
            download_urls=["ftp://massive/MSV0001/raw/a.raw"],
            raw_record={"filepath": "raw/a.raw"},
        )
    ]

    index.upsert_project(project)
    index.replace_files("massive", "MSV0001", files)

    assert index.get_project("massive", "MSV0001").native_accession == "MSV0001"
    assert index.find_files_by_name("massive", "a.raw")[0].logical_path == "raw/a.raw"


def test_download_layer_blocks_aspera_without_adapter_command(tmp_path: Path):
    from agent.assets.downloader import download_file_asset

    asset = FileAsset(
        repository="pride",
        original_file_name="sample.raw",
        resolved_asset_type="raw",
        project_accession="PXD000001",
        matched_project_file="sample.raw",
        download_url="user@example.test:/data/sample.raw",
        transfer_method="aspera",
        local_path=tmp_path / "sample.raw",
    )

    with pytest.raises(ValueError, match="Aspera"):
        download_file_asset(object(), asset)
