from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from agent.discovery.constraints import (
    ConstraintScope,
    ScientificConstraint,
    evaluate_constraint_value,
    normalize_constraint_bindings,
)
from agent.models import JsonModel
from agent.discovery.production_authority import (
    DurableAuthorityLedger,
    ProductionPublicationSigner,
    ProductionPublicationVerifier,
    authority_mode,
    publication_completion_context_digest,
    repair_completion_context_digest,
    repair_completion_context_token,
    sha256_digest,
)


BusinessCompletionStatus = Literal[
    "blocked",
    "blocked_with_progress",
    "running_progress",
    "build_ready_succeeded",
]
PublicationPackageKind = Literal["progress", "build_ready"]
_ISSUANCE_KEY = secrets.token_bytes(32)
_AUTHORITY_PUBLIC_EXPONENT = 65537
_AUTHORITY_PUBLIC_MODULUS = int(
    "2021929587445105315850592257869782914013618933887444656198791518505395907614"
    "1487102858679934784720652168314162799289941258227975740085968709942735057500"
    "8781849891840716027319212186415737829073473660648717703622492243190458917184"
    "3820489258482294405357617982825178327414378941066858277057754760838061433376"
    "0683079721193167627008291609374605787156211459891707011298022556767743449895"
    "3177194106300681796207830869316710281486572505292993156294774760283310547368"
    "9246133284055140555400966461359517109286443052600847656593417445253837786491"
    "2709024809083218758596919105806370735577477073231581206848847098230595264005"
    "569745931"
)
_SHA256_DIGEST_INFO_PREFIX = bytes.fromhex(
    "3031300d060960864801650304020105000420"
)
_DEV_AUTHORITY_PUBLIC_KEYS: dict[str, Any] = {}


class BuildReadyConstraintEvidence(JsonModel):
    """Authority-materialized evidence for one exact constraint binding."""

    observation_id: str = Field(min_length=1, max_length=160)
    constraint_id: str = Field(min_length=1, max_length=96)
    dimension: str = Field(min_length=1, max_length=120)
    scope: ConstraintScope
    operator: str = Field(min_length=1, max_length=64)
    observed_value: Any
    source_refs: list[str] = Field(min_length=1, max_length=50)

    @field_validator(
        "observation_id",
        "constraint_id",
        "dimension",
        "operator",
    )
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return " ".join(str(value or "").split()).strip()

    @field_validator("source_refs")
    @classmethod
    def normalize_refs(cls, values: list[str]) -> list[str]:
        refs = list(
            dict.fromkeys(
                " ".join(str(value or "").split()).strip()[:300]
                for value in values
                if str(value or "").strip()
            )
        )
        if not refs:
            raise ValueError("constraint evidence requires verified source_refs")
        return refs

    @model_validator(mode="after")
    def validate_normalized_identity(self) -> "BuildReadyConstraintEvidence":
        if not all(
            (
                self.observation_id,
                self.constraint_id,
                self.dimension,
                self.operator,
            )
        ):
            raise ValueError("constraint evidence identity must remain non-empty")
        return self


class AuthorityEvidenceObservation(JsonModel):
    """Observation already materialized in the Authority-owned EvidenceStore."""

    observation_id: str = Field(min_length=1, max_length=160)
    dimension: str = Field(min_length=1, max_length=120)
    scope: ConstraintScope
    observed_value: Any
    source_refs: list[str] = Field(min_length=1, max_length=50)

    @field_validator("observation_id", "dimension")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return " ".join(str(value or "").split()).strip()

    @field_validator("source_refs")
    @classmethod
    def normalize_refs(cls, values: list[str]) -> list[str]:
        refs = list(
            dict.fromkeys(
                " ".join(str(value or "").split()).strip()[:300]
                for value in values
                if str(value or "").strip()
            )
        )
        if not refs:
            raise ValueError("Authority observation requires source_refs")
        return refs


class PublicationAuthorityState(JsonModel):
    """Trusted artifact inventory populated by the deterministic plane."""

    schema_version: Literal["publication-authority-state/v1"] = (
        "publication-authority-state/v1"
    )
    issued_run_ids: list[str] = Field(min_length=1, max_length=1000)
    verified_audit_refs: list[str] = Field(min_length=1, max_length=1000)
    verified_manifest_refs: list[str] = Field(min_length=1, max_length=1000)
    verified_evidence_store_refs: list[str] = Field(min_length=1, max_length=1000)
    compatible_builder_entrypoints: list[str] = Field(min_length=1, max_length=100)
    verified_membership_refs: list[str] = Field(min_length=1, max_length=100000)
    observations: list[AuthorityEvidenceObservation] = Field(
        min_length=1,
        max_length=100000,
    )
    authorized_package_digest: str = Field(
        min_length=71,
        max_length=71,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    authority_mode: Literal["legacy", "dev", "production"] = "legacy"
    key_id: str | None = Field(default=None, max_length=200)
    issuance_token: str = Field(min_length=1, max_length=1000)

    @field_validator(
        "issued_run_ids",
        "verified_audit_refs",
        "verified_manifest_refs",
        "verified_evidence_store_refs",
        "compatible_builder_entrypoints",
        "verified_membership_refs",
    )
    @classmethod
    def normalize_refs(cls, values: list[str]) -> list[str]:
        refs = list(
            dict.fromkeys(
                " ".join(str(value or "").split()).strip()[:500]
                for value in values
                if str(value or "").strip()
            )
        )
        if not refs:
            raise ValueError("Authority inventory refs must remain non-empty")
        return refs

    @model_validator(mode="after")
    def validate_observation_ids(self) -> "PublicationAuthorityState":
        ids = [item.observation_id for item in self.observations]
        if len(ids) != len(set(ids)):
            raise ValueError("Authority observation ids must be unique")
        if self.authority_mode in {"dev", "production"} and not self.key_id:
            raise ValueError("signed Authority state requires key_id")
        return self


class BuildReadyFile(JsonModel):
    """One file that has passed the dataset-builder entry boundary."""

    file_id: str = Field(min_length=1, max_length=300)
    project_id: str = Field(min_length=1, max_length=240)
    download_url: str = Field(min_length=1, max_length=2000)
    expected_size_bytes: int = Field(gt=0)
    file_role: Literal["raw_acquisition", "converted_peaklist"]
    validity_status: Literal["valid"] = "valid"
    needs_review: Literal[False] = False
    evidence_observation_refs: list[str] = Field(min_length=1, max_length=100)
    membership_ref: str = Field(min_length=1, max_length=300)

    @field_validator(
        "file_id",
        "project_id",
        "download_url",
        "membership_ref",
    )
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return " ".join(str(value or "").split()).strip()

    @field_validator("evidence_observation_refs")
    @classmethod
    def normalize_evidence_refs(cls, values: list[str]) -> list[str]:
        refs = list(
            dict.fromkeys(
                " ".join(str(value or "").split()).strip()[:300]
                for value in values
                if str(value or "").strip()
            )
        )
        if not refs:
            raise ValueError("build-ready file requires evidence observation refs")
        return refs

    @model_validator(mode="after")
    def validate_normalized_identity(self) -> "BuildReadyFile":
        if not all(
            (
                self.file_id,
                self.project_id,
                self.download_url,
                self.membership_ref,
            )
        ):
            raise ValueError("build-ready file identity/provenance must be non-empty")
        return self


class BuildReadyPackage(JsonModel):
    """The only package kind that can graduate discovery into dataset build."""

    schema_version: Literal["discovery-build-ready-package/v1"] = (
        "discovery-build-ready-package/v1"
    )
    package_id: str = Field(min_length=1, max_length=160)
    authority: Literal["publication_contract_registry"]
    authority_run_id: str = Field(min_length=1, max_length=160)
    audit_ref: str = Field(min_length=1, max_length=300)
    manifest_ref: str = Field(min_length=1, max_length=500)
    evidence_store_ref: str = Field(min_length=1, max_length=500)
    builder_entrypoint: str = Field(min_length=1, max_length=300)
    builder_preflight_ref: str | None = Field(default=None, max_length=500)
    validated: Literal[True]
    builder_compatible: Literal[True]
    project_ids: list[str] = Field(min_length=1, max_length=10000)
    files: list[BuildReadyFile] = Field(min_length=1, max_length=100000)
    constraint_evidence: list[BuildReadyConstraintEvidence] = Field(
        default_factory=list,
        max_length=1000,
    )
    unresolved: list[str] = Field(default_factory=list, max_length=10000)
    excluded: list[str] = Field(default_factory=list, max_length=10000)

    @field_validator(
        "package_id",
        "authority_run_id",
        "audit_ref",
        "manifest_ref",
        "evidence_store_ref",
        "builder_entrypoint",
    )
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return " ".join(str(value or "").split()).strip()

    @field_validator("builder_preflight_ref")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        normalized = " ".join(str(value or "").split()).strip()
        return normalized or None

    @field_validator("project_ids", "unresolved", "excluded")
    @classmethod
    def normalize_string_lists(cls, values: list[str]) -> list[str]:
        return list(
            dict.fromkeys(
                " ".join(str(value or "").split()).strip()[:300]
                for value in values
                if str(value or "").strip()
            )
        )

    @model_validator(mode="after")
    def validate_package_boundary(self) -> "BuildReadyPackage":
        if not all(
            (
                self.package_id,
                self.authority_run_id,
                self.audit_ref,
                self.manifest_ref,
                self.evidence_store_ref,
                self.builder_entrypoint,
            )
        ):
            raise ValueError("build-ready package provenance must be non-empty")
        if not self.project_ids:
            raise ValueError("build-ready package requires project_ids")
        if self.unresolved:
            raise ValueError("build-ready package cannot contain unresolved items")
        known_projects = set(self.project_ids)
        file_projects = {item.project_id for item in self.files}
        if not file_projects.issubset(known_projects):
            raise ValueError("build-ready files must belong to package project_ids")
        if not known_projects.issubset(file_projects):
            raise ValueError("every build-ready project requires at least one file")
        file_ids = [item.file_id for item in self.files]
        if len(file_ids) != len(set(file_ids)):
            raise ValueError("build-ready file ids must be unique")
        return self


class PublicationProgress(JsonModel):
    candidate_projects: int = Field(default=0, ge=0)
    candidate_files: int = Field(default=0, ge=0)
    reviewed_projects: int = Field(default=0, ge=0)
    judgment_qualified_projects: int = Field(default=0, ge=0)
    build_ready_projects: int = Field(default=0, ge=0)
    build_ready_files: int = Field(default=0, ge=0)
    blocker_counts: dict[str, int] = Field(default_factory=dict)


class BusinessCompletionDecision(JsonModel):
    """Authority decision; only a verified BuildReadyPackage can succeed."""

    schema_version: str = "business-completion/v2"
    authority_source: Literal["publication_contract_registry"] = (
        "publication_contract_registry"
    )
    succeeded: bool
    status: BusinessCompletionStatus
    package_kind: PublicationPackageKind
    progress_visible: bool
    progress: PublicationProgress
    build_ready_package: BuildReadyPackage | None = None
    issuance_token: str | None = None
    repair_authority_id: str | None = None
    repair_attempt_id: str | None = None
    repair_attempt_nonce: str | None = None
    limitations: list[str] = Field(default_factory=list)
    success_ui_allowed: bool = False


class BuildReadyMaterializationResult(JsonModel):
    """Deterministic package material, never an Authority success decision."""

    package: BuildReadyPackage | None = None
    evidence_observations: list[AuthorityEvidenceObservation] = Field(
        default_factory=list
    )
    membership_refs: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    ready_for_authority_signing: bool = False


def materialize_build_ready_package(
    snapshot: Mapping[str, Any],
) -> BuildReadyMaterializationResult:
    """Build canonical package material from typed, deterministic run state.

    This function does not sign or graduate anything. Any missing audit,
    builder-preflight, membership, file, or hard-constraint evidence returns
    blockers and no package.
    """

    from agent.discovery.evidence_store import EvidenceStoreArtifact
    from agent.discovery.models import DatasetManifest

    blockers: list[str] = []
    run_id = str(snapshot.get("run_id") or "").strip()
    if not run_id:
        blockers.append("materialization_run_id_missing")

    audit = _as_mapping(snapshot.get("audit"))
    audit_status = str(audit.get("status") or "").strip().casefold()
    audit_run_id = str(audit.get("run_id") or "").strip()
    audit_ready = audit_status == "ready" and bool(
        audit.get("ready_for_selection")
    )
    audit_ref = str(audit.get("ref") or "").strip()
    if not audit_ready:
        blockers.append("materialization_audit_not_ready")
    if not audit_ref:
        blockers.append("materialization_audit_ref_missing")
    if audit_run_id and audit_run_id != run_id:
        blockers.append("materialization_audit_run_mismatch")

    try:
        manifest = DatasetManifest.model_validate(snapshot.get("manifest"))
    except Exception:
        manifest = None
        blockers.append("materialization_manifest_missing_or_invalid")

    try:
        evidence_store = EvidenceStoreArtifact.model_validate(
            snapshot.get("evidence_store")
        )
    except Exception:
        evidence_store = None
        blockers.append("materialization_evidence_store_missing_or_invalid")

    builder = _as_mapping(snapshot.get("builder"))
    builder_entrypoint = str(builder.get("entrypoint") or "").strip()
    builder_preflight_status = str(
        builder.get("preflight_status") or ""
    ).strip().casefold()
    builder_preflight_ref = str(builder.get("preflight_ref") or "").strip()
    if not builder_entrypoint:
        blockers.append("materialization_builder_entrypoint_missing")
    if builder_preflight_status not in {"ok", "ready"}:
        blockers.append("materialization_builder_preflight_not_ready")
    if not builder_preflight_ref:
        blockers.append("materialization_builder_preflight_ref_missing")

    membership_inventory = set(
        _string_list(snapshot.get("available_membership_refs"))
    )
    if not membership_inventory:
        blockers.append("materialization_membership_inventory_missing")

    raw_constraints = snapshot.get("constraints")
    invalid_constraints = _invalid_hard_constraint_inputs(raw_constraints)
    blockers.extend(
        f"materialization_{value}" for value in invalid_constraints
    )
    bindings = normalize_constraint_bindings(raw_constraints)

    if manifest is None or evidence_store is None:
        return BuildReadyMaterializationResult(
            blockers=list(dict.fromkeys(blockers))
        )
    if not manifest.projects:
        blockers.append("materialization_manifest_has_no_projects")
    if not manifest.files:
        blockers.append("materialization_manifest_has_no_files")
    if manifest.run_id and manifest.run_id != run_id:
        blockers.append("materialization_manifest_run_mismatch")

    observations = evidence_store.observations
    authority_observations = [
        AuthorityEvidenceObservation(
            observation_id=item.observation_id,
            dimension=item.dimension,
            scope=item.evidence_scope,
            observed_value=item.observed_value,
            source_refs=item.source_refs,
        )
        for item in observations
    ]
    known_projects = {item.project_accession for item in manifest.projects}
    package_files: list[BuildReadyFile] = []
    used_observation_ids: set[str] = set()
    used_memberships: set[str] = set()

    for item in manifest.files:
        file_id = str(
            item.file_accession_or_path
            or f"{item.project_accession}:{item.file_name}"
        ).strip()
        prefix = f"materialization_file:{file_id}:"
        if item.project_accession not in known_projects:
            blockers.append(prefix + "project_missing")
        if item.validity_status != "valid":
            blockers.append(prefix + "validity_not_valid")
        if item.needs_review:
            blockers.append(prefix + "needs_review")
        if not str(item.download_url or "").strip():
            blockers.append(prefix + "download_url_missing")
        if not item.expected_size_bytes or item.expected_size_bytes <= 0:
            blockers.append(prefix + "expected_size_missing")
        if item.file_role not in {"raw_acquisition", "converted_peaklist"}:
            blockers.append(prefix + "file_role_unsupported")

        file_observations = [
            observation
            for observation in observations
            if observation.subject_kind == "file"
            and observation.subject_id == file_id
            and observation.evidence_scope == "file"
            and observation.dimension == "builder_file_entry"
        ]
        if not file_observations:
            blockers.append(prefix + "evidence_missing")
        membership_candidates = sorted(
            {
                membership
                for observation in file_observations
                for membership in observation.membership_refs
                if membership in membership_inventory
            }
        )
        if not membership_candidates:
            blockers.append(prefix + "membership_missing")

        file_blocked = any(value.startswith(prefix) for value in blockers)
        if file_blocked:
            continue
        observation_ids = sorted(
            {observation.observation_id for observation in file_observations}
        )
        used_observation_ids.update(observation_ids)
        membership_ref = membership_candidates[0]
        used_memberships.add(membership_ref)
        package_files.append(
            BuildReadyFile(
                file_id=file_id,
                project_id=item.project_accession,
                download_url=str(item.download_url),
                expected_size_bytes=int(item.expected_size_bytes or 0),
                file_role=item.file_role,
                evidence_observation_refs=observation_ids,
                membership_ref=membership_ref,
            )
        )

    packaged_project_ids = {item.project_id for item in package_files}
    for project in manifest.projects:
        if project.project_accession not in packaged_project_ids:
            blockers.append(
                "materialization_project_has_no_build_ready_file:"
                + project.project_accession
            )

    constraint_evidence: list[BuildReadyConstraintEvidence] = []
    for binding in bindings:
        if binding.strength != "hard":
            continue
        matching = [
            observation
            for observation in observations
            if observation.dimension == binding.dimension
            and observation.evidence_scope == binding.scope
        ]
        passing = next(
            (
                observation
                for observation in matching
                if evaluate_constraint_value(binding, observation.observed_value)
                is True
            ),
            None,
        )
        if passing is None:
            outcome = (
                "conflict"
                if any(
                    evaluate_constraint_value(binding, item.observed_value)
                    is False
                    for item in matching
                )
                else "unknown"
            )
            blockers.append(
                f"materialization_hard_constraint_{outcome}:{binding.id}"
            )
            continue
        used_observation_ids.add(passing.observation_id)
        constraint_evidence.append(
            BuildReadyConstraintEvidence(
                observation_id=passing.observation_id,
                constraint_id=binding.id,
                dimension=binding.dimension,
                scope=binding.scope,
                operator=binding.operator,
                observed_value=passing.observed_value,
                source_refs=passing.source_refs,
            )
        )

    blockers = list(dict.fromkeys(blockers))
    if blockers:
        return BuildReadyMaterializationResult(
            evidence_observations=[
                item
                for item in authority_observations
                if item.observation_id in used_observation_ids
            ],
            membership_refs=sorted(used_memberships),
            blockers=blockers,
        )

    manifest_payload = manifest.model_dump(mode="json")
    evidence_payload = evidence_store.model_dump(mode="json")
    manifest_ref = _canonical_material_ref("manifest", manifest_payload)
    evidence_store_ref = _canonical_material_ref(
        "evidence-store", evidence_payload
    )
    project_ids = list(
        dict.fromkeys(item.project_id for item in package_files)
    )
    package_identity = {
        "run_id": run_id,
        "audit_ref": audit_ref,
        "manifest_ref": manifest_ref,
        "evidence_store_ref": evidence_store_ref,
        "builder_entrypoint": builder_entrypoint,
        "builder_preflight_ref": builder_preflight_ref,
    }
    package = BuildReadyPackage(
        package_id=_canonical_material_ref("package", package_identity),
        authority="publication_contract_registry",
        authority_run_id=run_id,
        audit_ref=audit_ref,
        manifest_ref=manifest_ref,
        evidence_store_ref=evidence_store_ref,
        builder_entrypoint=builder_entrypoint,
        builder_preflight_ref=builder_preflight_ref,
        validated=True,
        builder_compatible=True,
        project_ids=project_ids,
        files=package_files,
        constraint_evidence=constraint_evidence,
    )
    return BuildReadyMaterializationResult(
        package=package,
        evidence_observations=[
            item
            for item in authority_observations
            if item.observation_id in used_observation_ids
        ],
        membership_refs=sorted(used_memberships),
        ready_for_authority_signing=True,
    )


def _canonical_material_ref(prefix: str, value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{prefix}:sha256:{hashlib.sha256(encoded).hexdigest()}"


class PublicationContractRegistry:
    """Validate progress and the sole build-ready graduation package."""

    def __init__(
        self,
        *,
        production_verifier: ProductionPublicationVerifier | None = None,
        ledger: DurableAuthorityLedger | None = None,
    ) -> None:
        self.production_verifier = production_verifier
        self.ledger = ledger
        if authority_mode() == "production":
            self.production_verifier = self.production_verifier or (
                ProductionPublicationVerifier.from_environment(required=False)
            )
            self.ledger = self.ledger or DurableAuthorityLedger.from_environment(
                required=False
            )

    def evaluate(self, snapshot: Mapping[str, Any]) -> BusinessCompletionDecision:
        request = _as_mapping(snapshot.get("request"))
        state = _as_mapping(snapshot.get("state"))
        raw_constraints = request.get("constraints")
        bindings = normalize_constraint_bindings(raw_constraints)
        invalid_hard_bindings = _invalid_hard_constraint_inputs(raw_constraints)
        completion_context = _as_mapping(snapshot.get("completion_context"))
        repair_authority_id = str(
            completion_context.get("repair_authority_id") or ""
        ).strip() or None
        repair_attempt_id = str(
            completion_context.get("repair_attempt_id") or ""
        ).strip() or None
        repair_attempt_nonce = str(
            completion_context.get("repair_attempt_nonce") or ""
        ).strip() or None
        if len(
            {
                bool(repair_authority_id),
                bool(repair_attempt_id),
                bool(repair_attempt_nonce),
            }
        ) != 1:
            repair_authority_id = None
            repair_attempt_id = None
            repair_attempt_nonce = None

        audit_status = str(state.get("latest_audit_status") or "").strip().casefold()
        audit_ref = str(state.get("latest_audit_ref") or "").strip()
        audit_ready = audit_status == "ready" and bool(audit_ref)
        package, package_limitations = _validated_package(
            state.get("validated_build_ready_package"),
            audit_ref=audit_ref,
            audit_ready=audit_ready,
            state=state,
            authority_value=state.get("publication_authority"),
            production_verifier=self.production_verifier,
            ledger=self.ledger,
        )

        hard_conflicts, hard_unknowns = _hard_constraint_blockers(
            bindings,
            state,
            package,
        )
        hard_unknowns = list(
            dict.fromkeys([*invalid_hard_bindings, *hard_unknowns])
        )
        blocker_counts = _count_mapping(state.get("blocker_counts"))
        if hard_conflicts:
            blocker_counts["hard_conflicts"] = len(hard_conflicts)
        if hard_unknowns:
            blocker_counts["hard_unknowns"] = len(hard_unknowns)

        build_ready_projects = len(package.project_ids) if package is not None else 0
        build_ready_files = len(package.files) if package is not None else 0
        progress = PublicationProgress(
            candidate_projects=_max_count(state, "candidate_projects"),
            candidate_files=_max_count(state, "candidate_files"),
            reviewed_projects=_max_count(
                state,
                "reviewed_projects",
                "assessable_inspections",
            ),
            judgment_qualified_projects=_max_count(
                state,
                "judgment_qualified_projects",
                "qualified_projects",
            ),
            build_ready_projects=build_ready_projects,
            build_ready_files=build_ready_files,
            blocker_counts=blocker_counts,
        )
        missing_fields = _string_list(state.get("missing_build_ready_fields"))
        succeeded = bool(
            package is not None
            and build_ready_projects > 0
            and build_ready_files > 0
            and not missing_fields
            and not hard_conflicts
            and not hard_unknowns
            and audit_ready
        )
        progress_visible = any(
            (
                progress.candidate_projects,
                progress.candidate_files,
                progress.reviewed_projects,
                progress.judgment_qualified_projects,
                progress.build_ready_projects,
                progress.build_ready_files,
            )
        )
        limitations = [
            *package_limitations,
            *(f"missing_build_ready_field:{field}" for field in missing_fields),
            *(f"hard_conflict:{value}" for value in hard_conflicts),
            *(f"hard_unknown:{value}" for value in hard_unknowns),
        ]
        if not audit_status:
            limitations.append("audit_missing")
        elif audit_status != "ready":
            limitations.append(f"audit_not_ready:{audit_status}")
        elif not audit_ref:
            limitations.append("audit_provenance_missing")

        if succeeded:
            status: BusinessCompletionStatus = "build_ready_succeeded"
            package_kind: PublicationPackageKind = "build_ready"
        elif progress_visible:
            status = (
                "running_progress"
                if audit_status in {"running", "searching", "inspecting"}
                else "blocked_with_progress"
            )
            package_kind = "progress"
        else:
            status = "blocked"
            package_kind = "progress"

        decision = BusinessCompletionDecision(
            succeeded=succeeded,
            status=status,
            package_kind=package_kind,
            progress_visible=progress_visible,
            progress=progress,
            build_ready_package=package if succeeded else None,
            repair_authority_id=repair_authority_id,
            repair_attempt_id=repair_attempt_id,
            repair_attempt_nonce=repair_attempt_nonce,
            limitations=list(dict.fromkeys(limitations)),
            success_ui_allowed=succeeded,
        )
        if succeeded:
            authority = PublicationAuthorityState.model_validate(
                state.get("publication_authority")
            )
            if authority.authority_mode == "production":
                if self.ledger is None:
                    return decision.model_copy(
                        update={
                            "succeeded": False,
                            "status": "blocked_with_progress",
                            "package_kind": "progress",
                            "build_ready_package": None,
                            "success_ui_allowed": False,
                            "limitations": [
                                *decision.limitations,
                                "production_authority_ledger_unavailable",
                            ],
                        }
                    )
                if not all(
                    (
                        repair_authority_id,
                        repair_attempt_id,
                        repair_attempt_nonce,
                    )
                ):
                    return decision.model_copy(
                        update={
                            "succeeded": False,
                            "status": "blocked_with_progress",
                            "package_kind": "progress",
                            "build_ready_package": None,
                            "success_ui_allowed": False,
                            "limitations": [
                                *decision.limitations,
                                "production_completion_context_missing",
                            ],
                        }
                    )
                context_token = repair_completion_context_token(
                    repair_authority_id,
                    repair_attempt_id,
                )
                context_binding = {
                    "authority_id": repair_authority_id,
                    "attempt_id": repair_attempt_id,
                    "nonce": repair_attempt_nonce,
                }
                if repair_authority_id.startswith("publication-authority:"):
                    package_digest = _package_digest(package)
                    context_digest = publication_completion_context_digest(
                        repair_authority_id,
                        repair_attempt_id,
                        repair_attempt_nonce,
                        package.authority_run_id,
                        package.audit_ref,
                        package_digest,
                    )
                    context_binding.update(
                        {
                            "run_id": package.authority_run_id,
                            "audit_ref": package.audit_ref,
                            "package_digest": package_digest,
                        }
                    )
                else:
                    context_digest = repair_completion_context_digest(
                        repair_authority_id,
                        repair_attempt_id,
                        repair_attempt_nonce,
                    )
                if not self.ledger.verify(
                    "repair_completion_context",
                    context_token,
                    context_digest,
                    binding=context_binding,
                    allow_consumed=False,
                ):
                    return decision.model_copy(
                        update={
                            "succeeded": False,
                            "status": "blocked_with_progress",
                            "package_kind": "progress",
                            "build_ready_package": None,
                            "success_ui_allowed": False,
                            "limitations": [
                                *decision.limitations,
                                "production_completion_context_unissued",
                            ],
                        }
                    )
                digest = _completion_digest(decision)
                token = "durable-completion:" + hashlib.sha256(
                    f"{context_token}:{digest}".encode("utf-8")
                ).hexdigest()
                completion_binding = {
                    "run_id": package.authority_run_id,
                    "audit_ref": package.audit_ref,
                    "package_digest": _package_digest(package),
                    "key_id": authority.key_id or "",
                    "repair_authority_id": repair_authority_id,
                    "repair_attempt_id": repair_attempt_id,
                    "repair_attempt_nonce": repair_attempt_nonce,
                }
                reserved = self.ledger.reserve(
                    "business_completion",
                    token,
                    digest,
                    binding=completion_binding,
                )
                if not reserved and not self.ledger.verify(
                    "business_completion",
                    token,
                    digest,
                    binding=completion_binding,
                    allow_consumed=False,
                ):
                    return decision.model_copy(
                        update={
                            "succeeded": False,
                            "status": "blocked_with_progress",
                            "package_kind": "progress",
                            "build_ready_package": None,
                            "success_ui_allowed": False,
                            "limitations": [
                                *decision.limitations,
                                "production_completion_already_consumed",
                            ],
                        }
                    )
                decision = decision.model_copy(update={"issuance_token": token})
            else:
                decision = decision.model_copy(
                    update={"issuance_token": _completion_seal(decision)}
                )
        return decision


def _validated_package(
    value: Any,
    *,
    audit_ref: str,
    audit_ready: bool,
    state: Mapping[str, Any],
    authority_value: Any,
    production_verifier: ProductionPublicationVerifier | None = None,
    ledger: DurableAuthorityLedger | None = None,
) -> tuple[BuildReadyPackage | None, list[str]]:
    if value is None:
        return None, ["build_ready_package_missing"]
    if not audit_ready:
        return None, ["build_ready_package_audit_not_ready"]
    try:
        authority = (
            authority_value
            if isinstance(authority_value, PublicationAuthorityState)
            else PublicationAuthorityState.model_validate(authority_value)
        )
    except (TypeError, ValueError, ValidationError):
        return None, ["publication_authority_state_missing_or_invalid"]
    if not _verify_publication_authority_state(
        authority,
        production_verifier=production_verifier,
        ledger=ledger,
    ):
        return None, ["publication_authority_state_unissued"]
    try:
        package = (
            value
            if isinstance(value, BuildReadyPackage)
            else BuildReadyPackage.model_validate(value)
        )
    except (TypeError, ValueError, ValidationError):
        return None, ["build_ready_package_invalid"]
    if package.audit_ref != audit_ref:
        return None, ["build_ready_package_audit_mismatch"]
    if authority.authorized_package_digest != _package_digest(package):
        return None, ["build_ready_package_material_mismatch"]
    authority_checks = (
        (package.authority_run_id, authority.issued_run_ids, "run"),
        (package.audit_ref, authority.verified_audit_refs, "audit"),
        (package.manifest_ref, authority.verified_manifest_refs, "manifest"),
        (
            package.evidence_store_ref,
            authority.verified_evidence_store_refs,
            "evidence_store",
        ),
        (
            package.builder_entrypoint,
            authority.compatible_builder_entrypoints,
            "builder_entrypoint",
        ),
    )
    for ref, verified_refs, kind in authority_checks:
        if ref not in verified_refs:
            return None, [f"build_ready_package_unverified_ref:{kind}"]

    observations = {
        item.observation_id: item for item in authority.observations
    }
    verified_memberships = set(authority.verified_membership_refs)
    for file in package.files:
        if file.membership_ref not in verified_memberships:
            return None, ["build_ready_package_unverified_membership"]
        if any(
            observation_ref not in observations
            for observation_ref in file.evidence_observation_refs
        ):
            return None, ["build_ready_package_unverified_file_observation"]
    for evidence in package.constraint_evidence:
        observation = observations.get(evidence.observation_id)
        if observation is None:
            return None, ["build_ready_package_unverified_constraint_observation"]
        if (
            observation.dimension != evidence.dimension
            or observation.scope != evidence.scope
            or observation.observed_value != evidence.observed_value
            or set(observation.source_refs) != set(evidence.source_refs)
        ):
            return None, ["build_ready_package_constraint_observation_mismatch"]

    project_count = len(package.project_ids)
    file_count = len(package.files)
    for key in ("build_ready_count", "build_ready_projects"):
        if key in state and _safe_count(state.get(key)) != project_count:
            return None, [f"build_ready_package_count_mismatch:{key}"]
    if "build_ready_files" in state and _safe_count(
        state.get("build_ready_files")
    ) != file_count:
        return None, ["build_ready_package_count_mismatch:build_ready_files"]
    return package, []


def _package_digest(package: BuildReadyPackage) -> str:
    encoded = json.dumps(
        package.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def canonical_package_digest(
    package: BuildReadyPackage | Mapping[str, Any],
) -> str:
    """Return the publication contract's canonical digest for builder handoff."""

    normalized = (
        package
        if isinstance(package, BuildReadyPackage)
        else BuildReadyPackage.model_validate(package)
    )
    return _package_digest(normalized)


def verify_business_completion_issuance(
    decision: BusinessCompletionDecision,
    *,
    ledger: DurableAuthorityLedger | None = None,
) -> bool:
    token = str(decision.issuance_token or "")
    configured_mode = authority_mode()
    if configured_mode == "invalid":
        return False
    if token.startswith("durable-completion:"):
        active_ledger = ledger or DurableAuthorityLedger.from_environment(required=False)
        return bool(
            active_ledger is not None
            and active_ledger.verify(
                "business_completion",
                token,
                _completion_digest(decision),
            )
        )
    if configured_mode == "production":
        # A completion issued by the legacy in-process HMAC trust domain never
        # becomes production merely because the process mode changed later.
        return False
    return bool(token) and hmac.compare_digest(token, _completion_seal(decision))


def business_completion_allows_success(
    value: Any,
    *,
    ledger: DurableAuthorityLedger | None = None,
) -> bool:
    """Apply the sole business-graduation gate to an arbitrary value."""

    return bool(
        isinstance(value, BusinessCompletionDecision)
        and value.succeeded
        and value.status == "build_ready_succeeded"
        and value.package_kind == "build_ready"
        and value.success_ui_allowed
        and value.build_ready_package is not None
        and value.progress.build_ready_projects > 0
        and value.progress.build_ready_files > 0
        and verify_business_completion_issuance(value, ledger=ledger)
    )


def dev_publication_signing_enabled() -> bool:
    return str(os.getenv("DISCOVERY_AUTHORITY_DEV_SIGN") or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def issue_dev_publication_authority(
    package: BuildReadyPackage | Mapping[str, Any],
    *,
    observations: Sequence[AuthorityEvidenceObservation | Mapping[str, Any]],
    verified_membership_refs: Sequence[str],
    allow_dev_signing: bool = False,
) -> PublicationAuthorityState:
    """Issue an in-process development inventory; never enabled by default.

    The private key is generated per process, or loaded from an explicitly
    supplied Ed25519 PEM environment value. Only the public verifier is retained
    in the process registry used by this module; no key material is serialized.
    """

    if not allow_dev_signing and not dev_publication_signing_enabled():
        raise PermissionError("development publication signing is not explicitly enabled")
    normalized_package = (
        package
        if isinstance(package, BuildReadyPackage)
        else BuildReadyPackage.model_validate(package)
    )
    normalized_observations = [
        value
        if isinstance(value, AuthorityEvidenceObservation)
        else AuthorityEvidenceObservation.model_validate(value)
        for value in observations
    ]
    membership_refs = list(
        dict.fromkeys(
            " ".join(str(value or "").split()).strip()[:500]
            for value in verified_membership_refs
            if str(value or "").strip()
        )
    )
    observation_ids = {item.observation_id for item in normalized_observations}
    if not normalized_observations or not membership_refs:
        raise ValueError("development authority requires evidence and membership material")
    for file in normalized_package.files:
        if file.membership_ref not in membership_refs:
            raise ValueError("development authority membership material is incomplete")
        if any(ref not in observation_ids for ref in file.evidence_observation_refs):
            raise ValueError("development authority file evidence material is incomplete")
    if any(
        evidence.observation_id not in observation_ids
        for evidence in normalized_package.constraint_evidence
    ):
        raise ValueError("development authority constraint evidence material is incomplete")

    private_key, public_key, key_id = _dev_ed25519_keypair()
    authority = PublicationAuthorityState(
        issued_run_ids=[normalized_package.authority_run_id],
        verified_audit_refs=[normalized_package.audit_ref],
        verified_manifest_refs=[normalized_package.manifest_ref],
        verified_evidence_store_refs=[normalized_package.evidence_store_ref],
        compatible_builder_entrypoints=[normalized_package.builder_entrypoint],
        verified_membership_refs=membership_refs,
        observations=normalized_observations,
        authorized_package_digest=_package_digest(normalized_package),
        authority_mode="dev",
        key_id=key_id,
        issuance_token="pending-dev-signature",
    )
    signature = private_key.sign(_authority_payload_bytes(authority))
    _DEV_AUTHORITY_PUBLIC_KEYS[key_id] = public_key
    token = "dev-ed25519:" + key_id + ":" + base64.urlsafe_b64encode(
        signature
    ).decode("ascii")
    return authority.model_copy(
        update={"issuance_token": token, "key_id": key_id}
    )


def issue_production_publication_authority(
    package: BuildReadyPackage | Mapping[str, Any],
    *,
    observations: Sequence[AuthorityEvidenceObservation | Mapping[str, Any]],
    verified_membership_refs: Sequence[str],
    signer: ProductionPublicationSigner,
    verifier: ProductionPublicationVerifier,
    ledger: DurableAuthorityLedger,
) -> PublicationAuthorityState:
    """Issue through an external signer and durable ledger; never uses dev keys."""

    normalized_package, normalized_observations, membership_refs = (
        _validated_authority_material(
            package,
            observations=observations,
            verified_membership_refs=verified_membership_refs,
        )
    )
    package_digest = _package_digest(normalized_package)
    authority = PublicationAuthorityState(
        issued_run_ids=[normalized_package.authority_run_id],
        verified_audit_refs=[normalized_package.audit_ref],
        verified_manifest_refs=[normalized_package.manifest_ref],
        verified_evidence_store_refs=[normalized_package.evidence_store_ref],
        compatible_builder_entrypoints=[normalized_package.builder_entrypoint],
        verified_membership_refs=membership_refs,
        observations=normalized_observations,
        authorized_package_digest=package_digest,
        authority_mode="production",
        key_id=signer.key_id,
        issuance_token="pending-production-signature",
    )
    payload = _authority_payload_bytes(authority)
    payload_digest = sha256_digest(payload)
    if not ledger.reserve(
        "publication_package",
        package_digest,
        payload_digest,
        binding={
            "run_id": normalized_package.authority_run_id,
            "audit_ref": normalized_package.audit_ref,
            "key_id": signer.key_id,
        },
    ):
        raise ValueError("production package was already reserved or issued")
    try:
        result = signer.sign(payload, payload_digest=payload_digest)
        if not verifier.verify(
            key_id=result.key_id,
            payload=payload,
            signature=result.signature,
            allow_retired=False,
        ):
            raise ValueError("production signer result failed active-key verification")
        token = f"production-ed25519:{result.key_id}:{result.signature}"
        if not ledger.reserve(
            "publication_issuance",
            token,
            payload_digest,
            binding={
                "package_digest": package_digest,
                "key_id": result.key_id,
            },
        ):
            raise RuntimeError("production issuance token collision")
    except Exception:
        ledger.release("publication_package", package_digest, payload_digest)
        raise
    return authority.model_copy(update={"issuance_token": token})


def issue_configured_publication_authority(
    package: BuildReadyPackage | Mapping[str, Any],
    *,
    observations: Sequence[AuthorityEvidenceObservation | Mapping[str, Any]],
    verified_membership_refs: Sequence[str],
    signer: ProductionPublicationSigner | None = None,
    verifier: ProductionPublicationVerifier | None = None,
    ledger: DurableAuthorityLedger | None = None,
) -> PublicationAuthorityState:
    mode = authority_mode()
    if mode == "production":
        if signer is None or verifier is None or ledger is None:
            raise RuntimeError(
                "production Authority requires external signer, verifier, and durable ledger"
            )
        return issue_production_publication_authority(
            package,
            observations=observations,
            verified_membership_refs=verified_membership_refs,
            signer=signer,
            verifier=verifier,
            ledger=ledger,
        )
    if mode == "dev":
        return issue_dev_publication_authority(
            package,
            observations=observations,
            verified_membership_refs=verified_membership_refs,
        )
    raise PermissionError("publication Authority mode is off")


def _completion_seal(decision: BusinessCompletionDecision) -> str:
    payload = decision.model_dump(mode="json", exclude={"issuance_token"})
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "hmac-sha256:" + hmac.new(
        _ISSUANCE_KEY,
        encoded,
        hashlib.sha256,
    ).hexdigest()


def _completion_digest(decision: BusinessCompletionDecision) -> str:
    payload = decision.model_dump(mode="json", exclude={"issuance_token"})
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256_digest(encoded)


def _verify_publication_authority_state(
    authority: PublicationAuthorityState,
    *,
    production_verifier: ProductionPublicationVerifier | None = None,
    ledger: DurableAuthorityLedger | None = None,
) -> bool:
    # Runtime production mode has a single trust domain.  A dev or legacy
    # signature remains non-production even when its public key is still in
    # this process from an earlier dev-mode issuance.
    configured_mode = authority_mode()
    if configured_mode == "invalid":
        return False
    if configured_mode == "production" and authority.authority_mode != "production":
        return False
    if authority.issuance_token.startswith("dev-ed25519:"):
        return _verify_dev_publication_authority_state(authority)
    if authority.issuance_token.startswith("production-ed25519:"):
        if (
            authority.authority_mode != "production"
            or not authority.key_id
            or production_verifier is None
            or ledger is None
        ):
            return False
        try:
            _prefix, key_id, signature = authority.issuance_token.split(":", 2)
        except ValueError:
            return False
        payload = _authority_payload_bytes(authority)
        payload_digest = sha256_digest(payload)
        return bool(
            key_id == authority.key_id
            and production_verifier.verify(
                key_id=key_id,
                payload=payload,
                signature=signature,
            )
            and ledger.verify(
                "publication_issuance",
                authority.issuance_token,
                payload_digest,
                binding={
                    "package_digest": authority.authorized_package_digest,
                    "key_id": key_id,
                },
            )
        )
    try:
        signature = base64.urlsafe_b64decode(authority.issuance_token.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return False
    key_bytes = (_AUTHORITY_PUBLIC_MODULUS.bit_length() + 7) // 8
    if len(signature) != key_bytes:
        return False
    recovered = pow(
        int.from_bytes(signature, "big"),
        _AUTHORITY_PUBLIC_EXPONENT,
        _AUTHORITY_PUBLIC_MODULUS,
    ).to_bytes(key_bytes, "big")
    encoded = _authority_payload_bytes(authority)
    digest_info = _SHA256_DIGEST_INFO_PREFIX + hashlib.sha256(encoded).digest()
    padding_length = key_bytes - len(digest_info) - 3
    if padding_length < 8:
        return False
    expected = b"\x00\x01" + (b"\xff" * padding_length) + b"\x00" + digest_info
    return hmac.compare_digest(recovered, expected)


def _authority_payload_bytes(authority: PublicationAuthorityState) -> bytes:
    excluded = {"issuance_token"}
    if authority.authority_mode == "legacy":
        # Preserve verification of v1 inventories signed before mode/key_id
        # became explicit production fields.
        excluded.update({"authority_mode", "key_id"})
    payload = authority.model_dump(mode="json", exclude=excluded)
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _validated_authority_material(
    package: BuildReadyPackage | Mapping[str, Any],
    *,
    observations: Sequence[AuthorityEvidenceObservation | Mapping[str, Any]],
    verified_membership_refs: Sequence[str],
) -> tuple[BuildReadyPackage, list[AuthorityEvidenceObservation], list[str]]:
    normalized_package = (
        package
        if isinstance(package, BuildReadyPackage)
        else BuildReadyPackage.model_validate(package)
    )
    normalized_observations = [
        value
        if isinstance(value, AuthorityEvidenceObservation)
        else AuthorityEvidenceObservation.model_validate(value)
        for value in observations
    ]
    membership_refs = list(
        dict.fromkeys(
            " ".join(str(value or "").split()).strip()[:500]
            for value in verified_membership_refs
            if str(value or "").strip()
        )
    )
    observation_ids = {item.observation_id for item in normalized_observations}
    if not normalized_observations or not membership_refs:
        raise ValueError("Authority requires evidence and membership material")
    for file in normalized_package.files:
        if file.membership_ref not in membership_refs:
            raise ValueError("Authority membership material is incomplete")
        if any(ref not in observation_ids for ref in file.evidence_observation_refs):
            raise ValueError("Authority file evidence material is incomplete")
    if any(
        evidence.observation_id not in observation_ids
        for evidence in normalized_package.constraint_evidence
    ):
        raise ValueError("Authority constraint evidence material is incomplete")
    return normalized_package, normalized_observations, membership_refs


def _dev_ed25519_keypair() -> tuple[Any, Any, str]:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
    except ImportError as exc:  # pragma: no cover - environment-dependent dev extra
        raise RuntimeError("development publication signing requires cryptography") from exc

    pem = str(os.getenv("DISCOVERY_AUTHORITY_SIGNING_KEY") or "").strip()
    if pem:
        loaded = serialization.load_pem_private_key(
            pem.encode("utf-8"),
            password=None,
        )
        if not isinstance(loaded, Ed25519PrivateKey):
            raise TypeError("DISCOVERY_AUTHORITY_SIGNING_KEY must be Ed25519 PEM")
        private_key = loaded
    else:
        private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key_id = hashlib.sha256(public_bytes).hexdigest()[:32]
    return private_key, public_key, key_id


def _verify_dev_publication_authority_state(
    authority: PublicationAuthorityState,
) -> bool:
    try:
        _prefix, key_id, encoded_signature = authority.issuance_token.split(":", 2)
        signature = base64.urlsafe_b64decode(encoded_signature.encode("ascii"))
        public_key = _DEV_AUTHORITY_PUBLIC_KEYS[key_id]
        public_key.verify(signature, _authority_payload_bytes(authority))
    except Exception:
        return False
    return True


def _invalid_hard_constraint_inputs(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return ["invalid_hard_constraint_payload"]
    if len(value) > 100:
        return ["constraint_limit_exceeded"]
    invalid: list[str] = []
    id_strengths: dict[str, list[str]] = {}
    for index, raw in enumerate(value, start=1):
        if not isinstance(raw, Mapping):
            invalid.append(f"invalid_constraint:{index}")
            continue
        strength = str(raw.get("strength") or "soft").strip().casefold()
        if strength not in {"hard", "soft", "open"}:
            invalid.append(f"invalid_constraint:{index}")
            continue
        explicit_id = str(raw.get("id") or "").strip().casefold()
        normalized_any = normalize_constraint_bindings([dict(raw)])
        normalized_id = (
            normalized_any[0].id.casefold() if len(normalized_any) == 1 else ""
        )
        constraint_id = explicit_id or normalized_id
        if constraint_id:
            id_strengths.setdefault(constraint_id, []).append(strength)
        if strength != "hard":
            continue
        normalized = normalized_any
        if len(normalized) != 1 or normalized[0].strength != "hard":
            invalid.append(f"invalid_hard_constraint:{index}")
            continue
    for constraint_id, strengths in id_strengths.items():
        if "hard" in strengths and len(strengths) > 1:
            invalid.append(f"duplicate_hard_constraint:{constraint_id}")
    return invalid


def _hard_constraint_blockers(
    bindings: list[ScientificConstraint],
    state: Mapping[str, Any],
    package: BuildReadyPackage | None,
) -> tuple[list[str], list[str]]:
    conflicts = _named_state_flags(state.get("hard_conflicts"))
    unknowns = _named_state_flags(state.get("hard_unknowns"))
    claimed_assessments = _assessment_statuses(state.get("constraint_assessments"))
    observations = {
        item.constraint_id: item
        for item in (package.constraint_evidence if package is not None else [])
    }

    for binding in bindings:
        if binding.strength != "hard":
            continue
        claimed_status = claimed_assessments.get(
            binding.id,
            claimed_assessments.get(binding.dimension),
        )
        if claimed_status == "fail":
            conflicts.append(binding.dimension)
            continue
        if claimed_status in {"unknown", "partial"}:
            unknowns.append(binding.dimension)
            continue

        observation = observations.get(binding.id)
        if observation is None:
            unknowns.append(binding.dimension)
            continue
        if (
            observation.dimension != binding.dimension
            or observation.scope != binding.scope
            or _normal_operator(observation.operator)
            != _normal_operator(binding.operator)
            or not observation.source_refs
        ):
            unknowns.append(binding.dimension)
            continue
        result = evaluate_constraint_value(binding, observation.observed_value)
        if result is True:
            continue
        if result is False:
            conflicts.append(binding.dimension)
        else:
            unknowns.append(binding.dimension)
    return list(dict.fromkeys(conflicts)), list(dict.fromkeys(unknowns))


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _safe_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _max_count(
    values: Mapping[str, Any],
    *keys: str,
    default: int = 0,
) -> int:
    return max([default, *(_safe_count(values.get(key)) for key in keys)])


def _count_mapping(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): _safe_count(count)
        for key, count in value.items()
        if _safe_count(count) > 0
    }


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return list(
        dict.fromkeys(str(item).strip() for item in value if str(item).strip())
    )


def _named_state_flags(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        return [str(key) for key, present in value.items() if bool(present)]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [str(item) for item in value if str(item).strip()]
    count = _safe_count(value)
    return [f"reported_{index}" for index in range(1, count + 1)]


def _assessment_statuses(value: Any) -> dict[str, str]:
    rows: list[Mapping[str, Any]] = []
    if isinstance(value, Mapping):
        for key, status in value.items():
            if isinstance(status, Mapping):
                rows.append({"constraint_id": key, **status})
            else:
                rows.append({"constraint_id": key, "status": status})
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        rows = [item for item in value if isinstance(item, Mapping)]
    return {
        str(item.get("constraint_id") or "").strip(): str(item.get("status") or "")
        .strip()
        .casefold()
        for item in rows
        if str(item.get("constraint_id") or "").strip()
    }


def _normal_operator(value: str) -> str:
    return "_".join(str(value or "").strip().casefold().replace("-", " ").split())


__all__ = [
    "AuthorityEvidenceObservation",
    "BuildReadyConstraintEvidence",
    "BuildReadyFile",
    "BuildReadyMaterializationResult",
    "BuildReadyPackage",
    "BusinessCompletionDecision",
    "PublicationContractRegistry",
    "PublicationAuthorityState",
    "PublicationProgress",
    "business_completion_allows_success",
    "canonical_package_digest",
    "dev_publication_signing_enabled",
    "issue_dev_publication_authority",
    "issue_configured_publication_authority",
    "issue_production_publication_authority",
    "materialize_build_ready_package",
    "verify_business_completion_issuance",
]
