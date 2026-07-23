from __future__ import annotations

import importlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest


FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "discovery"
    / "scripted_repair_proposals.json"
)


def _fixture() -> dict[str, Any]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert payload["network_required"] is False
    assert payload["contains_secrets"] is False
    return payload


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value[name]
    return getattr(value, name)


def _future_authority() -> Any:
    try:
        capabilities = importlib.import_module("agent.control_plane.capabilities")
        repair = importlib.import_module("agent.control_plane.repair")
    except ModuleNotFoundError:
        pytest.fail(
            "WAVE 3 RED: implement agent.control_plane.capabilities and "
            "agent.control_plane.repair for the open RepairProposal Authority Plane",
            pytrace=False,
        )
    registry_type = getattr(capabilities, "CapabilityRegistry", None)
    authority_type = getattr(repair, "RepairAuthority", None)
    if registry_type is None or authority_type is None:
        pytest.fail(
            "WAVE 3 RED: CapabilityRegistry or RepairAuthority public seam is missing",
            pytrace=False,
        )
    registry = registry_type.default()
    return authority_type(registry=registry, no_progress_limit=2)


def _case(case_id: str) -> dict[str, Any]:
    return next(
        item for item in _fixture()["proposal_cases"] if item["case_id"] == case_id
    )


def test_repair_fixture_declares_additive_capabilities_and_authority_metrics() -> None:
    fixture = _fixture()

    assert {
        "search_expand",
        "inspect",
        "materialize_evidence",
        "recompute_validity",
        "refresh_auth_context",
        "select_manifest",
        "stop_with_limitations",
        "ask_user_blocking_question",
    } <= set(fixture["registered_capabilities"])
    assert "build_ready_project_count" in fixture["registered_metrics"]
    assert all(
        case["proposal"]["success_metric_spec"]
        for case in fixture["proposal_cases"]
    )


def test_no_progress_fixture_uses_locked_signature_and_limit_two() -> None:
    case = _fixture()["no_progress_case"]
    first, second = case["attempts"]
    signature_fields = (
        "approved_capability_set",
        "parameter_hash",
        "issue_code_set",
        "metric_id",
    )

    assert case["limit"] == 2
    assert all(first[field] == second[field] for field in signature_fields)
    assert [first["delta"], second["delta"]] == [0, 0]
    assert case["expected"]["stop"] is True
    assert "repair_succeeded" in case["expected"]["forbidden_events"]


def test_finished_attempt_fixture_never_equates_runner_return_with_success() -> None:
    case = _fixture()["attempt_finished_not_ready_case"]

    assert case["attempt_event"] == "repair_attempt_finished"
    assert case["audit_status"] == "repair_required"
    assert case["business_completion"]["succeeded"] is False
    assert case["business_completion"]["build_ready_projects"] == 0
    assert case["expected"]["success_ui_allowed"] is False
    assert "repair_succeeded" in case["expected"]["forbidden_events"]


@pytest.mark.parametrize(
    ("case_id", "expected_decision"),
    [
        ("open_intent_maps_to_registered_primitives", "approve"),
        ("unknown_capability_is_rejected", "reject"),
        ("uncomputable_metric_is_rejected", "reject"),
        ("stale_context_requests_refresh", "approve"),
    ],
)
def test_wave3_authority_reviews_open_repair_proposals(
    case_id: str,
    expected_decision: str,
) -> None:
    case = _case(case_id)
    authority = _future_authority()
    review = getattr(authority, "review_proposal", None)
    if not callable(review):
        pytest.fail(
            "WAVE 3 RED: RepairAuthority.review_proposal(proposal, context) is missing",
            pytrace=False,
        )

    decision = review(case["proposal"], case["authority_context"])

    assert _field(decision, "decision") == expected_decision
    if "reason_code" in case["expected"]:
        assert _field(decision, "reason_code") == case["expected"]["reason_code"]
    if "approved_capabilities" in case["expected"]:
        assert list(_field(decision, "approved_capabilities")) == case["expected"][
            "approved_capabilities"
        ]


def test_wave3_two_identical_no_progress_attempts_stop_honestly() -> None:
    case = _fixture()["no_progress_case"]
    authority = _future_authority()
    record_attempt = getattr(authority, "record_attempt", None)
    if not callable(record_attempt):
        pytest.fail(
            "WAVE 3 RED: RepairAuthority.record_attempt(attempt) is missing",
            pytrace=False,
        )

    first = record_attempt(case["attempts"][0])
    second = record_attempt(case["attempts"][1])

    assert _field(first, "stop") is False
    assert _field(second, "stop") is True
    assert _field(second, "reason_code") == "no_progress_limit_reached"
    assert "repair_succeeded" not in set(_field(second, "events"))


def test_wave3_finished_attempt_without_build_ready_cannot_emit_success() -> None:
    case = _fixture()["attempt_finished_not_ready_case"]
    authority = _future_authority()
    classify = getattr(authority, "events_for_finished_attempt", None)
    if not callable(classify):
        pytest.fail(
            "WAVE 3 RED: RepairAuthority.events_for_finished_attempt(...) is missing",
            pytrace=False,
        )

    events = set(
        classify(
            attempt_event=case["attempt_event"],
            audit_status=case["audit_status"],
            business_completion=case["business_completion"],
        )
    )

    assert events.isdisjoint(case["expected"]["forbidden_events"])
    assert "repair_incomplete" in events


def test_wave3_registry_mirrors_authority_metrics_and_issue_guidance() -> None:
    capabilities = importlib.import_module("agent.control_plane.capabilities")
    registry = capabilities.CapabilityRegistry.default()

    assert registry.metric("build_ready_project_count").aggregation == "count_distinct"
    policy = registry.issue_policy("qualified_project_has_no_delivery_assets")
    assert "materialize_evidence" in policy.capability_names
    assert policy.preferred_metric_ids == {"missing_build_ready_field_count"}


def test_wave3_parameter_schema_rejects_unregistered_side_effect_fields() -> None:
    authority = _future_authority()

    decision = authority.review_proposal(
        {
            "intent": "Expand a bounded search.",
            "rationale": "The candidate manifest is incomplete.",
            "requested_capabilities": ["search_expand"],
            "parameters": {"delete_database": True},
            "success_metric_spec": {
                "metric_id": "unique_candidate_count",
                "expected_delta_direction": "increase",
            },
            "risk_class": "expensive",
        },
        {
            "issue_code_set": ["candidate_manifest_missing"],
            "available_evidence_scopes": ["project"],
            "remaining_tool_calls": 2,
            "remaining_expensive_actions": 1,
        },
    )

    assert decision.decision == "reject"
    assert decision.reason_code == "parameter_schema_invalid"


def test_wave3_attempt_delta_is_computed_not_trusted_from_runner() -> None:
    authority = _future_authority()
    attempt = {
        "approved_capability_set": ["inspect"],
        "parameters": {"target_group": "unresolved_claims"},
        "issue_code_set": ["missing_file_evidence"],
        "metric_id": "unresolved_claim_count",
        "pre": 4,
        "post": 4,
        "delta": 99,
    }

    result = authority.record_attempt(attempt)

    assert result.delta is None
    assert result.progressed is False
    assert result.reason_code == "untrusted_metric_observation"
    assert "repair_succeeded" not in result.events


def test_wave3_authority_metric_observations_can_measure_progress() -> None:
    repair = importlib.import_module("agent.control_plane.repair")
    capabilities = importlib.import_module("agent.control_plane.capabilities")
    values = iter([4, 2])
    authority = repair.RepairAuthority(
        registry=capabilities.CapabilityRegistry.default(),
        metric_reader=lambda _metric, _scope: next(values),
    )
    attempt = {
        "approved_capability_set": ["inspect"],
        "parameters": {"target_group": "unresolved_claims"},
        "issue_code_set": ["constraint_assessment_evidence_invalid"],
        "metric_id": "unresolved_claim_count",
        "pre_observation": authority.capture_metric_observation(
            observation_id="metric:before",
            metric_id="unresolved_claim_count",
            scope_fingerprint="scope:stable",
        ),
        "post_observation": authority.capture_metric_observation(
            observation_id="metric:after",
            metric_id="unresolved_claim_count",
            scope_fingerprint="scope:stable",
        ),
    }

    result = authority.record_attempt(attempt)

    assert result.delta == -2
    assert result.progressed is True
    assert result.events == ["repair_attempt_finished", "repair_progressed"]


def test_wave3_success_events_require_full_build_ready_contract() -> None:
    authority = _future_authority()
    attempt_id = "repair-attempt:build-ready-test"
    publication = importlib.import_module("agent.discovery.publication")
    fixture = json.loads(
        (FIXTURE_PATH.parent / "synthetic_rt_psm_build_ready_transition.json").read_text(
            encoding="utf-8"
        )
    )
    eligible = publication.PublicationContractRegistry().evaluate(
        {
            "request": fixture["request"],
            "state": fixture["states"]["build_ready_control"],
            "completion_context": authority.completion_context(attempt_id),
        }
    )

    assert eligible.succeeded is True

    events = set(
        authority.events_for_finished_attempt(
            attempt_event="repair_attempt_finished",
            audit_status="ready",
            business_completion=eligible,
            attempt_id=attempt_id,
        )
    )

    assert {"repair_succeeded", "build_ready_succeeded"} <= events


@pytest.mark.parametrize(
    "missing_field",
    ["package_kind", "success_ui_allowed"],
)
def test_wave3_success_events_fail_closed_for_incomplete_contract(
    missing_field: str,
) -> None:
    authority = _future_authority()
    incomplete = {
        "succeeded": True,
        "package_kind": "build_ready",
        "success_ui_allowed": True,
        "progress": {"build_ready_projects": 1, "build_ready_files": 2},
    }
    incomplete.pop(missing_field)

    events = set(
        authority.events_for_finished_attempt(
            attempt_event="repair_attempt_finished",
            audit_status="ready",
            business_completion=incomplete,
        )
    )

    assert "repair_succeeded" not in events
    assert "repair_incomplete" in events


def test_wave3_v1_action_upgrade_is_explicit_and_still_authorized() -> None:
    repair = importlib.import_module("agent.control_plane.repair")
    proposal = repair.upgrade_v1_repair_action(
        {
            "action": "inspect_candidates",
            "reason": "Gather authority-backed inspection records.",
            "project_accessions": ["SYNTHETIC_PROJECT_1"],
        }
    )
    authority = _future_authority()

    decision = authority.review_proposal(
        proposal,
        {
            "issue_code_set": ["high_relevance_inspection_coverage_incomplete"],
            "available_evidence_scopes": ["project"],
            "remaining_tool_calls": 2,
            "remaining_expensive_actions": 0,
        },
    )

    assert decision.decision == "approve"
    assert decision.approved_capabilities == ["inspect"]
