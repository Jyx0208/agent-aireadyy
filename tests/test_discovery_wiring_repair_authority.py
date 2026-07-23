from __future__ import annotations

import json
from pathlib import Path

from agent.control_plane.models import (
    AgentRunRecord,
    DiscoveryAuditIssue,
    DiscoveryQualityAudit,
    DiscoveryRepairAction,
)
from agent.control_plane.openai_agents import (
    _runner_v2_repair_proposals,
    run_authority_repair_cycle,
)
from agent.control_plane.store import AgentRunStore
from agent.discovery.evidence_store import EvidenceObservation, EvidenceStoreArtifact


class _StableAuditService:
    def __init__(
        self,
        *,
        store: AgentRunStore,
        run_id: str,
        audit: DiscoveryQualityAudit,
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.audit = audit

    def audit_discovery_state(self, *, meter_tool: bool = False) -> DiscoveryQualityAudit:
        assert meter_tool is False
        # Prefer the run-persisted audit so safe adapters (e.g. refresh_auth_context)
        # can clear issues that discovery re-audit would otherwise re-inject in stubs.
        run = self.store.load_run(self.run_id)
        if run is not None and run.latest_discovery_audit is not None:
            return run.latest_discovery_audit
        return self.audit


def _repair_required_audit(run_id: str) -> DiscoveryQualityAudit:
    return DiscoveryQualityAudit(
        run_id=run_id,
        status="repair_required",
        ready_for_selection=False,
        counts={"candidate_projects": 5, "qualified_projects": 0},
        issues=[
            DiscoveryAuditIssue(
                code="autonomous_repair_ceiling_exhausted",
                severity="error",
                summary="The bounded repair ceiling is exhausted.",
                evidence_refs=["budget"],
            )
        ],
        repair_actions=[
            DiscoveryRepairAction(
                action="stop_with_limitations",
                reason="Stop honestly with retained progress.",
            )
        ],
        limitations=["hard_repository_request_limit"],
    )


def test_v1_repair_action_runs_through_authority_without_fake_success(
    tmp_path: Path,
) -> None:
    run_id = "authority-cycle-no-progress"
    store = AgentRunStore(tmp_path / "state.sqlite")
    store.save_run(AgentRunRecord(run_id=run_id, workflow="discovery"))
    audit = _repair_required_audit(run_id)
    service = _StableAuditService(store=store, run_id=run_id, audit=audit)

    result = run_authority_repair_cycle(service, audit)

    assert result["attempted"] == 1
    assert result["stopped"] is True
    assert result["stop_reason"] == "authority_stop_with_limitations"
    assert result["attempts"][0]["proposal_schema"] == "discovery-repair-proposal/v2"
    assert result["attempts"][0]["decision"] == "approve"
    assert result["attempts"][0]["metric_id"] == "audit_ready"
    assert result["attempts"][0]["delta"] == 0
    assert result["attempts"][0]["progressed"] is False
    assert "repair_incomplete" in result["attempts"][0]["events"]
    assert "repair_succeeded" not in result["attempts"][0]["events"]
    persisted = store.load_run(run_id)
    assert persisted is not None
    assert len(persisted.repair_execution_keys) == 1
    event_types = [event.event_type for event in store.list_events(run_id)]
    assert "repair_proposal_approved" in event_types
    assert "repair_attempt_started" in event_types
    assert "repair_no_progress" in event_types
    assert "repair_incomplete" in event_types
    assert "repair_succeeded" not in event_types
    assert "build_ready_succeeded" not in event_types


def test_runner_v2_proposal_uses_authority_and_cannot_fake_success(
    tmp_path: Path,
) -> None:
    run_id = "runner-v2-no-progress"
    store = AgentRunStore(tmp_path / "state.sqlite")
    store.save_run(AgentRunRecord(run_id=run_id, workflow="discovery"))
    audit = _repair_required_audit(run_id)
    service = _StableAuditService(store=store, run_id=run_id, audit=audit)
    proposal = {
        "schema_version": "discovery-repair-proposal/v2",
        "proposal_id": "runner-proposal-1",
        "intent": "Stop after the autonomous repair ceiling is exhausted.",
        "rationale": "Retain evidence and report limitations honestly.",
        "requested_capabilities": ["stop_with_limitations"],
        "parameters": {},
        "success_metric_spec": {
            "metric_id": "audit_ready",
            "expected_delta_direction": "increase",
        },
        "risk_class": "read_only",
    }

    result = run_authority_repair_cycle(service, audit, proposals=[proposal])

    assert result["attempted"] == 1
    assert result["stopped"] is True
    assert result["stop_reason"] == "authority_stop_with_limitations"
    assert result["attempts"][0]["progressed"] is False
    event_types = [event.event_type for event in store.list_events(run_id)]
    assert "repair_proposal_approved" in event_types
    assert "repair_no_progress" in event_types
    assert "repair_incomplete" in event_types
    assert "repair_succeeded" not in event_types
    assert "build_ready_succeeded" not in event_types


def test_runner_v2_unknown_capability_is_rejected_before_dispatch(
    tmp_path: Path,
) -> None:
    run_id = "runner-v2-unauthorized"
    store = AgentRunStore(tmp_path / "state.sqlite")
    store.save_run(AgentRunRecord(run_id=run_id, workflow="discovery"))
    audit = _repair_required_audit(run_id)
    service = _StableAuditService(store=store, run_id=run_id, audit=audit)
    proposal = {
        "schema_version": "discovery-repair-proposal/v2",
        "proposal_id": "runner-proposal-unknown",
        "intent": "Invoke an unregistered operation.",
        "rationale": "This must fail closed.",
        "requested_capabilities": ["run_arbitrary_command"],
        "parameters": {},
        "success_metric_spec": {
            "metric_id": "audit_ready",
            "expected_delta_direction": "increase",
        },
        "risk_class": "read_only",
    }

    result = run_authority_repair_cycle(service, audit, proposals=[proposal])

    assert result["attempted"] == 0
    assert result["stopped"] is True
    assert result["stop_reason"] == "unknown_capability"
    event_types = [event.event_type for event in store.list_events(run_id)]
    assert event_types.count("repair_proposal_rejected") == 1
    assert "repair_attempt_started" not in event_types
    assert "repair_succeeded" not in event_types
    assert "build_ready_succeeded" not in event_types


def test_runner_final_output_v2_envelope_enters_the_same_authority_admission(
    tmp_path: Path,
) -> None:
    run_id = "runner-final-output-v2-admission"
    store = AgentRunStore(tmp_path / "state.sqlite")
    store.save_run(AgentRunRecord(run_id=run_id, workflow="discovery"))
    audit = _repair_required_audit(run_id)
    service = _StableAuditService(store=store, run_id=run_id, audit=audit)
    final_output = json.dumps(
        {
            "repair_proposals": [
                {
                    "schema_version": "discovery-repair-proposal/v2",
                    "proposal_id": "runner-final-output-unknown",
                    "intent": "Try an unregistered side effect.",
                    "rationale": "Authority must reject this before dispatch.",
                    "requested_capabilities": ["run_arbitrary_command"],
                    "parameters": {},
                    "success_metric_spec": {
                        "metric_id": "audit_ready",
                        "expected_delta_direction": "increase",
                    },
                    "risk_class": "read_only",
                }
            ]
        }
    )

    proposals = _runner_v2_repair_proposals(final_output)
    result = run_authority_repair_cycle(service, audit, proposals=proposals)

    assert len(proposals) == 1
    assert result["attempted"] == 0
    assert result["stop_reason"] == "unknown_capability"
    events = store.list_events(run_id)
    admission = next(
        event for event in events if event.event_type == "repair_proposal_rejected"
    )
    assert admission.payload["proposal_source"] == "runner_v2"
    assert "repair_attempt_started" not in [event.event_type for event in events]
    assert "repair_succeeded" not in [event.event_type for event in events]
    assert "build_ready_succeeded" not in [event.event_type for event in events]


def test_persisted_idempotency_key_stops_duplicate_repair_dispatch(
    tmp_path: Path,
) -> None:
    run_id = "authority-cycle-idempotency"
    store = AgentRunStore(tmp_path / "state.sqlite")
    store.save_run(AgentRunRecord(run_id=run_id, workflow="discovery"))
    audit = _repair_required_audit(run_id)
    service = _StableAuditService(store=store, run_id=run_id, audit=audit)

    first = run_authority_repair_cycle(service, audit)
    second = run_authority_repair_cycle(service, audit)

    assert first["attempted"] == 1
    assert second["attempted"] == 0
    assert second["stopped"] is True
    assert second["stop_reason"] == "no_progress_limit_reached"
    persisted = store.load_run(run_id)
    assert persisted is not None
    assert persisted.repair_no_progress_count == 2
    event_types = [event.event_type for event in store.list_events(run_id)]
    assert event_types.count("repair_attempt_started") == 1
    assert "repair_proposal_rejected" in event_types
    assert event_types.count("repair_no_progress") == 2
    assert "repair_succeeded" not in event_types


def _evidence_issue_audit(run_id: str) -> DiscoveryQualityAudit:
    return DiscoveryQualityAudit(
        run_id=run_id,
        status="repair_required",
        ready_for_selection=False,
        counts={"candidate_projects": 3, "qualified_projects": 1},
        issues=[
            DiscoveryAuditIssue(
                code="constraint_assessment_evidence_invalid",
                severity="error",
                summary="Constraint assessments cite unavailable evidence refs.",
                evidence_refs=["project_judgments"],
            )
        ],
        repair_actions=[],
        limitations=["constraint_assessment_evidence_invalid"],
    )


def _stale_context_audit(run_id: str) -> DiscoveryQualityAudit:
    return DiscoveryQualityAudit(
        run_id=run_id,
        status="repair_required",
        ready_for_selection=False,
        counts={"candidate_projects": 2, "qualified_projects": 0},
        issues=[
            DiscoveryAuditIssue(
                code="stale_context",
                severity="error",
                summary="The active search context is stale.",
                evidence_refs=["latest_candidate_search_id"],
            )
        ],
        repair_actions=[],
        limitations=["stale_context"],
    )


def test_materialize_evidence_adapter_promotes_store_observations_without_success(
    tmp_path: Path,
) -> None:
    run_id = "materialize-evidence-adapter"
    store = AgentRunStore(tmp_path / "state.sqlite")
    store.save_run(
        AgentRunRecord(
            run_id=run_id,
            workflow="discovery",
            publication_membership_refs=["membership:A:A1"],
            publication_evidence_store=EvidenceStoreArtifact(
                observations=[
                    EvidenceObservation(
                        observation_id="obs:file:A1",
                        subject_kind="file",
                        subject_id="file:A1",
                        dimension="builder_file_entry",
                        observed_value="file:A1",
                        evidence_scope="file",
                        source_kind="manifest_inspection",
                        source_refs=["source:file:A1"],
                        membership_refs=["membership:A:A1"],
                    )
                ]
            ),
            latest_discovery_audit=_evidence_issue_audit(run_id),
        )
    )
    audit = _evidence_issue_audit(run_id)
    service = _StableAuditService(store=store, run_id=run_id, audit=audit)
    proposal = {
        "schema_version": "discovery-repair-proposal/v2",
        "proposal_id": "materialize-1",
        "intent": "Promote validated evidence already present in the store.",
        "rationale": "Authority inventory must only accept store-backed observations.",
        "requested_capabilities": ["materialize_evidence"],
        "parameters": {
            "observation_ids": ["obs:file:A1"],
            "source_refs": ["source:file:A1"],
            "membership_refs": ["membership:A:A1"],
        },
        "success_metric_spec": {
            "metric_id": "verified_observation_count",
            "expected_delta_direction": "increase",
        },
        "risk_class": "bounded_write",
    }

    result = run_authority_repair_cycle(service, audit, proposals=[proposal])

    assert result["attempted"] == 1
    dispatch = result["attempts"][0]["dispatch"]
    assert dispatch["outputs"][0]["capability"] == "materialize_evidence"
    assert dispatch["outputs"][0]["status"] == "completed"
    assert dispatch["outputs"][0]["added_observation_count"] == 1
    assert "registered_adapter_not_wired" not in str(dispatch)
    persisted = store.load_run(run_id)
    assert persisted is not None
    assert [item.observation_id for item in persisted.publication_evidence_observations] == [
        "obs:file:A1"
    ]
    assert result["attempts"][0]["progressed"] is True
    event_types = [event.event_type for event in store.list_events(run_id)]
    assert "repair_progressed" in event_types
    assert "repair_succeeded" not in event_types
    assert "build_ready_succeeded" not in event_types
    assert persisted.business_completion is None or not getattr(
        persisted.business_completion, "succeeded", False
    )


def test_materialize_evidence_adapter_rejects_unknown_observation_fail_closed(
    tmp_path: Path,
) -> None:
    run_id = "materialize-evidence-unknown"
    store = AgentRunStore(tmp_path / "state.sqlite")
    store.save_run(
        AgentRunRecord(
            run_id=run_id,
            workflow="discovery",
            publication_evidence_store=EvidenceStoreArtifact(
                observations=[
                    EvidenceObservation(
                        observation_id="obs:known",
                        subject_kind="project",
                        subject_id="project-1",
                        dimension="organism",
                        observed_value="human",
                        evidence_scope="project",
                        source_kind="repository",
                        source_refs=["repository:project-1"],
                    )
                ]
            ),
            latest_discovery_audit=_evidence_issue_audit(run_id),
        )
    )
    audit = _evidence_issue_audit(run_id)
    service = _StableAuditService(store=store, run_id=run_id, audit=audit)
    proposal = {
        "schema_version": "discovery-repair-proposal/v2",
        "proposal_id": "materialize-unknown",
        "intent": "Invent an observation that is not in the store.",
        "rationale": "Must fail closed.",
        "requested_capabilities": ["materialize_evidence"],
        "parameters": {"observation_ids": ["obs:invented"]},
        "success_metric_spec": {
            "metric_id": "verified_observation_count",
            "expected_delta_direction": "increase",
        },
        "risk_class": "bounded_write",
    }

    result = run_authority_repair_cycle(service, audit, proposals=[proposal])

    assert result["attempted"] == 1
    dispatch = result["attempts"][0]["dispatch"]
    assert dispatch["outputs"][0]["status"] == "blocked"
    assert dispatch["outputs"][0]["reason"] == "materialize_observation_not_in_store"
    persisted = store.load_run(run_id)
    assert persisted is not None
    assert persisted.publication_evidence_observations == []
    assert "repair_succeeded" not in result["attempts"][0]["events"]
    assert "build_ready_succeeded" not in result["attempts"][0]["events"]


def test_refresh_auth_context_adapter_clears_stale_flag_without_success(
    tmp_path: Path,
) -> None:
    run_id = "refresh-auth-adapter"
    store = AgentRunStore(tmp_path / "state.sqlite")
    audit = _stale_context_audit(run_id)
    store.save_run(
        AgentRunRecord(
            run_id=run_id,
            workflow="discovery",
            latest_candidate_search_id="search_ctx_new",
            active_grant_id="grant_new",
            latest_discovery_audit=audit,
        )
    )
    service = _StableAuditService(store=store, run_id=run_id, audit=audit)
    proposal = {
        "schema_version": "discovery-repair-proposal/v2",
        "proposal_id": "refresh-1",
        "intent": "Acknowledge a fresher search context already present on the run.",
        "rationale": "Stale grant identifiers must not invent credentials.",
        "requested_capabilities": ["refresh_auth_context"],
        "parameters": {
            "stale_context_id": "search_ctx_old",
            "stale_grant_id": "grant_old",
            "retry_operation": "inspect",
        },
        "success_metric_spec": {
            "metric_id": "active_context_freshness",
            "expected_delta_direction": "increase",
        },
        "risk_class": "bounded_write",
    }

    result = run_authority_repair_cycle(service, audit, proposals=[proposal])

    assert result["attempted"] == 1
    dispatch = result["attempts"][0]["dispatch"]
    assert dispatch["outputs"][0]["capability"] == "refresh_auth_context"
    assert dispatch["outputs"][0]["status"] == "completed"
    assert dispatch["outputs"][0]["cleared_stale_context_issue"] is True
    assert "registered_adapter_not_wired" not in str(dispatch)
    persisted = store.load_run(run_id)
    assert persisted is not None
    assert persisted.auth_refresh_attempts == 1
    assert persisted.latest_discovery_audit is not None
    assert all(
        issue.code != "stale_context" for issue in persisted.latest_discovery_audit.issues
    )
    assert result["attempts"][0]["progressed"] is True
    event_types = [event.event_type for event in store.list_events(run_id)]
    assert "repair_progressed" in event_types
    assert "repair_succeeded" not in event_types
    assert "build_ready_succeeded" not in event_types


def test_refresh_auth_context_adapter_blocks_when_context_still_stale(
    tmp_path: Path,
) -> None:
    run_id = "refresh-auth-still-stale"
    store = AgentRunStore(tmp_path / "state.sqlite")
    audit = _stale_context_audit(run_id)
    store.save_run(
        AgentRunRecord(
            run_id=run_id,
            workflow="discovery",
            latest_candidate_search_id="search_ctx_old",
            active_grant_id="grant_old",
            latest_discovery_audit=audit,
        )
    )
    service = _StableAuditService(store=store, run_id=run_id, audit=audit)
    proposal = {
        "schema_version": "discovery-repair-proposal/v2",
        "proposal_id": "refresh-still-stale",
        "intent": "Refresh when the active handle is still the stale one.",
        "rationale": "Must not pretend a refresh occurred.",
        "requested_capabilities": ["refresh_auth_context"],
        "parameters": {
            "stale_context_id": "search_ctx_old",
            "stale_grant_id": "grant_old",
        },
        "success_metric_spec": {
            "metric_id": "active_context_freshness",
            "expected_delta_direction": "increase",
        },
        "risk_class": "bounded_write",
    }

    result = run_authority_repair_cycle(service, audit, proposals=[proposal])

    assert result["attempted"] == 1
    dispatch = result["attempts"][0]["dispatch"]
    assert dispatch["outputs"][0]["status"] == "blocked"
    assert dispatch["outputs"][0]["reason"] == "refresh_context_still_stale"
    persisted = store.load_run(run_id)
    assert persisted is not None
    assert persisted.auth_refresh_attempts == 0
    assert any(
        issue.code == "stale_context" for issue in (persisted.latest_discovery_audit.issues or [])
    )
    assert "repair_succeeded" not in result["attempts"][0]["events"]
