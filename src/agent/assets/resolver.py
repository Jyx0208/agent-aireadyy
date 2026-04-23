from __future__ import annotations

from pathlib import Path, PurePath

from agent.input.normalizer import normalize_input
from agent.models import FileAsset, InputTask, ProjectContext


def _classify_asset_type(file_name: str) -> str:
    lower_name = file_name.lower()
    if lower_name.endswith(".mzml"):
        return "mzml"
    if lower_name.endswith(".d"):
        return "tims"
    if lower_name.endswith(".raw"):
        return "raw"
    if lower_name.endswith(".wiff"):
        return "wiff"
    return "unknown"


def _asset_priority(asset_type: str) -> int:
    priorities = {
        "mzml": 4,
        "tims": 3,
        "raw": 2,
        "wiff": 1,
        "unknown": 0,
    }
    return priorities.get(asset_type, 0)


def _match_info(task: InputTask, project_file_name: str) -> tuple[int, str] | None:
    project_task = normalize_input(project_file_name)

    if project_task.normalized_name == task.normalized_name:
        return 100, "exact"
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
            asset_confidence=0.0,
            match_type="unresolved",
        )

    best = max(candidates, key=_candidate_sort_key)
    match_score, _, match_type, file_record = best
    matched_file = str(file_record["fileName"])
    asset_type = _classify_asset_type(matched_file)

    downloads_dir = work_dir / "assets" / "downloads"
    prepared_dir = work_dir / "assets" / "prepared"
    local_path = downloads_dir / matched_file

    requires_conversion = asset_type in {"raw", "wiff"}
    if requires_conversion:
        prepared_path = prepared_dir / f"{PurePath(matched_file).stem}.mzML"
    else:
        prepared_path = local_path

    confidence = min(1.0, (match_score / 100) * 0.7 + (_asset_priority(asset_type) / 4) * 0.3)

    return FileAsset(
        original_file_name=task.file_name,
        resolved_asset_type=asset_type,  # type: ignore[arg-type]
        matched_project_file=matched_file,
        download_url=_first_download_url(file_record),
        local_path=local_path,
        prepared_path=prepared_path,
        requires_conversion=requires_conversion,
        asset_confidence=confidence,
        match_type=match_type,
    )
