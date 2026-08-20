from __future__ import annotations

from typing import Any

from agent.dataset_construction.models import ObservationRecord, SplitPolicy


def identity_values(
    row: ObservationRecord,
    field: str,
    policy: SplitPolicy | dict[str, Any] | None = None,
) -> list[str]:
    """Return versioned canonical identities shared by planner and auditor."""

    if policy is None:
        resolved = SplitPolicy()
    elif isinstance(policy, SplitPolicy):
        resolved = policy
    else:
        resolved = SplitPolicy.model_validate(policy)
    if (
        field == "modification_classes"
        and resolved.modification_identity_mode == "peptidoform"
    ):
        value = row.modified_peptide
    else:
        value = getattr(row, field)
    if isinstance(value, list):
        values = sorted(
            {str(item).strip().casefold() for item in value if str(item).strip()}
        )
    else:
        text = str(value or "").strip().casefold()
        values = [text] if text else []
    if field == "peptide" and resolved.peptide_identity_mode == "il_equivalent":
        values = [text.replace("i", "j").replace("l", "j") for text in values]
    return values
