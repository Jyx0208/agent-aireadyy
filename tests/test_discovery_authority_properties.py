from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from agent.control_plane.capabilities import CapabilityRegistry
from agent.control_plane.models import DiscoveryQualityAudit
from agent.control_plane.repair import RepairAuthority, upgrade_v1_repair_action
from agent.discovery.evidence_store import EvidenceStore
from agent.discovery.publication import PublicationContractRegistry


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "discovery"


def _fixture(name: str) -> dict[str, object]:
    payload = json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))
    assert payload["network_required"] is False
    assert payload["contains_secrets"] is False
    return payload


def _build_ready_snapshot() -> tuple[dict[str, object], dict[str, object]]:
    fixture = _fixture("synthetic_rt_psm_build_ready_transition.json")
    states = fixture["states"]
    assert isinstance(states, dict)
    request = fixture["request"]
    state = states["build_ready_control"]
    assert isinstance(request, dict)
    assert isinstance(state, dict)
    return copy.deepcopy(request), copy.deepcopy(state)


@pytest.mark.parametrize(
    ("scope", "operator", "value"),
    [
        ("project", "matches", "preferred-project-value"),
        ("assay", "eq", "preferred-assay-value"),
        ("file", "contains", "preferred-file-value"),
        ("sample", "not_contains", "excluded-soft-value"),
        ("spectrum", "exists", True),
        ("portfolio", "gte", 10),
    ],
)
def test_property_soft_preference_never_hard_excludes_build_ready(
    scope: str,
    operator: str,
    value: object,
) -> None:
    request, state = _build_ready_snapshot()
    constraints = request["constraints"]
    assert isinstance(constraints, list)
    constraints.append(
        {
            "id": f"soft.preference.{scope}",
            "label": f"soft preference at {scope}",
            "dimension": f"optional_{scope}_dimension",
            "operator": operator,
            "value": value,
            "strength": "soft",
            "evidence_scope": scope,
            "source": "accepted_recommendation",
        }
    )

    decision = PublicationContractRegistry().evaluate(
        {"request": request, "state": state}
    )

    assert decision.succeeded is True
    assert decision.success_ui_allowed is True
    assert not any("hard_" in item for item in decision.limitations)


@pytest.mark.parametrize(
    "scope",
    ["project", "assay", "file", "sample", "spectrum", "portfolio"],
)
def test_property_unknown_hard_constraint_never_becomes_pass(scope: str) -> None:
    request, state = _build_ready_snapshot()
    constraints = request["constraints"]
    assert isinstance(constraints, list)
    dimension = f"required_{scope}_dimension"
    constraints.append(
        {
            "id": f"hard.unknown.{scope}",
            "label": f"required evidence at {scope}",
            "dimension": dimension,
            "operator": "matches",
            "value": "required-value",
            "strength": "hard",
            "evidence_scope": scope,
            "source": "user",
        }
    )

    decision = PublicationContractRegistry().evaluate(
        {"request": request, "state": state}
    )

    assert decision.succeeded is False
    assert decision.success_ui_allowed is False
    assert f"hard_unknown:{dimension}" in decision.limitations


@pytest.mark.parametrize(
    ("dimension", "file_id"),
    [
        ("labeling_strategy", "file-1"),
        ("organism", "file-2"),
        ("acquisition_mode", "file-3"),
    ],
)
def test_property_project_evidence_never_descends_to_file(
    dimension: str,
    file_id: str,
) -> None:
    membership_ref = f"file:{file_id}"
    store = EvidenceStore(
        available_refs={"source:project"},
        available_membership_refs={membership_ref},
    )
    store.materialize(
        {
            "observation_id": f"observation:{dimension}",
            "subject_kind": "project",
            "subject_id": "project-1",
            "dimension": dimension,
            "observed_value": "observed",
            "evidence_scope": "project",
            "source_kind": "repository_record",
            "source_refs": ["source:project"],
            "membership_refs": [membership_ref],
        }
    )

    assert store.resolve(
        subject_kind="file",
        subject_id=file_id,
        dimension=dimension,
    ) == []


def test_property_two_authority_measured_no_progress_attempts_stop() -> None:
    values = iter([5, 5, 5, 5])
    authority = RepairAuthority(
        registry=CapabilityRegistry.default(),
        metric_reader=lambda _metric, _scope: next(values),
    )

    def attempt(index: int) -> dict[str, object]:
        return {
            "approved_capability_set": ["search_expand"],
            "parameter_hash": "sha256:stable-operation",
            "issue_code_set": ["candidate_manifest_missing"],
            "metric_id": "unique_candidate_count",
            "pre_observation": authority.capture_metric_observation(
                metric_id="unique_candidate_count",
                scope_fingerprint="run:stable",
                observation_id=f"metric:{index}:before",
            ),
            "post_observation": authority.capture_metric_observation(
                metric_id="unique_candidate_count",
                scope_fingerprint="run:stable",
                observation_id=f"metric:{index}:after",
            ),
        }

    first = authority.record_attempt(attempt(1))
    second = authority.record_attempt(attempt(2))

    assert first.stop is False
    assert first.no_progress_count == 1
    assert second.stop is True
    assert second.no_progress_count == 2
    assert second.reason_code == "no_progress_limit_reached"
    assert {"repair_no_progress", "repair_incomplete"} <= set(second.events)
    assert "repair_succeeded" not in second.events


def test_property_success_event_requires_issued_build_ready_completion() -> None:
    request, state = _build_ready_snapshot()
    authority = RepairAuthority(registry=CapabilityRegistry.default())
    attempt_id = "repair-attempt:property-success"
    issued = PublicationContractRegistry().evaluate(
        {
            "request": request,
            "state": state,
            "completion_context": authority.completion_context(attempt_id),
        }
    )

    events = authority.events_for_finished_attempt(
        attempt_event="repair_attempt_finished",
        audit_status="ready",
        business_completion=issued,
        attempt_id=attempt_id,
    )
    replay = authority.events_for_finished_attempt(
        attempt_event="repair_attempt_finished",
        audit_status="ready",
        business_completion=issued,
        attempt_id=attempt_id,
    )

    assert issued.succeeded is True
    assert {"repair_succeeded", "build_ready_succeeded"} <= set(events)
    assert "repair_succeeded" not in replay
    assert "repair_incomplete" in replay


def test_property_progress_counts_without_build_ready_never_emit_success() -> None:
    fixture = _fixture("real_derived_progress_without_build_ready.json")
    request = fixture["request"]
    state = fixture["observed_state"]
    assert isinstance(request, dict)
    assert isinstance(state, dict)
    decision = PublicationContractRegistry().evaluate(
        {"request": request, "state": state}
    )
    authority = RepairAuthority(registry=CapabilityRegistry.default())

    events = authority.events_for_finished_attempt(
        attempt_event="repair_attempt_finished",
        audit_status="repair_required",
        business_completion=decision,
    )

    assert decision.progress.candidate_projects == 32
    assert decision.progress.judgment_qualified_projects == 20
    assert decision.progress.build_ready_projects == 0
    assert decision.succeeded is False
    assert "repair_succeeded" not in events
    assert "build_ready_succeeded" not in events


@pytest.mark.parametrize(
    ("v1_action", "capability", "metric_id"),
    [
        ("search_more", "search_expand", "unique_candidate_count"),
        ("inspect_candidates", "inspect", "reviewed_project_count"),
        (
            "rescore_projects",
            "recompute_validity",
            "judgment_qualified_project_count",
        ),
        ("select_manifest", "select_manifest", "audit_ready"),
        ("stop_with_limitations", "stop_with_limitations", "audit_ready"),
    ],
)
def test_v1_repair_action_replay_upgrades_to_explicit_v2_proposal(
    v1_action: str,
    capability: str,
    metric_id: str,
) -> None:
    proposal = upgrade_v1_repair_action(
        {
            "action": v1_action,
            "reason": "Replay a bounded legacy audit action.",
            "project_accessions": ["GENERIC_PROJECT_1"],
            "constraint_ids": ["constraint-1"],
        },
        proposal_id=f"legacy:{v1_action}",
    )

    assert proposal.schema_version == "discovery-repair-proposal/v2"
    assert proposal.proposal_id == f"legacy:{v1_action}"
    assert proposal.requested_capabilities == [capability]
    assert proposal.success_metric_spec.metric_id == metric_id


def test_v1_audit_replay_preserves_actions_without_claiming_success() -> None:
    audit = DiscoveryQualityAudit.model_validate(
        {
            "schema_version": "discovery-quality-audit/v1",
            "run_id": "legacy-run-1",
            "status": "repair_required",
            "ready_for_selection": False,
            "counts": {"candidate_projects": 3, "build_ready_projects": 0},
            "issues": [
                {
                    "code": "candidate_manifest_missing",
                    "severity": "error",
                    "summary": "Legacy audit requires another bounded search.",
                }
            ],
            "repair_actions": [
                {
                    "action": "search_more",
                    "reason": "Replay legacy bounded repair.",
                }
            ],
        }
    )
    proposal = upgrade_v1_repair_action(audit.repair_actions[0])
    authority = RepairAuthority(registry=CapabilityRegistry.default())

    events = authority.events_for_finished_attempt(
        attempt_event="discovery_quality_repair_completed",
        audit_status=audit.status,
        business_completion={"succeeded": True},
    )

    assert audit.schema_version == "discovery-quality-audit/v1"
    assert proposal.requested_capabilities == ["search_expand"]
    assert events == ["repair_attempt_finished", "repair_incomplete"]
