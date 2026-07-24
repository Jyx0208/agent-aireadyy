from __future__ import annotations

from agent.discovery.agenda import agenda_for_manager
from agent.web import app as web_app


def test_species_stays_critical_when_policy_defaults_to_open():
    snap = {
        "task_type": "denovo",
        "acquisition_mode": "dda",
        "mixed_acquisition_policy": "reject_mixed",
        "species": [],
        "species_policy": "open",
        "run_horizon": "candidates_reviewed",
        "quota_flexibility": "open_ended",
        "coverage_mode": "exhaustive",
        "objective": "免疫肽 denovo",
    }
    resolved = {
        "task_type",
        "acquisition_mode",
        "mixed_acquisition_policy",
        "run_horizon",
        "quota_flexibility",
        "coverage_mode",
        "objective",
    }
    agenda = agenda_for_manager(snap, resolved_fields=resolved)
    critical_ids = [item["id"] for item in agenda if item.get("critical")]
    assert "generalization_scope" in critical_ids


def test_synthesize_species_next_decision_is_schema_valid():
    remaining = [
        {
            "id": "generalization_scope",
            "priority": 78,
            "critical": True,
            "target_fields": ["species", "species_policy", "species_coverage"],
            "reason": "Species scope matters for denovo benchmarks.",
        }
    ]
    decision = web_app._synthesize_discovery_next_decision_from_agenda(remaining)
    assert decision is not None
    assert decision["focus"]
    assert decision["question"]
    assert len(decision["options"]) >= 2
    assert decision["recommendation"].get("reason")
    for option in decision["options"]:
        assert isinstance(option.get("strategy_patch"), dict)
        assert option["strategy_patch"]
