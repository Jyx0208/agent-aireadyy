"""Leakage-aware dataset construction built downstream of existing Batch runs."""

from agent.dataset_construction.contracts import DatasetContractError, validate_catalog
from agent.dataset_construction.ingestion import ingest_existing_batch
from agent.dataset_construction.identity_ledger import build_identity_ledger
from agent.dataset_construction.leakage import audit_split
from agent.dataset_construction.models import (
    DatasetCatalog,
    DatasetConstructionJobSpec,
    DatasetReleaseResult,
    IdentityAssertion,
    IdentityDimensionSummary,
    IdentityLedger,
    LeakageAudit,
    LeakageFinding,
    ObservationRecord,
    SplitAllocation,
    SplitPlan,
    SplitPolicy,
    SplitSuite,
)
from agent.dataset_construction.persistence import (
    DatasetConstructionBase,
    DatasetReleaseRow,
    DatasetSplitAllocationRow,
)
from agent.dataset_construction.release import (
    build_dataset_release,
    register_dataset_release,
    registered_dataset_release,
)
from agent.dataset_construction.splitting import plan_split_suite
from agent.dataset_construction.workflow import (
    construct_dataset_release_from_batch,
    preview_split_suite_from_batch,
)

__all__ = [
    "DatasetCatalog",
    "DatasetContractError",
    "DatasetConstructionBase",
    "DatasetConstructionJobSpec",
    "DatasetReleaseResult",
    "IdentityAssertion",
    "IdentityDimensionSummary",
    "IdentityLedger",
    "DatasetReleaseRow",
    "DatasetSplitAllocationRow",
    "LeakageAudit",
    "LeakageFinding",
    "ObservationRecord",
    "SplitAllocation",
    "SplitPlan",
    "SplitPolicy",
    "SplitSuite",
    "ingest_existing_batch",
    "audit_split",
    "build_dataset_release",
    "register_dataset_release",
    "registered_dataset_release",
    "build_identity_ledger",
    "construct_dataset_release_from_batch",
    "plan_split_suite",
    "preview_split_suite_from_batch",
    "validate_catalog",
]
