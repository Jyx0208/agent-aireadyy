from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from pydantic import TypeAdapter

from agent.discovery.runtime_evaluation import (
    DiscoveryBenchmarkScenario,
    DiscoveryRuntimeBenchmarkReport,
    DiscoveryRuntimeResult,
    compare_runtime_pairs,
    result_from_record,
)
from agent.repositories.metering import meter_repository_requests
from agent.web import app as web_app


DEFAULT_SCENARIOS = Path(__file__).resolve().parents[1] / "benchmarks" / "discovery_runtime_scenarios.v1.json"


def load_scenarios(path: Path) -> list[DiscoveryBenchmarkScenario]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    scenarios = TypeAdapter(list[DiscoveryBenchmarkScenario]).validate_python(payload)
    if not scenarios:
        raise ValueError("benchmark scenario file is empty")
    ids = [scenario.id for scenario in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("benchmark scenario ids must be unique")
    return scenarios


def run_benchmark(
    *,
    scenarios: Sequence[DiscoveryBenchmarkScenario],
    output_root: Path,
) -> DiscoveryRuntimeBenchmarkReport:
    output_root.mkdir(parents=True, exist_ok=True)
    web_app._runs_dir = output_root / "runs"
    workflow_results: list[DiscoveryRuntimeResult] = []
    agent_results: list[DiscoveryRuntimeResult] = []
    previous_round_limit = os.environ.get("AGENT_MAX_DISCOVERY_ROUNDS")
    os.environ["AGENT_MAX_DISCOVERY_ROUNDS"] = "2"
    try:
        for scenario in scenarios:
            for runtime in ("workflow", "openai_agents"):
                print(f"[{scenario.id}] starting {runtime}", flush=True)
                request_count = 0

                def count_request(_repository: str, _operation: str) -> None:
                    nonlocal request_count
                    request_count += 1

                body: dict[str, Any] = {
                    **scenario.request.model_dump(mode="json"),
                    "prompt": scenario.prompt,
                    "task_type": scenario.task_type,
                    "runtime": runtime,
                    "source": "remote",
                    "agentic": True,
                    "agentic_rounds": 2,
                    "use_memory": False,
                    "save_memory": False,
                    "llm_config": {},
                }
                started = time.monotonic()
                with meter_repository_requests(count_request):
                    record = web_app._run_web_discovery(body, report=lambda message: print(
                        f"[{scenario.id}][{runtime}] {message}", flush=True
                    ))
                elapsed = time.monotonic() - started
                result = result_from_record(
                    scenario=scenario,
                    runtime=runtime,
                    record=record,
                    elapsed_seconds=elapsed,
                    repository_requests=(
                        int((record.get("agent") or {}).get("repository_requests") or 0)
                        if runtime == "openai_agents"
                        else request_count
                    ),
                )
                destination = output_root / f"{scenario.id}.{runtime}.json"
                destination.write_text(
                    json.dumps(
                        {"result": result.model_dump(mode="json"), "record": record},
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                (workflow_results if runtime == "workflow" else agent_results).append(result)
                print(
                    f"[{scenario.id}] {runtime}: score={result.quality_score:.3f}, "
                    f"recall={result.expected_accession_recall:.3f}, requests={result.repository_requests}",
                    flush=True,
                )
    finally:
        if previous_round_limit is None:
            os.environ.pop("AGENT_MAX_DISCOVERY_ROUNDS", None)
        else:
            os.environ["AGENT_MAX_DISCOVERY_ROUNDS"] = previous_round_limit
    report = compare_runtime_pairs(workflow=workflow_results, agent=agent_results)
    (output_root / "benchmark_report.json").write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_root / "benchmark_report.md").write_text(_markdown_report(report), encoding="utf-8")
    return report


def _markdown_report(report: DiscoveryRuntimeBenchmarkReport) -> str:
    verdict = "PASS: measured improvement" if report.agent_real_improvement else (
        "INCONCLUSIVE" if report.inconclusive else "FAIL: no measured improvement"
    )
    lines = [
        "# Discovery Agent vs Workflow Benchmark",
        "",
        f"Verdict: **{verdict}**",
        "",
        "| Scenario | Outcome | Workflow score | Agent score | Delta | Request ratio |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for pair in report.pairs:
        lines.append(
            f"| {pair.scenario_id} | {pair.outcome} | {pair.workflow.quality_score:.3f} | "
            f"{pair.agent.quality_score:.3f} | {pair.quality_delta:+.3f} | "
            f"{pair.repository_request_ratio:.2f} |"
        )
    lines.extend(
        [
            "",
            f"Eligible pairs: {report.eligible_pairs}/{report.total_pairs}",
            f"Agent wins/ties/workflow wins: {report.agent_wins}/{report.ties}/{report.workflow_wins}",
            f"Average quality delta: {report.average_quality_delta:+.3f}",
            f"Aggregate repository request ratio: {report.aggregate_repository_request_ratio:.2f}",
            f"Aggregate elapsed time ratio: {report.aggregate_elapsed_time_ratio:.2f}",
            f"False early stops: {report.false_early_stops}",
            f"Added hard-constraint violations: {report.added_hard_constraint_violations}",
        ]
    )
    if report.gate_reasons:
        lines.extend(["", "## Gate reasons", *[f"- {reason}" for reason in report.gate_reasons]])
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a paired Discovery Agent vs Workflow benchmark.")
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--scenario", action="append", dest="scenario_ids")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        scenarios = load_scenarios(args.scenarios)
        if args.scenario_ids:
            selected = set(args.scenario_ids)
            scenarios = [scenario for scenario in scenarios if scenario.id in selected]
            missing = selected - {scenario.id for scenario in scenarios}
            if missing:
                raise ValueError(f"unknown scenario ids: {', '.join(sorted(missing))}")
        report = run_benchmark(scenarios=scenarios, output_root=args.output_root)
    except Exception as exc:
        print(f"benchmark error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if report.agent_real_improvement else 1


if __name__ == "__main__":
    raise SystemExit(main())
