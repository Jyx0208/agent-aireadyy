from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.control_plane.discovery import DiscoveryToolService
from agent.control_plane.models import AgentRunRecord
from agent.control_plane.store import AgentRunStore
from agent.discovery.models import DatasetManifest, DatasetRequest, DiscoveredFile, DiscoveredProject
from pydantic import ValidationError

from agent.discovery.project_judgment import ProjectJudgmentInput


def _service(
    tmp_path: Path,
    *,
    run_id: str,
    max_projects: int = 2,
    inspected: list[str] | None = None,
) -> tuple[DiscoveryToolService, AgentRunStore, DatasetRequest]:
    request = DatasetRequest(repository="pride", max_projects=max_projects, max_files=10_000)
    store = AgentRunStore(tmp_path / f"{run_id}.sqlite")
    store.save_run(
        AgentRunRecord(
            run_id=run_id,
            workflow="discovery",
            status="running",
            request=request.model_dump(mode="json"),
            inspected_candidate_accessions=list(inspected or []),
        )
    )
    return (
        DiscoveryToolService(
            run_id=run_id,
            request=request,
            output_dir=tmp_path / run_id,
            store=store,
        ),
        store,
        request,
    )


def _judgment(
    accession: str,
    *,
    grade: int | None,
    status: str,
    hard_gate: str,
    decision: str,
    next_action: str,
    evidence_stage: str,
    target_file_count: int = 1,
) -> ProjectJudgmentInput:
    return ProjectJudgmentInput(
        project_accession=accession,
        grade=grade,
        status=status,
        hard_gate=hard_gate,
        confidence=0.45 if status != "evidence_backed" else 0.9,
        decision=decision,
        missing_information=["sample-level evidence"] if decision == "investigate" else [],
        next_action=next_action,
        explanation="The current judgment follows the available project evidence.",
        target_file_count=target_file_count,
        evidence_stage=evidence_stage,
    )


def test_decision_requires_a_matching_next_action() -> None:
    with pytest.raises(ValidationError, match="include requires next_action"):
        ProjectJudgmentInput(
            project_accession="PXD_MISMATCH",
            grade=3,
            status="evidence_backed",
            hard_gate="pass",
            confidence=0.9,
            decision="include",
            next_action="inspect_project_evidence",
            explanation="Include must point at the manifest admission action.",
            evidence_stage="inspection",
        )
    with pytest.raises(ValidationError, match="exclude requires next_action"):
        ProjectJudgmentInput(
            project_accession="PXD_MISMATCH",
            grade=0,
            status="rejected",
            hard_gate="fail",
            confidence=0.9,
            decision="exclude",
            next_action="include_in_manifest",
            explanation="Exclude must point at project exclusion.",
            evidence_stage="inspection",
        )


def test_inspection_stage_judgments_require_prior_inspection(tmp_path: Path) -> None:
    service, _store, _request = _service(tmp_path, run_id="needs_inspection")

    blocked = service.record_project_judgments(
        [
            _judgment(
                "PXD_UNSEEN",
                grade=None,
                status="needs_investigation",
                hard_gate="unknown",
                decision="investigate",
                next_action="investigate_missing_evidence",
                evidence_stage="inspection",
            )
        ]
    )

    assert blocked["status"] == "blocked"
    assert blocked["blockers"] == ["project_not_inspected:PXD_UNSEEN"]


def test_search_stage_low_evidence_remains_provisional_and_requests_investigation(
    tmp_path: Path,
) -> None:
    service, _store, _request = _service(tmp_path, run_id="low_evidence")

    result = service.record_project_judgments(
        [
            _judgment(
                "PXD_LOW",
                grade=None,
                status="provisional",
                hard_gate="unknown",
                decision="investigate",
                next_action="inspect_project_evidence",
                evidence_stage="search",
            )
        ]
    )
    state = service.get_discovery_state()

    assert result["status"] == "completed"
    assert result["project_accessions"] == ["PXD_LOW"]
    assert result["qualified_project_count"] == 0
    assert result["quality_target_reached"] is False
    assert state["qualified_project_count"] == 0
    assert state["quality_target_reached"] is False


def test_inspection_evidence_replaces_provisional_judgment_with_evidence_backed_result(
    tmp_path: Path,
) -> None:
    service, store, _request = _service(
        tmp_path,
        run_id="evidence_update",
        max_projects=1,
        inspected=["PXD_UPDATE"],
    )
    service.record_project_judgments(
        [
            _judgment(
                "PXD_UPDATE",
                grade=2,
                status="provisional",
                hard_gate="unknown",
                decision="investigate",
                next_action="investigate_missing_evidence",
                evidence_stage="search",
            )
        ]
    )

    updated = service.record_project_judgments(
        [
            _judgment(
                "PXD_UPDATE",
                grade=2,
                status="evidence_backed",
                hard_gate="pass",
                decision="include",
                next_action="include_in_manifest",
                evidence_stage="inspection",
            )
        ]
    )

    stored = store.load_run("evidence_update")
    assert stored is not None
    assert len(stored.project_judgments) == 1
    assert stored.project_judgments["PXD_UPDATE"].status == "evidence_backed"
    assert updated["updated_count"] == 1
    assert updated["qualified_project_count"] == 1
    assert updated["quality_target_reached"] is True


def test_manifest_selection_requires_pass_grade_two_and_evidence_backed_judgment(
    tmp_path: Path,
) -> None:
    accessions = ["PXD_OK", "PXD_PROVISIONAL", "PXD_HARD_FAIL", "PXD_GRADE_ONE"]
    service, store, request = _service(
        tmp_path,
        run_id="selection_gate",
        max_projects=4,
        inspected=accessions,
    )
    manifest = DatasetManifest(
        run_id="selection_gate",
        request=request,
        projects=[DiscoveredProject(project_accession=accession) for accession in accessions],
        files=[
            DiscoveredFile(
                project_accession=accession,
                file_name=f"{accession}.raw",
                file_type=".raw",
                validity_status="valid",
                evidence_level="file",
            )
            for accession in accessions
        ],
        summary={"selected_projects": 4, "selected_files": 4},
    )
    manifest_path = tmp_path / "selection_gate" / "candidate_pool.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    run = store.load_run("selection_gate")
    assert run is not None
    store.save_run(
        run.model_copy(
            update={
                "candidate_pool_manifest_path": str(manifest_path),
                "current_manifest_path": str(manifest_path),
            }
        )
    )
    service.record_project_judgments(
        [
            _judgment(
                "PXD_OK",
                grade=2,
                status="evidence_backed",
                hard_gate="pass",
                decision="include",
                next_action="include_in_manifest",
                evidence_stage="inspection",
            ),
            _judgment(
                "PXD_PROVISIONAL",
                grade=3,
                status="needs_investigation",
                hard_gate="unknown",
                decision="investigate",
                next_action="investigate_missing_evidence",
                evidence_stage="inspection",
            ),
            _judgment(
                "PXD_HARD_FAIL",
                grade=3,
                status="rejected",
                hard_gate="fail",
                decision="exclude",
                next_action="exclude_project",
                evidence_stage="inspection",
            ),
            _judgment(
                "PXD_GRADE_ONE",
                grade=1,
                status="evidence_backed",
                hard_gate="pass",
                decision="exclude",
                next_action="exclude_project",
                evidence_stage="inspection",
            ),
        ]
    )

    rejected = service.select_discovery_manifest(
        0,
        "Only evidence-backed relevant projects may enter the final manifest.",
        accessions,
    )
    selected = service.select_discovery_manifest(
        0,
        "Select every project that satisfies the evidence-backed admission gate.",
        [],
    )

    assert rejected["status"] == "blocked"
    assert len(rejected["blockers"]) == 1
    prefix, raw_accessions = rejected["blockers"][0].split(":", 1)
    assert prefix == "project_judgment_not_eligible"
    assert set(raw_accessions.split(",")) == {
        "PXD_GRADE_ONE",
        "PXD_HARD_FAIL",
        "PXD_PROVISIONAL",
    }
    assert selected["status"] == "completed"
    assert selected["selected_project_accessions"] == ["PXD_OK"]
    selected_manifest = json.loads(Path(selected["manifest_path"]).read_text(encoding="utf-8"))
    assert [project["project_accession"] for project in selected_manifest["projects"]] == ["PXD_OK"]


def test_quality_target_counts_qualified_projects_not_files_within_one_project(
    tmp_path: Path,
) -> None:
    service, _store, _request = _service(
        tmp_path,
        run_id="project_count_target",
        max_projects=2,
        inspected=["PXD_MANY_FILES", "PXD_SECOND"],
    )

    first = service.record_project_judgments(
        [
            _judgment(
                "PXD_MANY_FILES",
                grade=3,
                status="evidence_backed",
                hard_gate="pass",
                decision="include",
                next_action="include_in_manifest",
                evidence_stage="inspection",
                target_file_count=2_000,
            )
        ]
    )
    second = service.record_project_judgments(
        [
            _judgment(
                "PXD_SECOND",
                grade=2,
                status="evidence_backed",
                hard_gate="pass",
                decision="include",
                next_action="include_in_manifest",
                evidence_stage="inspection",
                target_file_count=1,
            )
        ]
    )

    assert first["qualified_project_count"] == 1
    assert first["target_project_count"] == 2
    assert first["quality_target_reached"] is False
    assert second["qualified_project_count"] == 2
    assert second["target_project_count"] == 2
    assert second["quality_target_reached"] is True


def test_selection_continues_until_target_or_repeated_no_qualified_gain(
    tmp_path: Path,
) -> None:
    service, store, request = _service(
        tmp_path,
        run_id="qualified_stop",
        max_projects=2,
        inspected=["PXD_ONE"],
    )
    manifest = DatasetManifest(
        run_id="qualified_stop",
        request=request,
        projects=[DiscoveredProject(project_accession="PXD_ONE")],
        files=[
            DiscoveredFile(
                project_accession="PXD_ONE",
                file_name="PXD_ONE.raw",
                file_type=".raw",
                validity_status="valid",
                evidence_level="file",
            )
        ],
        summary={"selected_projects": 1, "selected_files": 1},
    )
    manifest_path = tmp_path / "qualified_stop" / "candidate_pool.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    run = store.load_run("qualified_stop")
    assert run is not None
    store.save_run(
        run.model_copy(
            update={
                "candidate_pool_manifest_path": str(manifest_path),
                "current_manifest_path": str(manifest_path),
                "discovery_round_count": 1,
            }
        )
    )
    service.search_environment = object()
    service.record_project_judgments(
        [
            _judgment(
                "PXD_ONE",
                grade=3,
                status="evidence_backed",
                hard_gate="pass",
                decision="include",
                next_action="include_in_manifest",
                evidence_stage="inspection",
            )
        ]
    )

    continue_searching = service.select_discovery_manifest(
        0,
        "One qualified project is not yet enough for the requested target.",
        ["PXD_ONE"],
    )
    run = store.load_run("qualified_stop")
    assert run is not None
    store.save_run(run.model_copy(update={"qualified_no_gain_count": 2}))
    stopped = service.select_discovery_manifest(
        0,
        "Repeated investigation added no qualified projects; retain the best evidence-backed set.",
        ["PXD_ONE"],
    )

    assert continue_searching["blockers"] == [
        "qualified_project_target_requires_more_search"
    ]
    assert stopped["status"] == "completed"


def test_automatic_agent_fallback_cannot_restore_unqualified_projects(
    tmp_path: Path,
) -> None:
    accessions = ["PXD_KEEP", "PXD_DROP"]
    service, store, request = _service(
        tmp_path,
        run_id="auto_gate",
        max_projects=2,
        inspected=accessions,
    )
    manifest = DatasetManifest(
        run_id="auto_gate",
        request=request,
        projects=[DiscoveredProject(project_accession=item) for item in accessions],
        files=[
            DiscoveredFile(
                project_accession=item,
                file_name=f"{item}.raw",
                file_type=".raw",
                validity_status="valid",
                evidence_level="file",
            )
            for item in accessions
        ],
        summary={"selected_projects": 2, "selected_files": 2},
    )
    manifest_path = tmp_path / "auto_gate" / "candidate_pool.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    run = store.load_run("auto_gate")
    assert run is not None
    store.save_run(
        run.model_copy(
            update={
                "candidate_pool_manifest_path": str(manifest_path),
                "current_manifest_path": str(manifest_path),
            }
        )
    )
    service.record_project_judgments(
        [
            _judgment(
                "PXD_KEEP",
                grade=3,
                status="evidence_backed",
                hard_gate="pass",
                decision="include",
                next_action="include_in_manifest",
                evidence_stage="inspection",
            ),
            _judgment(
                "PXD_DROP",
                grade=1,
                status="evidence_backed",
                hard_gate="pass",
                decision="exclude",
                next_action="exclude_project",
                evidence_stage="inspection",
            ),
        ]
    )

    completed = service.auto_select_best_manifest()
    selected = DatasetManifest.model_validate_json(
        Path(completed.current_manifest_path or "").read_text(encoding="utf-8")
    )

    assert [project.project_accession for project in selected.projects] == ["PXD_KEEP"]
