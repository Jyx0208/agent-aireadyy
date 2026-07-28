from __future__ import annotations

import json
from pathlib import Path

from agent.control_plane.discovery import DiscoveryToolService
from agent.control_plane.models import AgentRunRecord
from agent.discovery.models import (
    DatasetManifest,
    DatasetRequest,
    DiscoveredFile,
    DiscoveredProject,
)
from agent.discovery.project_judgment import ProjectJudgmentInput


class _BatchStore:
    def __init__(self, run: AgentRunRecord) -> None:
        self.run = run
        self.events: list[tuple[str, dict]] = []

    def append_event(self, _run_id: str, event_type: str, payload: dict) -> None:
        self.events.append((event_type, payload))

    def save_run(self, run: AgentRunRecord) -> AgentRunRecord:
        self.run = run
        return run


def _verified_manifest(count: int) -> DatasetManifest:
    request = DatasetRequest(
        query_terms=["sensory neuron"],
        harvest_all_qualified=True,
        continuous_discovery=True,
        partial_delivery_batch_size=30,
    )
    projects = []
    files = []
    for index in range(1, count + 1):
        accession = f"PXD{index:06d}"
        projects.append(
            DiscoveredProject(
                project_accession=accession,
                project_title=f"Verified project {index}",
                validity_status="valid",
                needs_review=False,
            )
        )
        files.append(
            DiscoveredFile(
                project_accession=accession,
                project_title=f"Verified project {index}",
                file_accession_or_path=f"{accession}/{accession}.raw",
                file_name=f"{accession}.raw",
                download_url=f"https://example.test/{accession}.raw",
                file_type=".raw",
                file_role="raw_acquisition",
                validity_status="valid",
                needs_review=False,
                task_readiness_status="ready",
            )
        )
    return DatasetManifest(
        request=request,
        projects=projects,
        files=files,
        summary={"selected_projects": count, "selected_files": count},
    )


def _qualified_judgments(count: int) -> dict[str, ProjectJudgmentInput]:
    return {
        f"PXD{index:06d}": ProjectJudgmentInput(
            project_accession=f"PXD{index:06d}",
            grade=2,
            status="evidence_backed",
            hard_gate="pass",
            confidence=0.9,
            decision="include",
            next_action="include_in_manifest",
            explanation="Project-level evidence and file inspection support inclusion.",
            evidence_refs=["candidate_pool_manifest_path", "inspected_candidate_accessions"],
            evidence_stage="inspection",
        )
        for index in range(1, count + 1)
    }


def _verified_file_manifest(
    *,
    project_count: int,
    files_per_project: int,
    batch_size: int,
) -> DatasetManifest:
    manifest = _verified_manifest(project_count)
    files = []
    for project in manifest.projects:
        for index in range(1, files_per_project + 1):
            files.append(
                DiscoveredFile(
                    project_accession=project.project_accession,
                    project_title=project.project_title,
                    file_accession_or_path=f"{project.project_accession}/file-{index}.raw",
                    file_name=f"{project.project_accession}_{index}.raw",
                    download_url=(
                        f"https://example.test/{project.project_accession}_{index}.raw"
                    ),
                    file_type=".raw",
                    file_role="raw_acquisition",
                    validity_status="valid",
                    validity_reasons=["usable_inherited"],
                    needs_review=False,
                    task_readiness_status="ready",
                )
            )
    return manifest.model_copy(
        update={
            "request": manifest.request.model_copy(
                update={"partial_delivery_batch_size": batch_size}
            ),
            "files": files,
            "summary": {
                "selected_projects": project_count,
                "selected_files": len(files),
            },
        }
    )


def test_partial_delivery_batches_are_counted_by_usable_files(
    tmp_path: Path,
) -> None:
    manifest = _verified_file_manifest(
        project_count=2,
        files_per_project=2,
        batch_size=3,
    )
    run = AgentRunRecord(
        run_id="run_file_batches",
        workflow="discovery",
        project_judgments=_qualified_judgments(2),
    )
    store = _BatchStore(run)
    service = DiscoveryToolService(
        run_id=run.run_id,
        request=manifest.request,
        output_dir=tmp_path / "run",
        store=store,  # type: ignore[arg-type]
    )

    service._maybe_emit_partial_l1_delivery(run=store.run, manifest=manifest)
    service._maybe_emit_partial_l1_delivery(run=store.run, manifest=manifest)

    assert len(store.run.published_verified_project_batches) == 1
    batch = store.run.published_verified_project_batches[0]
    assert batch.batch_size == 3
    assert batch.file_count == 3
    assert batch.project_count == 2
    assert batch.cumulative_verified_file_count == 3
    assert len(batch.file_identifiers) == 3
    assert batch.delivery_unit == "file"


def test_terminal_delivery_publishes_the_final_short_file_batch(
    tmp_path: Path,
) -> None:
    manifest = _verified_file_manifest(
        project_count=2,
        files_per_project=2,
        batch_size=3,
    )
    run = AgentRunRecord(
        run_id="run_terminal_file_batch",
        workflow="discovery",
        project_judgments=_qualified_judgments(2),
    )
    store = _BatchStore(run)
    service = DiscoveryToolService(
        run_id=run.run_id,
        request=manifest.request,
        output_dir=tmp_path / "run",
        store=store,  # type: ignore[arg-type]
    )

    service._maybe_emit_partial_l1_delivery(run=store.run, manifest=manifest)
    service._maybe_emit_partial_l1_delivery(
        run=store.run,
        manifest=manifest,
        terminal=True,
    )

    assert [batch.file_count for batch in store.run.published_verified_project_batches] == [3, 1]
    assert [batch.terminal for batch in store.run.published_verified_project_batches] == [False, True]
    assert store.run.published_verified_project_batches[-1].cumulative_verified_file_count == 4


def test_browse_only_files_do_not_require_downstream_task_readiness(
    tmp_path: Path,
) -> None:
    manifest = _verified_file_manifest(
        project_count=2,
        files_per_project=2,
        batch_size=3,
    )
    manifest = manifest.model_copy(
        update={
            "files": [
                file.model_copy(update={"task_readiness_status": None})
                for file in manifest.files
            ]
        }
    )
    run = AgentRunRecord(
        run_id="run_browse_only_file_batch",
        workflow="discovery",
        project_judgments=_qualified_judgments(2),
    )
    store = _BatchStore(run)
    service = DiscoveryToolService(
        run_id=run.run_id,
        request=manifest.request,
        output_dir=tmp_path / "run",
        store=store,  # type: ignore[arg-type]
    )

    service._maybe_emit_partial_l1_delivery(run=store.run, manifest=manifest)

    assert len(store.run.published_verified_project_batches) == 1
    assert store.run.published_verified_project_batches[0].file_count == 3


def test_verified_batches_are_nonterminal_incremental_and_idempotent(
    tmp_path: Path,
) -> None:
    run = AgentRunRecord(
        run_id="run_batches",
        workflow="discovery",
        project_judgments=_qualified_judgments(65),
    )
    store = _BatchStore(run)
    request = _verified_manifest(65).request
    service = DiscoveryToolService(
        run_id=run.run_id,
        request=request,
        output_dir=tmp_path / "run",
        store=store,  # type: ignore[arg-type]
    )

    service._maybe_emit_partial_l1_delivery(
        run=store.run,
        manifest=_verified_manifest(65),
    )
    service._maybe_emit_partial_l1_delivery(
        run=store.run,
        manifest=_verified_manifest(65),
    )

    published = [
        payload
        for event_type, payload in store.events
        if event_type == "verified_project_batch_published"
    ]
    assert [item["batch_index"] for item in published] == [1, 2]
    assert all(item["project_count"] == 30 for item in published)
    assert all(item["terminal"] is False for item in published)
    assert len(store.run.verified_project_accessions) == 65
    assert len(store.run.published_verified_project_batches) == 2

    for batch_index in (1, 2):
        path = (
            tmp_path
            / "run"
            / "verified_batches"
            / f"batch_{batch_index:03d}"
            / "dataset_manifest.json"
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert len(payload["projects"]) == 30
        assert len(payload["files"]) == 30
        assert payload["summary"]["artifact_type"] == "verified_file_batch"
        assert payload["summary"]["delivery_unit"] == "file"
        assert payload["summary"]["terminal"] is False


def test_verified_batches_freeze_published_membership_when_earlier_accession_arrives(
    tmp_path: Path,
) -> None:
    initial_manifest = _verified_manifest(30)
    initial_judgments = _qualified_judgments(30)
    run = AgentRunRecord(
        run_id="run_stable_batches",
        workflow="discovery",
        project_judgments=initial_judgments,
    )
    store = _BatchStore(run)
    service = DiscoveryToolService(
        run_id=run.run_id,
        request=initial_manifest.request,
        output_dir=tmp_path / "run",
        store=store,  # type: ignore[arg-type]
    )
    service._maybe_emit_partial_l1_delivery(run=store.run, manifest=initial_manifest)
    first_batch = set(store.run.published_verified_project_batches[0].project_accessions)

    expanded = _verified_manifest(59)
    earlier_accession = "PXD000000"
    expanded = expanded.model_copy(
        update={
            "projects": [
                DiscoveredProject(
                    project_accession=earlier_accession,
                    project_title="Earlier accession",
                    validity_status="valid",
                    needs_review=False,
                ),
                *expanded.projects,
            ],
            "files": [
                    DiscoveredFile(
                        project_accession=earlier_accession,
                        project_title="Earlier accession",
                        file_accession_or_path=f"{earlier_accession}/{earlier_accession}.raw",
                        file_name=f"{earlier_accession}.raw",
                    download_url=f"https://example.test/{earlier_accession}.raw",
                    file_type=".raw",
                    file_role="raw_acquisition",
                    validity_status="valid",
                    needs_review=False,
                    task_readiness_status="ready",
                ),
                *expanded.files,
            ],
        }
    )
    store.run = store.run.model_copy(
        update={
            "project_judgments": {
                **_qualified_judgments(59),
                earlier_accession: ProjectJudgmentInput(
                    project_accession=earlier_accession,
                    grade=2,
                    status="evidence_backed",
                    hard_gate="pass",
                    confidence=0.9,
                    decision="include",
                    next_action="include_in_manifest",
                    explanation="Inspection evidence supports inclusion.",
                    evidence_refs=["candidate_pool_manifest_path"],
                    evidence_stage="inspection",
                ),
            }
        }
    )
    service._maybe_emit_partial_l1_delivery(run=store.run, manifest=expanded)

    assert len(store.run.published_verified_project_batches) == 2
    second_batch = set(store.run.published_verified_project_batches[1].project_accessions)
    assert earlier_accession in second_batch
    assert first_batch.isdisjoint(second_batch)
