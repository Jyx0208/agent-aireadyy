from __future__ import annotations

import re
from collections import defaultdict

from agent.dataset_construction.identities import identity_values
from agent.dataset_construction.models import (
    DatasetCatalog,
    IdentityAssertion,
    IdentityDimensionSummary,
    IdentityLedger,
    ObservationRecord,
    SplitPolicy,
)


IDENTITY_DIMENSIONS = (
    "project_id", "source_file_id", "file_family_id", "sample_id", "subject_id",
    "technical_replicate_id", "fraction_id", "tmt_plex_id", "lab_id",
    "instrument_id", "organism_id", "acquisition_id", "gradient_id",
    "search_workflow_id", "peptide", "modified_peptide", "protein_ids",
    "protein_family_ids", "modification_classes",
)
_ARTIFACT_DIMENSIONS = {
    "peptide", "modified_peptide", "protein_ids", "protein_family_ids",
    "modification_classes",
}
_TAXONOMY = {
    "homo sapiens": "ncbi:9606", "human": "ncbi:9606",
    "mus musculus": "ncbi:10090", "mouse": "ncbi:10090",
    "saccharomyces cerevisiae": "ncbi:4932", "yeast": "ncbi:4932",
    "arabidopsis thaliana": "ncbi:3702", "danio rerio": "ncbi:7955",
    "zebrafish": "ncbi:7955",
}


def _raw_values(row: ObservationRecord, dimension: str) -> list[str]:
    value = getattr(row, dimension)
    values = value if isinstance(value, list) else [value]
    return [str(item).strip() for item in values if str(item or "").strip()]


def _canonical_values(row: ObservationRecord, dimension: str, policy: SplitPolicy) -> list[str]:
    raw = _raw_values(row, dimension)
    if dimension == "organism_id":
        return sorted({_TAXONOMY.get(value.casefold(), value.casefold()) for value in raw})
    if dimension == "instrument_id":
        return sorted({re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") for value in raw})
    if dimension == "acquisition_id":
        return sorted({"+".join(token for token in ("dda", "dia", "hcd", "cid", "etd") if token in value.casefold()) or value.casefold() for value in raw})
    return identity_values(row, dimension, policy)


def _source(dimension: str, row: ObservationRecord) -> tuple[str, str, str]:
    if dimension in _ARTIFACT_DIMENSIONS:
        return "source_artifact", row.source_artifact_uri, "reported"
    if dimension == "file_family_id":
        return "derived", row.source_artifact_uri, "derived"
    return "batch_summary", row.source_artifact_uri, "reported"


def build_identity_ledger(catalog: DatasetCatalog, *, policy: SplitPolicy | None = None) -> IdentityLedger:
    """Build the auditable observation-to-identity authority for split planning."""

    policy = policy or SplitPolicy()
    assertions: list[IdentityAssertion] = []
    canonical_by_dimension: dict[str, set[str]] = defaultdict(set)
    present_by_dimension: dict[str, int] = defaultdict(int)
    for row in catalog.observations:
        for dimension in IDENTITY_DIMENSIONS:
            raw = _raw_values(row, dimension)
            canonical = _canonical_values(row, dimension, policy)
            if canonical:
                source_kind, source_uri, confidence = _source(dimension, row)
                present_by_dimension[dimension] += 1
                canonical_by_dimension[dimension].update(canonical)
                assertions.append(IdentityAssertion(observation_id=row.observation_id, dimension=dimension, raw_values=raw, canonical_values=canonical, status="present", source_kind=source_kind, source_uri=source_uri, confidence=confidence))
            else:
                assertions.append(IdentityAssertion(observation_id=row.observation_id, dimension=dimension, status="missing", source_kind="unavailable", source_uri=row.source_artifact_uri, confidence="unavailable", missing_reason="not_reported_by_batch_or_source_artifact"))
    total = len(catalog.observations)
    summaries = [IdentityDimensionSummary(dimension=dimension, observation_count=total, present_count=present_by_dimension[dimension], missing_count=total - present_by_dimension[dimension], unique_count=len(canonical_by_dimension[dimension]), coverage=present_by_dimension[dimension] / total if total else 0.0) for dimension in IDENTITY_DIMENSIONS]
    return IdentityLedger(source_batch_dir=catalog.source_batch_dir, observation_count=total, assertions=assertions, dimensions=summaries)
