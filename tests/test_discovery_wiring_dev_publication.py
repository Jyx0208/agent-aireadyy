from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.control_plane.models import AgentRunRecord, DiscoveryQualityAudit
from agent.control_plane.openai_agents import (
    _audit_reference,
    _persist_discovery_audit_snapshot,
)
from agent.control_plane.store import AgentRunStore
from agent.discovery.publication import (
    BuildReadyPackage,
    PublicationContractRegistry,
    business_completion_allows_success,
    issue_dev_publication_authority,
)


FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "discovery"
    / "synthetic_rt_psm_build_ready_transition.json"
)


def _fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _audit(run_id: str) -> DiscoveryQualityAudit:
    return DiscoveryQualityAudit(
        run_id=run_id,
        status="ready",
        ready_for_selection=True,
        counts={
            "candidate_projects": 4,
            "candidate_files": 6,
            "assessable_inspections": 3,
            "qualified_projects": 2,
        },
    )


def _run_with_material(run_id: str, audit: DiscoveryQualityAudit) -> AgentRunRecord:
    fixture = _fixture()
    state = fixture["states"]["build_ready_control"]
    package_payload = dict(state["validated_build_ready_package"])
    package_payload["authority_run_id"] = run_id
    package_payload["audit_ref"] = _audit_reference(audit)
    return AgentRunRecord(
        run_id=run_id,
        workflow="discovery",
        request={"scientific_constraints": fixture["request"]["constraints"]},
        build_ready_package_material=BuildReadyPackage.model_validate(package_payload),
        publication_evidence_observations=state["publication_authority"]["observations"],
        publication_membership_refs=state["publication_authority"][
            "verified_membership_refs"
        ],
    )


def test_dev_authority_signing_requires_explicit_enable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DISCOVERY_AUTHORITY_DEV_SIGN", raising=False)
    fixture = _fixture()
    state = fixture["states"]["build_ready_control"]

    with pytest.raises(PermissionError, match="explicitly enabled"):
        issue_dev_publication_authority(
            state["validated_build_ready_package"],
            observations=state["publication_authority"]["observations"],
            verified_membership_refs=state["publication_authority"][
                "verified_membership_refs"
            ],
        )


def test_complete_material_without_dev_sign_remains_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DISCOVERY_AUTHORITY_DEV_SIGN", raising=False)
    run_id = "dev-sign-disabled"
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
    assert persisted.business_completion is not None
    assert persisted.business_completion.succeeded is False
    assert persisted.business_completion.success_ui_allowed is False


def test_complete_material_with_explicit_dev_sign_can_graduate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DISCOVERY_AUTHORITY_DEV_SIGN", "1")
    run_id = "dev-sign-enabled"
    audit = _audit(run_id)
    store = AgentRunStore(tmp_path / "state.sqlite")
    store.save_run(_run_with_material(run_id, audit))

    _persist_discovery_audit_snapshot(
        SimpleNamespace(store=store, run_id=run_id),
        audit,
    )

    persisted = store.load_run(run_id)
    assert persisted is not None
    assert persisted.publication_authority is not None
    assert persisted.publication_authority.issuance_token.startswith("dev-ed25519:")
    assert persisted.business_completion is not None
    assert persisted.business_completion.succeeded is True
    assert persisted.business_completion.success_ui_allowed is True
    assert persisted.business_completion.progress.build_ready_projects == 2
    assert persisted.business_completion.progress.build_ready_files == 6


def test_production_mode_rejects_previously_issued_dev_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing mode must not turn an in-process dev key into production trust."""

    fixture = _fixture()
    state = dict(fixture["states"]["build_ready_control"])
    package = state["validated_build_ready_package"]
    unsigned_authority = state["publication_authority"]
    monkeypatch.setenv("DISCOVERY_AUTHORITY_MODE", "dev")
    monkeypatch.setenv("DISCOVERY_AUTHORITY_DEV_SIGN", "1")
    state["publication_authority"] = issue_dev_publication_authority(
        package,
        observations=unsigned_authority["observations"],
        verified_membership_refs=unsigned_authority["verified_membership_refs"],
    ).model_dump(mode="json")

    monkeypatch.setenv("DISCOVERY_AUTHORITY_MODE", "production")
    decision = PublicationContractRegistry().evaluate(
        {"request": fixture["request"], "state": state}
    )

    assert decision.succeeded is False
    assert decision.success_ui_allowed is False
    assert "publication_authority_state_unissued" in decision.limitations


def test_production_mode_rejects_previously_issued_hmac_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture()
    monkeypatch.setenv("DISCOVERY_AUTHORITY_MODE", "off")
    issued_in_legacy_domain = PublicationContractRegistry().evaluate(
        {
            "request": fixture["request"],
            "state": fixture["states"]["build_ready_control"],
        }
    )
    assert issued_in_legacy_domain.succeeded is True
    assert issued_in_legacy_domain.issuance_token.startswith("hmac-sha256:")

    monkeypatch.setenv("DISCOVERY_AUTHORITY_MODE", "production")

    assert business_completion_allows_success(issued_in_legacy_domain) is False
