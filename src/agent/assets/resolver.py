from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath

from agent.input.normalizer import normalize_input
from agent.models import FileAsset, InputTask, ProjectContext


def _classify_asset_type(file_name: str) -> str:
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


def _asset_priority(asset_type: str) -> int:
    priorities = {
        "mzml": 7,
        "mzxml": 6,
        "tims": 5,
        "raw": 4,
        "wiff": 3,
        "mgf": 2,
        "mzid": 1,
        "unknown": 0,
    }
    return priorities.get(asset_type, 0)


def _match_info(task: InputTask, project_file_name: str) -> tuple[int, str] | None:
    project_task = normalize_input(project_file_name)

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


def _candidate_sort_key(candidate: tuple[int, int, str, dict]) -> tuple[int, int]:
    match_score, asset_priority, _, _ = candidate
    return (match_score, asset_priority)


def _normalize_download_url(url: str | None) -> str | None:
    if not url:
        return None
    if url.startswith("ftp://ftp.pride.ebi.ac.uk/"):
        return url.replace("ftp://ftp.pride.ebi.ac.uk/", "https://ftp.pride.ebi.ac.uk/")
    return url


def _safe_file_name(file_name: str) -> str:
    posix_name = PurePosixPath(file_name).name
    windows_name = PureWindowsPath(posix_name).name
    if windows_name in {"", ".", ".."}:
        raise ValueError(f"无效的项目文件名：{file_name}")
    return windows_name


def _prepared_stem(file_name: str, asset_type: str) -> str:
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


def _is_sidecar_for(primary_file_name: str, project_file_name: str) -> bool:
    primary = primary_file_name.lower()
    candidate = project_file_name.lower()
    if primary.endswith(".wiff") and candidate.startswith(primary + "."):
        return True
    if primary.endswith(".wiff2") and candidate.startswith(primary + "."):
        return True
    return False


def _sidecar_files(primary_file_name: str, file_records: list[dict], downloads_dir: Path) -> list[dict[str, str]]:
    sidecars = []
    for file_record in file_records:
        file_name = str(file_record.get("fileName", ""))
        if not _is_sidecar_for(primary_file_name, file_name):
            continue
        download_url = _first_download_url(file_record)
        if not download_url:
            continue
        local_file_name = _safe_file_name(file_name)
        sidecars.append(
            {
                "file_name": file_name,
                "download_url": download_url,
                "local_path": str(downloads_dir / local_file_name),
            }
        )
    return sidecars


def _first_download_url(file_record: dict) -> str | None:
    locations = file_record.get("publicFileLocations", []) or []
    normalized_candidates: list[str] = []
    fallback_candidates: list[str] = []

    for location in locations:
        value = location.get("value")
        if value:
            normalized = _normalize_download_url(str(value))
            if normalized and normalized.startswith(("https://", "http://", "ftp://")):
                normalized_candidates.append(normalized)
            elif normalized:
                fallback_candidates.append(normalized)
    if normalized_candidates:
        return normalized_candidates[0]
    if fallback_candidates:
        return fallback_candidates[0]
    return None


def resolve_file_asset(task: InputTask, context: ProjectContext, work_dir: str | Path) -> FileAsset:
    work_dir = Path(work_dir)
    candidates: list[tuple[int, int, str, dict]] = []

    for file_record in context.project_files:
        project_file_name = file_record.get("fileName", "")
        match = _match_info(task, project_file_name)
        if not match:
            continue
        match_score, match_type = match
        asset_type = _classify_asset_type(project_file_name)
        candidates.append((match_score, _asset_priority(asset_type), match_type, file_record))

    if not candidates:
        return FileAsset(
            original_file_name=task.file_name,
            resolved_asset_type="unknown",
            project_accession=context.project_accession,
            asset_confidence=0.0,
            match_type="unresolved",
        )

    best = max(candidates, key=_candidate_sort_key)
    match_score, _, match_type, file_record = best
    matched_file = str(file_record["fileName"])
    local_file_name = _safe_file_name(matched_file)
    asset_type = _classify_asset_type(matched_file)

    downloads_dir = work_dir / "assets" / "downloads"
    prepared_dir = work_dir / "assets" / "prepared"
    local_path = downloads_dir / local_file_name

    prepared_stem = _prepared_stem(local_file_name, asset_type)
    requires_conversion = asset_type in {"raw", "wiff", "mzxml"}
    if requires_conversion:
        prepared_path = prepared_dir / f"{prepared_stem}.mzML"
    elif local_file_name.lower().endswith((".mzml.gz", ".mgf.gz", ".mzid.gz")):
        prepared_path = prepared_dir / prepared_stem
    elif asset_type == "tims" and local_file_name.lower().endswith((".d.zip", ".d.tar.gz", ".d.tgz")):
        prepared_path = prepared_dir / f"{prepared_stem}.d"
    else:
        prepared_path = local_path

    confidence = min(1.0, (match_score / 100) * 0.7 + (_asset_priority(asset_type) / 4) * 0.3)

    return FileAsset(
        original_file_name=task.file_name,
        resolved_asset_type=asset_type,  # type: ignore[arg-type]
        project_accession=context.project_accession,
        matched_project_file=matched_file,
        download_url=_first_download_url(file_record),
        local_path=local_path,
        prepared_path=prepared_path,
        expected_size_bytes=file_record.get("fileSizeBytes"),
        sidecar_files=_sidecar_files(matched_file, context.project_files, downloads_dir),
        requires_conversion=requires_conversion,
        asset_confidence=confidence,
        match_type=match_type,
    )
