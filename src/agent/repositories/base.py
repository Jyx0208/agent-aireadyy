from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol

from agent.input.normalizer import InputTask
from agent.metadata.canonical import CanonicalFile, CanonicalProject
from agent.models import FileAsset, ProjectContext, ProjectResolution, RepositoryName


class RepositoryAdapter(Protocol):
    name: RepositoryName

    def can_handle_accession(self, value: str) -> bool:
        ...

    def resolve_project(self, raw_input: str) -> ProjectResolution:
        ...

    def get_project(self, accession: str) -> CanonicalProject:
        ...

    def list_project_files(self, project: CanonicalProject) -> list[CanonicalFile]:
        ...

    def match_file(self, task: InputTask, files: list[CanonicalFile]) -> CanonicalFile | None:
        ...

    def build_project_context(self, resolution: ProjectResolution, file_name: str) -> ProjectContext:
        ...

    def resolve_file_asset(self, task: InputTask, context: ProjectContext, work_dir: str | Path) -> FileAsset:
        ...

    def download_file(self, asset: FileAsset, target_path: Path, report: Callable | None = None) -> Path:
        ...
