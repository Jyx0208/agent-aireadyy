from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from agent.discovery.models import DatasetRequest
from agent.discovery.replacement_evaluation import (
    PromptVariant,
    ReplacementBenchmarkScenario,
    ReplacementRun,
)


def _runner_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_discovery_replacement_benchmark.py"
    spec = importlib.util.spec_from_file_location("replacement_benchmark_runner", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _scenario() -> ReplacementBenchmarkScenario:
    return ReplacementBenchmarkScenario(
        id="human_neuron",
        hidden_request=DatasetRequest(
            query_terms=["human", "sensory neuron"],
            species=["Homo sapiens"],
            acquisition_mode="dda",
            labeling_strategy="label_free",
        ),
        task_type="psm_scoring",
        prompt_variants=[
            PromptVariant(
                id="clear",
                ambiguity_level="clear",
                mode="raw_prompt",
                prompt="Find human sensory-neuron proteomics.",
                hard_constraint_fields=["species"],
            )
        ],
        relevance_judgments={"PXD000001": 3},
    )


def _run(runtime: str, tier: str) -> ReplacementRun:
    return ReplacementRun(
        scenario_id="human_neuron",
        variant_id="clear",
        runtime=runtime,
        budget_tier=tier,
        status="completed",
        selected_project_accessions=["PXD000001"],
    )


def test_resume_reuses_validated_run_artifact(tmp_path: Path) -> None:
    runner = _runner_module()
    scenario = _scenario()
    variant = scenario.prompt_variants[0]
    path = runner._run_result_path(
        tmp_path,
        scenario=scenario,
        variant=variant,
        runtime="workflow",
        budget_tier="baseline",
        repeat=0,
    )
    path.write_text(
        json.dumps({"result": _run("workflow", "baseline").model_dump(mode="json"), "record": {}}),
        encoding="utf-8",
    )

    resumed = runner._run_or_resume(
        scenario=scenario,
        variant=variant,
        runtime="workflow",
        budget_tier="baseline",
        repeat=0,
        output_root=tmp_path,
        resume=True,
    )

    assert resumed.runtime == "workflow"
    assert resumed.selected_project_accessions == ["PXD000001"]


def test_blinded_pool_hides_runtime_and_accession(tmp_path: Path) -> None:
    runner = _runner_module()
    scenario = _scenario()
    project = {
        "project_accession": "PXD000001",
        "project_title": "Human sensory neuron proteomics",
        "species": ["human"],
        "acquisition_mode": "dda",
        "labeling_strategy": "label_free",
        "instrument_families": ["orbitrap"],
    }
    for runtime, tier in (("workflow", "baseline"), ("openai_agents", "2x")):
        run = _run(runtime, tier)
        path = tmp_path / f"human_neuron.clear.repeat-0.{runtime}.{tier}.json"
        path.write_text(
            json.dumps(
                {
                    "result": run.model_dump(mode="json"),
                    "record": {"projects": [project]},
                }
            ),
            encoding="utf-8",
        )

    runner._write_blinded_judgment_pool(tmp_path, [scenario])

    blinded_text = (tmp_path / "judgment_pool.blinded.json").read_text(encoding="utf-8")
    key_payload = json.loads((tmp_path / "judgment_pool.key.json").read_text(encoding="utf-8"))
    blinded = json.loads(blinded_text)
    assert len(blinded["candidates"]) == 1
    assert "PXD000001" not in blinded_text
    assert "openai_agents" not in blinded_text
    assert '"workflow"' not in blinded_text
    assert key_payload["candidates"][0]["project_accession"] == "PXD000001"
    assert len(key_payload["candidates"][0]["observed_in"]) == 2
    task = blinded["tasks"]["human_neuron:clear"]
    assert task["visible_prompt"] == "Find human sensory-neuron proteomics."
    assert task["visible_hard_constraint_fields"] == ["species"]


def test_blinded_pool_scores_same_project_separately_for_each_prompt(tmp_path: Path) -> None:
    runner = _runner_module()
    scenario = _scenario().model_copy(
        update={
            "prompt_variants": [
                _scenario().prompt_variants[0],
                PromptVariant(
                    id="vague",
                    ambiguity_level="vague",
                    mode="raw_prompt",
                    prompt="Find useful neuron data.",
                ),
            ]
        }
    )
    project = {
        "project_accession": "PXD000001",
        "project_title": "Human sensory neuron proteomics",
    }
    for variant_id in ("clear", "vague"):
        run = _run("openai_agents", "2x").model_copy(update={"variant_id": variant_id})
        path = tmp_path / f"human_neuron.{variant_id}.repeat-0.openai_agents.2x.json"
        path.write_text(
            json.dumps({"result": run.model_dump(mode="json"), "record": {"projects": [project]}}),
            encoding="utf-8",
        )

    runner._write_blinded_judgment_pool(tmp_path, [scenario])

    blinded = json.loads((tmp_path / "judgment_pool.blinded.json").read_text(encoding="utf-8"))
    key_payload = json.loads((tmp_path / "judgment_pool.key.json").read_text(encoding="utf-8"))
    assert len(blinded["candidates"]) == 2
    assert {item["variant_id"] for item in blinded["candidates"]} == {"clear", "vague"}
    assert len({item["candidate_id"] for item in key_payload["candidates"]}) == 2


def test_blinded_pool_includes_neutral_candidates_without_leaking_source(tmp_path: Path) -> None:
    runner = _runner_module()
    scenario = _scenario()
    (tmp_path / "neutral_pool.json").write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "scenario_id": "human_neuron",
                        "variant_id": "clear",
                        "project_accession": "PXD_NEUTRAL",
                        "matched_queries": ["neuron"],
                        "project_title": "Independent candidate",
                        "species": ["Homo sapiens"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    runner._write_blinded_judgment_pool(tmp_path, [scenario])

    blinded_text = (tmp_path / "judgment_pool.blinded.json").read_text(encoding="utf-8")
    key_payload = json.loads((tmp_path / "judgment_pool.key.json").read_text(encoding="utf-8"))
    assert "PXD_NEUTRAL" not in blinded_text
    assert "neutral_high_recall_pool" not in blinded_text
    assert key_payload["candidates"][0]["project_accession"] == "PXD_NEUTRAL"
    assert key_payload["candidates"][0]["observed_in"][0]["source"] == "neutral_high_recall_pool"


def test_benchmark_output_is_protected_from_web_result_cleanup(tmp_path: Path) -> None:
    runner = _runner_module()
    output_root = tmp_path / "runs" / "benchmarks" / "pilot"
    output_root.mkdir(parents=True)

    runner._protect_benchmark_output(output_root)

    assert (output_root / ".agent_keep").is_file()
    assert (tmp_path / "runs" / "benchmarks" / ".agent_keep").is_file()
