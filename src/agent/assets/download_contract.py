# -*- coding: utf-8 -*-
"""Atomic download materialization contract (WP-D)."""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

try:
    from agent.utils import emit
except Exception:  # pragma: no cover
    def emit(report, message):  # type: ignore
        if callable(report):
            report(str(message))


DownloadStatus = Literal[
    "planned",
    "downloading",
    "size_checked",
    "checksum_verified",
    "checksum_unknown",
    "published",
    "failed",
    "abandoned",
    "verified",
    "size_mismatch",
]


class DownloadContractError(IOError):
    def __init__(self, code: str, message: str, receipt: "DownloadReceipt | None" = None):
        super().__init__(message)
        self.code = code
        self.receipt = receipt


@dataclass
class ChecksumSpec:
    algorithm: str
    hex_digest: str

    def matches(self, actual_hex: str) -> bool:
        return self.hex_digest.casefold() == (actual_hex or "").casefold()


@dataclass
class DownloadReceipt:
    final_path: str
    part_path: str | None = None
    source_urls: list[str] = field(default_factory=list)
    status: str = "planned"
    expected_size_bytes: int | None = None
    actual_size_bytes: int | None = None
    expected_checksum: str | None = None
    expected_checksum_algorithm: str | None = None
    actual_checksum: str | None = None
    actual_checksum_algorithm: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    reused: bool = False

    @property
    def actual_size(self) -> int | None:
        return self.actual_size_bytes

    @property
    def expected_size(self) -> int | None:
        return self.expected_size_bytes

    @property
    def error(self) -> str | None:
        return self.error_code or self.error_message

    @property
    def published(self) -> bool:
        if self.error_code or self.error_message:
            return False
        return self.status in {
            "published",
            "verified",
            "checksum_verified",
            "checksum_unknown",
        }


def parse_checksum_spec(value: str | None) -> ChecksumSpec | None:
    text = str(value or "").strip()
    if not text:
        return None
    if ":" in text or "=" in text or " " in text:
        parts = re.split(r"[:\s=]+", text, maxsplit=1)
        if len(parts) == 2:
            algo = parts[0].strip().casefold().replace("-", "")
            digest = parts[1].strip().casefold()
            if algo in {"md5", "sha1", "sha256", "sha512"} and re.fullmatch(r"[0-9a-f]+", digest):
                return ChecksumSpec(algorithm=algo, hex_digest=digest)
    digest = text.casefold()
    if not re.fullmatch(r"[0-9a-f]+", digest):
        return None
    algo = {32: "md5", 40: "sha1", 64: "sha256", 128: "sha512"}.get(len(digest))
    if algo is None:
        return None
    return ChecksumSpec(algorithm=algo, hex_digest=digest)


def part_path_for(final_path: Path | str) -> Path:
    path = Path(final_path)
    return path.with_name(path.name + ".part")


def unlink_quiet(path: Path | str | None) -> None:
    if path is None:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def hash_file(path: Path | str, *, algorithm: str = "sha256", chunk_size: int = 1024 * 1024) -> str:
    algo = str(algorithm or "sha256").casefold().replace("-", "")
    digest = hashlib.new(algo)
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def sha256_file(path: Path | str, *, chunk_size: int = 1024 * 1024) -> str:
    return hash_file(path, algorithm="sha256", chunk_size=chunk_size)


def _failed_receipt(
    final_path: Path,
    *,
    part_path: Path | None = None,
    expected_size_bytes: int | None = None,
    expected_checksum: str | None = None,
    actual_size_bytes: int | None = None,
    actual_checksum: str | None = None,
    expected_algorithm: str | None = None,
    actual_algorithm: str | None = None,
    code: str,
    message: str,
    source_urls: list[str] | None = None,
    status: str = "failed",
) -> DownloadReceipt:
    return DownloadReceipt(
        final_path=str(final_path),
        part_path=str(part_path) if part_path is not None else None,
        source_urls=list(source_urls or []),
        status=status,
        expected_size_bytes=expected_size_bytes,
        actual_size_bytes=actual_size_bytes,
        expected_checksum=expected_checksum,
        expected_checksum_algorithm=expected_algorithm,
        actual_checksum=actual_checksum,
        actual_checksum_algorithm=actual_algorithm,
        error_code=code,
        error_message=message,
    )


def verify_existing_file(
    final_path: Path | str,
    *,
    expected_size_bytes: int | None = None,
    expected_checksum: str | None = None,
    require_checksum: bool = False,
    source_urls: list[str] | None = None,
) -> DownloadReceipt:
    path = Path(final_path)
    if not path.exists() or not path.is_file():
        return _failed_receipt(
            path,
            expected_size_bytes=expected_size_bytes,
            expected_checksum=expected_checksum,
            code="missing",
            message=f"final path missing: {path}",
            source_urls=source_urls,
        )
    actual_size = path.stat().st_size
    if actual_size <= 0:
        return _failed_receipt(
            path,
            expected_size_bytes=expected_size_bytes,
            expected_checksum=expected_checksum,
            actual_size_bytes=actual_size,
            code="empty_file",
            message=f"final path is empty: {path}",
            source_urls=source_urls,
        )
    if expected_size_bytes not in (None, 0) and actual_size != int(expected_size_bytes):
        return _failed_receipt(
            path,
            expected_size_bytes=expected_size_bytes,
            expected_checksum=expected_checksum,
            actual_size_bytes=actual_size,
            code="size_mismatch",
            message=f"size mismatch for {path}: {actual_size} != {expected_size_bytes}",
            source_urls=source_urls,
            status="size_mismatch",
        )
    spec = parse_checksum_spec(expected_checksum)
    if spec is not None:
        actual_checksum = hash_file(path, algorithm=spec.algorithm)
        if not spec.matches(actual_checksum):
            return _failed_receipt(
                path,
                expected_size_bytes=expected_size_bytes,
                expected_checksum=spec.hex_digest,
                expected_algorithm=spec.algorithm,
                actual_size_bytes=actual_size,
                actual_checksum=actual_checksum,
                actual_algorithm=spec.algorithm,
                code="checksum_mismatch",
                message=f"checksum mismatch for {path}",
                source_urls=source_urls,
            )
        return DownloadReceipt(
            final_path=str(path),
            source_urls=list(source_urls or []),
            status="published",
            expected_size_bytes=expected_size_bytes,
            actual_size_bytes=actual_size,
            expected_checksum=spec.hex_digest,
            expected_checksum_algorithm=spec.algorithm,
            actual_checksum=actual_checksum,
            actual_checksum_algorithm=spec.algorithm,
            reused=True,
        )
    if require_checksum:
        return _failed_receipt(
            path,
            expected_size_bytes=expected_size_bytes,
            expected_checksum=expected_checksum,
            actual_size_bytes=actual_size,
            code="checksum_required",
            message=f"checksum required but not provided for {path}",
            source_urls=source_urls,
        )
    return DownloadReceipt(
        final_path=str(path),
        source_urls=list(source_urls or []),
        status="checksum_unknown",
        expected_size_bytes=expected_size_bytes,
        actual_size_bytes=actual_size,
        reused=True,
    )


def publish_part_file(
    part_path: Path | str,
    final_path: Path | str,
    *,
    expected_size: int | None = None,
    expected_size_bytes: int | None = None,
    expected_sha256: str | None = None,
    expected_checksum: str | None = None,
    require_checksum: bool = False,
    source_urls: list[str] | None = None,
    report: Callable[[str], None] | None = None,
) -> DownloadReceipt:
    """Verify .part then atomic replace. Raises DownloadContractError on failure."""
    part = Path(part_path)
    final = Path(final_path)
    size_expected = expected_size_bytes if expected_size_bytes is not None else expected_size
    checksum_expected = expected_checksum if expected_checksum is not None else expected_sha256

    def _raise(receipt: DownloadReceipt) -> None:
        raise DownloadContractError(
            receipt.error_code or "failed",
            receipt.error_message or "download contract failed",
            receipt,
        )

    if not part.exists():
        _raise(
            _failed_receipt(
                final,
                part_path=part,
                expected_size_bytes=size_expected,
                expected_checksum=checksum_expected,
                code="part_missing",
                message=f"part path missing: {part}",
                source_urls=source_urls,
            )
        )

    actual_size = part.stat().st_size
    if size_expected not in (None, 0) and actual_size != int(size_expected):
        unlink_quiet(part)
        _raise(
            _failed_receipt(
                final,
                part_path=part,
                expected_size_bytes=size_expected,
                expected_checksum=checksum_expected,
                actual_size_bytes=actual_size,
                code="size_mismatch",
                message=f"part size mismatch for {part}: {actual_size} != {size_expected}",
                source_urls=source_urls,
                status="size_mismatch",
            )
        )

    checksum_provided = bool(str(checksum_expected or "").strip())
    spec = parse_checksum_spec(checksum_expected)
    actual_checksum = None
    publish_status = "checksum_unknown"
    if checksum_provided and spec is None:
        unlink_quiet(part)
        _raise(
            _failed_receipt(
                final,
                part_path=part,
                expected_size_bytes=size_expected,
                expected_checksum=str(checksum_expected),
                actual_size_bytes=actual_size,
                code="checksum_mismatch",
                message=f"unparseable or mismatched checksum for {part}: {checksum_expected!r}",
                source_urls=source_urls,
            )
        )
    if spec is not None:
        actual_checksum = hash_file(part, algorithm=spec.algorithm)
        if not spec.matches(actual_checksum):
            unlink_quiet(part)
            _raise(
                _failed_receipt(
                    final,
                    part_path=part,
                    expected_size_bytes=size_expected,
                    expected_checksum=spec.hex_digest,
                    expected_algorithm=spec.algorithm,
                    actual_size_bytes=actual_size,
                    actual_checksum=actual_checksum,
                    actual_algorithm=spec.algorithm,
                    code="checksum_mismatch",
                    message=f"part checksum mismatch for {part}",
                    source_urls=source_urls,
                )
            )
        publish_status = "verified"
    elif require_checksum:
        _raise(
            _failed_receipt(
                final,
                part_path=part,
                expected_size_bytes=size_expected,
                expected_checksum=checksum_expected,
                actual_size_bytes=actual_size,
                code="checksum_required",
                message=f"checksum required but not provided for {part}",
                source_urls=source_urls,
            )
        )

    final.parent.mkdir(parents=True, exist_ok=True)
    with part.open("rb") as handle:
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
    os.replace(part, final)
    emit(report, f"published download receipt for {final} status={publish_status}")
    return DownloadReceipt(
        final_path=str(final),
        part_path=str(part),
        source_urls=list(source_urls or []),
        status="published" if publish_status == "verified" else publish_status,
        expected_size_bytes=size_expected,
        actual_size_bytes=actual_size,
        expected_checksum=spec.hex_digest if spec else None,
        expected_checksum_algorithm=spec.algorithm if spec else None,
        actual_checksum=actual_checksum,
        actual_checksum_algorithm=spec.algorithm if spec else None,
        reused=False,
    )


def write_bytes_atomic(
    final_path: Path | str,
    payload: bytes,
    *,
    expected_size_bytes: int | None = None,
    expected_checksum: str | None = None,
    require_checksum: bool = False,
    source_urls: list[str] | None = None,
    report: Callable[[str], None] | None = None,
) -> DownloadReceipt:
    final = Path(final_path)
    part = part_path_for(final)
    final.parent.mkdir(parents=True, exist_ok=True)
    unlink_quiet(part)
    with part.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
    size_expected = expected_size_bytes if expected_size_bytes is not None else len(payload)
    return publish_part_file(
        part,
        final,
        expected_size_bytes=size_expected,
        expected_checksum=expected_checksum,
        require_checksum=require_checksum,
        source_urls=source_urls,
        report=report,
    )
