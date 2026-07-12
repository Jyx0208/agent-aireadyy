from __future__ import annotations

from agent.discovery.models import DatasetRequest
from agent.discovery.replacement_evaluation import (
    PromptVariant,
    ReplacementBenchmarkScenario,
    ReplacementGate,
    ReplacementRun,
    build_variant_runtime_input,
    evaluate_replacement,
    load_replacement_scenarios,
    replacement_run_from_record,
    score_replacement_run,
)


def _scenario() -> ReplacementBenchmarkScenario:
    return ReplacementBenchmarkScenario(
        id="human_neuron",
        hidden_request=DatasetRequest(
            species=["Homo sapiens"],
            species_policy="include_only",
            acquisition_mode="dda",
            labeling_strategy="label_free",
        ),
        prompt_variants=[
            PromptVariant(
                id="structured",
                ambiguity_level="structured",
                mode="parsed_spec",
                prompt="Find human label-free DDA sensory-neuron data.",
            ),
            PromptVariant(
                id="vague",
                ambiguity_level="vague",
                mode="raw_prompt",
                prompt="I need data for a model of chemotherapy nerve damage.",
            ),
        ],
        relevance_judgments={
            "PXD000001": 3,
            "PXD000002": 2,
            "PXD000003": 1,
            "PXD999999": 0,
        },
        variant_judgment_sources={
            "structured": "human_verified",
            "vague": "human_verified",
        },
    )


def _run(
    *,
    runtime: str,
    variant_id: str,
    accessions: list[str],
    tier: str = "baseline",
    requests: int = 50,
    violations: int = 0,
    file_bundle: float = 0.0,
    selected_files: int = 0,
    repeat: int = 0,
) -> ReplacementRun:
    return ReplacementRun(
        scenario_id="human_neuron",
        variant_id=variant_id,
        repeat=repeat,
        runtime=runtime,
        budget_tier=tier,
        status="completed",
        selected_project_accessions=accessions,
        hard_constraint_violations=violations,
        selected_file_count=selected_files,
        file_bundle_completeness=file_bundle,
        task_ready_precision=1.0,
        evidence_completeness=1.0,
        repository_requests=requests,
        elapsed_seconds=10.0,
    )


def test_graded_relevance_rewards_multiple_good_projects_in_rank_order() -> None:
    scenario = _scenario()
    best = score_replacement_run(
        scenario,
        _run(runtime="openai_agents", variant_id="structured", accessions=["PXD000001", "PXD000002"]),
    )
    reversed_result = score_replacement_run(
        scenario,
        _run(runtime="openai_agents", variant_id="structured", accessions=["PXD000002", "PXD000001"]),
    )

    assert best.ndcg_at_5 == 1.0
    assert best.high_relevance_recall == 1.0
    assert reversed_result.ndcg_at_5 < best.ndcg_at_5


def test_variant_specific_judgments_override_scenario_seed_labels() -> None:
    scenario = _scenario().model_copy(
        update={
            "variant_relevance_judgments": {
                "structured": {"PXD000001": 3},
                "vague": {"PXD000001": 1, "PXD000002": 3},
            }
        }
    )

    structured = score_replacement_run(
        scenario,
        _run(runtime="openai_agents", variant_id="structured", accessions=["PXD000001"]),
    )
    vague = score_replacement_run(
        scenario,
        _run(runtime="openai_agents", variant_id="vague", accessions=["PXD000001"]),
    )

    assert structured.relevance_grades == [3]
    assert vague.relevance_grades == [1]
    assert structured.quality_score > vague.quality_score


def test_unjudged_task_first_scenario_is_excluded_until_blind_review() -> None:
    scenario = _scenario().model_copy(
        update={
            "relevance_judgments": {},
            "variant_relevance_judgments": {},
            "prompt_variants": [_scenario().prompt_variants[1]],
        }
    )
    workflow = [
        _run(runtime="workflow", variant_id="vague", accessions=["PXD_WORKFLOW"])
    ]
    agent = [
        _run(
            runtime="openai_agents",
            variant_id="vague",
            accessions=["PXD_AGENT"],
            tier="2x",
        )
    ]

    report = evaluate_replacement(
        scenarios=[scenario],
        workflow=workflow,
        agent=agent,
        gate=ReplacementGate(min_pairs=1),
    )

    pair = report.tiers[0].pairs[0]
    assert pair.eligible is False
    assert pair.ineligible_reason == "missing_relevance_judgments"
    assert report.tiers[0].pair_count == 0
    assert report.replacement_ready is False


def test_raw_prompt_runtime_input_does_not_leak_hidden_request() -> None:
    scenario = _scenario()
    raw = build_variant_runtime_input(scenario, scenario.prompt_variants[1])
    controlled = build_variant_runtime_input(scenario, scenario.prompt_variants[0])

    assert raw == {"prompt": "I need data for a model of chemotherapy nerve damage."}
    assert controlled["request"]["species"] == ["Homo sapiens"]
    assert "hidden_request" not in raw


def test_replacement_record_uses_graded_task_readiness() -> None:
    scenario = _scenario()
    variant = scenario.prompt_variants[0]
    run = replacement_run_from_record(
        scenario=scenario,
        variant=variant,
        runtime="workflow",
        budget_tier="baseline",
        record={
            "status": "completed",
            "projects": [{"project_accession": "PXD000001"}],
            "files": [
                {
                    "project_accession": "PXD000001",
                    "file_role": "raw_acquisition",
                    "task_readiness_status": "weak_ready",
                    "evidence_completeness": 0.8,
                },
                {
                    "project_accession": "PXD000001",
                    "file_role": "search_result",
                    "task_ai_readiness_score": 0.9,
                    "evidence_completeness": 0.6,
                },
            ],
            "summary": {},
        },
        elapsed_seconds=1.0,
    )

    assert run.task_ready_precision == 0.75
    assert run.evidence_completeness == 0.7
    assert run.selected_file_count == 2
    assert run.raw_spectra_count == 1
    assert run.search_result_count == 1
    assert run.file_bundle_completeness == 0.8


def test_hard_constraint_scoring_normalizes_species_aliases() -> None:
    scenario = _scenario()
    variant = scenario.prompt_variants[0]
    run = replacement_run_from_record(
        scenario=scenario,
        variant=variant,
        runtime="workflow",
        budget_tier="baseline",
        record={
            "status": "completed",
            "projects": [{"project_accession": "PXD000001"}],
            "files": [
                {
                    "project_accession": "PXD000001",
                    "species": ["human"],
                    "canonical_species": ["human"],
                    "acquisition_mode": "dda",
                    "labeling_strategy": "label_free",
                    "validity_status": "valid",
                }
            ],
            "summary": {},
        },
        elapsed_seconds=1.0,
    )

    assert run.hard_constraint_violations == 0


def test_more_budget_is_allowed_when_agent_quality_is_materially_better() -> None:
    scenario = _scenario()
    workflow = [
        _run(runtime="workflow", variant_id=variant, accessions=["PXD000003"], requests=50)
        for variant in ("structured", "vague")
    ]
    agent = [
        _run(
            runtime="openai_agents",
            variant_id=variant,
            accessions=["PXD000001", "PXD000002"],
            tier="2x",
            requests=100,
        )
        for variant in ("structured", "vague")
    ]

    report = evaluate_replacement(
        scenarios=[scenario],
        workflow=workflow,
        agent=agent,
        gate=ReplacementGate(
            min_pairs=2,
            min_average_quality_delta=0.05,
            min_win_rate=0.60,
            max_loss_rate=0.10,
            min_vague_quality_delta=0.05,
            max_repository_request_ratio=3.0,
        ),
    )

    tier = report.tiers[0]
    assert tier.repository_request_ratio == 2.0
    assert tier.replacement_ready is True
    assert report.replacement_ready is True


def test_efficiency_without_quality_gain_does_not_pass_replacement_gate() -> None:
    scenario = _scenario()
    workflow = [
        _run(runtime="workflow", variant_id=variant, accessions=["PXD000001"], requests=80)
        for variant in ("structured", "vague")
    ]
    agent = [
        _run(
            runtime="openai_agents",
            variant_id=variant,
            accessions=["PXD000001"],
            tier="1x",
            requests=40,
        )
        for variant in ("structured", "vague")
    ]

    report = evaluate_replacement(
        scenarios=[scenario],
        workflow=workflow,
        agent=agent,
        gate=ReplacementGate(min_pairs=2, min_vague_quality_delta=0.0),
    )

    assert report.tiers[0].repository_request_ratio == 0.5
    assert report.replacement_ready is False
    assert "average quality delta below gate" in report.tiers[0].gate_reasons


def test_same_family_model_judgments_cannot_pass_formal_replacement_gate() -> None:
    scenario = _scenario().model_copy(
        update={
            "variant_judgment_sources": {
                "structured": "provisional_same_family",
                "vague": "provisional_same_family",
            }
        }
    )
    workflow = [
        _run(runtime="workflow", variant_id=variant, accessions=["PXD000003"])
        for variant in ("structured", "vague")
    ]
    agent = [
        _run(
            runtime="openai_agents",
            variant_id=variant,
            accessions=["PXD000001", "PXD000002"],
            tier="2x",
        )
        for variant in ("structured", "vague")
    ]

    report = evaluate_replacement(
        scenarios=[scenario],
        workflow=workflow,
        agent=agent,
        gate=ReplacementGate(
            min_pairs=2,
            min_average_quality_delta=0.05,
            min_win_rate=0.60,
            max_loss_rate=0.10,
            min_vague_quality_delta=0.05,
        ),
    )

    assert report.replacement_ready is False
    assert report.tiers[0].pair_count == 2
    assert "non-independent relevance judgments present" in report.tiers[0].gate_reasons


def test_added_hard_constraint_violation_blocks_replacement() -> None:
    scenario = _scenario()
    workflow = [
        _run(runtime="workflow", variant_id=variant, accessions=["PXD000003"])
        for variant in ("structured", "vague")
    ]
    agent = [
        _run(
            runtime="openai_agents",
            variant_id=variant,
            accessions=["PXD000001"],
            tier="max_quality",
            violations=1,
        )
        for variant in ("structured", "vague")
    ]

    report = evaluate_replacement(
        scenarios=[scenario],
        workflow=workflow,
        agent=agent,
        gate=ReplacementGate(min_pairs=2, min_vague_quality_delta=0.0),
    )

    assert report.replacement_ready is False
    assert report.tiers[0].added_hard_constraint_violations == 2


def test_file_manifest_regression_cannot_be_hidden_by_project_relevance() -> None:
    scenario = _scenario().model_copy(update={"prompt_variants": [_scenario().prompt_variants[0]]})
    workflow = [
        _run(
            runtime="workflow",
            variant_id="structured",
            accessions=["PXD000003"],
            file_bundle=1.0,
            selected_files=3,
        )
    ]
    agent = [
        _run(
            runtime="openai_agents",
            variant_id="structured",
            accessions=["PXD000001"],
            tier="2x",
            file_bundle=0.0,
            selected_files=0,
        )
    ]

    report = evaluate_replacement(
        scenarios=[scenario],
        workflow=workflow,
        agent=agent,
        gate=ReplacementGate(min_pairs=1, min_vague_quality_delta=0.0),
    )

    pair = report.tiers[0].pairs[0]
    assert pair.quality_delta > 0
    assert pair.outcome == "workflow_win"
    assert "agent file-manifest readiness regressed" in report.tiers[0].gate_reasons


def test_runs_must_be_paired_by_scenario_variant_and_repeat() -> None:
    scenario = _scenario()
    workflow = [_run(runtime="workflow", variant_id="structured", accessions=["PXD000003"])]
    agent = [_run(runtime="openai_agents", variant_id="vague", accessions=["PXD000001"], tier="2x")]

    try:
        evaluate_replacement(scenarios=[scenario], workflow=workflow, agent=agent)
    except ValueError as exc:
        assert "paired" in str(exc)
    else:
        raise AssertionError("expected unpaired replacement runs to fail")


def test_versioned_replacement_fixture_has_four_ambiguity_levels() -> None:
    from pathlib import Path

    scenarios = load_replacement_scenarios(
        Path(__file__).resolve().parents[1]
        / "benchmarks"
        / "discovery_replacement_scenarios.v2.json"
    )

    assert len(scenarios) == 3
    assert all(len(scenario.prompt_variants) == 4 for scenario in scenarios)
    assert {
        variant.ambiguity_level
        for scenario in scenarios
        for variant in scenario.prompt_variants
    } == {"structured", "clear", "vague", "ambiguous"}


def test_task_first_v3_fixtures_have_development_and_holdout_prompts() -> None:
    from pathlib import Path

    scenarios = load_replacement_scenarios(
        Path(__file__).resolve().parents[1]
        / "benchmarks"
        / "discovery_task_first_scenarios.v3.json"
    )

    holdout = load_replacement_scenarios(
        Path(__file__).resolve().parents[1]
        / "benchmarks"
        / "discovery_task_first_holdout.v3.json"
    )

    expected_tasks = {
        "rt_prediction",
        "fragment_intensity_prediction",
        "psm_scoring",
        "ptm_denovo",
        "chimeric_interpretation",
        "denovo",
    }
    for fixture in (scenarios, holdout):
        assert len(fixture) == 6
        assert all(not scenario.relevance_judgments for scenario in fixture)
        assert all(not scenario.variant_relevance_judgments for scenario in fixture)
        assert {scenario.task_type for scenario in fixture} == expected_tasks
        assert all(len(scenario.prompt_variants) == 3 for scenario in fixture)
        assert all(
            {variant.id for variant in scenario.prompt_variants}
            == {"explicit", "ordinary", "vague"}
            for scenario in fixture
        )
        assert all(
            "PXD" not in variant.prompt
            for scenario in fixture
            for variant in scenario.prompt_variants
        )
