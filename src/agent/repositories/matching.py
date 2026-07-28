from __future__ import annotations

import re
from pathlib import Path, PurePosixPath, PureWindowsPath

from agent.input.normalizer import InputTask, normalize_input
from agent.metadata.canonical import CanonicalFile, CanonicalProject
from agent.models import FileAsset


MASSIVE_COLLECTION_PRIORITY = {
    "raw": 100,
    "peak": 90,
    "ccms_peak": 80,
    "metadata": 20,
    "ccms_parameters": 10,
}


def classify_asset_type(file_name: str) -> str:
    lower_name = file_name.lower()
    if lower_name.endswith((".mzml", ".mzml.gz")):
        return "mzml"
    if lower_name.endswith((".mzxml", ".mzxml.gz")):
        return "mzxml"
    if lower_name.endswith((".d", ".d.zip", ".d.tar.gz", ".d.tgz")):
        return "tims"
    if lower_name.endswith((".raw", ".raw.zip")):
        return "raw"
    if lower_name.endswith((".wiff", ".wiff2")):
        return "wiff"
    if lower_name.endswith((".mgf", ".mgf.gz")):
        return "mgf"
    if lower_name.endswith((".mzid", ".mzid.gz")):
        return "mzid"
    return "unknown"


def asset_priority(asset_type: str) -> int:
    return {
        "mzml": 7,
        "mzxml": 6,
        "tims": 5,
        "raw": 4,
        "wiff": 3,
        "mgf": 2,
        "mzid": 1,
        "unknown": 0,
    }.get(asset_type, 0)


def safe_file_name(file_name: str) -> str:
    posix_name = PurePosixPath(file_name).name
    windows_name = PureWindowsPath(posix_name).name
    if windows_name in {"", ".", ".."}:
        raise ValueError(f"Invalid repository file name: {file_name}")
    return windows_name


def prepared_stem(file_name: str, asset_type: str) -> str:
    lower_name = file_name.lower()
    if lower_name.endswith(".mzxml.gz"):
        return file_name[: -len(".mzxml.gz")]
    for extension in (".mzml.gz", ".mgf.gz", ".mzid.gz"):
        if lower_name.endswith(extension):
            return file_name[:-3]
    for extension in (".d.tar.gz", ".d.tgz", ".d.zip", ".raw.zip"):
        if lower_name.endswith(extension):
            return file_name[: -len(extension)]
    if asset_type == "wiff" and lower_name.endswith(".wiff2"):
        return file_name[:-6]
    return Path(file_name).stem


def score_file_match(task: InputTask, file_name: str) -> tuple[int, str] | None:
    project_task = normalize_input(file_name)
    if project_task.normalized_name == task.normalized_name:
        return 100, "exact"
    if project_task.normalized_name == f"{task.normalized_name}.gz":
        return 100, "compressed"
    if project_task.stem.lower() == task.file_name.lower():
        return 100, "compressed"
    if project_task.stem.lower() == task.stem.lower():
        return 100, "stem"
    task_prefix = task.stem.lower().split("_")[0].split("-")[0]
    project_prefix = project_task.stem.lower().split("_")[0].split("-")[0]
    if task_prefix and task_prefix == project_prefix:
        return 70, "prefix"
    return None


def _collection_priority(file: CanonicalFile) -> int:
    category = (file.file_category or "").lower()
    return MASSIVE_COLLECTION_PRIORITY.get(category, 0)


def match_canonical_file(task: InputTask, files: list[CanonicalFile]) -> CanonicalFile | None:
    scored: list[tuple[int, int, int, CanonicalFile]] = []
    for file in files:
        match = score_file_match(task, file.file_name)
        if match is None:
            continue
        match_score, _ = match
        asset_type = classify_asset_type(file.file_name)
        scored.append((match_score, _collection_priority(file), asset_priority(asset_type), file))
    if not scored:
        return None
    return max(scored, key=lambda item: (item[0], item[1], item[2]))[3]


def canonical_files_to_project_file_records(files: list[CanonicalFile]) -> list[dict]:
    records = []
    for file in files:
        records.append(
            {
                "fileName": file.file_name,
                "logicalPath": file.logical_path,
                "fileSizeBytes": file.size_bytes,
                "fileCategory": {"value": file.file_category} if file.file_category else None,
                "publicFileLocations": [{"value": url} for url in file.download_urls],
                "repository": file.repository,
                "transferMethod": file.transfer_method,
                "rawRecord": file.raw_record,
            }
        )
    return records


def canonical_file_to_asset(
    task: InputTask,
    project: CanonicalProject,
    file: CanonicalFile,
    work_dir: str | Path,
) -> FileAsset:
    work_dir = Path(work_dir)
    asset_type = classify_asset_type(file.file_name)
    local_file_name = safe_file_name(file.file_name)
    downloads_dir = work_dir / "assets" / "downloads"
    prepared_dir = work_dir / "assets" / "prepared"
    local_path = downloads_dir / local_file_name
    stem = prepared_stem(local_file_name, asset_type)
    requires_conversion = asset_type in {"raw", "wiff", "mzxml"}
    if requires_conversion:
        prepared_path = prepared_dir / f"{stem}.mzML"
    elif local_file_name.lower().endswith((".mzml.gz", ".mgf.gz", ".mzid.gz")):
        prepared_path = prepared_dir / stem
    elif asset_type == "tims" and local_file_name.lower().endswith((".d.zip", ".d.tar.gz", ".d.tgz")):
        prepared_path = prepared_dir / f"{stem}.d"
    else:
        prepared_path = local_path

    match = score_file_match(task, file.file_name)
    match_score, match_type = match or (0, "unresolved")
    confidence = min(1.0, (match_score / 100) * 0.7 + (asset_priority(asset_type) / 7) * 0.3)
    download_url = file.download_urls[0] if file.download_urls else None
    transfer_method = file.transfer_method
    if transfer_method == "unknown" and download_url:
        if download_url.startswith("ftp://"):
            transfer_method = "ftp"
        elif download_url.startswith(("http://", "https://")):
            transfer_method = "https"
        elif "@" in download_url and ":" in download_url:
            transfer_method = "aspera"

    return FileAsset(
        repository=file.repository,
        original_file_name=task.file_name,
        resolved_asset_type=asset_type,  # type: ignore[arg-type]
        project_accession=project.primary_accession,
        native_project_accession=project.native_accession,
        matched_project_file=file.file_name,
        logical_path=file.logical_path,
        file_category=file.file_category,
        file_format=file.file_format or asset_type,
        download_url=download_url,
        download_urls=list(file.download_urls),
        transfer_method=transfer_method,  # type: ignore[arg-type]
        local_path=local_path,
        prepared_path=prepared_path,
        expected_size_bytes=file.size_bytes,
        checksum=file.checksum,
        requires_conversion=requires_conversion,
        asset_confidence=confidence,
        match_type=match_type,
    )


def canonical_date(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    return match.group(0) if match else text[:10]
