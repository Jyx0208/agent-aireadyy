from __future__ import annotations

from pathlib import Path

from agent.control_plane.discovery import DiscoveryToolService
from agent.control_plane.models import AgentRunRecord
from agent.control_plane.store import AgentRunStore
from agent.discovery.models import DatasetManifest, DatasetRequest, DiscoveredFile, DiscoveredProject
from agent.discovery.project_judgment import ProjectJudgmentInput


def _write_pool(path: Path, accessions: list[str]) -> Path:
    request = DatasetRequest(
        repository="pride",
        goal="general",
        max_projects=2000,
        max_files=100000,
        max_files_per_project=500,
        quantity_scope="portfolio",
        portfolio_size_preference="maximize_qualified_projects",
        harvest_all_qualified=True,
        species=["human"],
        species_policy="include_only",
        hard_constraint_fields=["repository", "species", "species_policy"],
    )
    projects = [
        DiscoveredProject(
            project_accession=acc,
            project_score=80,
            confidence=0.9,
            species=["human"],
            canonical_species=["human"],
        )
        for acc in accessions
    ]
    files = [
        DiscoveredFile(
            project_accession=acc,
            file_name=f"{acc}.raw",
            file_type=".raw",
            validity_status="valid",
            evidence_level="file",
            species=["human"],
            trust_score=0.8,
            file_score=50,
        )
        for acc in accessions
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = DatasetManifest(
        run_id="maximize_backfill",
        request=request,
        projects=projects,
        files=files,
        summary={"selected_projects": len(projects), "selected_files": len(files)},
    )
    path.write_text(manifest.model_dump_json(), encoding="utf-8")
    return path


def test_auto_select_never_promotes_unjudged_projects_from_technical_validity(tmp_path: Path) -> None:
    accessions = [f"PXD{i:06d}" for i in range(1, 51)]
    pool_path = _write_pool(tmp_path / "pool" / "dataset_manifest.json", accessions)
    request = DatasetRequest.model_validate_json(pool_path.read_text(encoding="utf-8")).request if False else DatasetRequest(
        repository="pride",
        goal="general",
        max_projects=2000,
        max_files=100000,
        max_files_per_project=500,
        quantity_scope="portfolio",
        portfolio_size_preference="maximize_qualified_projects",
        harvest_all_qualified=True,
        species=["human"],
        species_policy="include_only",
        hard_constraint_fields=["repository", "species", "species_policy"],
    )
    # Rebuild request from our explicit object (manifest request is embedded above).
    request = DatasetRequest(
        repository="pride",
        goal="general",
        max_projects=2000,
        max_files=100000,
        max_files_per_project=500,
        quantity_scope="portfolio",
        portfolio_size_preference="maximize_qualified_projects",
        harvest_all_qualified=True,
        species=["human"],
        species_policy="include_only",
        hard_constraint_fields=["repository", "species", "species_policy"],
    )
    store = AgentRunStore(tmp_path / "state.sqlite")
    # Agent judged only first 25 of 50 inspected projects.
    judged = {
        acc: ProjectJudgmentInput(
            project_accession=acc,
            grade=3,
            status="evidence_backed",
            hard_gate="pass",
            confidence=0.9,
            decision="include",
            next_action="include_in_manifest",
            explanation="Agent scored this inspected project.",
            evidence_refs=["project_description_excerpt"],
            target_file_count=1,
            evidence_stage="inspection",
        )
        for acc in accessions[:25]
    }
    store.save_run(
        AgentRunRecord(
            run_id="maximize_backfill",
            workflow="discovery",
            status="running",
            request=request.model_dump(mode="json"),
            candidate_pool_manifest_path=str(pool_path),
            current_manifest_path=str(pool_path),
            inspected_candidate_accessions=accessions,
            project_judgments=judged,
            qualified_project_count=25,
        )
    )
    service = DiscoveryToolService(
        run_id="maximize_backfill",
        request=request,
        output_dir=tmp_path / "out",
        store=store,
    )

    completed = service.auto_select_best_manifest()

    assert len(completed.project_judgments) == 25
    assert completed.selected_round_index is None
    assert "discovery_quality_audit_requires_repair" in completed.blockers
    assert completed.stop_reason == "selection_quality_gate_not_completed"
    assert not any(
        event.event_type == "manifest_selected"
        for event in store.list_events("maximize_backfill")
    )
    assert all(
        judgment.explanation == "Agent scored this inspected project."
        for judgment in completed.project_judgments.values()
    )
