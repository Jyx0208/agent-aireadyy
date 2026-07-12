from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from agent.discovery.blind_judging import (
    JUDGING_RUBRIC_VERSION,
    judge_blinded_pool,
    load_saved_judge,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Adaptively judge a blinded Discovery pool.")
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--progress", type=Path)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument(
        "--independent-model",
        action="store_true",
        help="Mark the judge as a model family independent from the evaluated Agent.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        pool = json.loads(args.pool.read_text(encoding="utf-8"))
        judge, config = load_saved_judge(args.config)
        judgment_source = (
            "provisional_independent_model"
            if args.independent_model
            else "provisional_same_family"
        )
        progress_path = args.progress or args.output.with_name(
            args.output.name + ".progress.jsonl"
        )
        cache_keys = {
            str(candidate.get("candidate_id") or ""): _cache_key(
                candidate,
                model=config["model"],
                judgment_source=judgment_source,
            )
            for candidate in pool.get("candidates") or []
            if isinstance(candidate, dict)
        }
        existing = (
            {}
            if args.no_resume
            else _load_progress(progress_path, cache_keys=cache_keys)
        )
        progress_path.parent.mkdir(parents=True, exist_ok=True)

        def save_progress(candidate: dict[str, object]) -> None:
            candidate_id = str(candidate.get("candidate_id") or "")
            with progress_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "candidate_id": candidate_id,
                            "cache_key": cache_keys[candidate_id],
                            "candidate": candidate,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

        reviewed = judge_blinded_pool(
            pool,
            judge,
            model_name=config["model"],
            judgment_source=judgment_source,
            workers=args.workers,
            existing_reviews=existing,
            on_review=save_progress,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(reviewed, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"Blind judging failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {**reviewed["review_summary"], "resumed_candidates": len(existing)},
            ensure_ascii=False,
        )
    )
    return 0


def _cache_key(candidate: dict[str, object], *, model: str, judgment_source: str) -> str:
    payload = {
        "candidate": candidate,
        "model": model,
        "judgment_source": judgment_source,
        "rubric_version": JUDGING_RUBRIC_VERSION,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_progress(
    path: Path,
    *,
    cache_keys: dict[str, str],
) -> dict[str, dict[str, object]]:
    if not path.is_file():
        return {}
    result: dict[str, dict[str, object]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        candidate_id = str(payload.get("candidate_id") or "")
        candidate = payload.get("candidate")
        if (
            candidate_id
            and isinstance(candidate, dict)
            and payload.get("cache_key") == cache_keys.get(candidate_id)
        ):
            result[candidate_id] = candidate
    return result


if __name__ == "__main__":
    raise SystemExit(main())
