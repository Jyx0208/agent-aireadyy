from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator, model_validator

from agent.discovery.constraints import ConstraintScope
from agent.models import JsonModel


class EvidenceObservation(JsonModel):
    """One versioned, provenance-backed observation at an explicit scope."""

    schema_version: str = "discovery-evidence-observation/v1"
    observation_id: str = Field(min_length=1, max_length=160)
    subject_kind: ConstraintScope
    subject_id: str = Field(min_length=1, max_length=240)
    dimension: str = Field(min_length=1, max_length=120)
    observed_value: Any = None
    evidence_scope: ConstraintScope
    source_kind: str = Field(min_length=1, max_length=80)
    source_refs: list[str] = Field(min_length=1, max_length=50)
    membership_refs: list[str] = Field(default_factory=list, max_length=500)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator(
        "observation_id",
        "subject_id",
        "dimension",
        "source_kind",
    )
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return " ".join(str(value or "").split()).strip()

    @field_validator("source_refs", "membership_refs")
    @classmethod
    def normalize_refs(cls, values: list[str]) -> list[str]:
        return list(
            dict.fromkeys(
                " ".join(str(value or "").split()).strip()[:300]
                for value in values
                if str(value or "").strip()
            )
        )

    @model_validator(mode="after")
    def validate_scope(self) -> "EvidenceObservation":
        if not self.source_refs:
            raise ValueError("source_refs must remain non-empty after normalization")
        if self.subject_kind != self.evidence_scope:
            raise ValueError("subject_kind must match evidence_scope")
        return self


class EvidenceStoreArtifact(JsonModel):
    schema_version: str = "discovery-evidence-store/v1"
    observations: list[EvidenceObservation] = Field(default_factory=list)


class EvidenceStore:
    """Materialize validated observations without implicit scope promotion."""

    def __init__(
        self,
        *,
        available_refs: set[str] | list[str] | tuple[str, ...],
        available_membership_refs: (
            set[str] | list[str] | tuple[str, ...]
        ) = (),
    ):
        self._available_refs = {
            str(value).strip() for value in available_refs if str(value).strip()
        }
        self._available_membership_refs = {
            str(value).strip()
            for value in available_membership_refs
            if str(value).strip()
        }
        self._observations: dict[str, EvidenceObservation] = {}

    def materialize(
        self,
        observation: EvidenceObservation | dict[str, Any],
    ) -> EvidenceObservation:
        parsed = (
            observation
            if isinstance(observation, EvidenceObservation)
            else EvidenceObservation.model_validate(observation)
        )
        unknown_refs = sorted(set(parsed.source_refs) - self._available_refs)
        if unknown_refs:
            raise ValueError(f"unknown evidence refs: {', '.join(unknown_refs)}")
        previous = self._observations.get(parsed.observation_id)
        if previous is not None and previous != parsed:
            raise ValueError(
                f"observation_id already materialized: {parsed.observation_id}"
            )
        self._observations[parsed.observation_id] = parsed
        return parsed

    def resolve(
        self,
        *,
        subject_kind: ConstraintScope,
        subject_id: str,
        dimension: str,
    ) -> list[EvidenceObservation]:
        subject_key = f"{subject_kind}:{str(subject_id).strip()}"
        resolved: list[EvidenceObservation] = []
        for observation in self._observations.values():
            if observation.dimension != dimension:
                continue
            if (
                observation.subject_kind == subject_kind
                and observation.subject_id == subject_id
            ):
                resolved.append(observation)
                continue
            if self._can_resolve_membership(
                observation,
                target_kind=subject_kind,
                target_key=subject_key,
            ):
                resolved.append(observation)
        return sorted(resolved, key=lambda item: item.observation_id)

    def to_artifact(self) -> EvidenceStoreArtifact:
        return EvidenceStoreArtifact(
            observations=sorted(
                self._observations.values(),
                key=lambda item: item.observation_id,
            )
        )

    def _can_resolve_membership(
        self,
        observation: EvidenceObservation,
        *,
        target_kind: ConstraintScope,
        target_key: str,
    ) -> bool:
        allowed_edges = {
            ("assay", "file"),
            ("file", "spectrum"),
            ("sample", "file"),
        }
        return (
            (observation.evidence_scope, target_kind) in allowed_edges
            and target_key in observation.membership_refs
            and target_key in self._available_membership_refs
        )
