from __future__ import annotations

import json
import os
import time
from collections import Counter
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from statistics import median
from typing import Any, Literal

from pydantic import Field, field_validator

from agent.discovery.agentic import OpenAICompatibleDiscoveryLLM
from agent.models import JsonModel
from agent.web.llm_config_store import LLMConfigStore


JudgmentConfidence = Literal["high", "medium", "low"]
JudgmentSource = Literal["provisional_same_family", "provisional_independent_model"]


class BlindJudgmentVote(JsonModel):
    grade: int = Field(ge=0, le=3)
    reason: str = Field(min_length=1)
    supporting_evidence: list[str] = Field(default_factory=list)
    constraint_conflicts: list[str] = Field(default_factory=list)

    @field_validator("supporting_evidence", "constraint_conflicts", mode="before")
    @classmethod
    def normalize_text_lists(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value.strip() else []
        if isinstance(value, (list, tuple, set)):
            return [str(item) for item in value if str(item).strip()]
        return [str(value)]


class BlindCandidateJudgment(JsonModel):
    candidate_id: str
    grade: int = Field(ge=0, le=3)
    confidence: JudgmentConfidence
    votes: list[BlindJudgmentVote]


JudgeCall = Callable[[str, str], Mapping[str, Any]]
ReviewCallback = Callable[[dict[str, Any]], None]
ReviewErrorCallback = Callable[[str, Exception], None]
ReviewStartCallback = Callable[[str], None]
JUDGING_RUBRIC_VERSION = "adaptive-blind-judge/v2"


_SYSTEM_PROMPTS = (
    "Act as a conservative proteomics dataset curator. Grade only from the visible repository evidence.",
    "Act as an AI-ready proteomics data reviewer. Test task suitability before giving a relevance grade.",
    "Audit this candidate independently. Look first for explicit constraint conflicts, then for useful evidence.",
    "Evaluate whether a data scientist could actually use this repository project for the stated task.",
    "Perform a final independent relevance review. Do not infer missing evidence as if it were observed.",
)


def judge_blinded_pool(
    pool: Mapping[str, Any],
    judge: JudgeCall,
    *,
    model_name: str,
    judgment_source: JudgmentSource = "provisional_same_family",
    workers: int = 1,
    existing_reviews: Mapping[str, Mapping[str, Any]] | None = None,
    on_start: ReviewStartCallback | None = None,
    on_review: ReviewCallback | None = None,
    on_error: ReviewErrorCallback | None = None,
) -> dict[str, Any]:
    candidates = [item for item in pool.get("candidates") or [] if isinstance(item, dict)]
    if workers < 1:
        raise ValueError("judge workers must be at least one")
    reviewed_by_id = {
        candidate_id: dict(item)
        for candidate_id, item in (existing_reviews or {}).items()
        if candidate_id
    }
    pending = [
        candidate
        for candidate in candidates
        if str(candidate.get("candidate_id") or "") not in reviewed_by_id
    ]

    def store_result(candidate: Mapping[str, Any], result: BlindCandidateJudgment) -> None:
        item = _reviewed_candidate(candidate, result, model_name=model_name)
        reviewed_by_id[result.candidate_id] = item
        if on_review is not None:
            on_review(item)

    def handle_error(candidate: Mapping[str, Any], exc: Exception) -> None:
        if str(exc) == "job_cancelled":
            raise exc
        if on_error is None:
            raise exc
        on_error(str(candidate.get("candidate_id") or ""), exc)

    def review_candidate(candidate: Mapping[str, Any]) -> BlindCandidateJudgment:
        if on_start is not None:
            on_start(str(candidate.get("candidate_id") or ""))
        return judge_candidate(candidate, judge)

    if workers == 1:
        for candidate in pending:
            try:
                store_result(candidate, review_candidate(candidate))
            except Exception as exc:
                handle_error(candidate, exc)
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(review_candidate, candidate): candidate
                for candidate in pending
            }
            for future in as_completed(futures):
                candidate = futures[future]
                try:
                    store_result(candidate, future.result())
                except Exception as exc:
                    handle_error(candidate, exc)
    reviewed = [
        reviewed_by_id[str(candidate.get("candidate_id") or "")]
        for candidate in candidates
        if str(candidate.get("candidate_id") or "") in reviewed_by_id
    ]
    confidence_counts: Counter[str] = Counter()
    vote_count = 0
    expanded_count = 0
    for item in reviewed:
        votes = list(item.get("machine_reviews") or [])
        confidence_counts[str(item.get("judgment_confidence") or "low")] += 1
        vote_count += len(votes)
        expanded_count += len(votes) == 5
    return {
        **{key: value for key, value in pool.items() if key != "candidates"},
        "schema_version": "discovery-judgment-pool-reviewed/v2",
        "judgment_source": judgment_source,
        "review_method": "adaptive_blind_llm_2_then_5",
        "rubric_version": JUDGING_RUBRIC_VERSION,
        "review_model": model_name,
        "candidates": reviewed,
        "review_summary": {
            "candidate_count": len(reviewed),
            "vote_count": vote_count,
            "two_vote_candidates": len(reviewed) - expanded_count,
            "five_vote_candidates": expanded_count,
            "confidence_counts": dict(confidence_counts),
            "formal_replacement_evidence": judgment_source
            == "provisional_independent_model",
        },
    }


def _reviewed_candidate(
    candidate: Mapping[str, Any],
    result: BlindCandidateJudgment,
    *,
    model_name: str,
) -> dict[str, Any]:
    item = dict(candidate)
    item.update(
        {
            "grade": result.grade,
            "review_notes": result.votes[-1].reason,
            "reviewer_id": f"llm:{model_name}",
            "judgment_confidence": result.confidence,
            "machine_reviews": [vote.model_dump(mode="json") for vote in result.votes],
        }
    )
    return item


def judge_candidate(candidate: Mapping[str, Any], judge: JudgeCall) -> BlindCandidateJudgment:
    candidate_id = str(candidate.get("candidate_id") or "").strip()
    if not candidate_id:
        raise ValueError("blind candidate requires candidate_id")
    visible = _visible_candidate(candidate)
    votes = [_call_judge(judge, visible, round_index=index) for index in range(2)]
    if votes[0].grade != votes[1].grade:
        votes.extend(_call_judge(judge, visible, round_index=index) for index in range(2, 5))
    grades = [vote.grade for vote in votes]
    return BlindCandidateJudgment(
        candidate_id=candidate_id,
        grade=int(median(grades)),
        confidence=_confidence(grades),
        votes=votes,
    )


def load_saved_judge(
    config_path: str | Path | None = None,
) -> tuple[JudgeCall, dict[str, str]]:
    path = Path(config_path or os.getenv("AGENT_LLM_CONFIG_PATH") or ".agent_secrets/llm_config.json")
    config = LLMConfigStore(path).load() or _environment_config()
    if config is None:
        raise ValueError(
            "No judge LLM API key found. Save API Configuration in the web UI or set an LLM API key environment variable."
        )
    client = OpenAICompatibleDiscoveryLLM(
        api_key=config["api_key"],
        model=config["model"],
        base_url=config["base_url"],
        timeout=float(config["timeout"]),
    )

    def complete(system_prompt: str, user_prompt: str) -> Mapping[str, Any]:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return client.complete_json(system_prompt=system_prompt, user_prompt=user_prompt)
            except Exception as exc:  # pragma: no cover - live API boundary
                last_error = exc
                if attempt < 2:
                    time.sleep(2**attempt)
        assert last_error is not None
        raise last_error

    return complete, config


def _call_judge(
    judge: JudgeCall,
    visible_candidate: Mapping[str, Any],
    *,
    round_index: int,
) -> BlindJudgmentVote:
    rubric = (
        "Return one JSON object with grade (integer 0-3), reason, supporting_evidence, and constraint_conflicts. "
        "Grade 3: directly satisfies the task and important explicit constraints. "
        "Grade 2: strongly relevant and usable with a minor scope or evidence gap. "
        "Grade 1: related but not a suitable answer. Grade 0: off-topic or contradicts an explicit hard constraint. "
        "Missing metadata is an evidence gap, not positive evidence. Do not mention or guess project accession or result origin."
    )
    payload = judge(
        f"{_SYSTEM_PROMPTS[round_index]} {rubric}",
        json.dumps(visible_candidate, ensure_ascii=False, sort_keys=round_index % 2 == 0),
    )
    return BlindJudgmentVote.model_validate(payload)


def _strip_hidden(value: Any) -> Any:
    hidden = {
        "grade", "review_notes", "reviewer_id", "human_grades", "judgment_confidence",
        "machine_reviews", "machine_review_runs", "judgment_source", "review_model",
        "review_method", "rubric_version", "runtime", "runtime_label", "project_accession",
        "accession", "observed_in", "source", "source_system", "system_name", "agent_runtime",
        "workflow_name",
    }
    if isinstance(value, Mapping):
        return {key: _strip_hidden(item) for key, item in value.items() if key not in hidden}
    if isinstance(value, list):
        return [_strip_hidden(item) for item in value]
    return value


def _visible_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return _strip_hidden(dict(candidate))


def _confidence(grades: list[int]) -> JudgmentConfidence:
    counts = Counter(grades)
    if len(grades) == 5 and max(counts.values()) >= 4:
        return "high"
    if len(grades) == 2 and grades[0] == grades[1]:
        return "medium"
    if max(counts.values()) >= 3 and max(grades) - min(grades) <= 1:
        return "medium"
    return "low"


def _environment_config() -> dict[str, str] | None:
    api_key = (
        os.getenv("AGENT_LLM_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or ""
    ).strip()
    if not api_key:
        return None
    return {
        "api_key": api_key,
        "base_url": (
            os.getenv("AGENT_LLM_BASE_URL")
            or os.getenv("DEEPSEEK_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        ).strip().rstrip("/"),
        "model": (
            os.getenv("AGENT_LLM_MODEL")
            or os.getenv("DEEPSEEK_MODEL")
            or os.getenv("OPENAI_DEFAULT_MODEL")
            or "gpt-5.4-mini"
        ).strip(),
        "timeout": (os.getenv("AGENT_LLM_TIMEOUT") or "120").strip(),
    }
