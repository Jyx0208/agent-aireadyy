from __future__ import annotations

import importlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "discovery"


def _load_fixture(name: str) -> dict[str, Any]:
    payload = json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))
    assert payload["network_required"] is False
    assert payload["contains_secrets"] is False
    return payload


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value[name]
    return getattr(value, name)


def _future_publication_registry() -> Any:
    try:
        module = importlib.import_module("agent.discovery.publication")
    except ModuleNotFoundError as exc:
        pytest.fail(
            "WAVE 2 RED: implement agent.discovery.publication with "
            "PublicationContractRegistry and BusinessCompletionDecision",
            pytrace=False,
        )
    registry_type = getattr(module, "PublicationContractRegistry", None)
    if registry_type is None:
        pytest.fail(
            "WAVE 2 RED: agent.discovery.publication.PublicationContractRegistry is missing",
            pytrace=False,
        )
    return registry_type()


def _evaluate(registry: Any, fixture: Mapping[str, Any], state: Mapping[str, Any]) -> Any:
    evaluate = getattr(registry, "evaluate", None)
    if not callable(evaluate):
        pytest.fail(
            "WAVE 2 RED: PublicationContractRegistry.evaluate(snapshot) is missing",
            pytrace=False,
        )
    return evaluate({"request": fixture["request"], "state": dict(state)})


def test_real_derived_fixture_is_sanitized_generic_offline_progress() -> None:
    fixture = _load_fixture("real_derived_progress_without_build_ready.json")
    serialized = json.dumps(fixture, ensure_ascii=False).casefold()

    assert "immunopeptide" not in serialized
    assert "pxd" not in serialized
    assert fixture["observed_state"]["candidate_projects"] == 32
    assert fixture["observed_state"]["judgment_qualified_projects"] == 20
    assert fixture["observed_state"]["build_ready_count"] == 0
    assert fixture["observed_state"]["build_ready_projects"] == 0
    assert fixture["expected"] == {
        "progress_visible": True,
        "business_status": "blocked_with_progress",
        "business_succeeded": False,
        "package_kind": "progress",
        "success_ui_allowed": False,
        "required_progress_metrics": [
            "candidate_projects",
            "assessable_inspections",
            "judgment_qualified_projects",
            "build_ready_projects",
            "blocker_counts",
        ],
    }


def test_real_derived_fixture_keeps_soft_and_hard_constraints_distinct() -> None:
    fixture = _load_fixture("real_derived_progress_without_build_ready.json")
    by_dimension = {
        item["dimension"]: item for item in fixture["request"]["constraints"]
    }

    assert by_dimension["acquisition_mode"]["strength"] == "soft"
    assert by_dimension["labeling_strategy"]["strength"] == "hard"
    assert by_dimension["labeling_strategy"]["evidence_scope"] == "assay"


def test_synthetic_fixture_contains_progress_and_build_ready_control_states() -> None:
    fixture = _load_fixture("synthetic_rt_psm_build_ready_transition.json")
    progress = fixture["states"]["progress_only"]
    control = fixture["states"]["build_ready_control"]

    assert fixture["request"]["task_family"] == "retention_time_prediction"
    assert progress["judgment_qualified_projects"] == 2
    assert progress["build_ready_projects"] == 0
    assert progress["missing_build_ready_fields"]
    assert control["build_ready_projects"] == 2
    assert control["build_ready_files"] == 6
    assert control["missing_build_ready_fields"] == []


def test_wave2_real_progress_does_not_graduate_without_build_ready_material() -> None:
    fixture = _load_fixture("real_derived_progress_without_build_ready.json")

    decision = _evaluate(
        _future_publication_registry(), fixture, fixture["observed_state"]
    )

    assert _field(decision, "succeeded") is False
    assert _field(decision, "status") == "blocked_with_progress"
    assert _field(decision, "package_kind") == "progress"
    assert _field(decision, "success_ui_allowed") is False


def test_wave2_review_progress_is_not_business_completion() -> None:
    fixture = _load_fixture("synthetic_rt_psm_build_ready_transition.json")

    decision = _evaluate(
        _future_publication_registry(), fixture, fixture["states"]["progress_only"]
    )

    assert _field(decision, "succeeded") is False
    assert _field(decision, "status") == "blocked_with_progress"
    assert _field(decision, "success_ui_allowed") is False


def test_wave2_build_ready_control_is_the_only_graduating_state() -> None:
    fixture = _load_fixture("synthetic_rt_psm_build_ready_transition.json")

    decision = _evaluate(
        _future_publication_registry(),
        fixture,
        fixture["states"]["build_ready_control"],
    )

    assert _field(decision, "succeeded") is True
    assert _field(decision, "status") == "build_ready_succeeded"
    assert _field(decision, "package_kind") == "build_ready"
    assert _field(decision, "success_ui_allowed") is True


def test_wave2_hard_unknown_blocks_apparent_build_ready_counts() -> None:
    fixture = _load_fixture("synthetic_rt_psm_build_ready_transition.json")
    state = json.loads(json.dumps(fixture["states"]["build_ready_control"]))
    state["validated_build_ready_package"]["constraint_evidence"] = []

    decision = _evaluate(_future_publication_registry(), fixture, state)

    assert _field(decision, "succeeded") is False
    assert _field(decision, "success_ui_allowed") is False
    assert any(
        "hard_unknown:labeling_strategy" in item
        for item in _field(decision, "limitations")
    )


def test_wave2_hard_conflict_blocks_apparent_build_ready_counts() -> None:
    fixture = _load_fixture("synthetic_rt_psm_build_ready_transition.json")
    state = json.loads(json.dumps(fixture["states"]["build_ready_control"]))
    state["constraint_assessments"] = [
        {"constraint_id": "labeling_strategy", "status": "fail"}
    ]

    decision = _evaluate(_future_publication_registry(), fixture, state)

    assert _field(decision, "succeeded") is False
    assert any(
        "hard_conflict:labeling_strategy" in item
        for item in _field(decision, "limitations")
    )


def test_wave2_hard_self_reported_pass_and_dimension_label_are_not_evidence() -> None:
    fixture = _load_fixture("synthetic_rt_psm_build_ready_transition.json")
    state = json.loads(json.dumps(fixture["states"]["build_ready_control"]))
    state["validated_build_ready_package"]["constraint_evidence"] = []
    state["constraint_assessments"] = {
        "constraint.labeling_strategy": "pass"
    }
    state["evidence"] = {"assay": ["labeling_strategy"]}

    decision = _evaluate(_future_publication_registry(), fixture, state)

    assert _field(decision, "succeeded") is False
    assert _field(decision, "success_ui_allowed") is False
    assert any(
        "hard_unknown:labeling_strategy" in item
        for item in _field(decision, "limitations")
    )


def test_wave2_missing_soft_preference_never_blocks_build_ready() -> None:
    fixture = {
        "request": {
            "constraints": [
                {
                    "dimension": "acquisition_mode",
                    "value": "dda",
                    "strength": "soft",
                    "evidence_scope": "assay",
                    "source": "accepted_preference",
                }
            ]
        }
    }
    control = _load_fixture("synthetic_rt_psm_build_ready_transition.json")
    state = control["states"]["build_ready_control"]

    decision = _evaluate(_future_publication_registry(), fixture, state)

    assert _field(decision, "succeeded") is True
    assert _field(decision, "success_ui_allowed") is True


def test_wave2_explicit_zero_build_ready_count_is_authoritative() -> None:
    fixture = {"request": {"constraints": []}}
    state = {
        "candidate_projects": 1,
        "build_ready_count": 0,
        "build_ready_projects": 0,
        "build_ready_files": 0,
        "missing_build_ready_fields": [],
        "latest_audit_status": "ready",
        "files": [
            {
                "project_accession": "PROJECT_1",
                "file_name": "sample.raw",
                "download_url": "https://example.invalid/sample.raw",
                "expected_size_bytes": 1024,
                "file_role": "raw_acquisition",
                "validity_status": "valid",
                "needs_review": False,
                "evidence_level": "file",
            }
        ],
    }

    decision = _evaluate(_future_publication_registry(), fixture, state)

    assert _field(decision, "succeeded") is False
    assert _field(decision, "package_kind") == "progress"
