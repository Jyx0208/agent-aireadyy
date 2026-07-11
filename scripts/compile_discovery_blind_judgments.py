from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def compile_blind_judgments(
    reviewed_pool: dict[str, Any],
    key_payload: dict[str, Any],
    *,
    allow_partial: bool = False,
) -> dict[str, Any]:
    key_by_id = {
        str(item.get("candidate_id") or ""): item
        for item in key_payload.get("candidates") or []
        if isinstance(item, dict) and str(item.get("candidate_id") or "")
    }
    candidates = [
        item for item in reviewed_pool.get("candidates") or [] if isinstance(item, dict)
    ]
    judgments: dict[str, dict[str, dict[str, int]]] = {}
    evidence: list[dict[str, Any]] = []
    missing: list[str] = []
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or "")
        key = key_by_id.get(candidate_id)
        if key is None:
            raise ValueError(f"reviewed candidate is missing from key: {candidate_id}")
        grade = candidate.get("grade")
        if grade is None:
            missing.append(candidate_id)
            continue
        if isinstance(grade, bool) or not isinstance(grade, int) or grade not in {0, 1, 2, 3}:
            raise ValueError(f"candidate grade must be an integer from 0 to 3: {candidate_id}")
        scenario_id = str(key.get("scenario_id") or "")
        variant_id = str(key.get("variant_id") or "")
        accession = str(key.get("project_accession") or "").strip().upper()
        if not scenario_id or not variant_id or not accession:
            raise ValueError(f"candidate key is incomplete: {candidate_id}")
        existing = judgments.setdefault(scenario_id, {}).setdefault(variant_id, {}).get(accession)
        if existing is not None and existing != grade:
            raise ValueError(f"conflicting grades for {scenario_id}:{variant_id}:{accession}")
        judgments[scenario_id][variant_id][accession] = grade
        evidence.append(
            {
                "candidate_id": candidate_id,
                "scenario_id": scenario_id,
                "variant_id": variant_id,
                "project_accession": accession,
                "grade": grade,
                "review_notes": str(candidate.get("review_notes") or ""),
                "reviewer_id": str(candidate.get("reviewer_id") or ""),
            }
        )
    if missing and not allow_partial:
        raise ValueError(f"{len(missing)} candidate(s) are not graded")
    if not evidence:
        raise ValueError("reviewed pool contains no graded candidates")
    return {
        "schema_version": "discovery-replacement-judgments/v1",
        "variant_relevance_judgments": judgments,
        "review_evidence": evidence,
        "review_summary": {
            "graded_candidates": len(evidence),
            "ungraded_candidates": len(missing),
            "complete": not missing,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compile blinded Discovery reviews for scoring.")
    parser.add_argument("--reviewed-pool", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-partial", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        reviewed = json.loads(args.reviewed_pool.read_text(encoding="utf-8"))
        key_payload = json.loads(args.key.read_text(encoding="utf-8"))
        compiled = compile_blind_judgments(
            reviewed,
            key_payload,
            allow_partial=args.allow_partial,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(compiled, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"Blind judgment compilation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(compiled["review_summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
