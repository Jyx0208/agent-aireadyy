from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from agent.input.normalizer import normalize_input
from agent.models import FileAsset, JsonModel, ProjectContext, ProjectResolution
from agent.repositories.registry import RepositoryRegistry
from agent.utils import write_json


RepositorySmokeMode = Literal["parameters", "prepare", "full"]


class RepositorySmokeResult(JsonModel):
    status: Literal["completed", "blocked", "failed"]
    repository: str
    requested_repository: str
    input_value: str
    mode: RepositorySmokeMode = "parameters"
    project_accession: str | None = None
    native_accession: str | None = None
    px_accession: str | None = None
    matched_file: str | None = None
    asset_type: str | None = None
    download_url: str | None = None
    transfer_method: str | None = None
    expected_size_bytes: int | None = None
    resolution_confidence: float = 0.0
    resolution_reason: str = ""
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    next_step: str = ""
    files: dict[str, str] = Field(default_factory=dict)


def run_repository_smoke(
    *,
    repository: str,
    input_value: str,
    mode: RepositorySmokeMode,
    output_dir: str | Path,
    registry: RepositoryRegistry | None = None,
) -> RepositorySmokeResult:
    requested_repository = _normalize_repository(repository)
    if mode not in {"parameters", "prepare", "full"}:
        raise ValueError("mode must be parameters, prepare, or full.")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    registry = registry or RepositoryRegistry()

    blockers: list[str] = []
    warnings: list[str] = []
    resolution: ProjectResolution | None = None
    context: ProjectContext | None = None
    asset: FileAsset | None = None
    adapter_name = requested_repository

    try:
        task = normalize_input(input_value)
        resolution = registry.resolve_project(requested_repository, input_value)
        if resolution.primary_project is None:
            blockers.extend(_resolution_blockers(requested_repository, resolution))
            warnings.extend(_resolution_warnings(requested_repository, resolution))
            adapter = registry.choose(requested_repository, input_value)
        else:
            adapter_name = resolution.primary_project.repository
            adapter = registry.get(adapter_name)
            context = adapter.build_project_context(resolution, task.file_name)
            asset = adapter.resolve_file_asset(task, context, output_dir)
            if asset.resolved_asset_type == "unknown":
                blockers.append("file_asset_not_resolved")
            if not asset.download_url and not asset.local_path:
                warnings.append("download_url_missing")
        if mode in {"prepare", "full"}:
            warnings.append("repository_smoke_v1_does_not_execute_download_or_full")
    except Exception as exc:  # pragma: no cover - defensive adapter boundary
        blockers.append(f"adapter_error:{type(exc).__name__}")
        warnings.append(str(exc))

    status: Literal["completed", "blocked", "failed"] = "completed" if not blockers else "blocked"
    primary = resolution.primary_project if resolution is not None else None
    result = RepositorySmokeResult(
        status=status,
        repository=adapter_name if adapter_name != "auto" else requested_repository,
        requested_repository=requested_repository,
        input_value=input_value,
        mode=mode,
        project_accession=primary.project_accession if primary is not None else None,
        native_accession=primary.native_accession if primary is not None else None,
        px_accession=primary.px_accession if primary is not None else None,
        matched_file=asset.matched_project_file if asset is not None else (primary.matched_file if primary is not None else None),
        asset_type=asset.resolved_asset_type if asset is not None else None,
        download_url=asset.download_url if asset is not None else None,
        transfer_method=asset.transfer_method if asset is not None else None,
        expected_size_bytes=asset.expected_size_bytes if asset is not None else None,
        resolution_confidence=resolution.resolution_confidence if resolution is not None else 0.0,
        resolution_reason=resolution.resolution_reason if resolution is not None else "",
        blockers=blockers,
        warnings=warnings,
        next_step=_next_step(status, mode, blockers),
    )

    files = {
        "repository_smoke_summary_json": output_dir / "repository_smoke_summary.json",
        "repository_smoke_summary_csv": output_dir / "repository_smoke_summary.csv",
        "repository_smoke_report_md": output_dir / "repository_smoke_report.md",
        "repository_resolution_json": output_dir / "repository_resolution.json",
        "repository_context_json": output_dir / "repository_context.json",
        "repository_asset_json": output_dir / "repository_asset.json",
    }
    result = result.model_copy(update={"files": {key: str(path) for key, path in files.items()}})
    write_json(files["repository_smoke_summary_json"], result.model_dump(mode="json"))
    _write_csv(files["repository_smoke_summary_csv"], result)
    write_json(files["repository_resolution_json"], resolution.model_dump(mode="json") if resolution is not None else {})
    write_json(files["repository_context_json"], context.model_dump(mode="json") if context is not None else {})
    write_json(files["repository_asset_json"], asset.model_dump(mode="json") if asset is not None else {})
    files["repository_smoke_report_md"].write_text(_markdown(result), encoding="utf-8")
    return result


def _normalize_repository(repository: str) -> str:
    value = str(repository or "auto").strip().lower().replace("-", "_")
    aliases = {
        "px": "pride",
        "proteomexchange": "pride",
        "msv": "massive",
        "massive_ucsd": "massive",
        "gnps": "massive",
        "ipx": "iprox",
        "all": "auto",
    }
    value = aliases.get(value, value)
    if value not in {"auto", "pride", "massive", "iprox"}:
        raise ValueError("repository must be auto, pride, massive, or iprox.")
    return value


def _next_step(status: str, mode: str, blockers: list[str]) -> str:
    if status != "completed":
        if "iprox_index_missing" in blockers:
            return "refresh_iprox_index_or_set_agent_iprox_index_xlsx"
        if "project_not_resolved" in blockers:
            return "provide_known_repository_accession_or_file_path"
        if "file_asset_not_resolved" in blockers:
            return "provide_more_specific_file_name_or_accession"
        return "inspect_repository_adapter_error"
    if mode == "parameters":
        return "run_one_click_parameters_or_prepare_when_ready"
    return "use_one_click_run_for_download_prepare_or_full"


def _resolution_blockers(repository: str, resolution: ProjectResolution) -> list[str]:
    reason = (resolution.resolution_reason or "").casefold()
    if (
        "mapping workbook not found" in reason
        or "iprox mapping workbook" in reason
        or "agent_iprox_index_xlsx" in reason
    ):
        return ["iprox_index_missing"]
    return ["project_not_resolved"]


def _resolution_warnings(repository: str, resolution: ProjectResolution) -> list[str]:
    if "iprox_index_missing" in _resolution_blockers(repository, resolution):
        return ["Run refresh-iprox-index to build a public JSONL cache, or set AGENT_IPROX_INDEX_DIR / AGENT_IPROX_INDEX_XLSX."]
    return []


def _write_csv(path: Path, result: RepositorySmokeResult) -> None:
    payload = result.model_dump(mode="json")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(payload))
        writer.writeheader()
        writer.writerow(
            {
                key: value if not isinstance(value, (dict, list)) else str(value)
                for key, value in payload.items()
            }
        )


def _markdown(result: RepositorySmokeResult) -> str:
    lines = [
        "# Repository Smoke Report",
        "",
        f"- Status: `{result.status}`",
        f"- Requested repository: `{result.requested_repository}`",
        f"- Resolved repository: `{result.repository}`",
        f"- Input: `{result.input_value}`",
        f"- Project: `{result.project_accession or 'unknown'}`",
        f"- Native accession: `{result.native_accession or 'unknown'}`",
        f"- PX accession: `{result.px_accession or 'unknown'}`",
        f"- Matched file: `{result.matched_file or 'unknown'}`",
        f"- Asset type: `{result.asset_type or 'unknown'}`",
        f"- Transfer method: `{result.transfer_method or 'unknown'}`",
        f"- Expected size bytes: `{result.expected_size_bytes or 'unknown'}`",
        f"- Resolution reason: `{result.resolution_reason or 'unknown'}`",
        f"- Blockers: {', '.join(result.blockers) if result.blockers else 'None'}",
        f"- Warnings: {', '.join(result.warnings) if result.warnings else 'None'}",
        f"- Next step: `{result.next_step}`",
        "",
    ]
    return "\n".join(lines)
