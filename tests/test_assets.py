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
    assert any("Downloading asset" in line for line in logs)
    assert any("Asset is already execution-ready" in line for line in logs)


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
    assert any("Preparing asset requires conversion" in line for line in logs)


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
    assert any("Primary conversion failed" in line for line in logs)
    assert any("Falling back to secondary converter" in line for line in logs)
