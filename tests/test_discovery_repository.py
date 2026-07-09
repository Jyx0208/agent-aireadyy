from __future__ import annotations

import csv
import json
from pathlib import Path

from agent.discovery.manifest import write_dataset_manifest
from agent.discovery.models import DatasetManifest, DatasetRequest, DiscoveredFile
from agent.discovery.repository_discovery import _merge_auto_manifests, discover_repository_dataset
from agent.metadata.canonical import CanonicalFile, CanonicalMetadataValue, CanonicalProject


def test_massive_discovery_uses_repository_adapter_contract() -> None:
    class FakeAdapter:
        name = "massive"

        def search_projects(self, query: str, limit: int = 30):
            return [
                CanonicalProject(
                    repository="massive",
                    primary_accession="MSV000000001",
                    native_accession="MSV000000001",
                    px_accession="PXD999001",
                    title="Human phospho DDA Orbitrap dataset",
                    description="Human phosphoproteomics DDA HCD.",
                    organisms=[CanonicalMetadataValue(value="Homo sapiens", source="fake")],
                    instruments=[CanonicalMetadataValue(value="Q Exactive", source="fake")],
                    experiment_types=[CanonicalMetadataValue(value="DDA", source="fake")],
                    keywords=["phospho", "DDA"],
                )
            ]

        def get_project(self, accession: str):
            return self.search_projects("")[0]

        def list_project_files(self, project):
            return [
                CanonicalFile(
                    repository="massive",
                    project_accession=project.primary_accession,
                    file_name="sample.raw",
                    logical_path="raw/sample.raw",
                    size_bytes=123,
                    download_urls=["https://massive.ucsd.edu/ProteoSAFe/DownloadResultFile?file=f.MSV000000001/raw/sample.raw"],
                    transfer_method="https",
                    file_category="raw",
                )
            ]

    class FakeRegistry:
        def get(self, repository: str):
            assert repository == "massive"
            return FakeAdapter()

    request = DatasetRequest(repository="massive", species=["human"])

    manifest = discover_repository_dataset(request, registry=FakeRegistry())

    assert manifest.request.repository == "massive"
    assert manifest.summary["repository_support_status"] == "remote_discovery_v1"
    assert manifest.summary["repository_audit"][0]["repository"] == "massive"
    assert manifest.summary["repository_audit"][0]["status"] == "completed"
    assert manifest.summary["repository_audit"][0]["selected_files"] == 1
    assert manifest.files
    assert manifest.files[0].repository == "massive"
    assert manifest.files[0].download_url.startswith("https://massive.ucsd.edu/")


def test_iprox_discovery_without_index_reports_refresh_blocker() -> None:
    class FakeAdapter:
        name = "iprox"

        def search_projects(self, query: str, limit: int = 30):
            raise FileNotFoundError("iProX public JSONL index not found: data/iprox_index/iprox_file_index.jsonl")

    class FakeRegistry:
        def get(self, repository: str):
            assert repository == "iprox"
            return FakeAdapter()

    request = DatasetRequest(repository="iprox", species=["human"])

    manifest = discover_repository_dataset(request, registry=FakeRegistry())

    assert manifest.summary["repository_support_status"] == "blocked"
    assert manifest.summary["failures"][0]["error"] == "iprox_index_missing"
    assert manifest.summary["next_step"] == "refresh_iprox_index_or_set_agent_iprox_index_xlsx"
    assert manifest.summary["repository_audit"][0]["repository"] == "iprox"
    assert manifest.summary["repository_audit"][0]["status"] == "blocked"
    assert manifest.summary["repository_audit"][0]["blocker"] == "iprox_index_missing"


def test_auto_repository_merge_preserves_repository_audit() -> None:
    request = DatasetRequest(repository="auto", species=["human"])
    pride = DatasetManifest(
        request=request.model_copy(update={"repository": "pride"}),
        files=[
            DiscoveredFile(
                repository="pride",
                project_accession="PXD000001",
                file_accession_or_path="sample_a.mzML",
                file_name="sample_a.mzML",
                file_type=".mzml",
                file_role="raw_acquisition",
                trust_score=0.8,
                file_score=80,
            )
        ],
        summary={
            "repository": "pride",
            "repository_support_status": "remote_discovery_v1",
            "candidate_projects_seen": 1,
            "eligible_projects_seen": 1,
            "selected_projects": 1,
            "selected_files": 1,
        },
    )
    iprox = DatasetManifest(
        request=request.model_copy(update={"repository": "iprox"}),
        files=[],
        summary={
            "repository": "iprox",
            "repository_support_status": "blocked",
            "candidate_projects_seen": 0,
            "eligible_projects_seen": 0,
            "selected_projects": 0,
            "selected_files": 0,
            "failures": [{"error": "iprox_index_missing"}],
            "next_step": "refresh_iprox_index_or_set_agent_iprox_index_xlsx",
        },
    )

    merged = _merge_auto_manifests(request, [pride, iprox])

    assert merged.summary["repository"] == "auto"
    audit = {row["repository"]: row for row in merged.summary["repository_audit"]}
    assert audit["pride"]["status"] == "completed"
    assert audit["iprox"]["status"] == "blocked"
    assert audit["iprox"]["next_step"] == "refresh_iprox_index_or_set_agent_iprox_index_xlsx"
    assert merged.summary["repository_counts"] == {"pride": 1}


def test_discovery_manifest_writes_repository_reproduction_fields(tmp_path: Path) -> None:
    request = DatasetRequest(repository="massive", species=["human"])
    manifest = DatasetManifest(
        request=request,
        files=[
            DiscoveredFile(
                repository="massive",
                project_accession="MSV000000001",
                native_accession="MSV000000001",
                px_accession="PXD999999",
                file_accession_or_path="raw/sample.mzML",
                file_name="sample.mzML",
                download_url="https://example.test/sample.mzML",
                transfer_method="https",
                file_type=".mzml",
                file_role="raw_acquisition",
            )
        ],
    )

    paths = write_dataset_manifest(manifest, tmp_path)
    rows = list(csv.DictReader(Path(paths["dataset_manifest_csv"]).open(encoding="utf-8")))
    audit_payload = json.loads(Path(paths["repository_audit_json"]).read_text(encoding="utf-8"))
    audit_rows = list(csv.DictReader(Path(paths["repository_audit_csv"]).open(encoding="utf-8")))
    audit_md = Path(paths["repository_audit_md"]).read_text(encoding="utf-8")

    assert rows[0]["repository"] == "massive"
    assert rows[0]["native_accession"] == "MSV000000001"
    assert rows[0]["px_accession"] == "PXD999999"
    assert rows[0]["file_accession_or_path"] == "raw/sample.mzML"
    assert rows[0]["transfer_method"] == "https"
    assert audit_payload["rows"][0]["repository"] == "massive"
    assert audit_payload["rows"][0]["status"] == "completed"
    assert audit_payload["rows"][0]["selected_files"] == 1
    assert audit_rows[0]["repository"] == "massive"
    assert "send_selected_to_batch_or_ai_ready_build" in audit_md
