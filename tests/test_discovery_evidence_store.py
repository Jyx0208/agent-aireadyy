from __future__ import annotations

import importlib
from typing import Any

import pytest


def _future_store(
    *,
    available_refs: set[str],
    available_membership_refs: set[str] | None = None,
) -> Any:
    try:
        module = importlib.import_module("agent.discovery.evidence_store")
    except ModuleNotFoundError:
        pytest.fail(
            "WAVE 2 RED: implement agent.discovery.evidence_store.EvidenceStore",
            pytrace=False,
        )
    store_type = getattr(module, "EvidenceStore", None)
    if store_type is None:
        pytest.fail("WAVE 2 RED: EvidenceStore is missing", pytrace=False)
    return store_type(
        available_refs=available_refs,
        available_membership_refs=available_membership_refs or set(),
    )


def test_materialize_rejects_unknown_source_refs() -> None:
    store = _future_store(available_refs={"repository:project-1"})

    with pytest.raises(ValueError, match="unknown evidence refs"):
        store.materialize(
            {
                "observation_id": "obs-missing-ref",
                "subject_kind": "project",
                "subject_id": "project-1",
                "dimension": "organism",
                "observed_value": "human",
                "evidence_scope": "project",
                "source_kind": "repository",
                "source_refs": ["repository:not-present"],
            }
        )


def test_project_observation_does_not_implicitly_become_file_evidence() -> None:
    store = _future_store(available_refs={"repository:project-1"})
    store.materialize(
        {
            "observation_id": "obs-project-label",
            "subject_kind": "project",
            "subject_id": "project-1",
            "dimension": "labeling_strategy",
            "observed_value": "label_free",
            "evidence_scope": "project",
            "source_kind": "repository",
            "source_refs": ["repository:project-1"],
        }
    )

    assert store.resolve(
        subject_kind="file",
        subject_id="file-1",
        dimension="labeling_strategy",
    ) == []


def test_assay_observation_requires_explicit_file_membership_to_resolve() -> None:
    store = _future_store(
        available_refs={"sdrf:assay-1"},
        available_membership_refs={"file:file-1"},
    )
    store.materialize(
        {
            "observation_id": "obs-assay-label",
            "subject_kind": "assay",
            "subject_id": "assay-1",
            "dimension": "labeling_strategy",
            "observed_value": "label_free",
            "evidence_scope": "assay",
            "source_kind": "sdrf",
            "source_refs": ["sdrf:assay-1"],
            "membership_refs": ["file:file-1"],
        }
    )

    resolved = store.resolve(
        subject_kind="file",
        subject_id="file-1",
        dimension="labeling_strategy",
    )

    assert [item.observation_id for item in resolved] == ["obs-assay-label"]
    assert store.resolve(
        subject_kind="file",
        subject_id="file-2",
        dimension="labeling_strategy",
    ) == []
