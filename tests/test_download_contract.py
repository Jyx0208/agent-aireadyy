from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agent.assets.download_contract import (
    DownloadContractError,
    parse_checksum_spec,
    part_path_for,
    publish_part_file,
    verify_existing_file,
    write_bytes_atomic,
)
from agent.assets.downloader import download_file_asset
from agent.models import FileAsset


def test_parse_checksum_spec_infers_algo() -> None:
    md5 = hashlib.md5(b"x").hexdigest()
    spec = parse_checksum_spec(md5)
    assert spec is not None
    assert spec.algorithm == "md5"
    sha = hashlib.sha256(b"x").hexdigest()
    assert parse_checksum_spec(f"sha256:{sha}").algorithm == "sha256"


def test_publish_part_file_checksum_mismatch_never_publishes(tmp_path: Path) -> None:
    final_path = tmp_path / "sample.bin"
    part_path = part_path_for(final_path)
    part_path.write_bytes(b"payload")
    expected = hashlib.md5(b"other").hexdigest()
    try:
        receipt = publish_part_file(part_path, final_path, expected_checksum=expected)
        assert not receipt.published
        assert receipt.error_code == "checksum_mismatch"
    except DownloadContractError as exc:
        assert exc.code == "checksum_mismatch"
        receipt = exc.receipt
    assert not final_path.exists()
    assert not part_path.exists()


def test_publish_part_file_size_mismatch_never_publishes(tmp_path: Path) -> None:
    final_path = tmp_path / "sample.bin"
    part_path = part_path_for(final_path)
    part_path.write_bytes(b"payload")
    try:
        receipt = publish_part_file(part_path, final_path, expected_size_bytes=999)
        assert not receipt.published
        assert receipt.error_code == "size_mismatch"
    except DownloadContractError as exc:
        assert exc.code == "size_mismatch"
    assert not final_path.exists()


def test_publish_part_file_atomic_success(tmp_path: Path) -> None:
    final_path = tmp_path / "sample.bin"
    payload = b"good-bytes"
    part_path = part_path_for(final_path)
    part_path.write_bytes(payload)
    receipt = publish_part_file(
        part_path,
        final_path,
        expected_size_bytes=len(payload),
        expected_checksum=hashlib.md5(payload).hexdigest(),
    )
    assert receipt.published
    assert final_path.read_bytes() == payload
    assert not part_path.exists()
    assert receipt.actual_checksum == hashlib.md5(payload).hexdigest()


def test_verify_existing_file_rejects_wrong_checksum(tmp_path: Path) -> None:
    path = tmp_path / "sample.bin"
    path.write_bytes(b"payload")
    receipt = verify_existing_file(path, expected_checksum=hashlib.md5(b"nope").hexdigest())
    assert receipt.status == "failed"
    assert receipt.error_code == "checksum_mismatch"


def test_write_bytes_atomic_and_reuse_via_downloader(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_REPOSITORY_CACHE_DIR", str(tmp_path / "cache"))
    payload = b"mzml-bytes"
    checksum = hashlib.md5(payload).hexdigest()

    class FakeClient:
        def download_binary(self, url: str) -> bytes:
            return payload

    asset = FileAsset(
        original_file_name="sample.mzML",
        resolved_asset_type="mzml",
        project_accession="PXD1",
        matched_project_file="sample.mzML",
        download_url="https://example.test/sample.mzML",
        local_path=tmp_path / "run" / "assets" / "downloads" / "sample.mzML",
        prepared_path=tmp_path / "run" / "assets" / "downloads" / "sample.mzML",
        expected_size_bytes=len(payload),
        checksum=checksum,
        requires_conversion=False,
        asset_confidence=1.0,
        match_type="exact",
    )
    first = download_file_asset(FakeClient(), asset)
    assert first.read_bytes() == payload

    class BoomClient:
        def download_binary(self, url: str) -> bytes:
            raise AssertionError("should reuse verified cache")

    second = download_file_asset(BoomClient(), asset)
    assert second.read_bytes() == payload


def test_downloader_rejects_corrupt_reuse_without_checksum_match(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PRIDE_CACHE_DIR", str(tmp_path / "cache"))
    good = b"raw-bytes"
    checksum = hashlib.md5(good).hexdigest()
    cache = tmp_path / "cache" / "PXD123456" / "sample.raw"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"corrupt")

    class FakeClient:
        calls = 0

        def download_binary(self, url: str) -> bytes:
            FakeClient.calls += 1
            return good

    asset = FileAsset(
        original_file_name="sample.raw",
        resolved_asset_type="raw",
        project_accession="PXD123456",
        matched_project_file="sample.raw",
        download_url="https://ftp.pride.ebi.ac.uk/pride/data/archive/sample.raw",
        local_path=tmp_path / "run" / "assets" / "downloads" / "sample.raw",
        prepared_path=tmp_path / "run" / "assets" / "prepared" / "sample.mzML",
        expected_size_bytes=len(good),
        checksum=checksum,
        requires_conversion=True,
        asset_confidence=1.0,
        match_type="exact",
    )
    downloaded = download_file_asset(FakeClient(), asset)
    assert downloaded.read_bytes() == good
    assert FakeClient.calls == 1
    assert cache.read_bytes() == good


def test_write_bytes_atomic_helper(tmp_path: Path) -> None:
    path = tmp_path / "out.bin"
    payload = b"abc"
    receipt = write_bytes_atomic(path, payload, expected_checksum=hashlib.md5(payload).hexdigest())
    assert receipt.published
    assert path.read_bytes() == payload
    assert not part_path_for(path).exists()
