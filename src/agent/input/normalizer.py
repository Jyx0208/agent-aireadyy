from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import PurePosixPath, PureWindowsPath
from urllib.parse import urlparse

from agent.models import InputTask


_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _normalize_name(file_name: str) -> str:
    if "." in file_name:
        stem, extension = file_name.rsplit(".", 1)
        normalized_stem = _NON_ALNUM.sub("-", stem.lower()).strip("-")
        normalized_ext = extension.lower()
        return f"{normalized_stem}.{normalized_ext}"
    return _NON_ALNUM.sub("-", file_name.lower()).strip("-")


def _extract_file_name(raw_input: str) -> tuple[str, str]:
    parsed = urlparse(raw_input)
    if parsed.scheme and parsed.netloc:
        file_name = PurePosixPath(parsed.path).name
        return file_name, "url"

    windows_path = PureWindowsPath(raw_input)
    posix_path = PurePosixPath(raw_input)
    if windows_path.name != raw_input or posix_path.name != raw_input or "\\" in raw_input or "/" in raw_input:
        file_name = windows_path.name if windows_path.name != raw_input else posix_path.name
        return file_name, "local_path"

    return raw_input, "file_name"


def normalize_input(raw_input: str) -> InputTask:
    file_name, source_type = _extract_file_name(raw_input)
    if "." in file_name:
        stem, extension = file_name.rsplit(".", 1)
        extension = f".{extension.lower()}"
    else:
        stem = file_name
        extension = ""

    normalized_name = _normalize_name(file_name)
    digest = hashlib.sha1(raw_input.encode("utf-8")).hexdigest()[:12]
    task_id = f"task-{uuid.uuid5(uuid.NAMESPACE_URL, raw_input)}-{digest}"[:48]

    return InputTask(
        task_id=task_id,
        original_input=raw_input,
        source_type=source_type,
        file_name=file_name,
        normalized_name=normalized_name,
        stem=stem,
        extension=extension,
    )
