from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent.control_plane.capabilities import CapabilityRegistry
from agent.control_plane.openai_agents import (
    _audit_and_persist,
    _persist_discovery_audit_snapshot,
)
from agent.control_plane.openai_agents import run_authority_repair_cycle
from agent.control_plane.models import (
    DiscoveryAuditIssue,
    DiscoveryQualityAudit,
    DiscoveryRepairAction,
)
from agent.control_plane.repair import RepairAuthority
from agent.control_plane.store import AgentRunStore
from agent.discovery.production_authority import (
    CallbackProductionPublicationSigner,
    DurableAuthorityLedger,
    ProductionSigningResult,
)
from agent.discovery.publication import canonical_package_digest

from test_discovery_wiring_dev_publication import _audit, _run_with_material


def _clear_production_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DISCOVERY_AUTHORITY_LEDGER_PATH",
        "DISCOVERY_AUTHORITY_TRUSTED_PUBLIC_KEYS",
        "DISCOVERY_AUTHORITY_SIGNER_ENDPOINT",
        "DISCOVERY_AUTHORITY_SIGNER_KEY_ID",
        "DISCOVERY_AUTHORITY_SIGNER_BEARER_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)


def _configure_production_signer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    key_id: str,
) -> CallbackProductionPublicationSigner:
    staging_smoke = str(os.getenv("DISCOVERY_STAGING_SMOKE") or "").strip() == "1"
    if not staging_smoke:
        _clear_production_environment(monkeypatch)
    monkeypatch.setenv("DISCOVERY_AUTHORITY_MODE", "production")
    if staging_smoke:
        assert os.getenv("DISCOVERY_AUTHORITY_LEDGER_PATH")
        encoded_private_key = str(
            os.getenv("DISCOVERY_STAGING_TEST_ED25519_PRIVATE_KEY_B64") or ""
        ).strip()
        assert encoded_private_key
        private_key = Ed25519PrivateKey.from_private_bytes(
            base64.b64decode(encoded_private_key)
        )
    else:
        monkeypatch.setenv(
            "DISCOVERY_AUTHORITY_LEDGER_PATH",
            str(tmp_path / "authority.sqlite"),
        )
        private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    monkeypatch.setenv(
        "DISCOVERY_AUTHORITY_TRUSTED_PUBLIC_KEYS",
        json.dumps(
            {
                key_id: {
                    "public_key": base64.b64encode(public_bytes).decode("ascii"),
                    "status": "active",
                }
            }
        ),
    )

    def sign(payload: bytes, payload_digest: str) -> ProductionSigningResult:
        return ProductionSigningResult(
            key_id=key_id,
            payload_digest=payload_digest,
            signature=base64.urlsafe_b64encode(private_key.sign(payload)).decode("ascii"),
        )

    return CallbackProductionPublicationSigner(key_id=key_id, callback=sign)


def test_production_run_path_fails_closed_without_signer_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_production_environment(monkeypatch)
    monkeypatch.setenv("DISCOVERY_AUTHORITY_MODE", "production")
    monkeypatch.setenv("DISCOVERY_AUTHORITY_DEV_SIGN", "1")
    run_id = "production-config-missing"
    audit = _audit(run_id)
    store = AgentRunStore(tmp_path / "state.sqlite")
    store.save_run(_run_with_material(run_id, audit))

    _persist_discovery_audit_snapshot(
        SimpleNamespace(store=store, run_id=run_id),
        audit,
    )

    persisted = store.load_run(run_id)
    assert persisted is not None
    assert persisted.publication_authority is None
    assert "production_authority_configuration_missing" in persisted.blockers
    assert persisted.business_completion is not None
    assert persisted.business_completion.succeeded is False
    assert persisted.business_completion.success_ui_allowed is False


def test_invalid_authority_mode_never_falls_back_to_dev_signing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_production_environment(monkeypatch)
    monkeypatch.setenv("DISCOVERY_AUTHORITY_MODE", "prodution")
    monkeypatch.setenv("DISCOVERY_AUTHORITY_DEV_SIGN", "1")
    run_id = "invalid-authority-mode"
    audit = _audit(run_id)
    store = AgentRunStore(tmp_path / "state.sqlite")
    store.save_run(_run_with_material(run_id, audit))

    _persist_discovery_audit_snapshot(
        SimpleNamespace(store=store, run_id=run_id),
        audit,
    )

    persisted = store.load_run(run_id)
    assert persisted is not None
    assert persisted.publication_authority is None
    assert "authority_mode_invalid" in persisted.blockers
    assert persisted.business_completion is not None
    assert persisted.business_completion.succeeded is False


def test_production_run_path_persists_issued_completion_and_builder_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_production_environment(monkeypatch)
    monkeypatch.setenv("DISCOVERY_AUTHORITY_MODE", "production")
    ledger_path = tmp_path / "authority.sqlite"
    monkeypatch.setenv("DISCOVERY_AUTHORITY_LEDGER_PATH", str(ledger_path))
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key_id = "staging-run-key"
    monkeypatch.setenv(
        "DISCOVERY_AUTHORITY_TRUSTED_PUBLIC_KEYS",
        json.dumps(
            {
                key_id: {
                    "public_key": base64.b64encode(public_bytes).decode("ascii"),
                    "status": "active",
                }
            }
        ),
    )

    def sign(payload: bytes, payload_digest: str) -> ProductionSigningResult:
        return ProductionSigningResult(
            key_id=key_id,
            payload_digest=payload_digest,
            signature=base64.urlsafe_b64encode(private_key.sign(payload)).decode("ascii"),
        )

    signer = CallbackProductionPublicationSigner(key_id=key_id, callback=sign)
    run_id = "production-run-happy"
    audit = _audit(run_id)
    store = AgentRunStore(tmp_path / "state.sqlite")
    run = _run_with_material(run_id, audit)
    assert run.build_ready_package_material is not None
    package = run.build_ready_package_material.model_copy(
        update={"builder_preflight_ref": "builder-preflight:staging:ready"}
    )
    store.save_run(run.model_copy(update={"build_ready_package_material": package}))
    repair = RepairAuthority(
        registry=CapabilityRegistry.default(),
        ledger=DurableAuthorityLedger(ledger_path),
        authority_id="repair-authority:production-run",
    )
    completion_context = repair.completion_context("attempt:production-run")

    def builder_adapter(issued_package: object) -> dict[str, object]:
        assert issued_package == package
        return {
            "accepted": True,
            "status": "ready",
            "package_digest": canonical_package_digest(package),
            "key_id": key_id,
            "builder_entrypoint": package.builder_entrypoint,
            "preflight_ref": package.builder_preflight_ref,
            "receipt_ref": "builder-receipt:staging:ready",
        }

    _persist_discovery_audit_snapshot(
        SimpleNamespace(store=store, run_id=run_id),
        audit,
        completion_context=completion_context,
        production_signer=signer,
        builder_adapter=builder_adapter,
    )

    persisted = store.load_run(run_id)
    assert persisted is not None
    assert persisted.publication_authority is not None
    assert persisted.publication_authority.authority_mode == "production"
    assert persisted.business_completion is not None
    assert persisted.business_completion.succeeded is True
    assert persisted.business_completion.success_ui_allowed is True
    assert persisted.builder_dry_run_result is not None
    assert persisted.builder_dry_run_result.accepted is True
    assert persisted.builder_dry_run_result.receipt_ref == (
        "builder-receipt:staging:ready"
    )

    blocked_audit = audit.model_copy(
        update={"status": "repair_required", "ready_for_selection": False}
    )
    _persist_discovery_audit_snapshot(
        SimpleNamespace(store=store, run_id=run_id),
        blocked_audit,
        production_signer=signer,
    )
    invalidated = store.load_run(run_id)
    assert invalidated is not None
    assert invalidated.business_completion is not None
    assert invalidated.business_completion.succeeded is False
    assert invalidated.builder_dry_run_result is None

    def failing_builder_adapter(_issued_package: object) -> dict[str, object]:
        raise RuntimeError("synthetic builder outage")

    _persist_discovery_audit_snapshot(
        SimpleNamespace(store=store, run_id=run_id),
        audit,
        completion_context=completion_context,
        production_signer=signer,
        builder_adapter=failing_builder_adapter,
    )
    failed = store.load_run(run_id)
    assert failed is not None and failed.builder_dry_run_result is not None
    assert failed.builder_dry_run_result.accepted is False
    assert "builder_adapter_failed" in failed.builder_dry_run_result.blockers
    assert "builder_adapter_failed" in failed.blockers

    _persist_discovery_audit_snapshot(
        SimpleNamespace(store=store, run_id=run_id),
        audit,
        completion_context=completion_context,
        production_signer=signer,
        builder_adapter=builder_adapter,
    )
    recovered = store.load_run(run_id)
    assert recovered is not None and recovered.builder_dry_run_result is not None
    assert recovered.builder_dry_run_result.accepted is True
    assert "builder_adapter_failed" not in recovered.builder_dry_run_result.blockers
    assert "builder_adapter_failed" not in recovered.blockers


class _ReadyAfterRepairService:
    def __init__(
        self,
        *,
        store: AgentRunStore,
        run_id: str,
        ready_audit: DiscoveryQualityAudit,
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.ready_audit = ready_audit

    def audit_discovery_state(
        self,
        *,
        meter_tool: bool = False,
    ) -> DiscoveryQualityAudit:
        assert isinstance(meter_tool, bool)
        return self.ready_audit


def test_normal_ready_audit_issues_durable_publication_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signer = _configure_production_signer(
        tmp_path,
        monkeypatch,
        key_id="normal-ready-key",
    )
    run_id = "normal-ready-production"
    ready_audit = _audit(run_id)
    store = AgentRunStore(tmp_path / "state.sqlite")
    store.save_run(_run_with_material(run_id, ready_audit))
    service = _ReadyAfterRepairService(
        store=store,
        run_id=run_id,
        ready_audit=ready_audit,
    )

    _audit_and_persist(service, production_signer=signer)

    persisted = store.load_run(run_id)
    assert persisted is not None
    assert persisted.business_completion is not None
    assert persisted.business_completion.succeeded is True
    assert persisted.business_completion.repair_authority_id == (
        f"publication-authority:{run_id}"
    )
    assert persisted.business_completion.repair_attempt_id.startswith(
        "publication-attempt:"
    )
    assert persisted.business_completion.repair_attempt_nonce.startswith(
        "publication-attempt-nonce:"
    )


def test_normal_ready_audit_rejects_caller_invented_publication_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signer = _configure_production_signer(
        tmp_path,
        monkeypatch,
        key_id="forged-context-key",
    )
    run_id = "forged-normal-publication-context"
    ready_audit = _audit(run_id)
    store = AgentRunStore(tmp_path / "state.sqlite")
    store.save_run(_run_with_material(run_id, ready_audit))

    _persist_discovery_audit_snapshot(
        SimpleNamespace(store=store, run_id=run_id),
        ready_audit,
        production_signer=signer,
        completion_context={
            "repair_authority_id": f"publication-authority:{run_id}",
            "repair_attempt_id": "publication-attempt:caller-invented",
            "repair_attempt_nonce": "publication-attempt-nonce:caller-invented",
        },
    )

    persisted = store.load_run(run_id)
    assert persisted is not None
    assert persisted.business_completion is not None
    assert persisted.business_completion.succeeded is False
    assert "production_completion_context_unissued" in (
        persisted.business_completion.limitations
    )


def test_normal_publication_attempt_cannot_cross_run_or_package_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signer = _configure_production_signer(
        tmp_path,
        monkeypatch,
        key_id="cross-run-key",
    )
    first_run_id = "publication-context-run-a"
    first_audit = _audit(first_run_id)
    first_store = AgentRunStore(tmp_path / "first-state.sqlite")
    first_store.save_run(_run_with_material(first_run_id, first_audit))
    first_service = _ReadyAfterRepairService(
        store=first_store,
        run_id=first_run_id,
        ready_audit=first_audit,
    )
    _audit_and_persist(first_service, production_signer=signer)
    first = first_store.load_run(first_run_id)
    assert first is not None and first.business_completion is not None
    issued_context = {
        "repair_authority_id": first.business_completion.repair_authority_id,
        "repair_attempt_id": first.business_completion.repair_attempt_id,
        "repair_attempt_nonce": first.business_completion.repair_attempt_nonce,
    }

    second_run_id = "publication-context-run-b"
    second_audit = _audit(second_run_id)
    second_store = AgentRunStore(tmp_path / "second-state.sqlite")
    second_store.save_run(_run_with_material(second_run_id, second_audit))
    _persist_discovery_audit_snapshot(
        SimpleNamespace(store=second_store, run_id=second_run_id),
        second_audit,
        production_signer=signer,
        completion_context=issued_context,
    )

    second = second_store.load_run(second_run_id)
    assert second is not None and second.business_completion is not None
    assert second.business_completion.succeeded is False
    assert "production_completion_context_unissued" in (
        second.business_completion.limitations
    )


def test_production_repair_cycle_uses_same_durable_ledger_for_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_production_environment(monkeypatch)
    monkeypatch.setenv("DISCOVERY_AUTHORITY_MODE", "production")
    monkeypatch.setenv(
        "DISCOVERY_REPAIR_AUTHORITY_ID",
        "repair-authority:production-cycle",
    )
    ledger_path = tmp_path / "authority.sqlite"
    monkeypatch.setenv("DISCOVERY_AUTHORITY_LEDGER_PATH", str(ledger_path))
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key_id = "production-cycle-key"
    monkeypatch.setenv(
        "DISCOVERY_AUTHORITY_TRUSTED_PUBLIC_KEYS",
        json.dumps(
            {
                key_id: {
                    "public_key": base64.b64encode(public_bytes).decode("ascii"),
                    "status": "active",
                }
            }
        ),
    )

    def sign(payload: bytes, payload_digest: str) -> ProductionSigningResult:
        return ProductionSigningResult(
            key_id=key_id,
            payload_digest=payload_digest,
            signature=base64.urlsafe_b64encode(private_key.sign(payload)).decode("ascii"),
        )

    signer = CallbackProductionPublicationSigner(key_id=key_id, callback=sign)
    run_id = "production-repair-cycle"
    ready_audit = _audit(run_id)
    repair_audit = DiscoveryQualityAudit(
        run_id=run_id,
        status="repair_required",
        ready_for_selection=False,
        counts=ready_audit.counts,
        issues=[
            DiscoveryAuditIssue(
                code="autonomous_repair_ceiling_exhausted",
                severity="error",
                summary="The bounded repair ceiling was reached.",
                evidence_refs=["budget"],
            )
        ],
        repair_actions=[
            DiscoveryRepairAction(
                action="stop_with_limitations",
                reason="Complete the bounded audit transition honestly.",
            )
        ],
    )
    store = AgentRunStore(tmp_path / "state.sqlite")
    store.save_run(_run_with_material(run_id, ready_audit))
    service = _ReadyAfterRepairService(
        store=store,
        run_id=run_id,
        ready_audit=ready_audit,
    )

    result = run_authority_repair_cycle(
        service,
        repair_audit,
        production_signer=signer,
    )

    persisted = store.load_run(run_id)
    assert persisted is not None
    assert persisted.business_completion is not None
    assert persisted.business_completion.succeeded is True
    assert persisted.business_completion.repair_authority_id == (
        "repair-authority:production-cycle"
    )
    assert result["attempted"] == 1
    assert "repair_succeeded" in result["attempts"][0]["events"]
    assert "build_ready_succeeded" in result["attempts"][0]["events"]
