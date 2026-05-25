from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.agent_core.models import AgentObservation
from agent.models import FileAsset, ProjectCandidate, ProjectContext, ProjectResolution


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _candidate_summary(candidate: ProjectCandidate | Any | None) -> dict[str, Any] | None:
    if candidate is None:
        return None
    return {
        "repository": _field(candidate, "repository"),
        "project_accession": _field(candidate, "project_accession"),
        "native_accession": _field(candidate, "native_accession"),
        "px_accession": _field(candidate, "px_accession"),
        "matched_file": _field(candidate, "matched_file"),
        "match_type": _field(candidate, "match_type"),
        "match_score": _field(candidate, "match_score"),
        "metadata_consistency": _field(candidate, "metadata_consistency"),
        "evidence": list(_field(candidate, "evidence", []) or []),
    }


def _metadata_entry(context: ProjectContext | Any, key: str) -> dict[str, Any] | None:
    metadata_map = _field(context, "metadata", {}) or {}
    metadata = metadata_map.get(key) if isinstance(metadata_map, dict) else None
    if metadata is None:
        return None
    return {
        "value": _field(metadata, "value"),
        "source": _field(metadata, "source"),
        "source_level": _field(metadata, "source_level"),
        "completeness": _field(metadata, "completeness"),
    }


def _asset_summary(asset: FileAsset | Any | None) -> dict[str, Any]:
    if asset is None:
        return {}
    return {
        "repository": _field(asset, "repository"),
        "project_accession": _field(asset, "project_accession"),
        "original_file_name": _field(asset, "original_file_name"),
        "resolved_asset_type": _field(asset, "resolved_asset_type"),
        "matched_project_file": _field(asset, "matched_project_file"),
        "logical_path": _field(asset, "logical_path"),
        "download_url": _field(asset, "download_url"),
        "download_urls": list(_field(asset, "download_urls", []) or []),
        "transfer_method": _field(asset, "transfer_method"),
        "requires_conversion": _field(asset, "requires_conversion"),
        "asset_confidence": _field(asset, "asset_confidence"),
        "match_type": _field(asset, "match_type"),
    }


def build_agent_observation(
    input_file: str,
    resolution: ProjectResolution,
    context: ProjectContext,
    *,
    asset: FileAsset | None = None,
    resource_state: dict[str, Any] | None = None,
) -> AgentObservation:
    primary = _field(resolution, "primary_project")
    primary_summary = _candidate_summary(primary)
    alternatives = list(_field(resolution, "alternative_projects", []) or [])
    candidates = [
        *([primary_summary] if primary_summary is not None else []),
        *[summary for candidate in alternatives if (summary := _candidate_summary(candidate)) is not None],
    ]
    metadata_evidence = {
        label: entry
        for label, entry in {
            "species": _metadata_entry(context, "organisms"),
            "instrument": _metadata_entry(context, "instruments"),
            "experiment_type": _metadata_entry(context, "experimentTypes"),
            "project_description": _metadata_entry(context, "projectDescription"),
            "sample_processing": _metadata_entry(context, "sampleProcessingProtocol"),
            "data_processing": _metadata_entry(context, "dataProcessingProtocol"),
        }.items()
        if entry is not None
    }
    return AgentObservation(
        input_file=input_file,
        repository_candidates=candidates,
        selected_project=primary_summary,
        metadata_evidence=metadata_evidence,
        asset_evidence=_asset_summary(asset),
        resource_state=resource_state or {},
    )
