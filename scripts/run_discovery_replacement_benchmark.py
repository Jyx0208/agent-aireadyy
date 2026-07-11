from __future__ import annotations

import argparse
import hashlib
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
    apply_replacement_judgment_overlay,
    build_variant_runtime_input,
    evaluate_replacement,
    load_replacement_scenarios,
    load_replacement_judgment_overlay,
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
    resume: bool = False,
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
                        _run_or_resume(
                            scenario=scenario,
                            variant=variant,
                            runtime="workflow",
                            budget_tier="baseline",
                            repeat=repeat,
                            output_root=output_root,
                            resume=resume,
                        )
                    )
                    for tier in tiers:
                        _set_agent_tier(tier)
                        agent_runs.append(
                            _run_or_resume(
                                scenario=scenario,
                                variant=variant,
                                runtime="openai_agents",
                                budget_tier=tier,
                                repeat=repeat,
                                output_root=output_root,
                                resume=resume,
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
    _write_blinded_judgment_pool(output_root, scenarios)
    return report


def _run_or_resume(
    *,
    scenario: ReplacementBenchmarkScenario,
    variant: PromptVariant,
    runtime: str,
    budget_tier: str,
    repeat: int,
    output_root: Path,
    resume: bool,
) -> ReplacementRun:
    path = _run_result_path(
        output_root,
        scenario=scenario,
        variant=variant,
        runtime=runtime,
        budget_tier=budget_tier,
        repeat=repeat,
    )
    if resume and path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
        run = ReplacementRun.model_validate(payload.get("result"))
        expected = (scenario.id, variant.id, repeat, runtime, budget_tier)
        actual = (
            run.scenario_id,
            run.variant_id,
            run.repeat,
            run.runtime,
            run.budget_tier,
        )
        if actual != expected:
            raise ValueError(f"resume artifact identity mismatch: {path}")
        print(
            f"[{scenario.id}/{variant.id}/repeat-{repeat}] resumed {runtime} {budget_tier}",
            flush=True,
        )
        return run
    return _run_one(
        scenario=scenario,
        variant=variant,
        runtime=runtime,
        budget_tier=budget_tier,
        repeat=repeat,
        output_root=output_root,
    )


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
    path = _run_result_path(
        output_root,
        scenario=scenario,
        variant=variant,
        runtime=runtime,
        budget_tier=budget_tier,
        repeat=repeat,
    )
    path.write_text(
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


def _run_result_path(
    output_root: Path,
    *,
    scenario: ReplacementBenchmarkScenario,
    variant: PromptVariant,
    runtime: str,
    budget_tier: str,
    repeat: int,
) -> Path:
    stem = f"{scenario.id}.{variant.id}.repeat-{repeat}.{runtime}.{budget_tier}"
    return output_root / f"{stem}.json"


def _write_blinded_judgment_pool(
    output_root: Path,
    scenarios: Sequence[ReplacementBenchmarkScenario],
) -> None:
    scenarios_by_id = {scenario.id: scenario for scenario in scenarios}
    pooled: dict[tuple[str, str, str], dict[str, Any]] = {}
    provenance: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for path in sorted(output_root.glob("*.repeat-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        result = payload.get("result") if isinstance(payload, dict) else None
        record = payload.get("record") if isinstance(payload, dict) else None
        if not isinstance(result, dict) or not isinstance(record, dict):
            continue
        scenario_id = str(result.get("scenario_id") or "")
        if scenario_id not in scenarios_by_id:
            continue
        variant_id = str(result.get("variant_id") or "")
        scenario = scenarios_by_id[scenario_id]
        variant = next(
            (item for item in scenario.prompt_variants if item.id == variant_id),
            None,
        )
        if variant is None:
            continue
        projects = [item for item in record.get("projects") or [] if isinstance(item, dict)]
        for project in projects:
            accession = str(project.get("project_accession") or "").strip().upper()
            if not accession:
                continue
            key = (scenario_id, variant_id, accession)
            candidate_id = "candidate_" + hashlib.sha256(
                f"{scenario_id}:{variant_id}:{accession}".encode("utf-8")
            ).hexdigest()[:12]
            metadata = {
                "candidate_id": candidate_id,
                "scenario_id": scenario_id,
                "variant_id": variant_id,
                "visible_prompt": variant.prompt,
                "visible_hard_constraint_fields": variant.hard_constraint_fields,
                "project_title": str(project.get("project_title") or ""),
                "project_description": str(project.get("project_description") or ""),
                "species": list(project.get("species") or []),
                "acquisition_mode": project.get("acquisition_mode"),
                "labeling_strategy": project.get("labeling_strategy"),
                "instrument_families": list(project.get("instrument_families") or []),
                "fragmentation_methods": list(project.get("fragmentation_methods") or []),
                "immunopeptide_scope": project.get("immunopeptide_scope"),
                "hla_class": list(project.get("hla_class") or []),
                "immunopeptide_enrichment_methods": list(
                    project.get("immunopeptide_enrichment_methods") or []
                ),
                "validity_status": project.get("validity_status"),
                "evidence_completeness": project.get("evidence_completeness"),
                "selected_file_count": project.get("selected_file_count"),
                "grade": None,
                "review_notes": "",
                "reviewer_id": "",
            }
            current = pooled.get(key)
            if current is None or len(json.dumps(metadata, ensure_ascii=False)) > len(
                json.dumps(current, ensure_ascii=False)
            ):
                pooled[key] = metadata
            provenance.setdefault(key, []).append(
                {
                    "runtime": result.get("runtime"),
                    "budget_tier": result.get("budget_tier"),
                    "repeat": result.get("repeat"),
                    "artifact": path.name,
                }
            )

    tasks = {
        f"{scenario.id}:{variant.id}": {
            "scenario_id": scenario.id,
            "variant_id": variant.id,
            "task_type": scenario.task_type,
            "visible_prompt": variant.prompt,
            "visible_hard_constraint_fields": variant.hard_constraint_fields,
        }
        for scenario in scenarios
        for variant in scenario.prompt_variants
    }
    blinded = {
        "schema_version": "discovery-judgment-pool/v1",
        "instructions": {
            "grade_3": "Directly satisfies the biological task and important explicit constraints.",
            "grade_2": "Strongly relevant and usable, with a minor scope or evidence gap.",
            "grade_1": "Related topic but not a suitable answer to the requested task.",
            "grade_0": "Off-topic or contradicts an explicit hard constraint.",
            "review_rule": "Judge repository metadata only. Candidate origin is intentionally hidden.",
        },
        "tasks": tasks,
        "candidates": [pooled[key] for key in sorted(pooled)],
    }
    key_payload = {
        "schema_version": "discovery-judgment-key/v1",
        "candidates": [
            {
                "candidate_id": pooled[key]["candidate_id"],
                "scenario_id": key[0],
                "variant_id": key[1],
                "project_accession": key[2],
                "observed_in": provenance[key],
            }
            for key in sorted(pooled)
        ],
    }
    (output_root / "judgment_pool.blinded.json").write_text(
        json.dumps(blinded, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_root / "judgment_pool.key.json").write_text(
        json.dumps(key_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


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
    parser.add_argument(
        "--judgments",
        type=Path,
        help="Optional reviewed per-variant relevance judgment overlay.",
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--tier", action="append", dest="tiers")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--scenario", action="append", dest="scenario_ids")
    parser.add_argument("--variant", action="append", dest="variant_ids")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse validated per-run JSON artifacts already present in output-root.",
    )
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
        if args.judgments:
            scenarios = apply_replacement_judgment_overlay(
                scenarios,
                load_replacement_judgment_overlay(args.judgments),
            )
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
            resume=args.resume,
        )
    except Exception as exc:
        print(f"replacement benchmark error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if report.replacement_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
