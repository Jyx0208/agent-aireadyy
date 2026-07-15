from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from agent.discovery.replacement_evaluation import (
    ReplacementBenchmarkScenario,
    ReplacementRun,
    score_replacement_run,
)
from agent.web.expert_review.grading import effective_grade


def load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("json_root_must_be_object")
    return payload


def build_judgment_map_from_pool_and_key(
    pool: Mapping[str, Any],
    key_payload: Mapping[str, Any],
) -> dict[str, dict[str, dict[str, int]]]:
    """scenario -> variant -> accession -> grade using effective grades."""
    key_by_id = {
        str(item.get("candidate_id") or ""): item
        for item in (key_payload.get("candidates") or [])
        if isinstance(item, dict) and str(item.get("candidate_id") or "")
    }
    judgments: dict[str, dict[str, dict[str, int]]] = {}
    for candidate in pool.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        cid = str(candidate.get("candidate_id") or "")
        key = key_by_id.get(cid)
        grade = effective_grade(candidate)
        if key is None or grade is None:
            continue
        scenario_id = str(key.get("scenario_id") or candidate.get("scenario_id") or "")
        variant_id = str(key.get("variant_id") or candidate.get("variant_id") or "")
        accession = str(key.get("project_accession") or "").strip().upper()
        if not scenario_id or not variant_id or not accession:
            continue
        judgments.setdefault(scenario_id, {}).setdefault(variant_id, {})[accession] = grade
    return judgments


def _scenario_from_judgments(
    scenario_id: str,
    variant_id: str,
    judgments: Mapping[str, int],
) -> ReplacementBenchmarkScenario:
    from agent.discovery.models import DatasetRequest

    ambiguity = "clear" if variant_id in {"clear", "structured", "vague", "ambiguous"} else "clear"
    if variant_id in {"structured", "clear", "vague", "ambiguous"}:
        ambiguity = variant_id  # type: ignore[assignment]
    mode = "parsed_spec" if variant_id == "structured" else "raw_prompt"
    return ReplacementBenchmarkScenario(
        id=scenario_id,
        hidden_request=DatasetRequest(species=[], goal="general"),
        prompt_variants=[
            {
                "id": variant_id,
                "ambiguity_level": ambiguity if ambiguity in {"structured", "clear", "vague", "ambiguous"} else "clear",
                "mode": mode,
                "prompt": f"{scenario_id}:{variant_id}",
            }
        ],
        relevance_judgments={},
        variant_relevance_judgments={variant_id: {k: int(v) for k, v in judgments.items()}},
        variant_judgment_sources={variant_id: "human_verified"},
    )


def _run_from_payload(payload: Mapping[str, Any]) -> ReplacementRun:
    data = dict(payload)
    if "selected_project_accessions" not in data:
        accessions = data.get("project_accessions") or data.get("accessions") or []
        data["selected_project_accessions"] = list(accessions)
    data.setdefault("status", "completed")
    runtime = str(data.get("runtime") or "openai_agents")
    if runtime not in {"workflow", "openai_agents"}:
        runtime = "openai_agents" if "agent" in runtime else "workflow"
    data["runtime"] = runtime
    tier = str(data.get("budget_tier") or "baseline")
    if tier not in {"baseline", "1x", "2x", "max_quality"}:
        tier = "baseline"
    data["budget_tier"] = tier
    data.setdefault("scenario_id", data.get("scenario_id") or "scenario")
    data.setdefault("variant_id", data.get("variant_id") or "clear")
    return ReplacementRun.model_validate(data)


def score_runs_with_judgments(
    runs: list[Mapping[str, Any]],
    judgments: Mapping[str, Mapping[str, Mapping[str, int]]],
) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for raw in runs:
        if not isinstance(raw, Mapping):
            continue
        run = _run_from_payload(raw)
        variant_judgments = judgments.get(run.scenario_id, {}).get(run.variant_id, {})
        if not variant_judgments:
            # still score against empty judgments (all zeros)
            variant_judgments = {}
        scenario = _scenario_from_judgments(run.scenario_id, run.variant_id, variant_judgments)
        # Avoid float-edge quality_score > 1.0 from weighted sum noise.
        soft = run.model_copy(
            update={
                "task_ready_precision": min(run.task_ready_precision, 0.999),
                "file_bundle_completeness": min(run.file_bundle_completeness, 0.999),
                "evidence_completeness": min(run.evidence_completeness, 0.999),
            }
        )
        result = score_replacement_run(scenario, soft)
        scored.append(result.model_dump(mode="json"))
    return scored


def compare_agent_workflow(scored: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Pair agent vs workflow runs by scenario/variant/repeat when possible."""
    by_key: dict[tuple[str, str, int], dict[str, Mapping[str, Any]]] = {}
    for item in scored:
        key = (
            str(item.get("scenario_id") or ""),
            str(item.get("variant_id") or ""),
            int(item.get("repeat") or 0),
        )
        runtime = str(item.get("runtime") or "")
        by_key.setdefault(key, {})[runtime] = item
    wins = losses = ties = 0
    deltas: list[float] = []
    pairs = 0
    for pair in by_key.values():
        agent = pair.get("openai_agents") or pair.get("agent")
        workflow = pair.get("workflow")
        if not agent or not workflow:
            continue
        pairs += 1
        delta = float(agent.get("quality_score") or 0.0) - float(workflow.get("quality_score") or 0.0)
        deltas.append(delta)
        if delta > 1e-9:
            wins += 1
        elif delta < -1e-9:
            losses += 1
        else:
            ties += 1
    avg_delta = sum(deltas) / len(deltas) if deltas else 0.0
    return {
        "pairs": pairs,
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "win_rate": (wins / pairs) if pairs else 0.0,
        "average_quality_delta": avg_delta,
    }


def agreement_stats(pool: Mapping[str, Any]) -> dict[str, Any]:
    agree = disagree = no_machine = 0
    dist: Counter[int] = Counter()
    graded = 0
    for candidate in pool.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        grade = effective_grade(candidate)
        if grade is None:
            continue
        graded += 1
        dist[grade] += 1
        votes = candidate.get("machine_reviews") or []
        if not votes:
            machine_grade = candidate.get("grade") if not candidate.get("human_grades") else None
            if machine_grade is None:
                no_machine += 1
                continue
            if int(machine_grade) == grade:
                agree += 1
            else:
                disagree += 1
            continue
        machine_grades = [int(v.get("grade")) for v in votes if isinstance(v, dict) and v.get("grade") is not None]
        if not machine_grades:
            no_machine += 1
            continue
        machine_grades.sort()
        machine = machine_grades[len(machine_grades) // 2]
        if machine == grade:
            agree += 1
        else:
            disagree += 1
    total = len([c for c in (pool.get("candidates") or []) if isinstance(c, dict)])
    return {
        "candidate_count": total,
        "graded_count": graded,
        "coverage": graded / total if total else 0.0,
        "grade_distribution": {str(k): dist[k] for k in sorted(dist)},
        "agree_with_machine": agree,
        "disagree_with_machine": disagree,
        "no_machine": no_machine,
    }


def compute_impact(
    *,
    pool_before: Mapping[str, Any],
    pool_after: Mapping[str, Any],
    key_payload: Mapping[str, Any] | None = None,
    runs: list[Mapping[str, Any]] | None = None,
    changed_candidate_id: str | None = None,
    grade_before: int | None = None,
    grade_after: int | None = None,
) -> dict[str, Any]:
    """Compute full metric impact when key+runs present, else degrade."""
    missing: list[str] = []
    if key_payload is None:
        missing.append("judgment_key")
    if not runs:
        missing.append("replacement_runs")

    base_stats = agreement_stats(pool_after)
    if missing:
        sentences = [
            f"当前已评分 {base_stats['graded_count']}/{base_stats['candidate_count']}（覆盖率 {base_stats['coverage']:.2%}）。",
            f"与机器票一致 {base_stats['agree_with_machine']}，不一致 {base_stats['disagree_with_machine']}，无机器票 {base_stats['no_machine']}。",
            "缺少 " + "、".join(missing) + "，暂无法重算 NDCG/recall/quality 与 Agent vs Workflow。",
        ]
        if grade_before is not None or grade_after is not None:
            sentences.insert(
                0,
                f"这项分从 {grade_before}→{grade_after} 已保存；完整替换指标需绑定 key 与 run。",
            )
        return {
            "mode": "degraded",
            "missing": missing,
            "agreement": base_stats,
            "metrics_before": None,
            "metrics_after": None,
            "pair_before": None,
            "pair_after": None,
            "sentences": sentences,
            "changed_candidate_id": changed_candidate_id,
            "grade_before": grade_before,
            "grade_after": grade_after,
        }

    assert key_payload is not None
    assert runs is not None
    before_map = build_judgment_map_from_pool_and_key(pool_before, key_payload)
    after_map = build_judgment_map_from_pool_and_key(pool_after, key_payload)
    scored_before = score_runs_with_judgments(list(runs), before_map)
    scored_after = score_runs_with_judgments(list(runs), after_map)
    pair_before = compare_agent_workflow(scored_before)
    pair_after = compare_agent_workflow(scored_after)

    def _avg(items: list[dict[str, Any]], field: str) -> float:
        if not items:
            return 0.0
        return sum(float(item.get(field) or 0.0) for item in items) / len(items)

    metrics_before = {
        "ndcg_at_5": _avg(scored_before, "ndcg_at_5"),
        "high_relevance_recall": _avg(scored_before, "high_relevance_recall"),
        "quality_score": _avg(scored_before, "quality_score"),
        "run_count": len(scored_before),
    }
    metrics_after = {
        "ndcg_at_5": _avg(scored_after, "ndcg_at_5"),
        "high_relevance_recall": _avg(scored_after, "high_relevance_recall"),
        "quality_score": _avg(scored_after, "quality_score"),
        "run_count": len(scored_after),
    }

    def _delta(field: str) -> float:
        return float(metrics_after[field]) - float(metrics_before[field])

    q_delta = _delta("quality_score")
    sentences = []
    if grade_before is not None or grade_after is not None:
        sentences.append(
            f"这项分从 {grade_before}→{grade_after} 后，平均 quality {metrics_before['quality_score']:.4f}→{metrics_after['quality_score']:.4f}（Δ {q_delta:+.4f}）。"
        )
    sentences.append(
        f"NDCG@5 {metrics_before['ndcg_at_5']:.4f}→{metrics_after['ndcg_at_5']:.4f}（Δ {_delta('ndcg_at_5'):+.4f}）；"
        f" high_relevance_recall {metrics_before['high_relevance_recall']:.4f}→{metrics_after['high_relevance_recall']:.4f}（Δ {_delta('high_relevance_recall'):+.4f}）。"
    )
    if pair_after["pairs"]:
        outcome_before = _pair_label(pair_before)
        outcome_after = _pair_label(pair_after)
        sentences.append(
            f"Agent vs Workflow：胜/负/平 {pair_before['wins']}/{pair_before['losses']}/{pair_before['ties']} → "
            f"{pair_after['wins']}/{pair_after['losses']}/{pair_after['ties']}；"
            f"均 quality Δ {pair_before['average_quality_delta']:+.4f}→{pair_after['average_quality_delta']:+.4f}；"
            f"态势 {outcome_before}→{outcome_after}。"
        )
    else:
        sentences.append("未找到可配对的 Agent/Workflow run，暂无胜负统计。")

    return {
        "mode": "full",
        "missing": [],
        "agreement": base_stats,
        "metrics_before": metrics_before,
        "metrics_after": metrics_after,
        "pair_before": pair_before,
        "pair_after": pair_after,
        "sentences": sentences,
        "changed_candidate_id": changed_candidate_id,
        "grade_before": grade_before,
        "grade_after": grade_after,
        "scored_runs_after": scored_after,
    }


def _pair_label(pair: Mapping[str, Any]) -> str:
    if not pair.get("pairs"):
        return "无配对"
    wins = int(pair.get("wins") or 0)
    losses = int(pair.get("losses") or 0)
    if wins > losses:
        return "胜"
    if losses > wins:
        return "负"
    return "平"
