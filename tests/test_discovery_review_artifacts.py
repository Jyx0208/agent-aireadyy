from __future__ import annotations

import csv
import json
from pathlib import Path

import agent.web.app as web_app
from agent.discovery.models import (
    DatasetManifest,
    DatasetRequest,
    DiscoveredFile,
    DiscoveredProject,
)


def _judgment(accession: str) -> dict[str, object]:
    return {
        "project_accession": accession,
        "grade": 2,
        "status": "evidence_backed",
        "hard_gate": "pass",
        "confidence": 0.85,
        "decision": "include",
        "next_action": "include_in_manifest",
        "explanation": "Inspection-backed inclusion with explicit limitations.",
        "evidence_refs": ["selected_file_examples", "validity_status_counts"],
        "constraint_assessments": [],
        "limitations": ["Fixture metadata is intentionally compact."],
        "rubric_version": "project-fit/v2",
        "target_file_count": 1,
        "evidence_stage": "inspection",
    }


def test_review_artifacts_separate_full_candidate_scorecard_from_final_selection(
    tmp_path: Path,
) -> None:
    request = DatasetRequest(repository="pride", max_projects=1, max_files=10)
    keep = DiscoveredProject(project_accession="PXD_KEEP", project_title="Keep")
    drop = DiscoveredProject(
        project_accession="PXD_DROP",
        project_title="Drop",
        needs_review=True,
    )
    keep_file = DiscoveredFile(
        project_accession="PXD_KEEP",
        file_name="keep.raw",
        file_type=".raw",
        validity_status="valid",
        evidence_level="file",
    )
    drop_file = DiscoveredFile(
        project_accession="PXD_DROP",
        file_name="drop.raw",
        file_type=".raw",
        validity_status="weak_keep",
        evidence_level="project",
        needs_review=True,
    )
    candidate = DatasetManifest(
        run_id="review_artifacts",
        request=request,
        projects=[keep, drop],
        files=[keep_file, drop_file],
    )
    selected = DatasetManifest(
        run_id="review_artifacts",
        request=request,
        projects=[keep],
        files=[keep_file],
    )
    candidate_path = tmp_path / "candidate_pool" / "dataset_manifest.json"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(candidate.model_dump_json(), encoding="utf-8")
    selected_path = tmp_path / "final_selection" / "dataset_manifest.json"
    selected_path.parent.mkdir(parents=True, exist_ok=True)
    selected_path.write_text(selected.model_dump_json(), encoding="utf-8")
    (tmp_path / "agents_discovery_summary.json").write_text(
        json.dumps(
            {
                "candidate_pool_manifest_path": str(candidate_path),
                "selected_manifest_path": str(selected_path),
                "selection_rationale": "PXD_KEEP passed the final quality audit.",
                "project_judgments": {
                    "PXD_KEEP": _judgment("PXD_KEEP"),
                    "PXD_DROP": _judgment("PXD_DROP"),
                },
            }
        ),
        encoding="utf-8",
    )

    paths = web_app._ensure_discovery_review_artifacts(tmp_path)
    with paths["project_judgments_table_csv"].open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        all_rows = list(csv.DictReader(handle))
    with paths["selected_projects_review_csv"].open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        selected_rows = list(csv.DictReader(handle))

    assert {row["project_accession"] for row in all_rows} == {"PXD_KEEP", "PXD_DROP"}
    assert [row["project_accession"] for row in selected_rows] == ["PXD_KEEP"]
    assert selected_rows[0]["actual_final_selection"] == "yes"
    assert selected_rows[0]["usable_for_delivery"] == "no"
    assert selected_rows[0]["judgment_evidence_refs"]
    assert (
        selected_rows[0]["review_provenance"]
        == "agent_judgment_legacy_or_unaudited"
    )
    dropped = next(row for row in all_rows if row["project_accession"] == "PXD_DROP")
    assert dropped["actual_final_selection"] == "no"
    assert dropped["usable_for_delivery"] == "no"
    assert dropped["needs_review_file_count"] == "1"
    selected_review = json.loads(
        paths["selected_projects_review_json"].read_text(encoding="utf-8")
    )
    assert selected_review["selected_count"] == 1
    assert selected_review["deliverable_count"] == 0
    record = web_app._public_discovery_record(
        discovery_id=selected.run_id,
        output_dir=tmp_path,
        manifest=selected,
    )
    assert record["status"] == "completed"
    assert record["projects"][0]["actual_final_selection"] is True
    assert (
        record["projects"][0]["review_provenance"]
        == "agent_judgment_legacy_or_unaudited"
    )
    assert record["projects"][0]["usable_for_delivery"] is False
    assert record["summary"]["deliverable_projects"] == 0


def test_public_discovery_record_excludes_review_pending_weak_files_from_usable_count(
    tmp_path: Path,
) -> None:
    request = DatasetRequest(repository="pride", max_projects=1, max_files=10)
    project = DiscoveredProject(project_accession="PXD_COUNT")
    manifest = DatasetManifest(
        run_id="public_count",
        request=request,
        projects=[project],
        files=[
            DiscoveredFile(
                project_accession="PXD_COUNT",
                file_name="valid.raw",
                file_type=".raw",
                validity_status="valid",
            ),
            DiscoveredFile(
                project_accession="PXD_COUNT",
                file_name="review.raw",
                file_type=".raw",
                validity_status="weak_keep",
                needs_review=True,
            ),
        ],
    )

    record = web_app._public_discovery_record(
        discovery_id="public_count",
        output_dir=tmp_path,
        manifest=manifest,
    )

    assert record["summary"]["valid_files"] == 1
    assert record["summary"]["weak_keep_files"] == 1
    assert record["summary"]["needs_review_files"] == 1
    assert record["summary"]["usable_files"] == 1


def test_blocked_agent_candidate_pool_is_not_reported_as_final_selection(
    tmp_path: Path,
) -> None:
    request = DatasetRequest(repository="pride", max_projects=20, max_files=100)
    project = DiscoveredProject(project_accession="PXD_CANDIDATE")
    candidate = DatasetManifest(
        run_id="blocked_candidates",
        request=request,
        projects=[project],
        files=[
            DiscoveredFile(
                project_accession="PXD_CANDIDATE",
                file_name="candidate.raw",
                file_accession_or_path="candidate.raw",
                download_url="https://ftp.pride.ebi.ac.uk/PXD_CANDIDATE/candidate.raw",
                file_type=".raw",
                file_role="raw_acquisition",
                validity_status="needs_review",
                needs_review=True,
            )
        ],
        summary={"selected_projects": 1, "selected_files": 1},
    )

    record = web_app._public_discovery_record(
        discovery_id="blocked_candidates",
        output_dir=tmp_path,
        manifest=candidate,
        status="blocked",
        runtime="openai_agents",
        agent={
            "status": "blocked",
            "selected_round_index": None,
            "stop_reason": "selection_quality_gate_not_completed",
        },
    )

    assert record["status"] == "blocked"
    assert record["project_count"] == 1
    assert record["summary"]["candidate_projects"] == 1
    assert record["summary"]["selected_projects"] == 0
    assert record["summary"]["selected_files"] == 0
    assert record["projects"][0]["actual_final_selection"] is False


def test_candidate_scorecard_merges_selected_and_control_judgments_by_accession(
    tmp_path: Path,
) -> None:
    request = DatasetRequest(repository="pride", max_projects=1, max_files=10)
    keep = DiscoveredProject(project_accession="PXD_KEEP", project_title="Keep")
    drop = DiscoveredProject(project_accession="PXD_DROP", project_title="Drop")
    candidate = DatasetManifest(
        run_id="merged_judgments",
        request=request,
        projects=[keep, drop],
        files=[],
    )
    selected_judgment = {
        **_judgment("PXD_KEEP"),
        "grade": 3,
        "explanation": "Final selected-manifest judgment.",
        "evidence_refs": ["selected-manifest-evidence"],
    }
    control_drop_judgment = {
        **_judgment("PXD_DROP"),
        "grade": 1,
        "hard_gate": "fail",
        "decision": "exclude",
        "explanation": "Control-summary judgment for an unselected candidate.",
        "evidence_refs": ["control-summary-evidence"],
    }
    selected = DatasetManifest(
        run_id=candidate.run_id,
        request=request,
        projects=[keep],
        files=[],
        summary={"project_judgments": {"PXD_KEEP": selected_judgment}},
    )
    candidate_path = tmp_path / "candidate_pool" / "dataset_manifest.json"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(candidate.model_dump_json(), encoding="utf-8")
    selected_path = tmp_path / "final_selection" / "dataset_manifest.json"
    selected_path.parent.mkdir(parents=True, exist_ok=True)
    selected_path.write_text(selected.model_dump_json(), encoding="utf-8")
    (tmp_path / "agents_discovery_summary.json").write_text(
        json.dumps(
            {
                "candidate_pool_manifest_path": str(candidate_path),
                "selected_manifest_path": str(selected_path),
                "project_judgments": {
                    "PXD_KEEP": {
                        **_judgment("PXD_KEEP"),
                        "grade": 1,
                        "explanation": "Stale control-summary judgment.",
                        "evidence_refs": ["stale-control-evidence"],
                    },
                    "PXD_DROP": control_drop_judgment,
                },
            }
        ),
        encoding="utf-8",
    )

    paths = web_app._ensure_discovery_review_artifacts(tmp_path)

    with paths["project_judgments_table_csv"].open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        rows = {
            row["project_accession"]: row
            for row in csv.DictReader(handle)
        }
    persisted_judgments = json.loads(
        paths["project_judgments_json"].read_text(encoding="utf-8")
    )

    assert rows["PXD_KEEP"]["final_grade"] == "3"
    assert rows["PXD_KEEP"]["judgment_explanation"] == selected_judgment["explanation"]
    assert rows["PXD_KEEP"]["judgment_evidence_refs"] == "selected-manifest-evidence"
    assert rows["PXD_DROP"]["final_grade"] == "1"
    assert rows["PXD_DROP"]["judgment_explanation"] == control_drop_judgment["explanation"]
    assert rows["PXD_DROP"]["judgment_evidence_refs"] == "control-summary-evidence"
    assert set(persisted_judgments) == {"PXD_KEEP", "PXD_DROP"}

    record = web_app._public_discovery_record(
        discovery_id=selected.run_id,
        output_dir=tmp_path,
        manifest=selected,
    )
    with paths["project_judgments_table_csv"].open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        exported_rows = {
            row["project_accession"]: row
            for row in csv.DictReader(handle)
        }

    assert set(record["project_judgments"]) == {"PXD_KEEP", "PXD_DROP"}
    assert set(exported_rows) == {"PXD_KEEP", "PXD_DROP"}
    assert exported_rows["PXD_DROP"]["final_grade"] == "1"
    assert (
        exported_rows["PXD_DROP"]["judgment_explanation"]
        == control_drop_judgment["explanation"]
    )
    assert (
        exported_rows["PXD_DROP"]["judgment_evidence_refs"]
        == "control-summary-evidence"
    )


def test_matching_ready_quality_audit_allows_qualified_project_delivery(
    tmp_path: Path,
) -> None:
    request = DatasetRequest(repository="pride", max_projects=1, max_files=10)
    project = DiscoveredProject(project_accession="PXD_READY")
    manifest = DatasetManifest(
        run_id="ready_delivery",
        request=request,
        projects=[project],
        files=[
            DiscoveredFile(
                project_accession=project.project_accession,
                file_name="ready.raw",
                file_type=".raw",
                validity_status="valid",
            )
        ],
        summary={
            "project_judgments": {
                project.project_accession: _judgment(project.project_accession)
            },
            "latest_discovery_audit": {
                "schema_version": "discovery-quality-audit/v1",
                "run_id": "ready_delivery",
                "status": "ready",
                "ready_for_selection": True,
            },
        },
    )

    record = web_app._public_discovery_record(
        discovery_id=manifest.run_id,
        output_dir=tmp_path,
        manifest=manifest,
    )

    assert (
        record["projects"][0]["review_provenance"]
        == "agent_judgment_with_server_quality_audit"
    )
    assert record["projects"][0]["usable_for_delivery"] is True
    assert record["summary"]["deliverable_projects"] == 1


def test_review_provenance_requires_matching_ready_server_audit() -> None:
    ready = {
        "schema_version": "discovery-quality-audit/v1",
        "run_id": "run-ready",
        "status": "ready",
        "ready_for_selection": True,
    }
    blocked = {
        **ready,
        "status": "blocked",
        "ready_for_selection": False,
    }

    assert web_app._discovery_review_provenance(
        {"latest_discovery_audit": ready},
        run_id="run-ready",
    ) == "agent_judgment_with_server_quality_audit"
    assert web_app._discovery_review_provenance(
        {"latest_discovery_audit": blocked},
        run_id="run-ready",
    ) == "agent_judgment_with_nonpassing_server_quality_audit"
    assert web_app._discovery_review_provenance(
        {"latest_discovery_audit": ready},
        run_id="different-run",
    ) == "agent_judgment_legacy_or_unaudited"
    assert web_app._discovery_review_provenance(
        {},
        run_id="run-ready",
    ) == "agent_judgment_legacy_or_unaudited"


def test_public_discovery_record_projects_audit_and_runtime_provenance(
    tmp_path: Path,
) -> None:
    request = DatasetRequest(repository="pride", max_projects=1, max_files=10)
    manifest = DatasetManifest(
        run_id="public-audited-run",
        request=request,
        projects=[],
        files=[],
    )
    audit = {
        "schema_version": "discovery-quality-audit/v1",
        "run_id": manifest.run_id,
        "status": "ready",
        "ready_for_selection": True,
        "counts": {},
    }
    expected_audit = web_app.DiscoveryQualityAudit.model_validate(audit).model_dump(
        mode="json"
    )
    provenance = {
        "schema_version": "runtime-provenance/v2",
        "git_sha": "a" * 40,
        "git_dirty": False,
        "git_diff_sha256": None,
        "python_version": "3.test",
        "package_versions": {},
        "loaded_module_paths": {},
    }
    expected_provenance = web_app.RuntimeProvenance.model_validate(
        provenance
    ).model_dump(mode="json")
    (tmp_path / "agents_discovery_summary.json").write_text(
        json.dumps(
            {
                "run_id": manifest.run_id,
                "latest_discovery_audit": audit,
                "runtime_provenance": provenance,
            }
        ),
        encoding="utf-8",
    )

    record = web_app._public_discovery_record(
        discovery_id="public-audited-run",
        output_dir=tmp_path,
        manifest=manifest,
        runtime="openai_agents",
    )

    assert record["latest_discovery_audit"] == expected_audit
    assert record["runtime_provenance"] == expected_provenance
    assert record["summary"]["latest_discovery_audit"] == expected_audit
    assert record["summary"]["runtime_provenance"] == expected_provenance


def test_delivery_projection_rejects_project_level_exclusion() -> None:
    projected = web_app._project_delivery_quality(
        {"validity_status": "exclude", "needs_review": False},
        _judgment("PXD_EXCLUDED"),
        [
            {
                "project_accession": "PXD_EXCLUDED",
                "file_name": "otherwise-valid.raw",
                "validity_status": "valid",
                "needs_review": False,
            }
        ],
        actually_selected=True,
    )

    assert projected["project_needs_review"] is True
    assert projected["usable_for_delivery"] is False
