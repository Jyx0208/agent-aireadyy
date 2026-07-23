from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.control_plane.models import (
    AgentRunRecord,
    DiscoveryAuditIssue,
    DiscoveryQualityAudit,
)
from agent.control_plane.openai_agents import (
    _audit_reference,
    _dispatch_materialize_evidence_adapter,
    _persist_discovery_audit_snapshot,
    run_authority_repair_cycle,
)
from agent.control_plane.store import AgentRunStore
from agent.discovery.evidence_store import EvidenceObservation, EvidenceStoreArtifact
from agent.discovery.models import (
    DatasetManifest,
    DatasetRequest,
    DiscoveredFile,
    DiscoveredProject,
)
from agent.discovery.publication import materialize_build_ready_package


def _constraints() -> list[dict[str, object]]:
    return [
        {
            "id": "constraint.labeling_strategy",
            "label": "labeling strategy",
            "dimension": "labeling_strategy",
            "value": "label_free",
            "strength": "hard",
            "scope": "assay",
            "source": "user",
        },
        {
            "id": "preference.acquisition",
            "label": "acquisition preference",
            "dimension": "acquisition_mode",
            "value": "dda",
            "strength": "soft",
            "scope": "assay",
            "source": "accepted_recommendation",
        },
    ]


def _manifest(
    *,
    run_id: str = "materialize-run",
    validity_status: str = "valid",
) -> DatasetManifest:
    return DatasetManifest(
        run_id=run_id,
        request=DatasetRequest(
            repository="pride",
            scientific_constraints=_constraints(),
        ),
        projects=[
            DiscoveredProject(
                project_accession="GENERIC_PROJECT_A",
                validity_status="valid",
            )
        ],
        files=[
            DiscoveredFile(
                project_accession="GENERIC_PROJECT_A",
                file_accession_or_path="file:A1",
                file_name="A1.raw",
                download_url="https://example.invalid/A1.raw",
                expected_size_bytes=10,
                file_type=".raw",
                file_role="raw_acquisition",
                validity_status=validity_status,
                needs_review=False,
            )
        ],
    )


def _evidence_store(*, include_membership: bool = True) -> EvidenceStoreArtifact:
    return EvidenceStoreArtifact(
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
                membership_refs=(
                    ["membership:A:A1"] if include_membership else []
                ),
            ),
            EvidenceObservation(
                observation_id="obs:constraint:labeling",
                subject_kind="assay",
                subject_id="assay:A",
                dimension="labeling_strategy",
                observed_value="label_free",
                evidence_scope="assay",
                source_kind="assay_evidence",
                source_refs=["source:assay:labeling"],
            ),
        ]
    )


def _snapshot(**updates: object) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "run_id": "materialize-run",
        "audit": {
            "run_id": "materialize-run",
            "status": "ready",
            "ready_for_selection": True,
            "ref": "audit:sha256:" + "a" * 64,
        },
        "manifest": _manifest().model_dump(mode="json"),
        "constraints": _constraints(),
        "evidence_store": _evidence_store().model_dump(mode="json"),
        "available_membership_refs": ["membership:A:A1"],
        "builder": {
            "entrypoint": "dataset-builder/v1",
            "preflight_status": "ready",
            "preflight_ref": "builder-preflight:sha256:" + "b" * 64,
        },
    }
    snapshot.update(updates)
    return snapshot


def test_materializer_builds_stable_canonical_material_without_signing() -> None:
    first = materialize_build_ready_package(_snapshot())
    second = materialize_build_ready_package(_snapshot())

    assert first.ready_for_authority_signing is True
    assert first.blockers == []
    assert first.package is not None
    assert first.package == second.package
    assert first.package.manifest_ref.startswith("manifest:sha256:")
    assert first.package.evidence_store_ref.startswith("evidence-store:sha256:")
    assert first.package.builder_preflight_ref.startswith(
        "builder-preflight:sha256:"
    )
    assert first.package.project_ids == ["GENERIC_PROJECT_A"]
    assert first.package.files[0].membership_ref == "membership:A:A1"
    assert first.package.constraint_evidence[0].constraint_id == (
        "constraint.labeling_strategy"
    )


def test_materializer_blocks_missing_membership_instead_of_inventing_one() -> None:
    result = materialize_build_ready_package(
        _snapshot(
            evidence_store=_evidence_store(
                include_membership=False
            ).model_dump(mode="json")
        )
    )

    assert result.package is None
    assert result.ready_for_authority_signing is False
    assert any(value.endswith(":membership_missing") for value in result.blockers)


def test_materializer_blocks_weak_keep_and_missing_hard_evidence() -> None:
    evidence_store = _evidence_store().model_copy(
        update={"observations": _evidence_store().observations[:1]}
    )
    result = materialize_build_ready_package(
        _snapshot(
            manifest=_manifest(validity_status="weak_keep").model_dump(mode="json"),
            evidence_store=evidence_store.model_dump(mode="json"),
        )
    )

    assert result.package is None
    assert any(value.endswith(":validity_not_valid") for value in result.blockers)
    assert "materialization_hard_constraint_unknown:constraint.labeling_strategy" in (
        result.blockers
    )


def test_materializer_keeps_32_candidates_zero_build_ready_as_progress() -> None:
    manifest = DatasetManifest(
        run_id="materialize-run",
        request=DatasetRequest(repository="pride", max_projects=32),
        projects=[
            DiscoveredProject(
                project_accession=f"GENERIC_PROJECT_{index:02d}"
            )
            for index in range(32)
        ],
        files=[],
        summary={"candidate_projects": 32, "judgment_qualified_projects": 20},
    )

    result = materialize_build_ready_package(
        _snapshot(manifest=manifest.model_dump(mode="json"))
    )

    assert result.package is None
    assert result.ready_for_authority_signing is False
    assert "materialization_manifest_has_no_files" in result.blockers


def test_audit_persistence_materializes_but_cannot_graduate_without_signer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DISCOVERY_AUTHORITY_DEV_SIGN", raising=False)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(_manifest().model_dump_json(), encoding="utf-8")
    audit = DiscoveryQualityAudit(
        run_id="materialize-run",
        status="ready",
        ready_for_selection=True,
        counts={
            "candidate_projects": 1,
            "candidate_files": 1,
            "assessable_inspections": 1,
            "qualified_projects": 1,
        },
    )
    store = AgentRunStore(tmp_path / "state.sqlite")
    store.save_run(
        AgentRunRecord(
            run_id="materialize-run",
            workflow="discovery",
            request={"scientific_constraints": _constraints()},
            current_manifest_path=str(manifest_path),
            publication_evidence_store=_evidence_store(),
            publication_membership_refs=["membership:A:A1"],
            publication_builder_entrypoint="dataset-builder/v1",
            publication_builder_preflight_status="ready",
            publication_builder_preflight_ref=(
                "builder-preflight:sha256:" + "b" * 64
            ),
        )
    )

    _persist_discovery_audit_snapshot(
        SimpleNamespace(store=store, run_id="materialize-run"),
        audit,
    )

    persisted = store.load_run("materialize-run")
    assert persisted is not None
    assert persisted.build_ready_package_material is not None
    assert persisted.build_ready_package_material.audit_ref == _audit_reference(audit)
    assert persisted.publication_materialization_blockers == []
    assert persisted.publication_authority is None
    assert persisted.business_completion is not None
    assert persisted.business_completion.succeeded is False
    assert persisted.business_completion.success_ui_allowed is False


def test_blocked_materialization_preserves_membership_inventory_for_repair(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(_manifest().model_dump_json(), encoding="utf-8")
    audit = DiscoveryQualityAudit(
        run_id="materialize-run",
        status="repair_required",
        ready_for_selection=False,
    )
    store = AgentRunStore(tmp_path / "state.sqlite")
    store.save_run(
        AgentRunRecord(
            run_id="materialize-run",
            workflow="discovery",
            request={"scientific_constraints": _constraints()},
            current_manifest_path=str(manifest_path),
            publication_membership_refs=["membership:A:A1"],
        )
    )

    _persist_discovery_audit_snapshot(
        SimpleNamespace(store=store, run_id="materialize-run"),
        audit,
    )

    persisted = store.load_run("materialize-run")
    assert persisted is not None
    assert persisted.build_ready_package_material is None
    assert persisted.publication_membership_refs == ["membership:A:A1"]
    assert persisted.publication_materialization_blockers


class _StableAuditService:
    """Minimal service stub matching repair-cycle audit re-read semantics."""

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
        run = self.store.load_run(self.run_id)
        if run is not None and run.latest_discovery_audit is not None:
            return run.latest_discovery_audit
        return self.audit


def _ready_audit(run_id: str = "materialize-run") -> DiscoveryQualityAudit:
    return DiscoveryQualityAudit(
        run_id=run_id,
        status="ready",
        ready_for_selection=True,
        counts={
            "candidate_projects": 1,
            "candidate_files": 1,
            "assessable_inspections": 1,
            "qualified_projects": 1,
        },
    )


def _evidence_gap_audit(run_id: str) -> DiscoveryQualityAudit:
    # Issue code must map to materialize_evidence in CapabilityRegistry policies.
    return DiscoveryQualityAudit(
        run_id=run_id,
        status="repair_required",
        ready_for_selection=False,
        counts={"candidate_projects": 1, "qualified_projects": 0},
        issues=[
            DiscoveryAuditIssue(
                code="constraint_assessment_evidence_invalid",
                severity="error",
                summary="Constraint assessments cite unavailable evidence refs.",
                evidence_refs=["project_judgments"],
            )
        ],
        limitations=["constraint_assessment_evidence_invalid"],
    )


def test_materialize_evidence_then_audit_builds_package_without_signer_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Integration path across three seams without false graduation:

    1. Authority repair cycle admits materialize_evidence and progresses inventory.
    2. Builder preflight gap keeps package material absent / blocked.
    3. Ready preflight + ready audit materializes package, still no signer success.

    Package materialization reads publication_evidence_store; the adapter promotes
    into publication_evidence_observations for Authority inventory. Both may be
    populated; neither may mint business success without an issued signature.
    """

    monkeypatch.delenv("DISCOVERY_AUTHORITY_DEV_SIGN", raising=False)
    run_id = "integrate-promote-then-materialize"
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        _manifest(run_id=run_id).model_dump_json(), encoding="utf-8"
    )
    store = AgentRunStore(tmp_path / "state.sqlite")
    # Attach store immediately but leave preflight pending so package stays
    # blocked while inventory promotion can still run.
    store.save_run(
        AgentRunRecord(
            run_id=run_id,
            workflow="discovery",
            request={"scientific_constraints": _constraints()},
            current_manifest_path=str(manifest_path),
            publication_membership_refs=["membership:A:A1"],
            publication_builder_entrypoint="dataset-builder/v1",
            publication_builder_preflight_status="pending",
            publication_builder_preflight_ref=(
                "builder-preflight:sha256:" + "b" * 64
            ),
            publication_evidence_store=_evidence_store(),
            latest_discovery_audit=_evidence_gap_audit(run_id),
        )
    )

    # Direct adapter call first: guarantees a real promotion even if a later
    # audit path already mirrored store rows into inventory.
    service = _StableAuditService(
        store=store, run_id=run_id, audit=_evidence_gap_audit(run_id)
    )
    direct = _dispatch_materialize_evidence_adapter(
        service,
        parameters={
            "observation_ids": ["obs:file:A1", "obs:constraint:labeling"],
            "source_refs": ["source:file:A1", "source:assay:labeling"],
            "membership_refs": ["membership:A:A1"],
        },
    )
    assert direct["status"] == "completed"
    assert direct["added_observation_count"] == 2

    after_promote = store.load_run(run_id)
    assert after_promote is not None
    assert {
        item.observation_id
        for item in after_promote.publication_evidence_observations
    } == {"obs:file:A1", "obs:constraint:labeling"}
    assert after_promote.build_ready_package_material is None
    assert after_promote.business_completion is None

    gap_audit = _evidence_gap_audit(run_id)
    proposal = {
        "schema_version": "discovery-repair-proposal/v2",
        "proposal_id": "promote-for-package",
        "intent": "Re-promote already inventoried store-backed observations.",
        "rationale": "Second promote must be idempotent and never grant success.",
        "requested_capabilities": ["materialize_evidence"],
        "parameters": {
            "observation_ids": ["obs:file:A1", "obs:constraint:labeling"],
            "source_refs": ["source:file:A1", "source:assay:labeling"],
            "membership_refs": ["membership:A:A1"],
        },
        "success_metric_spec": {
            "metric_id": "verified_observation_count",
            "expected_delta_direction": "increase",
        },
        "risk_class": "bounded_write",
    }

    repair = run_authority_repair_cycle(service, gap_audit, proposals=[proposal])

    assert repair["attempted"] == 1
    dispatch = repair["attempts"][0]["dispatch"]
    assert dispatch["outputs"][0]["capability"] == "materialize_evidence"
    # Already inventoried → noop progress semantics, never success.
    assert dispatch["outputs"][0]["status"] in {"noop", "completed"}
    assert "repair_succeeded" not in [e.event_type for e in store.list_events(run_id)]
    assert "build_ready_succeeded" not in [
        e.event_type for e in store.list_events(run_id)
    ]

    mid = store.load_run(run_id)
    assert mid is not None
    assert mid.build_ready_package_material is None
    assert "materialization_builder_preflight_not_ready" in (
        mid.publication_materialization_blockers
    )
    assert mid.business_completion is not None
    assert mid.business_completion.succeeded is False
    assert mid.business_completion.success_ui_allowed is False

    store.save_run(
        mid.model_copy(
            update={
                "publication_builder_preflight_status": "ready",
                "build_ready_package_material": None,
            }
        )
    )

    ready = _ready_audit(run_id)
    _persist_discovery_audit_snapshot(
        _StableAuditService(store=store, run_id=run_id, audit=ready),
        ready,
    )

    final = store.load_run(run_id)
    assert final is not None
    assert final.build_ready_package_material is not None
    assert final.build_ready_package_material.project_ids == ["GENERIC_PROJECT_A"]
    assert final.build_ready_package_material.files[0].membership_ref == (
        "membership:A:A1"
    )
    assert final.publication_materialization_blockers == []
    assert {
        item.observation_id
        for item in final.publication_evidence_observations
    } >= {"obs:file:A1", "obs:constraint:labeling"}
    assert final.publication_authority is None
    assert final.business_completion is not None
    assert final.business_completion.succeeded is False
    assert final.business_completion.success_ui_allowed is False
    event_types = [event.event_type for event in store.list_events(run_id)]
    assert "build_ready_succeeded" not in event_types
    assert "repair_succeeded" not in event_types


def test_promote_only_does_not_create_package_or_business_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """materialize_evidence alone must not write package material or graduate."""

    monkeypatch.delenv("DISCOVERY_AUTHORITY_DEV_SIGN", raising=False)
    run_id = "promote-only-no-package"
    store = AgentRunStore(tmp_path / "state.sqlite")
    store.save_run(
        AgentRunRecord(
            run_id=run_id,
            workflow="discovery",
            publication_membership_refs=["membership:A:A1"],
            publication_evidence_store=_evidence_store(),
            latest_discovery_audit=_evidence_gap_audit(run_id),
        )
    )
    service = _StableAuditService(
        store=store, run_id=run_id, audit=_evidence_gap_audit(run_id)
    )

    output = _dispatch_materialize_evidence_adapter(
        service,
        parameters={
            "observation_ids": ["obs:file:A1"],
            "source_refs": ["source:file:A1"],
            "membership_refs": ["membership:A:A1"],
        },
    )

    assert output["status"] == "completed"
    persisted = store.load_run(run_id)
    assert persisted is not None
    assert [item.observation_id for item in persisted.publication_evidence_observations] == [
        "obs:file:A1"
    ]
    assert persisted.build_ready_package_material is None
    assert persisted.publication_authority is None
    assert persisted.business_completion is None


def test_project_scope_observation_cannot_substitute_file_builder_entry() -> None:
    """Project-level builder_file_entry must not unlock a file package slot."""

    poisoned = EvidenceStoreArtifact(
        observations=[
            EvidenceObservation(
                observation_id="obs:project:fake-file",
                subject_kind="project",
                subject_id="GENERIC_PROJECT_A",
                dimension="builder_file_entry",
                observed_value="file:A1",
                evidence_scope="project",
                source_kind="repository",
                source_refs=["source:project:A"],
                membership_refs=["membership:A:A1"],
            ),
            EvidenceObservation(
                observation_id="obs:constraint:labeling",
                subject_kind="assay",
                subject_id="assay:A",
                dimension="labeling_strategy",
                observed_value="label_free",
                evidence_scope="assay",
                source_kind="assay_evidence",
                source_refs=["source:assay:labeling"],
            ),
        ]
    )
    result = materialize_build_ready_package(
        _snapshot(evidence_store=poisoned.model_dump(mode="json"))
    )

    assert result.package is None
    assert result.ready_for_authority_signing is False
    assert any(
        value.endswith(":evidence_missing") for value in result.blockers
    )


def test_soft_constraint_without_observation_does_not_block_materialization() -> None:
    result = materialize_build_ready_package(_snapshot())

    assert result.ready_for_authority_signing is True
    assert result.package is not None
    hard_ids = {item.constraint_id for item in result.package.constraint_evidence}
    assert hard_ids == {"constraint.labeling_strategy"}
    assert "preference.acquisition" not in hard_ids
    assert not any("preference.acquisition" in value for value in result.blockers)


def test_audit_persist_skips_re_materialize_when_package_already_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Second audit must not invent a different package or clear blockers inventively."""

    monkeypatch.delenv("DISCOVERY_AUTHORITY_DEV_SIGN", raising=False)
    first = materialize_build_ready_package(_snapshot())
    assert first.package is not None
    manifest_path = tmp_path / "manifest.json"
    # Corrupt manifest would fail if re-materialization were attempted.
    manifest_path.write_text("{not-json", encoding="utf-8")
    audit = _ready_audit()
    store = AgentRunStore(tmp_path / "state.sqlite")
    store.save_run(
        AgentRunRecord(
            run_id="materialize-run",
            workflow="discovery",
            request={"scientific_constraints": _constraints()},
            current_manifest_path=str(manifest_path),
            build_ready_package_material=first.package,
            publication_evidence_observations=first.evidence_observations,
            publication_membership_refs=first.membership_refs,
            publication_materialization_blockers=[],
            publication_evidence_store=_evidence_store(),
            publication_builder_entrypoint="dataset-builder/v1",
            publication_builder_preflight_status="ready",
            publication_builder_preflight_ref=(
                "builder-preflight:sha256:" + "b" * 64
            ),
        )
    )

    _persist_discovery_audit_snapshot(
        _StableAuditService(store=store, run_id="materialize-run", audit=audit),
        audit,
    )

    persisted = store.load_run("materialize-run")
    assert persisted is not None
    assert persisted.build_ready_package_material == first.package
    assert persisted.publication_materialization_blockers == []
    assert persisted.publication_authority is None
    assert persisted.business_completion is not None
    assert persisted.business_completion.succeeded is False


def test_corrupt_manifest_on_disk_blocks_package_without_inventing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DISCOVERY_AUTHORITY_DEV_SIGN", raising=False)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{broken", encoding="utf-8")
    audit = _ready_audit()
    store = AgentRunStore(tmp_path / "state.sqlite")
    store.save_run(
        AgentRunRecord(
            run_id="materialize-run",
            workflow="discovery",
            request={"scientific_constraints": _constraints()},
            current_manifest_path=str(manifest_path),
            publication_evidence_store=_evidence_store(),
            publication_membership_refs=["membership:A:A1"],
            publication_builder_entrypoint="dataset-builder/v1",
            publication_builder_preflight_status="ready",
            publication_builder_preflight_ref=(
                "builder-preflight:sha256:" + "b" * 64
            ),
        )
    )

    _persist_discovery_audit_snapshot(
        _StableAuditService(store=store, run_id="materialize-run", audit=audit),
        audit,
    )

    persisted = store.load_run("materialize-run")
    assert persisted is not None
    assert persisted.build_ready_package_material is None
    assert "materialization_manifest_missing_or_invalid" in (
        persisted.publication_materialization_blockers
    )
    assert persisted.business_completion is not None
    assert persisted.business_completion.succeeded is False
    assert persisted.business_completion.success_ui_allowed is False


def test_builder_preflight_not_ready_blocks_even_with_full_evidence_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DISCOVERY_AUTHORITY_DEV_SIGN", raising=False)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(_manifest().model_dump_json(), encoding="utf-8")
    audit = _ready_audit()
    store = AgentRunStore(tmp_path / "state.sqlite")
    store.save_run(
        AgentRunRecord(
            run_id="materialize-run",
            workflow="discovery",
            request={"scientific_constraints": _constraints()},
            current_manifest_path=str(manifest_path),
            publication_evidence_store=_evidence_store(),
            publication_membership_refs=["membership:A:A1"],
            publication_builder_entrypoint="dataset-builder/v1",
            publication_builder_preflight_status="pending",
            publication_builder_preflight_ref=(
                "builder-preflight:sha256:" + "b" * 64
            ),
        )
    )

    _persist_discovery_audit_snapshot(
        _StableAuditService(store=store, run_id="materialize-run", audit=audit),
        audit,
    )

    persisted = store.load_run("materialize-run")
    assert persisted is not None
    assert persisted.build_ready_package_material is None
    assert "materialization_builder_preflight_not_ready" in (
        persisted.publication_materialization_blockers
    )
    assert persisted.publication_authority is None
    assert persisted.business_completion is not None
    assert persisted.business_completion.succeeded is False
