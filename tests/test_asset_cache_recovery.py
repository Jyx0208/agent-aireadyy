from pathlib import Path

from agent.assets.downloader import download_file_asset
from agent.assets.preparer import prepare_file_asset
from agent.models import FileAsset


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


def test_prepare_file_asset_redownloads_once_when_fallback_reports_corrupt_raw(tmp_path: Path, monkeypatch):
    events: list[tuple[str, str]] = []
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("AGENT_PRIDE_CACHE_DIR", str(cache_dir))

    class FakeClient:
        def __init__(self) -> None:
            self.download_count = 0

        def download_binary(self, url: str) -> bytes:
            self.download_count += 1
            events.append(("download", str(self.download_count)))
            return f"raw-bytes-{self.download_count}".encode()

    class FailingConverter:
        def convert_to_mzml(self, source: Path, target: Path) -> Path:
            events.append(("primary", source.read_text(encoding="utf-8")))
            raise RuntimeError("primary converter unavailable")

    class FallbackConverter:
        def __init__(self) -> None:
            self.calls = 0

        def convert_to_mzml(self, source: Path, target: Path) -> Path:
            self.calls += 1
            events.append(("fallback", source.read_text(encoding="utf-8")))
            if self.calls == 1:
                raise RuntimeError("[RawFileImpl::ctor()] Corrupt RAW file")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"converted-after-redownload")
            return target

    asset = FileAsset(
        original_file_name="sample.raw",
        resolved_asset_type="raw",
        project_accession="PXD123456",
        matched_project_file="sample.raw",
        download_url="https://ftp.pride.ebi.ac.uk/pride/data/archive/sample.raw",
        local_path=tmp_path / "assets" / "downloads" / "sample.raw",
        prepared_path=tmp_path / "assets" / "prepared" / "sample.mzML",
        requires_conversion=True,
        asset_confidence=1.0,
        match_type="exact",
    )

    prepared = prepare_file_asset(
        FakeClient(),
        asset,
        FailingConverter(),
        fallback_converter=FallbackConverter(),
    )

    assert prepared.read_bytes() == b"converted-after-redownload"
    assert events == [
        ("download", "1"),
        ("primary", "raw-bytes-1"),
        ("fallback", "raw-bytes-1"),
        ("download", "2"),
        ("fallback", "raw-bytes-2"),
    ]
