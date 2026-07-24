from __future__ import annotations

from copy import deepcopy

from agent.discovery.agenda import (
    agenda_for_manager,
    build_critical_decision_agenda,
    next_critical_decision,
)


def _resolved_training_snapshot(**overrides: object) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "objective": "Build a chimeric-spectrum training benchmark",
        "task_type": "chimeric_interpretation",
        "run_horizon": "ai_ready_table",
        "target_project_count": 20,
        "quota_flexibility": "recommended",
        "acquisition_mode": "dda",
        "species": [],
        "species_policy": "open",
        "labeling_strategy": "unknown",
        "scientific_constraints": [],
    }
    snapshot.update(overrides)
    return snapshot


def test_chimeric_label_feasibility_precedes_optional_labeling() -> None:
    agenda = build_critical_decision_agenda(_resolved_training_snapshot())

    ids = [item.id for item in agenda]
    assert ids.index("chimeric_label_feasibility") < ids.index(
        "labeling_compatibility"
    )
    feasibility = next(
        item for item in agenda if item.id == "chimeric_label_feasibility"
    )
    assert feasibility.blocks_build_ready is True
    assert feasibility.decision_variables == [
        "label_provenance",
        "relabel_tolerance",
    ]
    assert "multi_peptide_assignment_provenance" in feasibility.required_evidence
    assert "raw_or_peaklist_files_for_relabeling" in feasibility.required_evidence
    assert feasibility.trigger_conditions


def test_chimeric_label_feasibility_is_resolved_by_open_constraints() -> None:
    snapshot = _resolved_training_snapshot(
        scientific_constraints=[
            {
                "dimension": "label_provenance",
                "strength": "open",
                "value": None,
            },
            {
                "dimension": "relabel_tolerance",
                "strength": "open",
                "value": None,
            },
        ]
    )

    ids = [item.id for item in build_critical_decision_agenda(snapshot)]

    assert "chimeric_label_feasibility" not in ids


def test_unrelated_scientific_constraint_does_not_resolve_chimeric_labels() -> None:
    snapshot = _resolved_training_snapshot(
        scientific_constraints=[
            {
                "dimension": "minimum_precursor_intensity",
                "strength": "hard",
                "value": 1000,
            }
        ]
    )

    ids = [item.id for item in build_critical_decision_agenda(snapshot)]

    assert "chimeric_label_feasibility" in ids


def test_browse_only_is_not_blocked_by_training_agenda() -> None:
    snapshot = _resolved_training_snapshot(
        task_type="browse_only",
        acquisition_mode="",
        species_policy="",
        labeling_strategy="",
    )

    agenda = build_critical_decision_agenda(snapshot)

    assert agenda == []


def test_explicit_open_values_are_resolved() -> None:
    snapshot = _resolved_training_snapshot(
        task_type="denovo",
        target_project_count=None,
        quota_flexibility="open_ended",
        acquisition_mode="unknown",
        # Empty species list still grills generalization_scope even when the
        # card defaults species_policy to open; only an explicit resolved
        # species scope (user chose open / listed taxa) suppresses it.
        species=["human"],
        species_policy="include_only",
        labeling_strategy="any",
    )

    ids = [item.id for item in build_critical_decision_agenda(snapshot)]

    assert "search_scale" not in ids
    assert "acquisition_compatibility" not in ids
    assert "generalization_scope" not in ids
    assert "labeling_compatibility" not in ids


def test_empty_species_keeps_generalization_scope_despite_open_policy() -> None:
    """P0-C: species empty + default open policy must still ask species scope."""

    snapshot = _resolved_training_snapshot(
        task_type="denovo",
        acquisition_mode="dda",
        species=[],
        species_policy="open",
        labeling_strategy="any",
    )

    ids = [item.id for item in build_critical_decision_agenda(snapshot)]

    assert "generalization_scope" in ids


def test_explicit_open_species_via_resolved_fields_suppresses_scope() -> None:
    """User-chosen open scope (resolved_fields) may leave species list empty."""

    snapshot = _resolved_training_snapshot(
        task_type="denovo",
        acquisition_mode="dda",
        species=[],
        species_policy="open",
        labeling_strategy="any",
    )

    ids = [
        item.id
        for item in build_critical_decision_agenda(
            snapshot,
            resolved_fields={"species", "species_policy", "species_coverage"},
        )
    ]

    assert "generalization_scope" not in ids


def test_resolved_fields_suppress_an_intentionally_open_unknown() -> None:
    snapshot = _resolved_training_snapshot(task_type="denovo")

    ids = [
        item.id
        for item in build_critical_decision_agenda(
            snapshot,
            resolved_fields={"labeling_strategy"},
        )
    ]

    assert "labeling_compatibility" not in ids


def test_next_critical_decision_returns_one_dynamic_item_without_mutation() -> None:
    snapshot: dict[str, object] = {
        "objective": "",
        "task_type": "",
        "run_horizon": "",
    }
    original = deepcopy(snapshot)

    decision = next_critical_decision(snapshot)

    assert decision is not None
    assert decision.id == "scientific_objective"
    assert not decision.id.lower().startswith("q")
    assert snapshot == original


def test_manager_payload_preserves_legacy_critical_contract() -> None:
    payload = agenda_for_manager(_resolved_training_snapshot())

    feasibility = next(
        item for item in payload if item["id"] == "chimeric_label_feasibility"
    )
    labeling = next(
        item for item in payload if item["id"] == "labeling_compatibility"
    )
    assert feasibility["critical"] is True
    assert feasibility["target_fields"] == ["scientific_constraints"]
    assert feasibility["source"] == "ask_user_preference"
    assert labeling["critical"] is False
