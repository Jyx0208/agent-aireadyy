from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent.control_plane.discovery import DiscoveryToolService
from agent.control_plane.models import (
    AgentBudget,
    AgentRunRecord,
    DynamicBudgetLimits,
    DynamicBudgetUsage,
)
from agent.control_plane.store import AgentRunStore
from agent.control_plane.openai_agents import _selected_manifest_stop_reason
from agent.discovery.constraints import (
    ConstraintAssessment,
    ScientificConstraint,
    evaluate_constraint_value,
)
from agent.discovery.models import (
    DatasetManifest,
    DatasetRequest,
    DiscoveredFile,
    DiscoveredProject,
)
from agent.discovery.project_judgment import ProjectJudgmentInput


def _service(
    tmp_path: Path,
    *,
    project_needs_review: bool = False,
    project_validity_status: str = "weak_keep",
    file_needs_review: bool = False,
    scientific_constraints: list[ScientificConstraint] | None = None,
    max_projects: int = 1,
    request_updates: dict[str, Any] | None = None,
    project_updates: dict[str, Any] | None = None,
    file_updates: dict[str, Any] | None = None,
) -> tuple[DiscoveryToolService, AgentRunStore, Path]:
    request = DatasetRequest.model_validate(
        {
            "repository": "pride",
            "max_projects": max_projects,
            "max_files": 100,
            "scientific_constraints": scientific_constraints or [],
            **(request_updates or {}),
        }
    )
    store = AgentRunStore(tmp_path / "state.sqlite")
    manifest = DatasetManifest(
        run_id="audit_run",
        request=request,
        projects=[
            DiscoveredProject.model_validate(
                {
                    "project_accession": "PXD_AUDIT",
                    "project_title": "Auditable project",
                    "validity_status": project_validity_status,
                    "needs_review": project_needs_review,
                    **(project_updates or {}),
                }
            )
        ],
        files=[
            DiscoveredFile.model_validate(
                {
                    "project_accession": "PXD_AUDIT",
                    "file_name": "audit.raw",
                    "file_accession_or_path": "audit.raw",
                    "download_url": "https://ftp.pride.ebi.ac.uk/PXD_AUDIT/audit.raw",
                    "transfer_method": "https",
                    "file_type": ".raw",
                    "file_role": "raw_acquisition",
                    "validity_status": "weak_keep" if file_needs_review else "valid",
                    "evidence_level": "project" if file_needs_review else "file",
                    "expected_size_bytes": 1024,
                    "needs_review": file_needs_review,
                    **(file_updates or {}),
                }
            )
        ],
        summary={"selected_projects": 1, "selected_files": 1},
    )
    manifest_path = tmp_path / "candidate_pool" / "dataset_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    store.save_run(
        AgentRunRecord(
            run_id="audit_run",
            workflow="discovery",
            status="running",
            request=request.model_dump(mode="json"),
            budget=AgentBudget(max_turns=20, max_tool_calls=50, max_discovery_rounds=4),
            discovery_round_count=1,
            candidate_inspection_count=1,
            inspected_candidate_accessions=["PXD_AUDIT"],
            latest_high_relevance_candidate_count=1,
            candidate_pool_manifest_path=str(manifest_path),
            current_manifest_path=str(manifest_path),
        )
    )
    return (
        DiscoveryToolService(
            run_id="audit_run",
            request=request,
            output_dir=tmp_path / "output",
            store=store,
        ),
        store,
        manifest_path,
    )


def _include_judgment(
    *,
    constraint_assessments: list[ConstraintAssessment] | None = None,
) -> ProjectJudgmentInput:
    return ProjectJudgmentInput(
        project_accession="PXD_AUDIT",
        grade=2,
        status="evidence_backed",
        hard_gate="pass",
        confidence=0.85,
        decision="include",
        next_action="include_in_manifest",
        explanation="A directly inspected file supports inclusion.",
        evidence_refs=["selected_file_examples", "validity_status_counts"],
        constraint_assessments=constraint_assessments or [],
        limitations=[],
        target_file_count=1,
        evidence_stage="inspection",
    )


def _search_stage_judgment() -> ProjectJudgmentInput:
    return ProjectJudgmentInput(
        project_accession="PXD_AUDIT",
        grade=1,
        status="provisional",
        hard_gate="unknown",
        confidence=0.5,
        decision="investigate",
        missing_information=["Inspect project files."],
        next_action="inspect_project_evidence",
        explanation="Search metadata is provisional and cannot support delivery.",
        evidence_refs=["project_title"],
        limitations=["File evidence has not been scored."],
        target_file_count=0,
        evidence_stage="search",
    )


@pytest.mark.parametrize(
    "operator",
    ["not_contains", "not_matches", "exclude_if_matches"],
)
@pytest.mark.parametrize(
    "observed_value",
    [None, "", "   ", "unknown", [], {}, ["MCF7", ""]],
)
def test_evidence_required_exclusions_do_not_pass_unknown_observations(
    operator: str,
    observed_value: Any,
) -> None:
    constraint = ScientificConstraint(
        id="exclude_hela",
        label="Exclude HeLa",
        dimension="cell_line",
        operator=operator,
        value="HeLa",
        strength="hard",
        evidence_required=True,
    )

    assert evaluate_constraint_value(constraint, observed_value) is not True


@pytest.mark.parametrize("operator", ["exists", "nonempty", "present"])
@pytest.mark.parametrize(
    "observed_value",
    [
        "unknown",
        "not reported",
        "not_specified",
        ["N/A"],
        {"value": "unavailable"},
    ],
)
def test_presence_operators_reject_explicit_unknown_sentinels(
    operator: str,
    observed_value: Any,
) -> None:
    constraint = ScientificConstraint(
        id="reported_sex",
        label="Sex is reported",
        dimension="sex",
        operator=operator,
        strength="hard",
    )

    assert evaluate_constraint_value(constraint, observed_value) is False


@pytest.mark.parametrize(
    "operator",
    ["not_contains", "not_matches", "exclude_if_matches"],
)
def test_canonical_exclusion_operators_share_fail_closed_semantics(
    operator: str,
) -> None:
    constraint = ScientificConstraint(
        id="exclude_hela",
        label="Exclude HeLa",
        dimension="cell_line",
        operator=operator,
        value="HeLa",
        strength="hard",
    )

    assert evaluate_constraint_value(constraint, "HeLa immunopeptidomics") is False
    assert evaluate_constraint_value(constraint, "MCF7 breast cancer cells") is True


def test_exclude_if_matches_accepts_a_user_facing_exclusion_directive() -> None:
    constraint = ScientificConstraint(
        id="exclude_hela",
        label="Exclude HeLa",
        dimension="cell_line",
        operator="exclude_if_matches",
        value="Exclude HeLa",
        strength="hard",
    )

    assert evaluate_constraint_value(constraint, "HeLa immunopeptidomics") is False


def test_quality_audit_is_ready_only_after_inspection_scoring_and_file_evidence(
    tmp_path: Path,
) -> None:
    service, store, _manifest_path = _service(tmp_path)
    recorded = service.record_project_judgments([_include_judgment()])
    assert recorded["status"] == "completed"

    report = service.audit_discovery_state()

    assert report.status == "ready"
    assert report.ready_for_selection is True
    assert report.counts["inspected_projects"] == 1
    assert report.counts["judged_projects"] == 1
    assert report.counts["delivery_eligible_projects"] == 1
    assert [action.action for action in report.repair_actions] == ["select_manifest"]
    assert any(
        event.event_type == "discovery_quality_audited"
        for event in store.list_events("audit_run")
    )


def test_quality_audit_allows_review_mixed_project_after_file_evidence_resolves_mode(
    tmp_path: Path,
) -> None:
    service, _store, _manifest_path = _service(
        tmp_path,
        project_validity_status="weak_keep",
        request_updates={
            "acquisition_mode": "dda",
            "mixed_acquisition_policy": "review_mixed",
            "hard_constraint_fields": ["repository", "acquisition_mode"],
        },
        project_updates={
            "acquisition_mode": "dda",
            "validity_reasons": ["mixed_acquisition_project"],
        },
        file_updates={
            "acquisition_mode": "dda",
            "evidence_level": "file",
            "validity_reasons": ["strong_acquisition_evidence"],
        },
    )
    assert service.record_project_judgments([_include_judgment()])["status"] == "completed"

    report = service.audit_discovery_state(meter_tool=False)

    assert report.ready_for_selection is True
    assert report.counts["delivery_eligible_projects"] == 1


def test_selected_manifest_cannot_be_invalidated_by_a_late_judgment(
    tmp_path: Path,
) -> None:
    service, store, _manifest_path = _service(tmp_path)
    assert service.record_project_judgments([_include_judgment()])["status"] == "completed"
    selected = service.select_discovery_manifest(
        0,
        "Select the inspected evidence-backed project.",
        ["PXD_AUDIT"],
    )
    assert selected["status"] == "completed"
    excluded = ProjectJudgmentInput(
        project_accession="PXD_AUDIT",
        grade=0,
        status="rejected",
        hard_gate="fail",
        confidence=0.95,
        decision="exclude",
        next_action="exclude_project",
        explanation="Later evidence would exclude the project.",
        evidence_refs=["project_title"],
        target_file_count=0,
        evidence_stage="inspection",
    )

    late_update = service.record_project_judgments([excluded])

    assert late_update["status"] == "blocked"
    assert late_update["blockers"] == ["manifest_already_selected"]

    # A corrupted/legacy store must still fail closed at the publication seam.
    run = store.load_run("audit_run")
    assert run is not None
    store.save_run(
        run.model_copy(update={"project_judgments": {"PXD_AUDIT": excluded}})
    )
    assert service.publish_latest_manifest() == {}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("file_accession_or_path", None),
        ("download_url", None),
        ("file_role", "unknown"),
    ],
)
def test_delivery_assets_are_revalidated_instead_of_trusting_status_flags(
    tmp_path: Path,
    field: str,
    value: Any,
) -> None:
    service, _store, _manifest_path = _service(
        tmp_path,
        file_updates={field: value},
    )
    assert service.record_project_judgments([_include_judgment()])["status"] == "completed"

    report = service.audit_discovery_state(meter_tool=False)

    assert report.ready_for_selection is False
    assert report.counts["delivery_eligible_projects"] == 0
    assert any(issue.code == "qualified_project_has_no_delivery_assets" for issue in report.issues)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("evidence_level", "project"),
        ("expected_size_bytes", None),
    ],
)
def test_delivery_allows_inherited_evidence_and_unknown_size(
    tmp_path: Path,
    field: str,
    value: Any,
) -> None:
    service, _store, _manifest_path = _service(
        tmp_path,
        file_updates={field: value},
    )
    assert service.record_project_judgments([_include_judgment()])["status"] == "completed"

    report = service.audit_discovery_state(meter_tool=False)

    assert report.ready_for_selection is True
    assert report.counts["delivery_eligible_projects"] == 1
    assert report.counts["usable_files"] == 1


def test_quality_audit_requests_rescoring_for_inspected_but_unjudged_projects(
    tmp_path: Path,
) -> None:
    service, _store, _manifest_path = _service(tmp_path)

    report = service.audit_discovery_state()

    assert report.status == "repair_required"
    assert report.ready_for_selection is False
    rescore = next(action for action in report.repair_actions if action.action == "rescore_projects")
    assert rescore.project_accessions == ["PXD_AUDIT"]
    assert any(issue.code == "inspected_projects_missing_judgments" for issue in report.issues)


def test_quality_audit_does_not_require_judgment_for_inspected_project_without_assessable_files(
    tmp_path: Path,
) -> None:
    service, store, _manifest_path = _service(tmp_path)
    run = store.load_run("audit_run")
    assert run is not None
    store.save_run(
        run.model_copy(
            update={
                "inspected_candidate_accessions": ["PXD_AUDIT", "PXD_EMPTY"],
            }
        )
    )

    report = service.audit_discovery_state(meter_tool=False)

    rescore = next(action for action in report.repair_actions if action.action == "rescore_projects")
    assert rescore.project_accessions == ["PXD_AUDIT"]
    assert "PXD_EMPTY" not in report.succeeded_inspection_accessions
    assert "PXD_EMPTY" in report.non_assessable_inspection_accessions
    assert report.counts["non_assessable_inspections"] == 1


def test_quality_audit_requests_inspection_rescore_when_only_search_judgment_exists(
    tmp_path: Path,
) -> None:
    service, _store, _manifest_path = _service(tmp_path)
    assert service.record_project_judgments([_search_stage_judgment()])["status"] == "completed"

    report = service.audit_discovery_state()

    assert report.status == "repair_required"
    rescore = next(
        action for action in report.repair_actions if action.action == "rescore_projects"
    )
    assert rescore.project_accessions == ["PXD_AUDIT"]
    assert any(
        issue.code == "inspected_projects_missing_judgments"
        and issue.project_accessions == ["PXD_AUDIT"]
        for issue in report.issues
    )


def test_quality_audit_rejects_delivery_when_project_or_files_still_need_review(
    tmp_path: Path,
) -> None:
    service, _store, _manifest_path = _service(
        tmp_path,
        project_needs_review=True,
        file_needs_review=True,
    )
    recorded = service.record_project_judgments([_include_judgment()])
    assert recorded["status"] == "completed"

    report = service.audit_discovery_state()

    assert report.status == "repair_required"
    assert report.counts["delivery_eligible_projects"] == 0
    assert report.counts["needs_review_files"] == 1
    assert any(issue.code == "qualified_project_still_needs_review" for issue in report.issues)

    selected = service.select_discovery_manifest(
        0,
        "Attempting to select a project whose evidence remains unresolved.",
        ["PXD_AUDIT"],
    )
    assert selected["status"] == "blocked"
    assert "discovery_quality_audit_requires_repair" in selected["blockers"]


def test_final_selection_removes_non_delivery_files_from_an_eligible_project(
    tmp_path: Path,
) -> None:
    service, store, manifest_path = _service(tmp_path)
    manifest = DatasetManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    manifest.files.append(
        DiscoveredFile(
            project_accession="PXD_AUDIT",
            file_name="review.raw",
            file_type=".raw",
            validity_status="needs_review",
            evidence_level="project",
            needs_review=True,
        )
    )
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    assert service.record_project_judgments([_include_judgment()])["status"] == "completed"

    selected = service.select_discovery_manifest(
        0,
        "Retain only files whose persisted evidence is delivery eligible.",
        ["PXD_AUDIT"],
    )

    assert selected["status"] == "completed"
    final_manifest = DatasetManifest.model_validate_json(
        Path(selected["manifest_path"]).read_text(encoding="utf-8")
    )
    assert [file.file_name for file in final_manifest.files] == ["audit.raw"]
    persisted = store.load_run("audit_run")
    assert persisted is not None
    assert persisted.latest_discovery_audit is not None
    assert persisted.latest_discovery_audit.ready_for_selection is True
    assert persisted.latest_discovery_audit.counts["final_selection"] == 1


@pytest.mark.parametrize("project_validity_status", ["needs_review", "exclude"])
def test_project_validity_status_must_be_delivery_eligible_for_selection(
    tmp_path: Path,
    project_validity_status: str,
) -> None:
    service, _store, _manifest_path = _service(
        tmp_path,
        project_validity_status=project_validity_status,
    )
    assert service.record_project_judgments([_include_judgment()])["status"] == "completed"

    report = service.audit_discovery_state()
    selected = service.select_discovery_manifest(
        0,
        "A project-level unresolved validity state cannot be delivered.",
        ["PXD_AUDIT"],
    )

    assert report.ready_for_selection is False
    assert report.counts["delivery_eligible_projects"] == 0
    assert any(issue.code == "qualified_project_still_needs_review" for issue in report.issues)
    assert selected["status"] == "blocked"


def test_hard_per_project_min_files_is_enforced_by_the_quality_gate(
    tmp_path: Path,
) -> None:
    service, _store, _manifest_path = _service(
        tmp_path,
        request_updates={
            "per_project_min_files": 2,
            "hard_constraint_fields": ["repository", "per_project_min_files"],
        },
    )
    assert service.record_project_judgments([_include_judgment()])["status"] == "completed"

    report = service.audit_discovery_state(meter_tool=False)

    assert report.ready_for_selection is False
    assert any(
        issue.code == "hard_per_project_min_files_not_met"
        and issue.project_accessions == ["PXD_AUDIT"]
        for issue in report.issues
    )


def test_post_selection_audit_rejects_per_project_minimum_lost_to_file_truncation(
    tmp_path: Path,
) -> None:
    service, store, manifest_path = _service(
        tmp_path,
        request_updates={
            "max_files": 1,
            "per_project_min_files": 2,
            "hard_constraint_fields": ["repository", "per_project_min_files"],
        },
    )
    manifest = DatasetManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    manifest.files.append(
        manifest.files[0].model_copy(
            update={
                "file_name": "audit-2.raw",
                "file_accession_or_path": "audit-2.raw",
                "download_url": "https://ftp.pride.ebi.ac.uk/PXD_AUDIT/audit-2.raw",
            }
        )
    )
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    judgment = _include_judgment().model_copy(update={"target_file_count": 2})
    assert service.record_project_judgments([judgment])["status"] == "completed"
    assert service.audit_discovery_state(meter_tool=False).ready_for_selection is True

    selected = service.select_discovery_manifest(
        0,
        "The exact final manifest must retain the hard per-project file minimum.",
        ["PXD_AUDIT"],
    )

    assert selected["status"] == "blocked"
    assert selected["blockers"] == ["final_manifest_quality_audit_requires_repair"]
    persisted = store.load_run("audit_run")
    assert persisted is not None
    assert persisted.selected_round_index is None
    assert persisted.latest_discovery_audit is not None
    assert any(
        issue.code == "hard_per_project_min_files_not_met"
        for issue in persisted.latest_discovery_audit.issues
    )


def test_post_selection_audit_rechecks_portfolio_constraints_on_selected_subset(
    tmp_path: Path,
) -> None:
    constraint = ScientificConstraint(
        id="two_project_portfolio",
        label="At least two selected projects",
        dimension="project_count",
        operator="gte",
        value=2,
        strength="hard",
        scope="portfolio",
    )
    service, store, manifest_path = _service(
        tmp_path,
        max_projects=1,
        scientific_constraints=[constraint],
    )
    manifest = DatasetManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    manifest.projects.append(
        manifest.projects[0].model_copy(
            update={
                "project_accession": "PXD_SECOND",
                "project_title": "Second auditable project",
            }
        )
    )
    manifest.files.append(
        manifest.files[0].model_copy(
            update={
                "project_accession": "PXD_SECOND",
                "file_name": "second.raw",
                "file_accession_or_path": "second.raw",
                "download_url": "https://ftp.pride.ebi.ac.uk/PXD_SECOND/second.raw",
            }
        )
    )
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    run = store.load_run("audit_run")
    assert run is not None
    store.save_run(
        run.model_copy(
            update={
                "inspected_candidate_accessions": ["PXD_AUDIT", "PXD_SECOND"],
                "latest_high_relevance_candidate_count": 2,
            }
        )
    )
    second_judgment = _include_judgment().model_copy(
        update={"project_accession": "PXD_SECOND"}
    )
    assert service.record_project_judgments(
        [_include_judgment(), second_judgment]
    )["status"] == "completed"
    assert service.audit_discovery_state(meter_tool=False).ready_for_selection is True

    selected = service.select_discovery_manifest(
        0,
        "Select one project only if the final portfolio still satisfies aggregate constraints.",
        ["PXD_AUDIT"],
    )

    assert selected["status"] == "blocked"
    assert selected["blockers"] == ["final_manifest_quality_audit_requires_repair"]
    persisted = store.load_run("audit_run")
    assert persisted is not None
    assert persisted.latest_discovery_audit is not None
    assert any(
        issue.code == "hard_portfolio_constraint_not_met"
        for issue in persisted.latest_discovery_audit.issues
    )


def test_publication_reaudits_the_exact_selected_manifest(
    tmp_path: Path,
) -> None:
    service, _store, manifest_path = _service(
        tmp_path,
        request_updates={
            "max_files": 2,
            "per_project_min_files": 2,
            "hard_constraint_fields": ["repository", "per_project_min_files"],
        },
    )
    manifest = DatasetManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    manifest.files.append(
        manifest.files[0].model_copy(
            update={
                "file_name": "audit-2.raw",
                "file_accession_or_path": "audit-2.raw",
                "download_url": "https://ftp.pride.ebi.ac.uk/PXD_AUDIT/audit-2.raw",
            }
        )
    )
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    judgment = _include_judgment().model_copy(update={"target_file_count": 2})
    assert service.record_project_judgments([judgment])["status"] == "completed"
    selected = service.select_discovery_manifest(
        0,
        "Select the two-file project after all hard constraints pass.",
        ["PXD_AUDIT"],
    )
    assert selected["status"] == "completed"

    selected_path = Path(selected["manifest_path"])
    corrupted = DatasetManifest.model_validate_json(selected_path.read_text(encoding="utf-8"))
    corrupted = corrupted.model_copy(
        update={
            "files": corrupted.files[:1],
            "summary": {
                **corrupted.summary,
                "selected_files": 1,
            },
        }
    )
    selected_path.write_text(corrupted.model_dump_json(), encoding="utf-8")

    assert service.publish_latest_manifest() == {}


def test_hard_portfolio_constraint_is_evaluated_against_manifest_diversity(
    tmp_path: Path,
) -> None:
    constraint = ScientificConstraint(
        id="instrument_diversity",
        label="At least two instrument families",
        dimension="instrument_family_count",
        operator="gte",
        value=2,
        strength="hard",
        scope="portfolio",
    )
    service, _store, _manifest_path = _service(
        tmp_path,
        scientific_constraints=[constraint],
        project_updates={"instrument_families": ["Orbitrap"]},
    )
    assert service.record_project_judgments([_include_judgment()])["status"] == "completed"

    report = service.audit_discovery_state(meter_tool=False)

    assert report.ready_for_selection is False
    assert any(
        issue.code == "hard_portfolio_constraint_not_met"
        and issue.constraint_ids == ["instrument_diversity"]
        for issue in report.issues
    )


@pytest.mark.parametrize("selection_mode", ["explicit", "auto"])
def test_file_scoped_hard_constraint_filters_only_the_violating_file(
    tmp_path: Path,
    selection_mode: str,
) -> None:
    constraint = ScientificConstraint(
        id="minimum_samples_per_file",
        label="At least 30 samples represented per file",
        dimension="sample_count",
        operator="gte",
        value=30,
        strength="hard",
        scope="file",
    )
    service, _store, manifest_path = _service(
        tmp_path,
        scientific_constraints=[constraint],
        project_updates={
            "sdrf_summary": {
                "status": "available",
                "row_count": 2,
                "per_file_sample_count": {"audit.raw": 30, "small.raw": 1},
            }
        },
    )
    manifest = DatasetManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    manifest.files.append(
        DiscoveredFile(
            project_accession="PXD_AUDIT",
            file_name="small.raw",
            file_accession_or_path="small.raw",
            download_url="https://ftp.pride.ebi.ac.uk/PXD_AUDIT/small.raw",
            transfer_method="https",
            file_type=".raw",
            file_role="raw_acquisition",
            validity_status="valid",
            evidence_level="file",
            expected_size_bytes=512,
        )
    )
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    judgment = _include_judgment(
        constraint_assessments=[
            ConstraintAssessment(
                constraint_id=constraint.id,
                status="partial",
                reason="Only the first persisted file reaches the sample threshold.",
                evidence_refs=["selected_file_examples", "sdrf"],
                observed_value={"audit.raw": 30, "small.raw": 1},
            )
        ]
    ).model_copy(update={"target_file_count": 2})

    assert service.record_project_judgments([judgment])["status"] == "completed"
    report = service.audit_discovery_state(meter_tool=False)
    if selection_mode == "explicit":
        selected = service.select_discovery_manifest(
            0,
            "Deliver only files that pass every hard file-scoped constraint.",
            ["PXD_AUDIT"],
        )
        assert selected["status"] == "completed"
        selected_path = Path(selected["manifest_path"])
    else:
        completed = service.auto_select_best_manifest()
        assert completed.selected_round_index == 0
        assert completed.current_manifest_path is not None
        selected_path = Path(completed.current_manifest_path)

    assert report.ready_for_selection is True
    delivered = DatasetManifest.model_validate_json(
        selected_path.read_text(encoding="utf-8")
    )
    assert [file.file_name for file in delivered.files] == ["audit.raw"]


def test_constraint_assessment_refs_must_be_nonempty_and_available_for_the_project(
    tmp_path: Path,
) -> None:
    constraint = ScientificConstraint(
        id="sample_context",
        label="Requested sample context",
        dimension="sample_context",
        value="auditable",
        strength="soft",
    )
    service, _store, _manifest_path = _service(
        tmp_path,
        scientific_constraints=[constraint],
    )

    fabricated = service.record_project_judgments(
        [
            _include_judgment(
                constraint_assessments=[
                    ConstraintAssessment(
                        constraint_id=constraint.id,
                        status="pass",
                        reason="Claims species evidence that the persisted project does not have.",
                        evidence_refs=["species"],
                    )
                ]
            )
        ]
    )
    empty = service.record_project_judgments(
        [
            _include_judgment(
                constraint_assessments=[
                    ConstraintAssessment(
                        constraint_id=constraint.id,
                        status="unknown",
                        reason="No evidence reference was supplied.",
                        evidence_refs=[],
                    )
                ]
            )
        ]
    )
    persisted = service.record_project_judgments(
        [
            _include_judgment(
                constraint_assessments=[
                    ConstraintAssessment(
                        constraint_id=constraint.id,
                        status="pass",
                        reason="The persisted inspected file is available for audit.",
                        evidence_refs=["selected_file_examples"],
                        observed_value="audit.raw",
                    )
                ]
            )
        ]
    )

    assert fabricated["status"] == "blocked"
    assert fabricated["blockers"] == [
        "unavailable_constraint_evidence_ref:PXD_AUDIT:sample_context:species"
    ]
    assert empty["status"] == "blocked"
    assert empty["blockers"] == [
        "constraint_evidence_refs_required:PXD_AUDIT:sample_context"
    ]
    assert persisted["status"] == "completed"


@pytest.mark.parametrize("observed_value", ["", "MCF7"])
def test_constraint_evidence_must_support_the_claimed_observed_value(
    tmp_path: Path,
    observed_value: str,
) -> None:
    constraint = ScientificConstraint(
        id="exclude_hela",
        label="Exclude HeLa",
        dimension="cell_line",
        operator="not_matches",
        value="HeLa",
        strength="hard",
        evidence_required=True,
    )
    service, _store, _manifest_path = _service(
        tmp_path,
        scientific_constraints=[constraint],
        project_updates={"project_title": "HeLa immunopeptidomics"},
    )

    result = service.record_project_judgments(
        [
            _include_judgment(
                constraint_assessments=[
                    ConstraintAssessment(
                        constraint_id=constraint.id,
                        status="pass",
                        reason="Claims an observation not substantiated by the cited title.",
                        evidence_refs=["project_title"],
                        observed_value=observed_value,
                    )
                ]
            )
        ]
    )

    assert result["status"] == "blocked"
    assert (
        "constraint_observed_value_not_supported_by_evidence:"
        "PXD_AUDIT:exclude_hela"
    ) in result["blockers"]
    repair = result["repair_context"]["PXD_AUDIT"]
    assert "project_title" in repair["available_evidence_refs"]
    assert repair["constraint_assessments"] == [
        {
            "constraint_id": "exclude_hela",
            "cited_evidence_values": {
                "project_title": "HeLa immunopeptidomics",
            },
        }
    ]


def test_weak_keep_files_are_pending_and_not_silently_delivered(
    tmp_path: Path,
) -> None:
    service, _store, _manifest_path = _service(
        tmp_path,
        file_updates={
            "validity_status": "weak_keep",
            "needs_review": False,
            "evidence_level": "file",
        },
    )
    assert service.record_project_judgments([_include_judgment()])["status"] == "completed"

    audit = service.audit_discovery_state(meter_tool=False)

    assert audit.status == "repair_required"
    assert audit.ready_for_selection is False
    assert audit.counts["strict_valid_files"] == 0
    assert audit.counts["weak_keep_files"] == 0
    assert audit.counts["pending_files"] == 1
    assert audit.counts["usable_files"] == 0
    assert any(
        issue.code == "qualified_project_has_no_delivery_assets"
        for issue in audit.issues
    )


@pytest.mark.parametrize(
    ("observed", "title"),
    [
        ("male", "Proteomics from female donors"),
        ("male donors", "Male"),
    ],
    ids=["token-collision", "unsupported-observed-expansion"],
)
def test_constraint_evidence_grounding_rejects_token_collisions_and_expansion(
    tmp_path: Path,
    observed: str,
    title: str,
) -> None:
    constraint = ScientificConstraint(
        id="donor_sex",
        label="Male donors",
        dimension="sex",
        operator="matches",
        value=observed,
        strength="hard",
    )
    service, _store, _manifest_path = _service(
        tmp_path,
        scientific_constraints=[constraint],
        project_updates={"project_title": title},
    )

    result = service.record_project_judgments(
        [
            _include_judgment(
                constraint_assessments=[
                    ConstraintAssessment(
                        constraint_id=constraint.id,
                        status="pass",
                        reason="Claims more than the cited title substantively supports.",
                        evidence_refs=["project_title"],
                        observed_value=observed,
                    )
                ]
            )
        ]
    )

    assert result["status"] == "blocked"
    assert (
        "constraint_observed_value_not_supported_by_evidence:"
        "PXD_AUDIT:donor_sex"
    ) in result["blockers"]


@pytest.mark.parametrize(
    ("operator", "expected", "observed", "title"),
    [
        ("matches", "female donors", "female donors", "Cohort of female donors"),
        ("gte", 20, 30, "Cohort of 30 donors"),
    ],
    ids=["token-bounded-phrase", "number-in-text"],
)
def test_constraint_evidence_grounding_keeps_legitimate_phrases_and_numbers(
    tmp_path: Path,
    operator: str,
    expected: Any,
    observed: Any,
    title: str,
) -> None:
    constraint = ScientificConstraint(
        id="cohort_evidence",
        label="Cohort evidence",
        dimension="cohort",
        operator=operator,
        value=expected,
        strength="hard",
    )
    service, _store, _manifest_path = _service(
        tmp_path,
        scientific_constraints=[constraint],
        project_updates={"project_title": title},
    )

    result = service.record_project_judgments(
        [
            _include_judgment(
                constraint_assessments=[
                    ConstraintAssessment(
                        constraint_id=constraint.id,
                        status="pass",
                        reason="The cited title directly reports the observation.",
                        evidence_refs=["project_title"],
                        observed_value=observed,
                    )
                ]
            )
        ]
    )

    assert result["status"] == "completed"


def test_top_level_judgment_refs_must_exist_in_the_persisted_project(
    tmp_path: Path,
) -> None:
    service, _store, _manifest_path = _service(tmp_path)
    judgment = _include_judgment().model_copy(
        update={"evidence_refs": ["species"]}
    )

    result = service.record_project_judgments([judgment])

    assert result["status"] == "blocked"
    assert result["blockers"] == [
        "unavailable_evidence_ref:PXD_AUDIT:species"
    ]


def test_empty_or_failed_sdrf_is_not_available_constraint_evidence(
    tmp_path: Path,
) -> None:
    constraint = ScientificConstraint(
        id="exclude_hela",
        label="Exclude HeLa",
        dimension="cell_line",
        operator="not_contains",
        value="HeLa",
        strength="hard",
    )
    service, _store, _manifest_path = _service(
        tmp_path,
        scientific_constraints=[constraint],
        project_updates={
            "project_title": "HeLa immunopeptidomics",
            "sdrf_summary": {
                "status": "not_found",
                "row_count": 0,
                "canonical_fields": {},
            },
        },
    )
    judgment = _include_judgment(
        constraint_assessments=[
            ConstraintAssessment(
                constraint_id=constraint.id,
                status="pass",
                reason="Claims the absent SDRF proves HeLa is excluded.",
                evidence_refs=["sdrf"],
                observed_value="HeLa",
            )
        ]
    )

    result = service.record_project_judgments([judgment])

    assert result["status"] == "blocked"
    assert set(result["blockers"]) == {
        "unavailable_constraint_evidence_ref:PXD_AUDIT:exclude_hela:sdrf",
        "hard_constraint_observed_value_conflict:PXD_AUDIT:exclude_hela",
    }


def test_hard_constraint_pass_must_match_its_observed_value(
    tmp_path: Path,
) -> None:
    constraint = ScientificConstraint(
        id="exclude_hela",
        label="Exclude HeLa",
        dimension="cell_line",
        operator="not_contains",
        value="HeLa",
        strength="hard",
    )
    service, _store, _manifest_path = _service(
        tmp_path,
        scientific_constraints=[constraint],
        project_updates={"project_title": "HeLa immunopeptidomics"},
    )
    judgment = _include_judgment(
        constraint_assessments=[
            ConstraintAssessment(
                constraint_id=constraint.id,
                status="pass",
                reason="The title identifies the observed cell line.",
                evidence_refs=["project_title"],
                observed_value="HeLa",
            )
        ]
    )

    result = service.record_project_judgments([judgment])

    assert result["status"] == "blocked"
    assert result["blockers"] == [
        "hard_constraint_observed_value_conflict:PXD_AUDIT:exclude_hela"
    ]


def test_quality_audit_revalidates_persisted_constraint_evidence_refs(
    tmp_path: Path,
) -> None:
    constraint = ScientificConstraint(
        id="sample_context",
        label="Requested sample context",
        dimension="sample_context",
        value="auditable",
        strength="soft",
    )
    service, store, _manifest_path = _service(
        tmp_path,
        scientific_constraints=[constraint],
    )
    fabricated = _include_judgment(
        constraint_assessments=[
            ConstraintAssessment(
                constraint_id=constraint.id,
                status="pass",
                reason="A legacy record cites an unavailable project field.",
                evidence_refs=["species"],
            )
        ]
    )
    run = store.load_run("audit_run")
    assert run is not None
    store.save_run(
        run.model_copy(update={"project_judgments": {"PXD_AUDIT": fabricated}})
    )

    report = service.audit_discovery_state()

    assert report.ready_for_selection is False
    assert any(
        issue.code == "constraint_assessment_evidence_invalid"
        and issue.project_accessions == ["PXD_AUDIT"]
        and issue.constraint_ids == ["sample_context"]
        for issue in report.issues
    )
    assert any(
        action.action == "rescore_projects"
        and action.project_accessions == ["PXD_AUDIT"]
        for action in report.repair_actions
    )


def test_quality_audit_reports_requested_successful_and_failed_inspections_truthfully(
    tmp_path: Path,
) -> None:
    service, store, _manifest_path = _service(tmp_path)
    store.append_event(
        "audit_run",
        "candidate_inspection_completed",
        {
            "action": {"accessions": ["PXD_AUDIT", "PXD_FAILED"]},
            "observation": {
                "project_assessments": [{"project_accession": "PXD_AUDIT"}],
                "warnings": ["inspection_failed_accessions:PXD_FAILED"],
            },
        },
    )

    report = service.audit_discovery_state(meter_tool=False)

    assert report.requested_inspection_accessions == ["PXD_AUDIT", "PXD_FAILED"]
    assert report.succeeded_inspection_accessions == ["PXD_AUDIT"]
    assert report.failed_inspection_accessions == ["PXD_FAILED"]


def test_quality_audit_removes_retry_successes_from_failed_inspections(
    tmp_path: Path,
) -> None:
    service, store, _manifest_path = _service(tmp_path)
    store.append_event(
        "audit_run",
        "candidate_inspection_completed",
        {
            "action": {"accessions": ["PXD_AUDIT"]},
            "observation": {
                "project_assessments": [],
                "warnings": ["inspection_failed_accessions:PXD_AUDIT"],
            },
        },
    )
    store.append_event(
        "audit_run",
        "candidate_inspection_completed",
        {
            "action": {"accessions": ["PXD_AUDIT"]},
            "observation": {
                "project_assessments": [{"project_accession": "PXD_AUDIT"}],
                "warnings": [],
            },
        },
    )

    report = service.audit_discovery_state(meter_tool=False)

    assert report.succeeded_inspection_accessions == ["PXD_AUDIT"]
    assert report.failed_inspection_accessions == []
    assert not any(issue.code == "candidate_inspections_failed" for issue in report.issues)


def test_quality_audit_distinguishes_preview_coverage_from_selection_backed_coverage(
    tmp_path: Path,
) -> None:
    service, store, _manifest_path = _service(tmp_path)
    assert service.record_project_judgments([_include_judgment()])["status"] == "completed"
    store.append_event(
        "audit_run",
        "candidate_search_completed",
        {
            "observation": {
                "intent_terms": ["cell model", "disease context"],
                "covered_intent_terms": ["cell model", "disease context"],
                "previews": [
                    {
                        "project_accession": "PXD_AUDIT",
                        "matched_intent_terms": ["cell model"],
                    },
                    {
                        "project_accession": "PXD_OTHER",
                        "matched_intent_terms": ["disease context"],
                    },
                ],
            }
        },
    )

    report = service.audit_discovery_state(meter_tool=False)

    assert report.ready_for_selection is True
    assert report.selection_backed_coverage == 0.5
    assert report.selection_backed_intent_terms == ["cell model"]
    assert report.uncovered_intent_terms == ["disease context"]
    assert report.unsupported_coverage_terms == ["disease context"]
    assert any(issue.code == "preview_coverage_not_backed_by_selection" for issue in report.issues)


@pytest.mark.parametrize(
    ("ceiling", "limitation"),
    [
        ("turn", "agent_turn_budget_exhausted"),
        ("tool", "tool_call_budget_exhausted"),
    ],
)
def test_quality_audit_persists_stop_when_autonomous_repair_ceiling_is_exhausted(
    tmp_path: Path,
    ceiling: str,
    limitation: str,
) -> None:
    service, store, _manifest_path = _service(tmp_path)
    run = store.load_run("audit_run")
    assert run is not None
    if ceiling == "turn":
        store.save_run(
            run.model_copy(update={"sdk_turn_count": run.budget.max_turns})
        )
    else:
        store.save_run(
            run.model_copy(update={"tool_call_count": run.budget.max_tool_calls})
        )

    report = service.audit_discovery_state(meter_tool=False)

    assert report.status == "blocked"
    assert [action.action for action in report.repair_actions] == [
        "stop_with_limitations"
    ]
    assert limitation in report.limitations
    persisted = store.load_run("audit_run")
    assert persisted is not None
    assert persisted.latest_discovery_audit == report


def test_quality_audit_stops_search_repair_at_dynamic_hard_ceilings(
    tmp_path: Path,
) -> None:
    service, store, manifest_path = _service(tmp_path)
    manifest_path.unlink()
    service.search_environment = SimpleNamespace(candidate_accessions=[])
    run = store.load_run("audit_run")
    assert run is not None
    store.save_run(
        run.model_copy(
            update={
                "dynamic_limits": DynamicBudgetLimits(
                    max_query_units=1,
                    max_repository_requests=1,
                ),
                "dynamic_usage": DynamicBudgetUsage(
                    query_units=1,
                    repository_requests=1,
                ),
            }
        )
    )

    report = service.audit_discovery_state(meter_tool=False)

    assert report.status == "blocked"
    assert [action.action for action in report.repair_actions] == [
        "stop_with_limitations"
    ]
    assert "hard_query_unit_limit" in report.limitations
    assert "hard_repository_request_limit" in report.limitations
    assert report.counts["query_units_remaining"] == 0
    assert report.counts["repository_requests_remaining"] == 0


@pytest.mark.parametrize(
    ("request_updates", "incomplete_limitation"),
    [
        (
            {
                "quantity_scope": "portfolio",
                "portfolio_size_preference": "maximize_coverage",
                "harvest_all_qualified": True,
            },
            "portfolio_maximize_incomplete",
        ),
        (
            {"quota_flexibility": "open_ended"},
            "open_ended_search_incomplete",
        ),
    ],
)
def test_open_ended_modes_preserve_hard_ceiling_and_inspection_limitations(
    tmp_path: Path,
    request_updates: dict[str, Any],
    incomplete_limitation: str,
) -> None:
    service, store, _manifest_path = _service(
        tmp_path,
        request_updates=request_updates,
    )
    service.search_environment = SimpleNamespace(
        candidate_accessions=["PXD_AUDIT", "PXD_UNINSPECTED"]
    )
    assert service.record_project_judgments([_include_judgment()])["status"] == "completed"
    run = store.load_run("audit_run")
    assert run is not None
    store.save_run(
        run.model_copy(
            update={
                "discovery_round_count": run.budget.max_discovery_rounds,
                "latest_high_relevance_candidate_count": 2,
                "search_stopped": True,
                "search_stop_reason": "hard_discovery_round_limit",
            }
        )
    )

    report = service.audit_discovery_state(meter_tool=False)

    assert report.ready_for_selection is True
    assert incomplete_limitation in report.limitations
    assert "discovery_round_budget_exhausted" in report.limitations
    assert "high_relevance_inspection_coverage_incomplete" in report.limitations
    assert any(
        issue.code == "portfolio_search_stopped_at_hard_ceiling"
        for issue in report.issues
    )
    persisted = store.load_run("audit_run")
    assert persisted is not None
    assert _selected_manifest_stop_reason(persisted) == "selected_with_limitations"
    assert (
        _selected_manifest_stop_reason(
            persisted.model_copy(update={"latest_discovery_audit": None})
        )
        == "selected_with_limitations"
    )


def test_fixed_project_target_shortfall_is_blocked_at_the_safety_ceiling(
    tmp_path: Path,
) -> None:
    service, store, _manifest_path = _service(
        tmp_path,
        max_projects=2,
        request_updates={"quota_flexibility": "fixed"},
    )
    service.search_environment = SimpleNamespace(candidate_accessions=["PXD_AUDIT"])
    assert service.record_project_judgments([_include_judgment()])["status"] == "completed"
    run = store.load_run("audit_run")
    assert run is not None
    store.save_run(
        run.model_copy(
            update={
                "discovery_round_count": run.budget.max_discovery_rounds,
                "search_stopped": True,
                "search_stop_reason": "hard_discovery_round_limit",
            }
        )
    )

    report = service.audit_discovery_state(meter_tool=False)
    selected = service.select_discovery_manifest(
        0,
        "A fixed target cannot be declared complete while one project short.",
        ["PXD_AUDIT"],
    )

    assert report.status == "blocked"
    assert report.ready_for_selection is False
    assert "discovery_round_budget_exhausted" in report.limitations
    assert "fixed_project_target_shortfall" in report.limitations
    assert any(
        issue.code == "fixed_quality_target_shortfall"
        for issue in report.issues
    )
    assert [action.action for action in report.repair_actions] == [
        "stop_with_limitations"
    ]
    assert selected["status"] == "blocked"


def test_quality_audit_uses_shared_sdk_and_provider_turn_budget(
    tmp_path: Path,
) -> None:
    service, store, _manifest_path = _service(tmp_path)
    run = store.load_run("audit_run")
    assert run is not None
    store.save_run(
        run.model_copy(
            update={
                "model_requests": run.budget.max_turns + 100,
                "sdk_turn_count": 1,
            }
        )
    )

    report = service.audit_discovery_state(meter_tool=False)
    summary = service.state_summary()

    assert report.status == "blocked"
    assert report.counts["agent_turns_used"] == 1
    assert report.counts["agent_turns_remaining"] == 0
    assert [action.action for action in report.repair_actions] == [
        "stop_with_limitations"
    ]
    assert summary["model_usage"]["sdk_turns"] == 1
    assert summary["hard_budget_remaining"]["model_turns"] == 0


@pytest.mark.parametrize("selection_mode", ["explicit", "auto"])
def test_selection_does_not_require_unfunded_search_after_turn_budget_exhaustion(
    tmp_path: Path,
    selection_mode: str,
) -> None:
    service, store, _manifest_path = _service(tmp_path, max_projects=2)
    service.search_environment = SimpleNamespace(
        candidate_accessions=["PXD_AUDIT"]
    )
    assert service.record_project_judgments([_include_judgment()])["status"] == "completed"
    run = store.load_run("audit_run")
    assert run is not None
    store.save_run(
        run.model_copy(update={"sdk_turn_count": run.budget.max_turns})
    )

    if selection_mode == "explicit":
        result = service.select_discovery_manifest(
            0,
            "Select the qualified persisted evidence because no search turn remains.",
            ["PXD_AUDIT"],
        )
        assert result["status"] == "completed"
    else:
        result = service.auto_select_best_manifest()
        assert result.selected_round_index == 0


@pytest.mark.parametrize(
    ("field_name", "requested", "observed"),
    [
        ("acquisition_mode", "dia", None),
        ("acquisition_mode", "dia", "dda"),
        ("labeling_strategy", "TMT", None),
        ("labeling_strategy", "TMT", "SILAC"),
    ],
    ids=[
        "acquisition-missing",
        "acquisition-conflict",
        "labeling-missing",
        "labeling-conflict",
    ],
)
def test_hard_builtin_constraints_override_a_passing_agent_judgment_at_selection(
    tmp_path: Path,
    field_name: str,
    requested: str,
    observed: str | None,
) -> None:
    service, _store, _manifest_path = _service(
        tmp_path,
        request_updates={
            field_name: requested,
            "hard_constraint_fields": ["repository", field_name],
        },
        project_updates={field_name: observed},
        file_updates={field_name: observed},
    )
    assert service.record_project_judgments([_include_judgment()])["status"] == "completed"

    report = service.audit_discovery_state(meter_tool=False)
    selected = service.select_discovery_manifest(
        0,
        "A passing Agent grade cannot replace persisted hard-constraint evidence.",
        ["PXD_AUDIT"],
    )

    assert report.ready_for_selection is False
    assert report.counts["usable_files"] == 0
    assert any(
        issue.code == "hard_builtin_constraint_not_met"
        and field_name in issue.constraint_ids
        for issue in report.issues
    )
    assert selected["status"] == "blocked"
    assert "discovery_quality_audit_requires_repair" in selected["blockers"]


@pytest.mark.parametrize(
    ("field_name", "requested"),
    [
        ("acquisition_mode", "dia"),
        ("labeling_strategy", "TMT"),
    ],
)
def test_hard_builtin_constraint_alone_requires_publication_audit(
    tmp_path: Path,
    field_name: str,
    requested: str,
) -> None:
    service, store, _manifest_path = _service(
        tmp_path,
        request_updates={
            field_name: requested,
            "hard_constraint_fields": ["repository", field_name],
        },
        project_updates={field_name: None},
        file_updates={field_name: None},
    )

    assert service.publish_latest_manifest() == {}
    persisted = store.load_run("audit_run")
    assert persisted is not None
    assert persisted.latest_discovery_audit is not None
    assert persisted.latest_discovery_audit.ready_for_selection is False


@pytest.mark.parametrize(
    ("field_name", "requested", "matching", "tampered"),
    [
        ("acquisition_mode", "dia", "dia", "dda"),
        ("labeling_strategy", "TMT", "TMT", "SILAC"),
    ],
)
def test_publication_reaudits_hard_builtins_on_the_exact_selected_manifest(
    tmp_path: Path,
    field_name: str,
    requested: str,
    matching: str,
    tampered: str,
) -> None:
    service, store, _manifest_path = _service(
        tmp_path,
        request_updates={
            field_name: requested,
            "hard_constraint_fields": ["repository", field_name],
        },
        project_updates={field_name: matching},
        file_updates={field_name: matching},
    )
    assert service.record_project_judgments([_include_judgment()])["status"] == "completed"
    selected = service.select_discovery_manifest(
        0,
        "Select the project while its persisted hard evidence matches.",
        ["PXD_AUDIT"],
    )
    assert selected["status"] == "completed"

    selected_path = Path(selected["manifest_path"])
    manifest = DatasetManifest.model_validate_json(
        selected_path.read_text(encoding="utf-8")
    )
    tampered_manifest = manifest.model_copy(
        update={
            "projects": [
                project.model_copy(update={field_name: tampered})
                for project in manifest.projects
            ],
            "files": [
                file.model_copy(update={field_name: tampered})
                for file in manifest.files
            ],
        }
    )
    selected_path.write_text(tampered_manifest.model_dump_json(), encoding="utf-8")

    assert service.publish_latest_manifest() == {}
    persisted = store.load_run("audit_run")
    assert persisted is not None
    assert persisted.latest_discovery_audit is not None
    assert any(
        issue.code == "hard_builtin_constraint_not_met"
        and field_name in issue.constraint_ids
        for issue in persisted.latest_discovery_audit.issues
    )


def test_soft_builtin_preferences_do_not_activate_the_publication_quality_gate(
    tmp_path: Path,
) -> None:
    service, store, _manifest_path = _service(
        tmp_path,
        request_updates={
            "acquisition_mode": "dia",
            "labeling_strategy": "TMT",
            "hard_constraint_fields": ["repository"],
        },
        project_updates={"acquisition_mode": None, "labeling_strategy": None},
        file_updates={"acquisition_mode": None, "labeling_strategy": None},
    )

    published = service.publish_latest_manifest()

    assert "dataset_manifest_json" in published
    persisted = store.load_run("audit_run")
    assert persisted is not None
    assert persisted.latest_discovery_audit is None


@pytest.mark.parametrize(
    ("request_updates", "ceiling", "limitation"),
    [
        (
            {
                "quantity_scope": "portfolio",
                "portfolio_size_preference": "maximize_qualified_projects",
                "harvest_all_qualified": True,
            },
            "query",
            "hard_query_unit_limit",
        ),
        (
            {
                "quantity_scope": "portfolio",
                "portfolio_size_preference": "maximize_qualified_projects",
                "harvest_all_qualified": True,
            },
            "repository",
            "hard_repository_request_limit",
        ),
        (
            {"quota_flexibility": "open_ended"},
            "query",
            "hard_query_unit_limit",
        ),
        (
            {"quota_flexibility": "open_ended"},
            "repository",
            "hard_repository_request_limit",
        ),
    ],
    ids=[
        "maximize-query-ceiling",
        "maximize-repository-ceiling",
        "open-ended-query-ceiling",
        "open-ended-repository-ceiling",
    ],
)
@pytest.mark.parametrize("selection_mode", ["explicit", "auto"])
def test_selection_honors_ready_with_limitations_at_dynamic_hard_ceilings(
    tmp_path: Path,
    request_updates: dict[str, Any],
    ceiling: str,
    limitation: str,
    selection_mode: str,
) -> None:
    service, store, _manifest_path = _service(
        tmp_path,
        max_projects=2,
        request_updates=request_updates,
    )
    service.search_environment = SimpleNamespace(
        candidate_accessions=["PXD_AUDIT", "PXD_UNINSPECTED"]
    )
    assert service.record_project_judgments([_include_judgment()])["status"] == "completed"
    run = store.load_run("audit_run")
    assert run is not None
    limits = DynamicBudgetLimits(max_query_units=10, max_repository_requests=10)
    usage = DynamicBudgetUsage(
        query_units=10 if ceiling == "query" else 0,
        repository_requests=10 if ceiling == "repository" else 0,
    )
    store.save_run(
        run.model_copy(
            update={
                "dynamic_limits": limits,
                "dynamic_usage": usage,
                "latest_high_relevance_candidate_count": 2,
            }
        )
    )

    report = service.audit_discovery_state(meter_tool=False)
    if selection_mode == "explicit":
        selected = service.select_discovery_manifest(
            0,
            "Select the usable evidence and preserve the authoritative ceiling limitation.",
            ["PXD_AUDIT"],
        )
        assert selected["status"] == "completed"
    else:
        selected_run = service.auto_select_best_manifest()
        assert selected_run.selected_round_index == 0

    assert report.ready_for_selection is True
    assert limitation in report.limitations
    assert "high_relevance_inspection_coverage_incomplete" in report.limitations
    persisted = store.load_run("audit_run")
    assert persisted is not None
    assert persisted.latest_discovery_audit is not None
    assert persisted.latest_discovery_audit.ready_for_selection is True
    assert limitation in persisted.latest_discovery_audit.limitations
