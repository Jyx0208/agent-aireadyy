from __future__ import annotations

from pathlib import Path

import pytest

from agent.input.normalizer import normalize_input
from agent.metadata.canonical import CanonicalFile, CanonicalProject
from agent.models import FileAsset, ProjectContext
from agent.repositories.index import RepositoryIndex
from agent.repositories.iprox_adapter import IproxAdapter
from agent.repositories.massive_adapter import MassiveAdapter
from agent.repositories.matching import canonical_files_to_project_file_records
from agent.repositories.pride_adapter import PrideAdapter
from agent.repositories.registry import RepositoryRegistry


def test_cli_exposes_repository_index_sync_command():
    cli_text = Path("src/agent/cli.py").read_text(encoding="utf-8")

    assert '@app.command("sync-repository-index")' in cli_text
    assert "--xml-dir" in cli_text
    assert "--year" in cli_text


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
    assert asset.transfer_method == "ftp"
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
    assert asset.download_url == "ftp://massive.ucsd.edu/v01/MSV000068064/datasets/68064/srm_74_3.raw"
    assert asset.transfer_method == "ftp"


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
    assert file.download_urls == ["ftp://massive.ucsd.edu/v01/MSV000068064/datasets/68064/srm_74_3.raw"]


def test_iprox_index_resolves_exact_file_candidate(tmp_path: Path):
    index = RepositoryIndex(tmp_path / "iprox.sqlite")
    project = CanonicalProject(
        repository="iprox",
        primary_accession="IPX000001",
        native_accession="IPX000001",
        px_accession="PXD999999",
        title="iProX dataset",
    )
    file = CanonicalFile(
        repository="iprox",
        project_accession="IPX000001",
        file_name="sample.raw",
        logical_path="raw/sample.raw",
        transfer_method="aspera",
        download_urls=["user@download.iprox.org:/data/iprox/IPX000001/raw/sample.raw"],
    )
    index.upsert_project(project)
    index.replace_files("iprox", "IPX000001", [file])
    adapter = IproxAdapter(client=None, index=index)

    resolution = adapter.resolve_project("sample.raw")
    context = adapter.build_project_context(resolution, "sample.raw")
    asset = adapter.resolve_file_asset(normalize_input("sample.raw"), context, tmp_path)

    assert resolution.primary_project is not None
    assert resolution.primary_project.repository == "iprox"
    assert resolution.primary_project.project_accession == "IPX000001"
    assert context.repository == "iprox"
    assert asset.transfer_method == "aspera"
    assert "ascp" in adapter.aspera_command(asset, tmp_path)


def test_iprox_adapter_imports_px_xml_into_file_index(tmp_path: Path):
    index = RepositoryIndex(tmp_path / "iprox.sqlite")
    xml_path = tmp_path / "PX_IPX000001.xml"
    xml_path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
        <ProteomeXchangeDataset id="PXD999999">
          <DatasetIdentifierList>
            <DatasetIdentifier repository="iProX" accession="IPX000001"/>
            <DatasetIdentifier repository="ProteomeXchange" accession="PXD999999"/>
          </DatasetIdentifierList>
          <DatasetSummary>
            <title>iProX XML dataset</title>
            <speciesList><cvParam name="taxonomy: scientific name" value="Homo sapiens"/></speciesList>
            <instrumentList><cvParam name="Orbitrap Fusion"/></instrumentList>
          </DatasetSummary>
          <DatasetFileList>
            <DatasetFile id="FILE_1" name="sample.raw">
              <cvParam name="Associated raw file URI" value="aspera://download.iprox.org/data/iprox/IPX000001/raw/sample.raw"/>
            </DatasetFile>
            <DatasetFile id="FILE_2" name="result.mzML">
              <cvParam name="Associated peak list file URI" value="https://example.test/result.mzML"/>
            </DatasetFile>
          </DatasetFileList>
        </ProteomeXchangeDataset>
        """,
        encoding="utf-8",
    )
    adapter = IproxAdapter(client=None, index=index)

    summary = adapter.sync_index_from_xml_files([xml_path])
    resolution = adapter.resolve_project("sample.raw")
    context = adapter.build_project_context(resolution, "sample.raw")
    asset = adapter.resolve_file_asset(normalize_input("sample.raw"), context, tmp_path)

    assert summary["projects"] == 1
    assert summary["files"] == 2
    assert resolution.primary_project is not None
    assert resolution.primary_project.project_accession == "IPX000001"
    assert context.px_accession == "PXD999999"
    assert asset.transfer_method == "aspera"
    assert asset.logical_path == "raw/sample.raw"


def test_iprox_adapter_syncs_project_ids_from_official_date_api(tmp_path: Path):
    class FakeIproxClient:
        def list_project_ids_by_date(self, granularity: str, value: str):
            assert granularity == "year"
            assert value == "2020"
            return ["IPX000001", "IPX000002"]

        def get_dataset(self, accession: str):
            return {
                "accession": accession,
                "title": f"Project {accession}",
                "species": "Homo sapiens",
                "dataFiles": [{"fileName": f"{accession}.raw", "filePath": f"raw/{accession}.raw"}],
            }

    index = RepositoryIndex(tmp_path / "iprox.sqlite")
    adapter = IproxAdapter(client=FakeIproxClient(), index=index)

    summary = adapter.sync_index_by_date("year", "2020", limit=1)

    assert summary["projects"] == 1
    assert summary["files"] == 1
    assert index.get_project("iprox", "IPX000001").title == "Project IPX000001"
    assert index.find_files_by_name("iprox", "IPX000001.raw")[0].logical_path == "raw/IPX000001.raw"


def test_iprox_adapter_indexes_project_xml_placeholder_when_date_sync_has_no_file_list(tmp_path: Path):
    class FakeIproxClient:
        def list_project_ids_by_date(self, granularity: str, value: str):
            return ["IPX000001"]

        def get_dataset(self, accession: str):
            return {"accession": accession, "title": "Project without PROXI files", "dataFiles": []}

    index = RepositoryIndex(tmp_path / "iprox.sqlite")
    adapter = IproxAdapter(client=FakeIproxClient(), index=index)

    summary = adapter.sync_index_by_date("year", "2020", limit=1)
    xml_file = index.find_files_by_name("iprox", "PX_IPX000001.xml")[0]

    assert summary["projects"] == 1
    assert summary["files"] == 1
    assert summary["xml_placeholders"] == 1
    assert xml_file.logical_path == "PX_IPX000001.xml"
    assert xml_file.transfer_method == "aspera"
    assert xml_file.download_urls == ["<iprox_username>@download.iprox.org:/data/iprox/IPX000001/PX_IPX000001.xml"]


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
        repository="iprox",
        original_file_name="sample.raw",
        resolved_asset_type="raw",
        project_accession="IPX000001",
        matched_project_file="sample.raw",
        download_url="user@download.iprox.org:/data/iprox/IPX000001/sample.raw",
        transfer_method="aspera",
        local_path=tmp_path / "sample.raw",
    )

    with pytest.raises(ValueError, match="Aspera"):
        download_file_asset(object(), asset)
