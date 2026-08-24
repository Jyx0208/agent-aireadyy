from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from agent.assets.resolver import resolve_file_asset
from agent.input.normalizer import InputTask
from agent.metadata.canonical import CanonicalFile, CanonicalMetadataValue, CanonicalProject
from agent.metadata.context import build_project_context
from agent.models import FileAsset, ProjectCandidate, ProjectContext, ProjectResolution
from agent.pride.client import (
    PrideClient,
    PridePaginationState,
    list_project_files_paginated_with_state,
)
from agent.pride.resolver import resolve_input_to_project


def _entry_names(entries: list[dict[str, Any]] | None) -> list[str]:
    names: list[str] = []
    for entry in entries or []:
        name = entry.get("name")
        if name:
            names.append(str(name))
    return names


def _metadata_values(values: list[str], source: str) -> list[CanonicalMetadataValue]:
    return [CanonicalMetadataValue(value=value, source=source, raw_keys=["name"]) for value in values]


class PrideAdapter:
    name = "pride"

    def __init__(self, client: PrideClient | None = None):
        self.client = client or PrideClient()

    def can_handle_accession(self, value: str) -> bool:
        return value.upper().startswith("PXD")

    def resolve_project(self, raw_input: str) -> ProjectResolution:
        resolution = resolve_input_to_project(self.client, raw_input)
        return resolution.model_copy(
            update={
                "primary_project": self._tag_candidate(resolution.primary_project),
                "alternative_projects": [self._tag_candidate(candidate) for candidate in resolution.alternative_projects],
            }
        )

    def _tag_candidate(self, candidate: ProjectCandidate | None) -> ProjectCandidate | None:
        if candidate is None:
            return None
        return candidate.model_copy(
            update={
                "repository": self.name,
                "px_accession": candidate.px_accession or candidate.project_accession,
            }
        )

    def get_project(self, accession: str) -> CanonicalProject:
        raw = self.client.get_project(accession)
        return self.map_project(raw)

    def list_project_files(self, project: CanonicalProject) -> list[CanonicalFile]:
        files, _state = self.list_project_files_with_state(project)
        return files

    def list_project_files_with_state(
        self,
        project: CanonicalProject,
    ) -> tuple[list[CanonicalFile], PridePaginationState]:
        result = list_project_files_paginated_with_state(
            self.client,
            project.primary_accession,
            mode="exhaustive",
        )
        return [self.map_file(record, project) for record in result.records], result.state

    def match_file(self, task: InputTask, files: list[CanonicalFile]) -> CanonicalFile | None:
        from agent.repositories.matching import match_canonical_file

        return match_canonical_file(task, files)

    def build_project_context(self, resolution: ProjectResolution, file_name: str) -> ProjectContext:
        if resolution.primary_project is None:
            raise ValueError("Cannot build PRIDE context without a primary project.")
        context = build_project_context(self.client, resolution.primary_project.project_accession, file_name)
        return context.model_copy(
            update={
                "repository": self.name,
                "px_accession": resolution.primary_project.project_accession,
                "raw_project_metadata": context.raw_project_metadata,
            }
        )

    def resolve_file_asset(self, task: InputTask, context: ProjectContext, work_dir: str | Path) -> FileAsset:
        asset = resolve_file_asset(task=task, context=context, work_dir=work_dir)
        download_urls = [asset.download_url] if asset.download_url else []
        transfer_method = "https" if asset.download_url and asset.download_url.startswith(("http://", "https://")) else "unknown"
        return asset.model_copy(
            update={
                "repository": self.name,
                "download_urls": download_urls,
                "transfer_method": transfer_method,
            }
        )

    def download_file(self, asset: FileAsset, target_path: Path, report: Callable | None = None) -> Path:
        url = asset.download_url or (asset.download_urls[0] if asset.download_urls else None)
        if not url:
            raise ValueError("PRIDE asset has no download URL.")
        return self.client.download_to_path(url, target_path, report=report)

    def download_to_path(self, url: str, target_path: str | Path, report: Callable | None = None) -> Path:
        return self.client.download_to_path(url, target_path, report=report)

    def map_project(self, raw: dict[str, Any]) -> CanonicalProject:
        accession = str(raw.get("accession") or raw.get("projectAccession") or "")
        return CanonicalProject(
            repository=self.name,
            primary_accession=accession,
            px_accession=accession or None,
            title=raw.get("title"),
            description=raw.get("projectDescription"),
            organisms=_metadata_values(_entry_names(raw.get("organisms")), "pride.organisms"),
            instruments=_metadata_values(_entry_names(raw.get("instruments")), "pride.instruments"),
            experiment_types=_metadata_values(_entry_names(raw.get("experimentTypes")), "pride.experimentTypes"),
            keywords=[str(item) for item in raw.get("keywords", []) or []],
            sample_processing_protocol=CanonicalMetadataValue(
                value=raw.get("sampleProcessingProtocol"),
                source="pride.sampleProcessingProtocol",
                raw_keys=["sampleProcessingProtocol"],
            )
            if raw.get("sampleProcessingProtocol")
            else None,
            data_processing_protocol=CanonicalMetadataValue(
                value=raw.get("dataProcessingProtocol"),
                source="pride.dataProcessingProtocol",
                raw_keys=["dataProcessingProtocol"],
            )
            if raw.get("dataProcessingProtocol")
            else None,
            submission_date=raw.get("submissionDate"),
            publication_date=raw.get("publicationDate"),
            raw_metadata=raw,
        )

    def map_file(self, raw: dict[str, Any], project: CanonicalProject) -> CanonicalFile:
        download_url = self.client.first_download_url(raw)
        category = raw.get("fileCategory")
        if isinstance(category, dict):
            category = category.get("value")
        return CanonicalFile(
            repository=self.name,
            project_accession=project.primary_accession,
            file_name=str(raw.get("fileName") or raw.get("name") or ""),
            file_category=str(category) if category else None,
            size_bytes=raw.get("fileSizeBytes"),
            checksum=raw.get("checksum"),
            download_urls=[download_url] if download_url else [],
            transfer_method="https" if download_url and download_url.startswith(("http://", "https://")) else "unknown",
            raw_record=raw,
        )
