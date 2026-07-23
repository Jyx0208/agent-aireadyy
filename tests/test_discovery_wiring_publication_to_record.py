from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import agent.control_plane.openai_agents as openai_agents
from agent.control_plane.discovery import DiscoveryToolService
from agent.control_plane.models import AgentRunRecord, DiscoveryQualityAudit
from agent.control_plane.store import AgentRunStore
from agent.discovery.publication import (
    BusinessCompletionDecision,
    PublicationContractRegistry,
)


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "discovery"


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def _run(run_id: str, *, request: dict[str, object] | None = None) -> AgentRunRecord:
    return AgentRunRecord(
        run_id=run_id,
        workflow="discovery",
        request=request or {},
    )


def test_agent_run_record_round_trips_typed_business_completion(tmp_path: Path) -> None:
    store = AgentRunStore(tmp_path / "state.sqlite")
    decision = PublicationContractRegistry().evaluate(
        {
            "request": {"constraints": []},
            "state": {
                "latest_audit_status": "ready",
                "latest_audit_ref": "audit:round-trip",
                "candidate_projects": 3,
            },
        }
    )

    store.save_run(_run("round-trip").model_copy(update={"business_completion": decision}))

    loaded = store.load_run("round-trip")
    assert loaded is not None
    assert isinstance(loaded.business_completion, BusinessCompletionDecision)
    assert loaded.business_completion == decision
    assert loaded.business_completion.succeeded is False


def test_audit_persistence_evaluates_32_0_as_progress_not_completion(
    tmp_path: Path,
) -> None:
    fixture = _fixture("real_derived_progress_without_build_ready.json")
    observed = fixture["observed_state"]
    assert isinstance(observed, dict)
    store = AgentRunStore(tmp_path / "state.sqlite")
    run_id = "generic-progress-32-0"
    store.save_run(
        _run(
            run_id,
            request={"scientific_constraints": fixture["request"]["constraints"]},
        )
    )
    audit = DiscoveryQualityAudit(
        run_id=run_id,
        status="ready",
        ready_for_selection=True,
        counts={
            "candidate_projects": int(observed["candidate_projects"]),
            "assessable_inspections": int(observed["assessable_inspections"]),
            "qualified_projects": int(observed["judgment_qualified_projects"]),
        },
    )

    openai_agents._persist_discovery_audit_snapshot(
        SimpleNamespace(store=store, run_id=run_id),
        audit,
    )

    persisted = store.load_run(run_id)
    assert persisted is not None
    assert isinstance(persisted.business_completion, BusinessCompletionDecision)
    assert persisted.business_completion.progress.candidate_projects == 32
    assert persisted.business_completion.progress.judgment_qualified_projects == 20
    assert persisted.business_completion.progress.build_ready_projects == 0
    assert persisted.business_completion.status == "blocked_with_progress"
    assert persisted.business_completion.succeeded is False
    assert persisted.business_completion.success_ui_allowed is False
    assert "build_ready_package_missing" in persisted.business_completion.limitations


def test_run_output_summary_projects_business_completion(tmp_path: Path) -> None:
    store = AgentRunStore(tmp_path / "state.sqlite")
    decision = PublicationContractRegistry().evaluate(
        {
            "request": {"constraints": []},
            "state": {"candidate_projects": 2},
        }
    )
    run = store.save_run(
        _run("summary-projection").model_copy(
            update={"business_completion": decision}
        )
    )

    files = openai_agents._write_run_outputs(store, run, tmp_path)
    summary = json.loads(
        Path(files["agents_discovery_summary_json"]).read_text(encoding="utf-8")
    )

    assert summary["business_completion"] == decision.model_dump(mode="json")
    assert summary["business_completion"]["success_ui_allowed"] is False


def test_legacy_repair_completed_envelope_is_attempt_finished_not_success() -> None:
    audit = DiscoveryQualityAudit(
        run_id="repair-attempt",
        status="repair_required",
        ready_for_selection=False,
    )

    payload = openai_agents._legacy_repair_finished_payload(audit, None)

    assert payload["attempt_status"] == "finished"
    assert payload["business_completion"] is None
    assert payload["audit"]["status"] == "repair_required"
    assert "succeeded" not in payload
    assert "success_ui_allowed" not in payload


def test_completion_gate_rejects_runner_or_unissued_decisions() -> None:
    blocked = PublicationContractRegistry().evaluate(
        {
            "request": {"constraints": []},
            "state": {"candidate_projects": 1},
        }
    )
    forged = BusinessCompletionDecision(
        succeeded=True,
        status="build_ready_succeeded",
        package_kind="build_ready",
        progress_visible=True,
        progress={
            "build_ready_projects": 1,
            "build_ready_files": 1,
        },
        success_ui_allowed=True,
    )

    assert openai_agents._business_completion_allows_success(blocked) is False
    assert openai_agents._business_completion_allows_success(forged) is False


def test_completion_gate_accepts_registry_issued_build_ready_decision() -> None:
    fixture = _fixture("synthetic_rt_psm_build_ready_transition.json")
    states = fixture["states"]
    assert isinstance(states, dict)
    issued = PublicationContractRegistry().evaluate(
        {
            "request": fixture["request"],
            "state": states["build_ready_control"],
        }
    )

    assert issued.succeeded is True
    assert openai_agents._business_completion_allows_success(issued) is True


def test_auto_selection_stops_before_manifest_when_publication_is_not_ready(
    tmp_path: Path,
) -> None:
    store = AgentRunStore(tmp_path / "state.sqlite")
    blocked = PublicationContractRegistry().evaluate(
        {
            "request": {"constraints": []},
            "state": {"candidate_projects": 4},
        }
    )
    store.save_run(
        _run("selection-gate").model_copy(
            update={"business_completion": blocked}
        )
    )
    service = object.__new__(DiscoveryToolService)
    service.store = store
    service.run_id = "selection-gate"

    result = service.auto_select_best_manifest()

    assert result.selected_round_index is None
    assert result.current_manifest_path is None
    assert "build_ready_publication_contract_not_satisfied" in result.blockers
    events = store.list_events(result.run_id)
    assert events[-1].event_type == "manifest_selection_rejected"
    assert events[-1].payload["business_completion"]["succeeded"] is False
