from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent.control_plane.capabilities import CapabilityRegistry
from agent.control_plane.repair import AuthorityMetricObservation, RepairAuthority
from agent.discovery.evidence_store import EvidenceObservation, EvidenceStore
from agent.discovery.publication import (
    BusinessCompletionDecision,
    PublicationContractRegistry,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "discovery"
    / "synthetic_rt_psm_build_ready_transition.json"
)


def _authority() -> RepairAuthority:
    return RepairAuthority(registry=CapabilityRegistry.default())


def _eligible_completion() -> dict[str, object]:
    return {
        "succeeded": True,
        "status": "build_ready_succeeded",
        "package_kind": "build_ready",
        "success_ui_allowed": True,
        "progress": {"build_ready_projects": 1, "build_ready_files": 2},
    }


def _build_ready_fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_publication_fails_closed_when_latest_audit_is_missing() -> None:
    decision = PublicationContractRegistry().evaluate(
        {
            "request": {"constraints": []},
            "state": {
                "build_ready_count": 1,
                "build_ready_projects": 1,
                "build_ready_files": 1,
                "missing_build_ready_fields": [],
            },
        }
    )

    assert decision.succeeded is False
    assert decision.success_ui_allowed is False


def test_publication_rejects_naked_counts_without_validated_build_ready_package() -> None:
    decision = PublicationContractRegistry().evaluate(
        {
            "request": {"constraints": []},
            "state": {
                "build_ready_count": 1,
                "build_ready_projects": 1,
                "build_ready_files": 1,
                "missing_build_ready_fields": [],
                "latest_audit_status": "ready",
            },
        }
    )

    assert decision.succeeded is False
    assert decision.package_kind == "progress"


def test_publication_does_not_derive_build_ready_from_weak_keep_file() -> None:
    decision = PublicationContractRegistry().evaluate(
        {
            "request": {"constraints": []},
            "state": {
                "latest_audit_status": "ready",
                "missing_build_ready_fields": [],
                "files": [
                    {
                        "project_accession": "GENERIC_PROJECT_1",
                        "file_accession_or_path": "generic.raw",
                        "download_url": "https://example.invalid/generic.raw",
                        "expected_size_bytes": 10,
                        "file_role": "raw_acquisition",
                        "validity_status": "weak_keep",
                        "needs_review": False,
                        "evidence_level": "file",
                    }
                ],
            },
        }
    )

    assert decision.succeeded is False
    assert decision.progress.build_ready_files == 0


def test_evidence_observation_rejects_refs_empty_after_normalization() -> None:
    with pytest.raises((ValidationError, ValueError)):
        EvidenceObservation(
            observation_id="observation-1",
            subject_kind="file",
            subject_id="file-1",
            dimension="file_role",
            observed_value="raw_acquisition",
            evidence_scope="file",
            source_kind="repository_record",
            source_refs=["   "],
        )


def test_unverified_membership_ref_cannot_promote_assay_evidence_to_file() -> None:
    store = EvidenceStore(available_refs={"source:verified"})
    store.materialize(
        {
            "observation_id": "observation-1",
            "subject_kind": "assay",
            "subject_id": "assay-1",
            "dimension": "labeling_strategy",
            "observed_value": "label_free",
            "evidence_scope": "assay",
            "source_kind": "repository_record",
            "source_refs": ["source:verified"],
            "membership_refs": ["file:invented"],
        }
    )

    resolved = store.resolve(
        subject_kind="file",
        subject_id="invented",
        dimension="labeling_strategy",
    )

    assert resolved == []


def test_issue_policy_is_an_authority_admission_boundary() -> None:
    decision = _authority().review_proposal(
        {
            "intent": "Try a registered but issue-incompatible primitive.",
            "rationale": "The authority must apply the issue policy, not only registry membership.",
            "requested_capabilities": ["search_expand"],
            "parameters": {"max_items": 1},
            "success_metric_spec": {
                "metric_id": "unique_candidate_count",
                "expected_delta_direction": "increase",
            },
            "risk_class": "expensive",
        },
        {
            "issue_code_set": ["stale_context"],
            "issue_codes": ["stale_context"],
            "remaining_tool_calls": 2,
            "remaining_expensive_actions": 1,
        },
    )

    assert decision.decision in {"degrade", "reject"}


def test_runner_return_cannot_supply_its_own_success_decision() -> None:
    events = _authority().events_for_finished_attempt(
        attempt_event="runner_returned",
        audit_status="ready",
        business_completion=_eligible_completion(),
    )

    assert "repair_succeeded" not in events
    assert "build_ready_succeeded" not in events


def test_default_no_progress_limit_is_two() -> None:
    authority = _authority()
    attempt = {
        "approved_capability_set": ["inspect"],
        "parameter_hash": "sha256:stable",
        "issue_code_set": ["constraint_assessment_evidence_invalid"],
        "metric_id": "unresolved_claim_count",
        "pre": 3,
        "post": 3,
    }

    first = authority.record_attempt(attempt)
    second = authority.record_attempt(attempt)

    assert first.stop is False
    assert second.stop is True
    assert second.no_progress_count == 2
    assert {"repair_no_progress", "repair_incomplete"} <= set(second.events)


@pytest.mark.parametrize(
    "missing_path",
    [
        "succeeded",
        "status",
        "package_kind",
        "success_ui_allowed",
        "progress.build_ready_projects",
        "progress.build_ready_files",
    ],
)
def test_success_event_gate_independently_requires_every_contract_field(
    missing_path: str,
) -> None:
    completion = _eligible_completion()
    if missing_path.startswith("progress."):
        progress = completion["progress"]
        assert isinstance(progress, dict)
        progress.pop(missing_path.removeprefix("progress."))
    else:
        completion.pop(missing_path)

    events = _authority().events_for_finished_attempt(
        attempt_event="repair_attempt_finished",
        audit_status="ready",
        business_completion=completion,
    )

    assert "repair_succeeded" not in events
    assert "build_ready_succeeded" not in events
    assert "repair_incomplete" in events


def test_self_certified_package_with_unverified_refs_cannot_graduate() -> None:
    fixture = _build_ready_fixture()
    request = fixture["request"]
    states = fixture["states"]
    assert isinstance(states, dict)
    state = copy.deepcopy(states["build_ready_control"])
    package = state["validated_build_ready_package"]
    authority = state["publication_authority"]

    package["authority_run_id"] = "run:does-not-exist"
    state["latest_audit_ref"] = "audit:does-not-exist"
    package["audit_ref"] = "audit:does-not-exist"
    package["manifest_ref"] = "manifest:does-not-exist"
    package["evidence_store_ref"] = "evidence-store:does-not-exist"
    package["builder_entrypoint"] = "builder:does-not-exist"
    for item in package["files"]:
        item["membership_ref"] = f"membership:does-not-exist:{item['file_id']}"
        item["evidence_observation_refs"] = [
            f"observation:does-not-exist:{item['file_id']}"
        ]
    for item in package["constraint_evidence"]:
        item["source_refs"] = ["source:does-not-exist"]

    authority["issued_run_ids"] = [package["authority_run_id"]]
    authority["verified_audit_refs"] = [package["audit_ref"]]
    authority["verified_manifest_refs"] = [package["manifest_ref"]]
    authority["verified_evidence_store_refs"] = [package["evidence_store_ref"]]
    authority["compatible_builder_entrypoints"] = [package["builder_entrypoint"]]
    authority["verified_membership_refs"] = [
        item["membership_ref"] for item in package["files"]
    ]
    authority["observations"] = [
        *(
            {
                "observation_id": observation_id,
                "dimension": "forged_file_entry",
                "scope": "file",
                "observed_value": item["file_id"],
                "source_refs": [f"source:does-not-exist:{item['file_id']}"],
            }
            for item in package["files"]
            for observation_id in item["evidence_observation_refs"]
        ),
        *(
            {
                "observation_id": item["observation_id"],
                "dimension": item["dimension"],
                "scope": item["scope"],
                "observed_value": item["observed_value"],
                "source_refs": item["source_refs"],
            }
            for item in package["constraint_evidence"]
        ),
    ]

    decision = PublicationContractRegistry().evaluate(
        {"request": request, "state": state}
    )

    assert decision.succeeded is False
    assert decision.success_ui_allowed is False


def test_hard_constraint_cannot_be_downgraded_by_duplicate_soft_id() -> None:
    fixture = _build_ready_fixture()
    states = fixture["states"]
    assert isinstance(states, dict)
    state = copy.deepcopy(states["build_ready_control"])
    request = {
        "constraints": [
            {
                "id": "duplicate.constraint",
                "dimension": "critical_dimension",
                "value": "expected",
                "strength": "hard",
                "evidence_scope": "assay",
                "source": "user",
            },
            {
                "id": "duplicate.constraint",
                "dimension": "critical_dimension",
                "value": "preferred",
                "strength": "soft",
                "evidence_scope": "assay",
                "source": "accepted_recommendation",
            },
        ]
    }

    decision = PublicationContractRegistry().evaluate(
        {"request": request, "state": state}
    )

    assert decision.succeeded is False
    assert decision.success_ui_allowed is False


def test_hard_constraint_beyond_normalization_limit_cannot_be_dropped() -> None:
    fixture = _build_ready_fixture()
    states = fixture["states"]
    assert isinstance(states, dict)
    state = copy.deepcopy(states["build_ready_control"])
    constraints = [
        {
            "id": f"soft.constraint.{index}",
            "dimension": f"soft_dimension_{index}",
            "value": "preferred",
            "strength": "soft",
            "evidence_scope": "project",
            "source": "preference",
        }
        for index in range(100)
    ]
    constraints.append(
        {
            "id": "overflow.hard.constraint",
            "dimension": "overflow_hard_dimension",
            "value": "required",
            "strength": "hard",
            "evidence_scope": "project",
            "source": "user",
        }
    )

    decision = PublicationContractRegistry().evaluate(
        {"request": {"constraints": constraints}, "state": state}
    )

    assert decision.succeeded is False
    assert decision.success_ui_allowed is False


def test_invalid_hard_constraint_cannot_be_silently_dropped() -> None:
    fixture = _build_ready_fixture()
    states = fixture["states"]
    assert isinstance(states, dict)
    state = copy.deepcopy(states["build_ready_control"])
    state["validated_build_ready_package"]["constraint_evidence"] = []
    request = {
        "constraints": [
            {
                "dimension": "critical_dimension",
                "value": "expected",
                "strength": "hard",
                "evidence_scope": "invalid-scope",
                "source": "user",
            }
        ]
    }

    decision = PublicationContractRegistry().evaluate(
        {"request": request, "state": state}
    )

    assert decision.succeeded is False
    assert decision.success_ui_allowed is False


def test_signed_inventory_cannot_authorize_a_substituted_package() -> None:
    fixture = _build_ready_fixture()
    states = fixture["states"]
    assert isinstance(states, dict)
    state = copy.deepcopy(states["build_ready_control"])
    package = state["validated_build_ready_package"]
    project_map = {
        project_id: f"FORGED_{index}"
        for index, project_id in enumerate(package["project_ids"], start=1)
    }
    package["project_ids"] = list(project_map.values())
    for index, item in enumerate(package["files"], start=1):
        item["file_id"] = f"forged-file-{index}"
        item["project_id"] = project_map[item["project_id"]]
        item["download_url"] = f"https://example.invalid/forged-{index}.raw"
        item["expected_size_bytes"] = index

    decision = PublicationContractRegistry().evaluate(
        {"request": fixture["request"], "state": state}
    )

    assert decision.succeeded is False
    assert decision.success_ui_allowed is False


def test_caller_constructed_metric_observations_are_not_authority_capture() -> None:
    authority = _authority()
    common = {
        "captured_by": "repair_authority",
        "metric_id": "unique_candidate_count",
        "source": "candidate_manifest.project_id",
        "aggregation": "count_distinct",
        "scope_fingerprint": "caller-controlled-scope",
    }
    result = authority.record_attempt(
        {
            "approved_capability_set": ["search_expand"],
            "parameter_hash": "sha256:caller-controlled",
            "issue_code_set": ["candidate_manifest_missing"],
            "metric_id": "unique_candidate_count",
            "pre_observation": AuthorityMetricObservation(
                observation_id="caller:before",
                value=0,
                **common,
            ),
            "post_observation": AuthorityMetricObservation(
                observation_id="caller:after",
                value=999,
                **common,
            ),
        }
    )

    assert result.progressed is False
    assert "repair_progressed" not in result.events


def test_missing_issue_context_cannot_bypass_lp6_admission() -> None:
    decision = _authority().review_proposal(
        {
            "intent": "Try a registered primitive without an authority issue context.",
            "rationale": "LP6 admission must not disappear when issue codes are omitted.",
            "requested_capabilities": ["search_expand"],
            "parameters": {"max_items": 1},
            "success_metric_spec": {
                "metric_id": "unique_candidate_count",
                "expected_delta_direction": "increase",
            },
            "risk_class": "expensive",
        },
        {"remaining_tool_calls": 2, "remaining_expensive_actions": 1},
    )

    assert decision.decision in {"degrade", "reject"}


def test_idempotency_cannot_be_bypassed_by_changing_only_the_metric() -> None:
    authority = _authority()
    context = {
        "issue_code_set": ["hard_builtin_constraint_not_met"],
        "available_evidence_scopes": ["project"],
        "remaining_tool_calls": 4,
    }
    base = {
        "intent": "Inspect the same constrained target.",
        "rationale": "Equivalent capability parameters identify the same operation.",
        "requested_capabilities": ["inspect"],
        "parameters": {"constraint_ids": ["constraint-1"]},
        "risk_class": "read_only",
    }
    first = authority.review_proposal(
        {
            **base,
            "success_metric_spec": {
                "metric_id": "hard_conflict_count",
                "expected_delta_direction": "decrease",
            },
        },
        context,
    )
    assert first.decision == "approve"
    assert first.idempotency_key

    second = authority.review_proposal(
        {
            **base,
            "success_metric_spec": {
                "metric_id": "hard_unknown_count",
                "expected_delta_direction": "decrease",
            },
        },
        {**context, "executed_idempotency_keys": [first.idempotency_key]},
    )

    assert second.decision in {"degrade", "reject"}


def test_typed_but_unissued_completion_cannot_emit_success() -> None:
    fixture = _build_ready_fixture()
    states = fixture["states"]
    assert isinstance(states, dict)
    legitimate = PublicationContractRegistry().evaluate(
        {
            "request": fixture["request"],
            "state": states["build_ready_control"],
        }
    )
    payload = legitimate.model_dump(mode="json")
    package = payload["build_ready_package"]
    package["authority_run_id"] = "runner:forged"
    package["audit_ref"] = "audit:runner-forged"
    forged = BusinessCompletionDecision.model_validate(payload)

    events = _authority().events_for_finished_attempt(
        attempt_event="repair_attempt_finished",
        audit_status="ready",
        business_completion=forged,
    )

    assert "repair_succeeded" not in events
    assert "build_ready_succeeded" not in events


def test_issued_completion_cannot_be_replayed_in_a_new_repair_authority() -> None:
    fixture = _build_ready_fixture()
    states = fixture["states"]
    assert isinstance(states, dict)
    issued = PublicationContractRegistry().evaluate(
        {
            "request": fixture["request"],
            "state": states["build_ready_control"],
        }
    )
    assert issued.succeeded is True
    assert issued.issuance_token

    events = _authority().events_for_finished_attempt(
        attempt_event="repair_attempt_finished",
        audit_status="ready",
        business_completion=issued,
    )

    assert "repair_succeeded" not in events
    assert "build_ready_succeeded" not in events


def test_completion_recipient_cannot_be_self_certified_by_copying_authority_id() -> None:
    fixture = _build_ready_fixture()
    states = fixture["states"]
    assert isinstance(states, dict)
    issuer = _authority()
    attempt_id = "attempt:issued"
    issued = PublicationContractRegistry().evaluate(
        {
            "request": fixture["request"],
            "state": states["build_ready_control"],
            "completion_context": issuer.completion_context(attempt_id),
        }
    )
    assert issued.succeeded is True
    assert issued.repair_authority_id == issuer.authority_id

    recipient = _authority()
    recipient.authority_id = issuer.authority_id
    recipient.completion_context(attempt_id)
    events = recipient.events_for_finished_attempt(
        attempt_event="repair_attempt_finished",
        audit_status="ready",
        business_completion=issued,
        attempt_id=attempt_id,
    )

    assert "repair_succeeded" not in events
    assert "build_ready_succeeded" not in events


def test_issued_metric_observation_pair_cannot_be_replayed_as_new_progress() -> None:
    values = iter([0, 1])

    def metric_reader(metric: object, scope_fingerprint: str) -> int:
        del metric, scope_fingerprint
        return next(values)

    authority = RepairAuthority(
        registry=CapabilityRegistry.default(),
        metric_reader=metric_reader,
    )
    pre = authority.capture_metric_observation(
        metric_id="unique_candidate_count",
        scope_fingerprint="run:current",
        observation_id="metric:before",
    )
    post = authority.capture_metric_observation(
        metric_id="unique_candidate_count",
        scope_fingerprint="run:current",
        observation_id="metric:after",
    )
    attempt = {
        "approved_capability_set": ["search_expand"],
        "parameter_hash": "sha256:current-operation",
        "issue_code_set": ["candidate_manifest_missing"],
        "metric_id": "unique_candidate_count",
        "pre_observation": pre,
        "post_observation": post,
    }

    first = authority.record_attempt(attempt)
    replay = authority.record_attempt(attempt)

    assert first.progressed is True
    assert replay.progressed is False
    assert "repair_progressed" not in replay.events
