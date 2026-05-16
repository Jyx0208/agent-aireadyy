import gzip
import zipfile
from pathlib import Path

from agent.assets.downloader import download_file_asset
from agent.assets.preparer import prepare_file_asset
from agent.assets.resolver import resolve_file_asset
from agent.input.normalizer import normalize_input
from agent.models import FileAsset, ProjectContext


def _project_context(file_name: str, project_files: list[dict]) -> ProjectContext:
    return ProjectContext(
        project_accession="PXD123456",
        file_name=file_name,
        project_files=project_files,
    )


def test_resolve_file_asset_prefers_matching_mzml_over_raw(tmp_path: Path):
    task = normalize_input("WT_5_Lys-c.raw")
    context = _project_context(
        task.file_name,
        [
            {
                "fileName": "WT_5_Lys-c.raw",
                "publicFileLocations": [{"value": "ftp://ftp.pride.ebi.ac.uk/pride/data/archive/WT_5_Lys-c.raw"}],
            },
            {
                "fileName": "WT_5_Lys-c.mzML",
                "publicFileLocations": [{"value": "ftp://ftp.pride.ebi.ac.uk/pride/data/archive/WT_5_Lys-c.mzML"}],
            },
        ],
    )

    asset = resolve_file_asset(task=task, context=context, work_dir=tmp_path)

    assert asset.resolved_asset_type == "mzml"
    assert asset.matched_project_file == "WT_5_Lys-c.mzML"
    assert asset.requires_conversion is False
    assert asset.download_url.endswith("WT_5_Lys-c.mzML")
    assert asset.local_path.name == "WT_5_Lys-c.mzML"
    assert asset.prepared_path == asset.local_path


def test_resolve_file_asset_marks_raw_for_conversion(tmp_path: Path):
    task = normalize_input("WT_5_Lys-c.raw")
    context = _project_context(
        task.file_name,
        [
            {
                "fileName": "WT_5_Lys-c.raw",
                "publicFileLocations": [{"value": "ftp://ftp.pride.ebi.ac.uk/pride/data/archive/WT_5_Lys-c.raw"}],
            }
        ],
    )

    asset = resolve_file_asset(task=task, context=context, work_dir=tmp_path)

    assert asset.resolved_asset_type == "raw"
    assert asset.requires_conversion is True
    assert asset.local_path.name == "WT_5_Lys-c.raw"
    assert asset.prepared_path.name == "WT_5_Lys-c.mzML"


def test_prepare_file_asset_decompresses_mzml_gz(tmp_path: Path):
    class FakeClient:
        def download_binary(self, url: str) -> bytes:
            assert url == "https://ftp.pride.ebi.ac.uk/pride/data/archive/sample.mzML.gz"
            return gzip.compress(b"<mzML />")

    task = normalize_input("sample.raw")
    context = _project_context(
        task.file_name,
        [
            {
                "fileName": "sample.mzML.gz",
                "publicFileLocations": [{"value": "ftp://ftp.pride.ebi.ac.uk/pride/data/archive/sample.mzML.gz"}],
            }
        ],
    )
    asset = resolve_file_asset(task=task, context=context, work_dir=tmp_path)

    prepared_path = prepare_file_asset(FakeClient(), asset, converter=None)  # type: ignore[arg-type]

    assert asset.resolved_asset_type == "mzml"
    assert asset.local_path == tmp_path / "assets" / "downloads" / "sample.mzML.gz"
    assert prepared_path == tmp_path / "assets" / "prepared" / "sample.mzML"
    assert prepared_path.read_bytes() == b"<mzML />"


def test_resolve_file_asset_marks_mzxml_for_conversion(tmp_path: Path):
    task = normalize_input("sample.mzXML")
    context = _project_context(
        task.file_name,
        [
            {
                "fileName": "sample.mzXML",
                "publicFileLocations": [{"value": "ftp://ftp.pride.ebi.ac.uk/pride/data/archive/sample.mzXML"}],
            }
        ],
    )

    asset = resolve_file_asset(task=task, context=context, work_dir=tmp_path)

    assert asset.resolved_asset_type == "mzxml"
    assert asset.requires_conversion is True
    assert asset.prepared_path == tmp_path / "assets" / "prepared" / "sample.mzML"


def test_prepare_file_asset_decompresses_mgf_gz(tmp_path: Path):
    class FakeClient:
        def download_binary(self, url: str) -> bytes:
            assert url == "https://ftp.pride.ebi.ac.uk/pride/data/archive/sample.mgf.gz"
            return gzip.compress(b"BEGIN IONS\nEND IONS\n")

    task = normalize_input("sample.mgf")
    context = _project_context(
        task.file_name,
        [
            {
                "fileName": "sample.mgf.gz",
                "publicFileLocations": [{"value": "ftp://ftp.pride.ebi.ac.uk/pride/data/archive/sample.mgf.gz"}],
            }
        ],
    )
    asset = resolve_file_asset(task=task, context=context, work_dir=tmp_path)

    prepared_path = prepare_file_asset(FakeClient(), asset, converter=None)  # type: ignore[arg-type]

    assert asset.resolved_asset_type == "mgf"
    assert prepared_path == tmp_path / "assets" / "prepared" / "sample.mgf"
    assert prepared_path.read_bytes() == b"BEGIN IONS\nEND IONS\n"


def test_prepare_file_asset_extracts_raw_zip_before_conversion(tmp_path: Path):
    events: list[tuple[str, str]] = []

    class FakeClient:
        def download_binary(self, url: str) -> bytes:
            archive_path = tmp_path / "sample.raw.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("nested/sample.raw", "raw")
            return archive_path.read_bytes()

    class FakeConverter:
        def convert_to_mzml(self, source: Path, target: Path) -> Path:
            events.append(("convert", source.name))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"mzml")
            return target

    asset = FileAsset(
        original_file_name="sample.raw",
        resolved_asset_type="raw",
        matched_project_file="sample.raw.zip",
        download_url="https://ftp.pride.ebi.ac.uk/pride/data/archive/sample.raw.zip",
        local_path=tmp_path / "assets" / "downloads" / "sample.raw.zip",
        prepared_path=tmp_path / "assets" / "prepared" / "sample.mzML",
        requires_conversion=True,
        asset_confidence=1.0,
        match_type="stem",
    )

    prepared_path = prepare_file_asset(FakeClient(), asset, FakeConverter())

    assert prepared_path.read_bytes() == b"mzml"
    assert events == [("convert", "sample.raw")]


def test_prepare_file_asset_extracts_bruker_d_zip(tmp_path: Path):
    class FakeClient:
        def download_binary(self, url: str) -> bytes:
            assert url == "https://ftp.pride.ebi.ac.uk/pride/data/archive/sample.d.zip"
            archive_path = tmp_path / "sample.d.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("sample.d/analysis.tdf", "tdf")
            return archive_path.read_bytes()

    task = normalize_input("sample.d")
    context = _project_context(
        task.file_name,
        [
            {
                "fileName": "sample.d.zip",
                "publicFileLocations": [{"value": "ftp://ftp.pride.ebi.ac.uk/pride/data/archive/sample.d.zip"}],
            }
        ],
    )
    asset = resolve_file_asset(task=task, context=context, work_dir=tmp_path)

    prepared_path = prepare_file_asset(FakeClient(), asset, converter=None)  # type: ignore[arg-type]

    assert asset.resolved_asset_type == "tims"
    assert asset.local_path == tmp_path / "assets" / "downloads" / "sample.d.zip"
    assert prepared_path == tmp_path / "assets" / "prepared" / "sample.d"
    assert (prepared_path / "analysis.tdf").read_text(encoding="utf-8") == "tdf"


def test_resolve_file_asset_uses_tims_without_conversion(tmp_path: Path):
    task = normalize_input("2021-10-26_Gabriel_29_PaSER_dda_30SPD_S1-C9_1_3176.d")
    context = _project_context(
        task.file_name,
        [
            {
                "fileName": "2021-10-26_Gabriel_29_PaSER_dda_30SPD_S1-C9_1_3176.d",
                "publicFileLocations": [{"value": "ftp://ftp.pride.ebi.ac.uk/pride/data/archive/sample.d"}],
            }
        ],
    )

    asset = resolve_file_asset(task=task, context=context, work_dir=tmp_path)

    assert asset.resolved_asset_type == "tims"
    assert asset.requires_conversion is False
    assert asset.local_path.name.endswith(".d")
    assert asset.prepared_path == asset.local_path


def test_resolve_file_asset_links_wiff_sidecars(tmp_path: Path):
    task = normalize_input("sample.wiff")
    context = _project_context(
        task.file_name,
        [
            {
                "fileName": "sample.wiff",
                "publicFileLocations": [{"value": "ftp://ftp.pride.ebi.ac.uk/pride/data/archive/sample.wiff"}],
            },
            {
                "fileName": "sample.wiff.scan",
                "publicFileLocations": [{"value": "ftp://ftp.pride.ebi.ac.uk/pride/data/archive/sample.wiff.scan"}],
            },
            {
                "fileName": "sample.txt",
                "publicFileLocations": [{"value": "ftp://ftp.pride.ebi.ac.uk/pride/data/archive/sample.txt"}],
            },
        ],
    )

    asset = resolve_file_asset(task=task, context=context, work_dir=tmp_path)

    assert asset.resolved_asset_type == "wiff"
    assert asset.requires_conversion is True
    assert asset.sidecar_files == [
        {
            "file_name": "sample.wiff.scan",
            "download_url": "https://ftp.pride.ebi.ac.uk/pride/data/archive/sample.wiff.scan",
            "local_path": str(tmp_path / "assets" / "downloads" / "sample.wiff.scan"),
        }
    ]


def test_prepare_file_asset_downloads_wiff_sidecars_before_conversion(tmp_path: Path):
    events: list[tuple[str, str]] = []

    class FakeClient:
        def download_binary(self, url: str) -> bytes:
            events.append(("download", url))
            return url.rsplit("/", 1)[-1].encode()

    class FakeConverter:
        def convert_to_mzml(self, source: Path, target: Path) -> Path:
            events.append(("convert", source.name))
            assert (source.parent / "sample.wiff.scan").exists()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"converted")
            return target

    asset = FileAsset(
        original_file_name="sample.wiff",
        resolved_asset_type="wiff",
        matched_project_file="sample.wiff",
        download_url="https://ftp.pride.ebi.ac.uk/pride/data/archive/sample.wiff",
        local_path=tmp_path / "assets" / "downloads" / "sample.wiff",
        prepared_path=tmp_path / "assets" / "prepared" / "sample.mzML",
        sidecar_files=[
            {
                "file_name": "sample.wiff.scan",
                "download_url": "https://ftp.pride.ebi.ac.uk/pride/data/archive/sample.wiff.scan",
                "local_path": str(tmp_path / "assets" / "downloads" / "sample.wiff.scan"),
            }
        ],
        requires_conversion=True,
        asset_confidence=1.0,
        match_type="exact",
    )

    prepared_path = prepare_file_asset(FakeClient(), asset, FakeConverter())

    assert prepared_path.read_bytes() == b"converted"
    assert events == [
        ("download", "https://ftp.pride.ebi.ac.uk/pride/data/archive/sample.wiff"),
        ("download", "https://ftp.pride.ebi.ac.uk/pride/data/archive/sample.wiff.scan"),
        ("convert", "sample.wiff"),
    ]


def test_download_file_asset_writes_bytes_to_local_path(tmp_path: Path):
    class FakeClient:
        def download_binary(self, url: str) -> bytes:
            assert url == "https://ftp.pride.ebi.ac.uk/pride/data/archive/WT_5_Lys-c.mzML"
            return b"mzml-bytes"

    asset = FileAsset(
        original_file_name="WT_5_Lys-c.raw",
        resolved_asset_type="mzml",
        matched_project_file="WT_5_Lys-c.mzML",
        download_url="https://ftp.pride.ebi.ac.uk/pride/data/archive/WT_5_Lys-c.mzML",
        local_path=tmp_path / "assets" / "downloads" / "WT_5_Lys-c.mzML",
        prepared_path=tmp_path / "assets" / "downloads" / "WT_5_Lys-c.mzML",
        requires_conversion=False,
        asset_confidence=1.0,
        match_type="stem",
    )

    downloaded = download_file_asset(FakeClient(), asset)

    assert downloaded.exists()
    assert downloaded.read_bytes() == b"mzml-bytes"


def test_download_file_asset_reuses_project_cache(tmp_path: Path, monkeypatch):
    events: list[tuple[str, str]] = []
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("AGENT_PRIDE_CACHE_DIR", str(cache_dir))

    class FakeClient:
        def download_binary(self, url: str) -> bytes:
            events.append(("download", url))
            return b"raw-bytes"

    first_asset = FileAsset(
        original_file_name="sample.raw",
        resolved_asset_type="raw",
        project_accession="PXD123456",
        matched_project_file="sample.raw",
        download_url="https://ftp.pride.ebi.ac.uk/pride/data/archive/sample.raw",
        local_path=tmp_path / "run1" / "assets" / "downloads" / "sample.raw",
        prepared_path=tmp_path / "run1" / "assets" / "prepared" / "sample.mzML",
        requires_conversion=True,
        asset_confidence=1.0,
        match_type="exact",
    )
    second_asset = first_asset.model_copy(
        update={
            "local_path": tmp_path / "run2" / "assets" / "downloads" / "sample.raw",
            "prepared_path": tmp_path / "run2" / "assets" / "prepared" / "sample.mzML",
        }
    )

    first_path = download_file_asset(FakeClient(), first_asset)
    second_path = download_file_asset(FakeClient(), second_asset)

    assert first_path.read_bytes() == b"raw-bytes"
    assert second_path.read_bytes() == b"raw-bytes"
    assert (cache_dir / "PXD123456" / "sample.raw").read_bytes() == b"raw-bytes"
    assert events == [("download", "https://ftp.pride.ebi.ac.uk/pride/data/archive/sample.raw")]


def test_download_file_asset_redownloads_cache_with_wrong_size(tmp_path: Path, monkeypatch):
    events: list[tuple[str, str]] = []
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("AGENT_PRIDE_CACHE_DIR", str(cache_dir))
    cached = cache_dir / "PXD123456" / "sample.raw"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"bad")

    class FakeClient:
        def download_binary(self, url: str) -> bytes:
            events.append(("download", url))
            return b"raw-bytes"

    asset = FileAsset(
        original_file_name="sample.raw",
        resolved_asset_type="raw",
        project_accession="PXD123456",
        matched_project_file="sample.raw",
        download_url="https://ftp.pride.ebi.ac.uk/pride/data/archive/sample.raw",
        local_path=tmp_path / "run" / "assets" / "downloads" / "sample.raw",
        prepared_path=tmp_path / "run" / "assets" / "prepared" / "sample.mzML",
        expected_size_bytes=len(b"raw-bytes"),
        requires_conversion=True,
        asset_confidence=1.0,
        match_type="exact",
    )

    downloaded = download_file_asset(FakeClient(), asset)

    assert downloaded.read_bytes() == b"raw-bytes"
    assert cached.read_bytes() == b"raw-bytes"
    assert events == [("download", "https://ftp.pride.ebi.ac.uk/pride/data/archive/sample.raw")]


def test_download_file_asset_tries_alternate_urls_when_primary_fails(tmp_path: Path, monkeypatch):
    events: list[str] = []
    monkeypatch.setenv("AGENT_REPOSITORY_CACHE_DIR", str(tmp_path / "cache"))

    class FakeClient:
        def download_to_path(self, url: str, target_path: Path, report=None) -> Path:
            events.append(url)
            if "primary" in url:
                raise OSError("primary mirror unavailable")
            target_path.write_bytes(b"raw-bytes")
            return target_path

    asset = FileAsset(
        repository="massive",
        original_file_name="sample.raw",
        resolved_asset_type="raw",
        project_accession="MSV000001",
        matched_project_file="sample.raw",
        download_url="https://massive.ucsd.edu/primary/sample.raw",
        download_urls=[
            "https://massive.ucsd.edu/primary/sample.raw",
            "ftp://massive.ucsd.edu/v01/MSV000001/raw/sample.raw",
        ],
        transfer_method="https",
        local_path=tmp_path / "run" / "assets" / "downloads" / "sample.raw",
        prepared_path=tmp_path / "run" / "assets" / "prepared" / "sample.mzML",
        expected_size_bytes=len(b"raw-bytes"),
        requires_conversion=True,
        asset_confidence=1.0,
        match_type="exact",
    )

    downloaded = download_file_asset(FakeClient(), asset)

    assert downloaded.read_bytes() == b"raw-bytes"
    assert events == [
        "https://massive.ucsd.edu/primary/sample.raw",
        "ftp://massive.ucsd.edu/v01/MSV000001/raw/sample.raw",
    ]


def test_resolve_file_asset_prefers_http_compatible_url_over_aspera(tmp_path: Path):
    task = normalize_input("sample.raw")
    context = _project_context(
        task.file_name,
        [
            {
                "fileName": "sample.raw",
                "publicFileLocations": [
                    {"name": "Aspera Protocol", "value": "prd_ascp@fasp.ebi.ac.uk:pride/data/archive/sample.raw"},
                    {"name": "FTP Protocol", "value": "ftp://ftp.pride.ebi.ac.uk/pride/data/archive/sample.raw"},
                ],
            }
        ],
    )

    asset = resolve_file_asset(task=task, context=context, work_dir=tmp_path)

    assert asset.download_url == "https://ftp.pride.ebi.ac.uk/pride/data/archive/sample.raw"


def test_resolve_file_asset_sanitizes_project_file_name_for_local_paths(tmp_path: Path):
    task = normalize_input("sample.raw")
    context = _project_context(
        task.file_name,
        [
            {
                "fileName": "../unsafe/sample.raw",
                "publicFileLocations": [{"value": "ftp://ftp.pride.ebi.ac.uk/pride/data/archive/sample.raw"}],
            }
        ],
    )

    asset = resolve_file_asset(task=task, context=context, work_dir=tmp_path)

    assert asset.matched_project_file == "../unsafe/sample.raw"
    assert asset.local_path == tmp_path / "assets" / "downloads" / "sample.raw"
    assert asset.prepared_path == tmp_path / "assets" / "prepared" / "sample.mzML"


def test_prepare_file_asset_for_mzml_only_downloads(tmp_path: Path):
    events: list[tuple[str, str]] = []
    logs: list[str] = []

    class FakeClient:
        def download_binary(self, url: str) -> bytes:
            events.append(("download", url))
            return b"mzml-bytes"

    class FakeConverter:
        def convert_to_mzml(self, source: Path, target: Path) -> Path:
            raise AssertionError("converter should not be called for mzML assets")

    asset = FileAsset(
        original_file_name="WT_5_Lys-c.raw",
        resolved_asset_type="mzml",
        matched_project_file="WT_5_Lys-c.mzML",
        download_url="https://ftp.pride.ebi.ac.uk/pride/data/archive/WT_5_Lys-c.mzML",
        local_path=tmp_path / "assets" / "downloads" / "WT_5_Lys-c.mzML",
        prepared_path=tmp_path / "assets" / "downloads" / "WT_5_Lys-c.mzML",
        requires_conversion=False,
        asset_confidence=1.0,
        match_type="stem",
    )

    prepared = prepare_file_asset(FakeClient(), asset, FakeConverter(), report=logs.append)

    assert prepared == asset.local_path
    assert prepared.read_bytes() == b"mzml-bytes"
    assert events == [("download", "https://ftp.pride.ebi.ac.uk/pride/data/archive/WT_5_Lys-c.mzML")]
    assert any("正在下载数据文件" in line for line in logs)
    assert any("已可直接用于执行" in line for line in logs)


def test_prepare_file_asset_for_raw_downloads_then_converts(tmp_path: Path):
    events: list[tuple[str, str]] = []
    logs: list[str] = []

    class FakeClient:
        def download_binary(self, url: str) -> bytes:
            events.append(("download", url))
            return b"raw-bytes"

    class FakeConverter:
        def convert_to_mzml(self, source: Path, target: Path) -> Path:
            events.append(("convert", f"{source.name}->{target.name}"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"converted-mzml")
            return target

    asset = FileAsset(
        original_file_name="WT_5_Lys-c.raw",
        resolved_asset_type="raw",
        matched_project_file="WT_5_Lys-c.raw",
        download_url="https://ftp.pride.ebi.ac.uk/pride/data/archive/WT_5_Lys-c.raw",
        local_path=tmp_path / "assets" / "downloads" / "WT_5_Lys-c.raw",
        prepared_path=tmp_path / "assets" / "prepared" / "WT_5_Lys-c.mzML",
        requires_conversion=True,
        asset_confidence=1.0,
        match_type="exact",
    )

    prepared = prepare_file_asset(FakeClient(), asset, FakeConverter(), report=logs.append)

    assert prepared == asset.prepared_path
    assert prepared.read_bytes() == b"converted-mzml"
    assert events == [
        ("download", "https://ftp.pride.ebi.ac.uk/pride/data/archive/WT_5_Lys-c.raw"),
        ("convert", "WT_5_Lys-c.raw->WT_5_Lys-c.mzML"),
    ]
    assert any("需要格式转换" in line for line in logs)


def test_prepare_file_asset_reuses_existing_prepared_mzml_for_raw(tmp_path: Path):
    class FakeClient:
        def download_binary(self, url: str) -> bytes:
            raise AssertionError("download should be skipped when prepared mzML exists")

    class FakeConverter:
        def convert_to_mzml(self, source: Path, target: Path) -> Path:
            raise AssertionError("conversion should be skipped when prepared mzML exists")

    prepared_path = tmp_path / "assets" / "prepared" / "sample.mzML"
    prepared_path.parent.mkdir(parents=True)
    prepared_path.write_bytes(b"existing-mzml")
    asset = FileAsset(
        original_file_name="sample.raw",
        resolved_asset_type="raw",
        matched_project_file="sample.raw",
        download_url="https://ftp.pride.ebi.ac.uk/pride/data/archive/sample.raw",
        local_path=tmp_path / "assets" / "downloads" / "sample.raw",
        prepared_path=prepared_path,
        requires_conversion=True,
        asset_confidence=1.0,
        match_type="exact",
    )

    prepared = prepare_file_asset(FakeClient(), asset, FakeConverter())

    assert prepared == prepared_path
    assert prepared.read_bytes() == b"existing-mzml"


def test_prepare_file_asset_reuses_existing_extracted_d_directory(tmp_path: Path):
    class FakeClient:
        def download_binary(self, url: str) -> bytes:
            raise AssertionError("download should be skipped when prepared .d exists")

    prepared_path = tmp_path / "assets" / "prepared" / "sample.d"
    prepared_path.mkdir(parents=True)
    (prepared_path / "analysis.tdf").write_text("tdf", encoding="utf-8")
    asset = FileAsset(
        original_file_name="sample.d",
        resolved_asset_type="tims",
        matched_project_file="sample.d.zip",
        download_url="https://ftp.pride.ebi.ac.uk/pride/data/archive/sample.d.zip",
        local_path=tmp_path / "assets" / "downloads" / "sample.d.zip",
        prepared_path=prepared_path,
        requires_conversion=False,
        asset_confidence=1.0,
        match_type="stem",
    )

    prepared = prepare_file_asset(FakeClient(), asset, converter=None)  # type: ignore[arg-type]

    assert prepared == prepared_path
    assert (prepared / "analysis.tdf").read_text(encoding="utf-8") == "tdf"


def test_prepare_file_asset_reuses_existing_sidecar_before_conversion(tmp_path: Path):
    events: list[tuple[str, str]] = []
    sidecar_path = tmp_path / "assets" / "downloads" / "sample.wiff.scan"
    sidecar_path.parent.mkdir(parents=True)
    sidecar_path.write_bytes(b"existing-sidecar")

    class FakeClient:
        def download_binary(self, url: str) -> bytes:
            events.append(("download", url))
            return b"primary-wiff"

    class FakeConverter:
        def convert_to_mzml(self, source: Path, target: Path) -> Path:
            events.append(("convert", source.name))
            assert sidecar_path.read_bytes() == b"existing-sidecar"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"converted")
            return target

    asset = FileAsset(
        original_file_name="sample.wiff",
        resolved_asset_type="wiff",
        matched_project_file="sample.wiff",
        download_url="https://ftp.pride.ebi.ac.uk/pride/data/archive/sample.wiff",
        local_path=tmp_path / "assets" / "downloads" / "sample.wiff",
        prepared_path=tmp_path / "assets" / "prepared" / "sample.mzML",
        sidecar_files=[
            {
                "file_name": "sample.wiff.scan",
                "download_url": "https://ftp.pride.ebi.ac.uk/pride/data/archive/sample.wiff.scan",
                "local_path": str(sidecar_path),
            }
        ],
        requires_conversion=True,
        asset_confidence=1.0,
        match_type="exact",
    )

    prepared = prepare_file_asset(FakeClient(), asset, FakeConverter())

    assert prepared.read_bytes() == b"converted"
    assert events == [
        ("download", "https://ftp.pride.ebi.ac.uk/pride/data/archive/sample.wiff"),
        ("convert", "sample.wiff"),
    ]


def test_prepare_file_asset_uses_fallback_converter_when_primary_fails(tmp_path: Path):
    events: list[tuple[str, str]] = []
    logs: list[str] = []

    class FakeClient:
        def download_binary(self, url: str) -> bytes:
            events.append(("download", url))
            return b"raw-bytes"

    class FailingConverter:
        def convert_to_mzml(self, source: Path, target: Path) -> Path:
            events.append(("primary", source.name))
            raise RuntimeError("primary converter unavailable")

    class FallbackConverter:
        def convert_to_mzml(self, source: Path, target: Path) -> Path:
            events.append(("fallback", f"{source.name}->{target.name}"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"converted-by-fallback")
            return target

    asset = FileAsset(
        original_file_name="WT_5_Lys-c.raw",
        resolved_asset_type="raw",
        matched_project_file="WT_5_Lys-c.raw",
        download_url="https://ftp.pride.ebi.ac.uk/pride/data/archive/WT_5_Lys-c.raw",
        local_path=tmp_path / "assets" / "downloads" / "WT_5_Lys-c.raw",
        prepared_path=tmp_path / "assets" / "prepared" / "WT_5_Lys-c.mzML",
        requires_conversion=True,
        asset_confidence=1.0,
        match_type="exact",
    )

    prepared = prepare_file_asset(
        FakeClient(),
        asset,
        FailingConverter(),
        fallback_converter=FallbackConverter(),
        report=logs.append,
    )

    assert prepared.read_bytes() == b"converted-by-fallback"
    assert events == [
        ("download", "https://ftp.pride.ebi.ac.uk/pride/data/archive/WT_5_Lys-c.raw"),
        ("primary", "WT_5_Lys-c.raw"),
        ("fallback", "WT_5_Lys-c.raw->WT_5_Lys-c.mzML"),
    ]
    assert any("主转换器失败" in line for line in logs)
    assert any("备用转换器" in line for line in logs)
