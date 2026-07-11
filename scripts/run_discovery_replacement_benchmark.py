from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from agent.discovery.replacement_evaluation import (
    BudgetTier,
    PromptVariant,
    ReplacementBenchmarkReport,
    ReplacementBenchmarkScenario,
    ReplacementRun,
    build_variant_runtime_input,
    evaluate_replacement,
    load_replacement_scenarios,
    replacement_run_from_record,
)
from agent.repositories.metering import meter_repository_requests
from agent.web import app as web_app


DEFAULT_SCENARIOS = (
    Path(__file__).resolve().parents[1]
    / "benchmarks"
    / "discovery_replacement_scenarios.v2.json"
)
TIER_LIMITS: dict[str, dict[str, str]] = {
    "1x": {
        "AGENT_INITIAL_QUERY_UNITS": "12",
        "AGENT_EXPANDED_QUERY_UNITS": "12",
        "AGENT_MAX_QUERY_UNITS": "12",
        "AGENT_INITIAL_REPOSITORY_REQUESTS": "80",
        "AGENT_EXPANDED_REPOSITORY_REQUESTS": "80",
        "AGENT_MAX_REPOSITORY_REQUESTS": "80",
        "AGENT_MAX_ELAPSED_SECONDS": "900",
    },
    "2x": {
        "AGENT_INITIAL_QUERY_UNITS": "12",
        "AGENT_EXPANDED_QUERY_UNITS": "30",
        "AGENT_MAX_QUERY_UNITS": "30",
        "AGENT_INITIAL_REPOSITORY_REQUESTS": "80",
        "AGENT_EXPANDED_REPOSITORY_REQUESTS": "160",
        "AGENT_MAX_REPOSITORY_REQUESTS": "160",
        "AGENT_MAX_ELAPSED_SECONDS": "1200",
    },
    "max_quality": {
        "AGENT_INITIAL_QUERY_UNITS": "12",
        "AGENT_EXPANDED_QUERY_UNITS": "30",
        "AGENT_MAX_QUERY_UNITS": "60",
        "AGENT_INITIAL_REPOSITORY_REQUESTS": "80",
        "AGENT_EXPANDED_REPOSITORY_REQUESTS": "160",
        "AGENT_MAX_REPOSITORY_REQUESTS": "300",
        "AGENT_MAX_ELAPSED_SECONDS": "1800",
    },
}


def run_replacement_benchmark(
    *,
    scenarios: Sequence[ReplacementBenchmarkScenario],
    output_root: Path,
    tiers: Sequence[str],
    repeats: int,
) -> ReplacementBenchmarkReport:
    output_root.mkdir(parents=True, exist_ok=True)
    web_app._runs_dir = output_root / "runs"
    workflow_runs: list[ReplacementRun] = []
    agent_runs: list[ReplacementRun] = []
    managed_env = {
        "AGENT_DISCOVERY_MODE",
        "AGENT_INITIAL_QUERY_UNITS",
        "AGENT_EXPANDED_QUERY_UNITS",
        "AGENT_MAX_QUERY_UNITS",
        "AGENT_INITIAL_REPOSITORY_REQUESTS",
        "AGENT_EXPANDED_REPOSITORY_REQUESTS",
        "AGENT_MAX_REPOSITORY_REQUESTS",
        "AGENT_MAX_ELAPSED_SECONDS",
    }
    previous_env = {name: os.environ.get(name) for name in managed_env}
    try:
        for repeat in range(repeats):
            for scenario in scenarios:
                for variant in scenario.prompt_variants:
                    workflow_runs.append(
                        _run_one(
                            scenario=scenario,
                            variant=variant,
                            runtime="workflow",
                            budget_tier="baseline",
                            repeat=repeat,
                            output_root=output_root,
                        )
                    )
                    for tier in tiers:
                        _set_agent_tier(tier)
                        agent_runs.append(
                            _run_one(
                                scenario=scenario,
                                variant=variant,
                                runtime="openai_agents",
                                budget_tier=tier,
                                repeat=repeat,
                                output_root=output_root,
                            )
                        )
    finally:
        for name, value in previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    report = evaluate_replacement(
        scenarios=scenarios,
        workflow=workflow_runs,
        agent=agent_runs,
    )
    _write_report(output_root, report, workflow_runs, agent_runs)
    return report


def _run_one(
    *,
    scenario: ReplacementBenchmarkScenario,
    variant: PromptVariant,
    runtime: str,
    budget_tier: str,
    repeat: int,
    output_root: Path,
) -> ReplacementRun:
    print(
        f"[{scenario.id}/{variant.id}/repeat-{repeat}] starting {runtime} {budget_tier}",
        flush=True,
    )
    runtime_input = build_variant_runtime_input(scenario, variant)
    request_payload = runtime_input.get("request")
    body: dict[str, Any] = {
        "prompt": runtime_input["prompt"],
        "runtime": runtime,
        "source": "remote",
        "task_type": scenario.task_type,
        "use_memory": False,
        "save_memory": False,
        "llm_config": {},
    }
    if isinstance(request_payload, dict):
        body.update(request_payload)
    if runtime == "workflow":
        body.update({"agentic": True, "agentic_rounds": 2})
    request_count = 0

    def count_request(_repository: str, _operation: str) -> None:
        nonlocal request_count
        request_count += 1

    started = time.monotonic()
    with meter_repository_requests(count_request):
        record = web_app._run_web_discovery(
            body,
            report=lambda message: print(
                f"[{scenario.id}/{variant.id}/{runtime}/{budget_tier}] {message}",
                flush=True,
            ),
        )
    elapsed = time.monotonic() - started
    result = replacement_run_from_record(
        scenario=scenario,
        variant=variant,
        runtime=runtime,
        budget_tier=budget_tier,
        record=record,
        elapsed_seconds=elapsed,
        repeat=repeat,
        repository_requests=(
            int((record.get("agent") or {}).get("repository_requests") or 0)
            if runtime == "openai_agents"
            else request_count
        ),
    )
    stem = f"{scenario.id}.{variant.id}.repeat-{repeat}.{runtime}.{budget_tier}"
    (output_root / f"{stem}.json").write_text(
        json.dumps(
            {"result": result.model_dump(mode="json"), "record": record},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"[{scenario.id}/{variant.id}] {runtime} {budget_tier}: "
        f"projects={len(result.selected_project_accessions)}, "
        f"requests={result.repository_requests}, elapsed={result.elapsed_seconds:.1f}s",
        flush=True,
    )
    return result


def _set_agent_tier(tier: str) -> None:
    limits = TIER_LIMITS.get(tier)
    if limits is None:
        raise ValueError(f"unknown Agent budget tier: {tier}")
    os.environ["AGENT_DISCOVERY_MODE"] = "multi_agent"
    os.environ.update(limits)


def _write_report(
    output_root: Path,
    report: ReplacementBenchmarkReport,
    workflow_runs: Sequence[ReplacementRun],
    agent_runs: Sequence[ReplacementRun],
) -> None:
    payload = {
        "report": report.model_dump(mode="json"),
        "workflow_runs": [run.model_dump(mode="json") for run in workflow_runs],
        "agent_runs": [run.model_dump(mode="json") for run in agent_runs],
    }
    (output_root / "replacement_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Discovery Agent Replacement Benchmark",
        "",
        f"Replacement ready: **{report.replacement_ready}**",
        f"Winning budget tier: `{report.winning_budget_tier or 'none'}`",
        "",
        "| Tier | Eligible pairs | Wins/Ties/Losses | Quality delta | Vague delta | Request ratio | Ready |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for tier in report.tiers:
        lines.append(
            f"| {tier.budget_tier} | {tier.pair_count}/{tier.total_pairs} | "
            f"{tier.agent_wins}/{tier.ties}/{tier.workflow_wins} | "
            f"{tier.average_quality_delta:+.3f} | {tier.vague_quality_delta:+.3f} | "
            f"{tier.repository_request_ratio:.2f} | {tier.replacement_ready} |"
        )
        if tier.gate_reasons:
            lines.append(f"|  |  | Gate | {'; '.join(tier.gate_reasons)} |  |  |  |")
    (output_root / "replacement_report.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the quality-first Discovery Agent replacement benchmark."
    )
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--tier", action="append", dest="tiers")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--scenario", action="append", dest="scenario_ids")
    parser.add_argument("--variant", action="append", dest="variant_ids")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        if args.repeat < 1:
            raise ValueError("repeat must be at least one")
        tiers = args.tiers or ["1x", "2x", "max_quality"]
        if any(tier not in TIER_LIMITS for tier in tiers):
            raise ValueError("tier must be one of: 1x, 2x, max_quality")
        scenarios = load_replacement_scenarios(args.scenarios)
        if args.scenario_ids:
            selected = set(args.scenario_ids)
            scenarios = [scenario for scenario in scenarios if scenario.id in selected]
        if args.variant_ids:
            selected_variants = set(args.variant_ids)
            scenarios = [
                scenario.model_copy(
                    update={
                        "prompt_variants": [
                            variant
                            for variant in scenario.prompt_variants
                            if variant.id in selected_variants
                        ]
                    }
                )
                for scenario in scenarios
                if any(variant.id in selected_variants for variant in scenario.prompt_variants)
            ]
        if not scenarios:
            raise ValueError("no replacement scenarios selected")
        report = run_replacement_benchmark(
            scenarios=scenarios,
            output_root=args.output_root,
            tiers=tiers,
            repeats=args.repeat,
        )
    except Exception as exc:
        print(f"replacement benchmark error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if report.replacement_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
