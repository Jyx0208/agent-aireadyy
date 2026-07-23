from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.discovery.builder_contract import BuilderDryRunContract
from agent.control_plane.capabilities import CapabilityRegistry
from agent.control_plane.repair import RepairAuthority
from agent.discovery.production_authority import DurableAuthorityLedger
from agent.discovery.publication import (
    PublicationContractRegistry,
    canonical_package_digest,
    issue_configured_publication_authority,
    issue_production_publication_authority,
    materialize_build_ready_package,
)

from test_discovery_build_ready_materialization import _snapshot as _materialization_snapshot
from test_discovery_production_authority import _production_signing_seam


FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "discovery"
    / "synthetic_rt_psm_build_ready_transition.json"
)
AUTHORITY_ID = "repair-authority:m5-staged"
ATTEMPT_ID = "repair-attempt:m5-staged"


def _signed_snapshot(
    ledger: DurableAuthorityLedger,
) -> tuple[dict[str, object], object, str]:
    from agent.discovery.publication import issue_production_publication_authority

    materialization_snapshot = _materialization_snapshot()
    materialized = materialize_build_ready_package(materialization_snapshot)
    assert materialized.ready_for_authority_signing is True
    assert materialized.package is not None
    package = materialized.package
    signer, verifier = _production_signing_seam()
    publication_authority = issue_production_publication_authority(
        package,
        observations=materialized.evidence_observations,
        verified_membership_refs=materialized.membership_refs,
        signer=signer,
        verifier=verifier,
        ledger=ledger,
    )
    state = {
        "candidate_projects": 1,
        "reviewed_projects": 1,
        "judgment_qualified_projects": 1,
        "build_ready_count": 1,
        "build_ready_projects": 1,
        "build_ready_files": 1,
        "missing_build_ready_fields": [],
        "latest_audit_status": "ready",
        "latest_audit_ref": package.audit_ref,
        "publication_authority": publication_authority.model_dump(mode="json"),
        "validated_build_ready_package": package.model_dump(mode="json"),
    }
    repair = RepairAuthority(
        registry=CapabilityRegistry.default(),
        ledger=ledger,
        authority_id=AUTHORITY_ID,
    )
    snapshot = {
        "request": {"constraints": materialization_snapshot["constraints"]},
        "state": state,
        "completion_context": repair.completion_context(ATTEMPT_ID),
    }
    return snapshot, verifier, signer.key_id


def test_stage3_builder_dry_run_accepts_only_issued_canonical_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DISCOVERY_AUTHORITY_MODE", "production")
    ledger = DurableAuthorityLedger(tmp_path / "authority.sqlite")
    snapshot, verifier, key_id = _signed_snapshot(ledger)
    registry = PublicationContractRegistry(
        production_verifier=verifier,
        ledger=ledger,
    )
    decision = registry.evaluate(snapshot)
    assert decision.build_ready_package is not None
    package = decision.build_ready_package

    result = BuilderDryRunContract(registry=registry).evaluate(
        snapshot,
        builder_result={
            "accepted": True,
            "status": "ready",
            "package_digest": canonical_package_digest(package),
            "key_id": key_id,
            "builder_entrypoint": package.builder_entrypoint,
            "preflight_ref": package.builder_preflight_ref,
            "receipt_ref": "builder-receipt:synthetic:v1",
        },
    )

    assert result.accepted is True
    assert result.status == "builder_dry_run_accepted"
    assert result.receipt_ref == "builder-receipt:synthetic:v1"


def test_stage3_http_200_without_typed_builder_receipt_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DISCOVERY_AUTHORITY_MODE", "production")
    ledger = DurableAuthorityLedger(tmp_path / "authority.sqlite")
    snapshot, verifier, _key_id = _signed_snapshot(ledger)

    result = BuilderDryRunContract(
        registry=PublicationContractRegistry(
            production_verifier=verifier,
            ledger=ledger,
        )
    ).evaluate(snapshot, builder_result={"http_status": 200})

    assert result.accepted is False
    assert result.status == "builder_dry_run_blocked"
    assert "builder_receipt_not_accepted" in result.blockers


def test_stage2_substituted_package_is_rejected_before_builder_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DISCOVERY_AUTHORITY_MODE", "production")
    ledger = DurableAuthorityLedger(tmp_path / "authority.sqlite")
    snapshot, verifier, _key_id = _signed_snapshot(ledger)
    state = snapshot["state"]
    assert isinstance(state, dict)
    state["validated_build_ready_package"]["files"][0]["expected_size_bytes"] += 1

    result = BuilderDryRunContract(
        registry=PublicationContractRegistry(
            production_verifier=verifier,
            ledger=ledger,
        )
    ).evaluate(snapshot, builder_result={"http_status": 200})

    assert result.accepted is False
    assert "publication_not_build_ready" in result.blockers


def test_stage2_progress_only_32_0_equivalent_never_reaches_builder() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    result = BuilderDryRunContract(
        registry=PublicationContractRegistry()
    ).evaluate(
        {"request": fixture["request"], "state": fixture["states"]["progress_only"]},
        builder_result={"http_status": 200},
    )

    assert result.accepted is False
    assert result.business_completion_status == "blocked_with_progress"
    assert "publication_not_build_ready" in result.blockers


def test_stage2_production_mode_never_falls_back_when_signer_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    state = fixture["states"]["build_ready_control"]
    monkeypatch.setenv("DISCOVERY_AUTHORITY_MODE", "production")
    monkeypatch.setenv("DISCOVERY_AUTHORITY_DEV_SIGN", "1")

    with pytest.raises(RuntimeError, match="external signer"):
        issue_configured_publication_authority(
            state["validated_build_ready_package"],
            observations=state["publication_authority"]["observations"],
            verified_membership_refs=state["publication_authority"][
                "verified_membership_refs"
            ],
        )


def test_stage2_bad_membership_is_rejected_before_signing(
    tmp_path: Path,
) -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    state = fixture["states"]["build_ready_control"]
    signer, verifier = _production_signing_seam()
    memberships = list(state["publication_authority"]["verified_membership_refs"])

    with pytest.raises(ValueError, match="membership material is incomplete"):
        issue_production_publication_authority(
            state["validated_build_ready_package"],
            observations=state["publication_authority"]["observations"],
            verified_membership_refs=memberships[1:],
            signer=signer,
            verifier=verifier,
            ledger=DurableAuthorityLedger(tmp_path / "authority.sqlite"),
        )


def test_stage2_completion_reissue_is_idempotent_and_replay_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DISCOVERY_AUTHORITY_MODE", "production")
    ledger = DurableAuthorityLedger(tmp_path / "authority.sqlite")
    snapshot, verifier, _key_id = _signed_snapshot(ledger)
    registry = PublicationContractRegistry(
        production_verifier=verifier,
        ledger=ledger,
    )

    first = registry.evaluate(snapshot)
    repeated = registry.evaluate(snapshot)
    assert first.succeeded is True
    assert repeated.issuance_token == first.issuance_token

    consumer = RepairAuthority(
        registry=CapabilityRegistry.default(),
        ledger=ledger,
        authority_id=AUTHORITY_ID,
    )
    first_events = consumer.events_for_finished_attempt(
        attempt_event="repair_attempt_finished",
        audit_status="ready",
        business_completion=first,
        attempt_id=ATTEMPT_ID,
    )
    replay_events = RepairAuthority(
        registry=CapabilityRegistry.default(),
        ledger=ledger,
        authority_id=AUTHORITY_ID,
    ).events_for_finished_attempt(
        attempt_event="repair_attempt_finished",
        audit_status="ready",
        business_completion=repeated,
        attempt_id=ATTEMPT_ID,
    )

    assert "repair_succeeded" in first_events
    assert "repair_succeeded" not in replay_events


def test_stage2_two_identical_no_progress_attempts_stop() -> None:
    values = iter([0, 0, 0, 0])
    authority = RepairAuthority(
        registry=CapabilityRegistry.default(),
        metric_reader=lambda _metric, _scope: next(values),
        no_progress_limit=2,
    )
    attempt = {
        "approved_capability_set": ["search_expand"],
        "parameter_hash": "sha256:m5-no-progress",
        "issue_code_set": ["candidate_manifest_missing"],
        "metric_id": "unique_candidate_count",
    }

    def measured_attempt() -> dict[str, object]:
        return {
            **attempt,
            "pre_observation": authority.capture_metric_observation(
                metric_id="unique_candidate_count",
                scope_fingerprint="run:m5-no-progress",
            ),
            "post_observation": authority.capture_metric_observation(
                metric_id="unique_candidate_count",
                scope_fingerprint="run:m5-no-progress",
            ),
        }

    first = authority.record_attempt(measured_attempt())
    second = authority.record_attempt(measured_attempt())

    assert first.stop is False
    assert second.stop is True
    assert second.reason_code == "no_progress_limit_reached"
    assert "repair_succeeded" not in second.events
