from __future__ import annotations

import base64
import copy
import json
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent.control_plane.capabilities import CapabilityRegistry
from agent.control_plane.repair import RepairAuthority
from agent.discovery.production_authority import (
    CallbackProductionPublicationSigner,
    DurableAuthorityLedger,
    ProductionPublicationVerifier,
    ProductionSigningResult,
)
from agent.discovery.publication import (
    PublicationContractRegistry,
    issue_production_publication_authority,
)


FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "discovery"
    / "synthetic_rt_psm_build_ready_transition.json"
)


def _production_signing_seam() -> tuple[
    CallbackProductionPublicationSigner,
    ProductionPublicationVerifier,
]:
    encoded_private_key = str(
        os.getenv("DISCOVERY_STAGING_TEST_ED25519_PRIVATE_KEY_B64") or ""
    ).strip()
    private_key = (
        Ed25519PrivateKey.from_private_bytes(base64.b64decode(encoded_private_key))
        if encoded_private_key
        else Ed25519PrivateKey.generate()
    )
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key_id = "test-runtime-ed25519"

    def sign(payload: bytes, payload_digest: str) -> ProductionSigningResult:
        return ProductionSigningResult(
            key_id=key_id,
            payload_digest=payload_digest,
            signature=base64.urlsafe_b64encode(private_key.sign(payload)).decode("ascii"),
        )

    return (
        CallbackProductionPublicationSigner(key_id=key_id, callback=sign),
        ProductionPublicationVerifier({key_id: public_bytes}),
    )


def _production_snapshot(
    ledger: DurableAuthorityLedger,
    *,
    completion_context: dict[str, str] | None = None,
) -> tuple[dict[str, object], ProductionPublicationVerifier]:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    state = copy.deepcopy(fixture["states"]["build_ready_control"])
    source_authority = state["publication_authority"]
    signer, verifier = _production_signing_seam()
    state["publication_authority"] = issue_production_publication_authority(
        state["validated_build_ready_package"],
        observations=source_authority["observations"],
        verified_membership_refs=source_authority["verified_membership_refs"],
        signer=signer,
        verifier=verifier,
        ledger=ledger,
    ).model_dump(mode="json")
    snapshot: dict[str, object] = {"request": fixture["request"], "state": state}
    if completion_context is not None:
        snapshot["completion_context"] = completion_context
    return snapshot, verifier


def _proposal() -> dict[str, object]:
    return {
        "intent": "Expand a bounded candidate search.",
        "rationale": "The authority audit reports a missing candidate manifest.",
        "requested_capabilities": ["search_expand"],
        "parameters": {"max_items": 1},
        "success_metric_spec": {
            "metric_id": "unique_candidate_count",
            "expected_delta_direction": "increase",
        },
        "risk_class": "expensive",
    }


def _context() -> dict[str, object]:
    return {
        "issue_code_set": ["candidate_manifest_missing"],
        "available_evidence_scopes": ["project"],
        "remaining_tool_calls": 2,
        "remaining_expensive_actions": 1,
    }


def test_durable_idempotency_reservation_survives_authority_restart(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "authority.sqlite"
    first = RepairAuthority(
        registry=CapabilityRegistry.default(),
        ledger=DurableAuthorityLedger(ledger_path),
        authority_id="repair-authority:production",
    )
    decision = first.review_proposal(_proposal(), _context())
    assert decision.decision == "approve"
    first.mark_execution_started(decision)

    restarted = RepairAuthority(
        registry=CapabilityRegistry.default(),
        ledger=DurableAuthorityLedger(ledger_path),
        authority_id="repair-authority:production",
    )
    replay = restarted.review_proposal(_proposal(), _context())

    assert replay.decision == "reject"
    assert replay.reason_code == "duplicate_idempotent_execution"


def test_durable_metric_pair_is_settled_once_across_authority_restart(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "authority.sqlite"
    values = iter([0, 1])
    issuer = RepairAuthority(
        registry=CapabilityRegistry.default(),
        metric_reader=lambda _metric, _scope: next(values),
        ledger=DurableAuthorityLedger(ledger_path),
        authority_id="repair-authority:production",
    )
    pre = issuer.capture_metric_observation(
        metric_id="unique_candidate_count",
        scope_fingerprint="run:durable",
        observation_id="durable:before",
    )
    post = issuer.capture_metric_observation(
        metric_id="unique_candidate_count",
        scope_fingerprint="run:durable",
        observation_id="durable:after",
    )
    attempt = {
        "approved_capability_set": ["search_expand"],
        "parameter_hash": "sha256:durable-operation",
        "issue_code_set": ["candidate_manifest_missing"],
        "metric_id": "unique_candidate_count",
        "pre_observation": pre,
        "post_observation": post,
    }

    restarted = RepairAuthority(
        registry=CapabilityRegistry.default(),
        ledger=DurableAuthorityLedger(ledger_path),
        authority_id="repair-authority:production",
    )
    first_result = restarted.record_attempt(attempt)
    replayed = RepairAuthority(
        registry=CapabilityRegistry.default(),
        ledger=DurableAuthorityLedger(ledger_path),
        authority_id="repair-authority:production",
    ).record_attempt(attempt)

    assert first_result.progressed is True
    assert replayed.progressed is False
    assert "repair_progressed" not in replayed.events


def test_production_signer_and_ledger_issue_build_ready_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DISCOVERY_AUTHORITY_MODE", "production")
    ledger = DurableAuthorityLedger(tmp_path / "authority.sqlite")
    repair = RepairAuthority(
        registry=CapabilityRegistry.default(),
        ledger=ledger,
        authority_id="repair-authority:production",
    )
    snapshot, verifier = _production_snapshot(
        ledger,
        completion_context=repair.completion_context("attempt:production-happy"),
    )

    decision = PublicationContractRegistry(
        production_verifier=verifier,
        ledger=ledger,
    ).evaluate(snapshot)

    assert decision.succeeded is True
    assert decision.status == "build_ready_succeeded"
    assert decision.issuance_token.startswith("durable-completion:")


def test_production_completion_without_issued_recipient_context_is_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DISCOVERY_AUTHORITY_MODE", "production")
    ledger = DurableAuthorityLedger(tmp_path / "authority.sqlite")
    snapshot, verifier = _production_snapshot(ledger)

    decision = PublicationContractRegistry(
        production_verifier=verifier,
        ledger=ledger,
    ).evaluate(snapshot)

    assert decision.succeeded is False
    assert "production_completion_context_missing" in decision.limitations


def test_durable_completion_is_consumed_once_after_authority_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DISCOVERY_AUTHORITY_MODE", "production")
    ledger_path = tmp_path / "authority.sqlite"
    authority_id = "repair-authority:production"
    issuer = RepairAuthority(
        registry=CapabilityRegistry.default(),
        ledger=DurableAuthorityLedger(ledger_path),
        authority_id=authority_id,
    )
    attempt_id = "repair-attempt:durable-completion"
    snapshot, verifier = _production_snapshot(
        DurableAuthorityLedger(ledger_path),
        completion_context=issuer.completion_context(attempt_id),
    )
    decision = PublicationContractRegistry(
        production_verifier=verifier,
        ledger=DurableAuthorityLedger(ledger_path),
    ).evaluate(snapshot)
    assert decision.succeeded is True

    restarted = RepairAuthority(
        registry=CapabilityRegistry.default(),
        ledger=DurableAuthorityLedger(ledger_path),
        authority_id=authority_id,
    )
    first_events = restarted.events_for_finished_attempt(
        attempt_event="repair_attempt_finished",
        audit_status="ready",
        business_completion=decision,
        attempt_id=attempt_id,
    )
    replayed_events = RepairAuthority(
        registry=CapabilityRegistry.default(),
        ledger=DurableAuthorityLedger(ledger_path),
        authority_id=authority_id,
    ).events_for_finished_attempt(
        attempt_event="repair_attempt_finished",
        audit_status="ready",
        business_completion=decision,
        attempt_id=attempt_id,
    )

    assert "repair_succeeded" in first_events
    assert "build_ready_succeeded" in first_events
    assert "repair_succeeded" not in replayed_events
    assert "build_ready_succeeded" not in replayed_events


def test_failed_external_signer_does_not_poison_package_retry(tmp_path: Path) -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    state = fixture["states"]["build_ready_control"]
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    calls = 0

    def flaky_sign(payload: bytes, payload_digest: str) -> ProductionSigningResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic signer outage")
        return ProductionSigningResult(
            key_id="retry-key",
            payload_digest=payload_digest,
            signature=base64.urlsafe_b64encode(private_key.sign(payload)).decode("ascii"),
        )

    signer = CallbackProductionPublicationSigner(
        key_id="retry-key",
        callback=flaky_sign,
    )
    verifier = ProductionPublicationVerifier({"retry-key": public_bytes})
    ledger = DurableAuthorityLedger(tmp_path / "authority.sqlite")
    kwargs = {
        "observations": state["publication_authority"]["observations"],
        "verified_membership_refs": state["publication_authority"][
            "verified_membership_refs"
        ],
        "signer": signer,
        "verifier": verifier,
        "ledger": ledger,
    }

    with pytest.raises(RuntimeError, match="signer outage"):
        issue_production_publication_authority(
            state["validated_build_ready_package"],
            **kwargs,
        )
    issued = issue_production_publication_authority(
        state["validated_build_ready_package"],
        **kwargs,
    )

    assert issued.issuance_token.startswith("production-ed25519:retry-key:")


def test_production_key_lifecycle_accepts_retired_history_but_rejects_new_or_revoked() -> None:
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    payload = b"signed historical publication payload"
    signature = base64.urlsafe_b64encode(private_key.sign(payload)).decode("ascii")

    retired = ProductionPublicationVerifier(
        {"key": (public_bytes, "retired")}
    )
    revoked = ProductionPublicationVerifier(
        {"key": (public_bytes, "revoked")}
    )

    assert retired.verify(key_id="key", payload=payload, signature=signature) is True
    assert retired.verify(
        key_id="key",
        payload=payload,
        signature=signature,
        allow_retired=False,
    ) is False
    assert revoked.verify(key_id="key", payload=payload, signature=signature) is False


def test_durable_consume_rejects_duplicate_entries_atomically(tmp_path: Path) -> None:
    ledger = DurableAuthorityLedger(tmp_path / "authority.sqlite")
    assert ledger.reserve("metric_observation", "token", "sha256:digest") is True
    duplicate = (
        "metric_observation",
        "token",
        "sha256:digest",
    )

    assert ledger.consume_many([duplicate, duplicate]) is False
    assert ledger.verify(
        "metric_observation",
        "token",
        "sha256:digest",
        allow_consumed=False,
    ) is True
    assert ledger.consume_many([duplicate]) is True
