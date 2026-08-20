from __future__ import annotations

from typing import Any

import pandas as pd
import pandera.pandas as pa
from pandera.errors import SchemaErrors

from agent.dataset_construction.models import DatasetCatalog
from agent.dataset_construction.ingestion import canonical_task_type


class DatasetContractError(ValueError):
    """Raised when a catalog cannot safely enter split construction."""


CATALOG_SCHEMA = pa.DataFrameSchema(
    {
        "observation_id": pa.Column(
            str,
            checks=pa.Check.str_length(min_value=1),
            nullable=False,
            unique=True,
        ),
        "task_type": pa.Column(str, checks=pa.Check.str_length(min_value=1)),
        "project_id": pa.Column(str, checks=pa.Check.str_length(min_value=1)),
        "source_file_id": pa.Column(str, checks=pa.Check.str_length(min_value=1)),
        "file_family_id": pa.Column(str, checks=pa.Check.str_length(min_value=1)),
        "source_artifact_uri": pa.Column(str, checks=pa.Check.str_length(min_value=1)),
        "source_row_number": pa.Column(int, checks=pa.Check.ge(0)),
        "spectrum_id": pa.Column(str, checks=pa.Check.str_length(min_value=1)),
    },
    strict=False,
    coerce=False,
    name="dataset_construction_catalog_v1",
)


_PEPTIDE_LABEL_TASKS = {
    "denovo",
    "ptm_denovo",
    "fragment_intensity_prediction",
    "rt_prediction",
}


def validate_catalog(
    catalog: DatasetCatalog,
    *,
    task_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate release-blocking invariants and return a compact evidence record."""

    if not catalog.observations:
        raise DatasetContractError("catalog contains no model observations")
    frame = pd.DataFrame(
        [observation.model_dump(mode="python") for observation in catalog.observations]
    )
    try:
        validated = CATALOG_SCHEMA.validate(frame, lazy=True)
    except SchemaErrors as exc:
        failures = exc.failure_cases.to_dict(orient="records")
        raise DatasetContractError(
            f"catalog violates dataset contract: {failures}"
        ) from exc
    task_spec = task_spec or {}
    task_type = canonical_task_type(task_spec.get("task_type"))
    if task_spec and not task_type:
        raise DatasetContractError("task_spec.task_type is required")
    label_policy = task_spec.get("label_policy") or {}
    if not isinstance(label_policy, dict):
        raise DatasetContractError("task_spec.label_policy must be a JSON object")
    require_peptide = bool(
        label_policy.get("require_peptide", task_type in _PEPTIDE_LABEL_TASKS)
    )
    require_q_value = bool(
        label_policy.get("require_q_value", task_type == "denovo")
    )
    require_confidence = bool(
        label_policy.get(
            "require_confidence",
            task_type in _PEPTIDE_LABEL_TASKS | {"fragment_intensity_prediction"},
        )
    )
    max_q_value_raw = label_policy.get(
        "max_q_value",
        0.01 if task_type in _PEPTIDE_LABEL_TASKS else None,
    )
    max_q_value = (
        float(max_q_value_raw) if max_q_value_raw is not None else None
    )
    violations: list[str] = []
    for observation in catalog.observations:
        if task_type and canonical_task_type(observation.task_type) != task_type:
            violations.append(
                f"wrong_task_type:{observation.observation_id}:{observation.task_type}"
            )
        if require_peptide and not observation.peptide.strip():
            violations.append(f"missing_peptide:{observation.observation_id}")
        if (
            require_confidence
            and observation.q_value is None
            and observation.psm_probability is None
        ):
            violations.append(f"missing_confidence:{observation.observation_id}")
        if require_q_value and observation.q_value is None:
            violations.append(f"missing_q_value:{observation.observation_id}")
        if (
            max_q_value is not None
            and observation.q_value is not None
            and observation.q_value > max_q_value
        ):
            violations.append(
                f"q_value_above_threshold:{observation.observation_id}:{observation.q_value}"
            )
        label = observation.label_payload
        if task_type == "rt_prediction":
            value = label.get("retention_time")
            if not isinstance(value, (int, float)):
                violations.append(f"missing_retention_time:{observation.observation_id}")
            if not str(label.get("unit") or "").strip():
                violations.append(f"missing_retention_time_unit:{observation.observation_id}")
        elif task_type == "fragment_intensity_prediction":
            if int(label.get("target_count") or 0) <= 0:
                violations.append(f"missing_fragment_targets:{observation.observation_id}")
        elif task_type == "psm_scoring":
            target_decoy = str(label.get("target_decoy") or "").strip().casefold()
            if target_decoy not in {"target", "decoy", "true", "false", "0", "1"}:
                violations.append(f"missing_target_decoy_label:{observation.observation_id}")
        elif task_type == "ptm_denovo":
            if not observation.modified_peptide.strip() or not label.get("modification_tokens"):
                violations.append(f"missing_modified_peptide_label:{observation.observation_id}")
    if violations:
        raise DatasetContractError(
            f"catalog violates task label policy: {violations}"
        )
    return {
        "contract": CATALOG_SCHEMA.name,
        "status": "pass",
        "observation_count": int(len(validated)),
        "unique_observation_count": int(validated["observation_id"].nunique()),
        "source_artifact_count": int(validated["source_artifact_uri"].nunique()),
        "file_family_count": int(validated["file_family_id"].nunique()),
        "label_policy": {
            "task_type": task_type,
            "require_peptide": require_peptide,
            "require_q_value": require_q_value,
            "require_confidence": require_confidence,
            "max_q_value": max_q_value,
            "status": "pass",
        },
    }
