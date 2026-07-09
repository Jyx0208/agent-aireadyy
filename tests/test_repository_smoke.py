from __future__ import annotations

from pathlib import Path

from agent.input.normalizer import InputTask
from agent.metadata.canonical import CanonicalFile, CanonicalProject
from agent.models import FileAsset, ProjectCandidate, ProjectContext, ProjectResolution
from agent.repositories.registry import RepositoryRegistry
from agent.repositories.smoke import run_repository_smoke


class FakeMassiveAdapter:
    name = "massive"

    def can_handle_accession(self, value: str) -> bool:
        return "MSV" in value

    def resolve_project(self, raw_input: str) -> ProjectResolution:
        return ProjectResolution(
            primary_project=ProjectCandidate(
                repository="massive",
                project_accession="MSV000000001",
                native_accession="MSV000000001",
                px_accession="PXD999999",
                matched_file="raw/sample.mzML",
                match_type="accession",
                match_score=95,
                metadata_consistency=0.9,
            ),
            resolution_confidence=0.95,
            resolution_reason="fake massive match",
        )

    def get_project(self, accession: str) -> CanonicalProject:  # pragma: no cover - not used by smoke fake
        return CanonicalProject(repository="massive", primary_accession=accession)

    def list_project_files(self, project: CanonicalProject) -> list[CanonicalFile]:  # pragma: no cover
        return []

    def match_file(self, task: InputTask, files: list[CanonicalFile]) -> CanonicalFile | None:  # pragma: no cover
        return None

    def build_project_context(self, resolution: ProjectResolution, file_name: str) -> ProjectContext:
        primary = resolution.primary_project
        assert primary is not None
        return ProjectContext(
            repository="massive",
            project_accession=primary.project_accession,
            native_accession=primary.native_accession,
            px_accession=primary.px_accession,
            file_name=file_name,
            project_files=[{"file_name": "sample.mzML"}],
        )

    def resolve_file_asset(self, task: InputTask, context: ProjectContext, work_dir: str | Path) -> FileAsset:
        return FileAsset(
            repository="massive",
            original_file_name=task.file_name,
            resolved_asset_type="mzml",
            project_accession=context.project_accession,
            native_project_accession=context.native_accession,
            matched_project_file="raw/sample.mzML",
            download_url="https://example.test/sample.mzML",
            transfer_method="https",
            expected_size_bytes=1234,
            asset_confidence=0.9,
            match_type="fake",
        )

    def download_file(self, asset: FileAsset, target_path: Path, report=None) -> Path:  # pragma: no cover
        raise AssertionError("repository smoke must not download")


class FakeIproxMissingIndexAdapter:
    name = "iprox"

    def can_handle_accession(self, value: str) -> bool:
        return "IPX" in value

    def resolve_project(self, raw_input: str) -> ProjectResolution:
        return ProjectResolution.empty().model_copy(
            update={"resolution_reason": "iProX mapping workbook not found: /missing/iprophet.xlsx"}
        )

    def get_project(self, accession: str) -> CanonicalProject:  # pragma: no cover - not used
        raise AssertionError("missing index smoke should not fetch project")

    def list_project_files(self, project: CanonicalProject) -> list[CanonicalFile]:  # pragma: no cover
        return []

    def match_file(self, task: InputTask, files: list[CanonicalFile]) -> CanonicalFile | None:  # pragma: no cover
        return None

    def build_project_context(self, resolution: ProjectResolution, file_name: str) -> ProjectContext:  # pragma: no cover
        raise AssertionError("missing index smoke should not build context")

    def resolve_file_asset(self, task: InputTask, context: ProjectContext, work_dir: str | Path) -> FileAsset:  # pragma: no cover
        raise AssertionError("missing index smoke should not resolve asset")

    def download_file(self, asset: FileAsset, target_path: Path, report=None) -> Path:  # pragma: no cover
        raise AssertionError("repository smoke must not download")


def test_repository_smoke_resolves_context_and_asset_without_download(tmp_path: Path) -> None:
    registry = RepositoryRegistry(adapters=[FakeMassiveAdapter()])
    result = run_repository_smoke(
        repository="massive",
        input_value="MSV000000001/raw/sample.mzML",
        mode="parameters",
        output_dir=tmp_path,
        registry=registry,
    )

    assert result.status == "completed"
    assert result.repository == "massive"
    assert result.project_accession == "MSV000000001"
    assert result.native_accession == "MSV000000001"
    assert result.px_accession == "PXD999999"
    assert result.asset_type == "mzml"
    assert result.transfer_method == "https"
    assert (tmp_path / "repository_smoke_summary.json").exists()
    assert (tmp_path / "repository_context.json").exists()
    assert (tmp_path / "repository_asset.json").exists()


def test_repository_smoke_reports_iprox_index_missing(tmp_path: Path) -> None:
    registry = RepositoryRegistry(adapters=[FakeIproxMissingIndexAdapter()])
    result = run_repository_smoke(
        repository="iprox",
        input_value="IPX0015463001/OsGF14f interacting proteins.raw",
        mode="parameters",
        output_dir=tmp_path,
        registry=registry,
    )

    assert result.status == "blocked"
    assert "iprox_index_missing" in result.blockers
    assert result.next_step == "refresh_iprox_index_or_set_agent_iprox_index_xlsx"
    assert "refresh-iprox-index" in ";".join(result.warnings)
