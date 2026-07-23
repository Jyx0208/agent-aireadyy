from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import Field

from agent.discovery.publication import (
    PublicationAuthorityState,
    PublicationContractRegistry,
    business_completion_allows_success,
    canonical_package_digest,
)
from agent.models import JsonModel


class BuilderDryRunResult(JsonModel):
    """Deterministic builder admission receipt; transport success is irrelevant."""

    accepted: bool = False
    status: Literal["builder_dry_run_accepted", "builder_dry_run_blocked"]
    business_completion_status: str
    package_digest: str | None = None
    key_id: str | None = None
    blockers: list[str] = Field(default_factory=list)
    receipt_ref: str | None = None


class BuilderDryRunContract:
    """Validate publication issuance and a typed builder preflight response."""

    def __init__(self, *, registry: PublicationContractRegistry) -> None:
        self.registry = registry

    def evaluate(
        self,
        snapshot: Mapping[str, Any],
        *,
        builder_result: Mapping[str, Any] | None,
    ) -> BuilderDryRunResult:
        decision = self.registry.evaluate(snapshot)
        if not business_completion_allows_success(
            decision,
            ledger=self.registry.ledger,
        ):
            return BuilderDryRunResult(
                status="builder_dry_run_blocked",
                business_completion_status=decision.status,
                blockers=["publication_not_build_ready", *decision.limitations],
            )

        package = decision.build_ready_package
        if package is None:  # defensive: business_completion gate already checks this
            return BuilderDryRunResult(
                status="builder_dry_run_blocked",
                business_completion_status=decision.status,
                blockers=["publication_package_missing"],
            )
        state = snapshot.get("state")
        raw_authority = state.get("publication_authority") if isinstance(state, Mapping) else None
        try:
            authority = PublicationAuthorityState.model_validate(raw_authority)
        except (TypeError, ValueError):
            return BuilderDryRunResult(
                status="builder_dry_run_blocked",
                business_completion_status=decision.status,
                package_digest=canonical_package_digest(package),
                blockers=["publication_authority_missing"],
            )

        expected_digest = canonical_package_digest(package)
        raw = builder_result if isinstance(builder_result, Mapping) else {}
        blockers: list[str] = []
        if raw.get("accepted") is not True:
            blockers.append("builder_receipt_not_accepted")
        if str(raw.get("status") or "").strip().casefold() not in {"ready", "accepted"}:
            blockers.append("builder_receipt_status_not_ready")
        if str(raw.get("package_digest") or "").strip() != expected_digest:
            blockers.append("builder_receipt_package_digest_mismatch")
        if not authority.key_id or str(raw.get("key_id") or "").strip() != authority.key_id:
            blockers.append("builder_receipt_key_id_mismatch")
        if str(raw.get("builder_entrypoint") or "").strip() != package.builder_entrypoint:
            blockers.append("builder_receipt_entrypoint_mismatch")
        if not package.builder_preflight_ref:
            blockers.append("builder_preflight_ref_missing")
        elif str(raw.get("preflight_ref") or "").strip() != package.builder_preflight_ref:
            blockers.append("builder_receipt_preflight_ref_mismatch")
        receipt_ref = str(raw.get("receipt_ref") or "").strip()
        if not receipt_ref:
            blockers.append("builder_receipt_ref_missing")

        if blockers:
            return BuilderDryRunResult(
                status="builder_dry_run_blocked",
                business_completion_status=decision.status,
                package_digest=expected_digest,
                key_id=authority.key_id,
                blockers=list(dict.fromkeys(blockers)),
            )
        return BuilderDryRunResult(
            accepted=True,
            status="builder_dry_run_accepted",
            business_completion_status=decision.status,
            package_digest=expected_digest,
            key_id=authority.key_id,
            receipt_ref=receipt_ref,
        )


__all__ = ["BuilderDryRunContract", "BuilderDryRunResult"]
