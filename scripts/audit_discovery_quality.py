from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one latest-worktree Discovery prompt and retain its audit artifacts."
    )
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--goal", default="general")
    parser.add_argument("--max-projects", type=int, default=10)
    parser.add_argument("--max-candidate-projects", type=int, default=300)
    parser.add_argument("--max-repository-requests", type=int, default=80)
    args = parser.parse_args()

    env_path = next(
        (directory / ".env" for directory in (ROOT, *ROOT.parents) if (directory / ".env").is_file()),
        None,
    )
    if env_path is not None:
        load_dotenv(env_path, override=False)
    repository_limit = str(max(1, args.max_repository_requests))
    os.environ["AGENT_INITIAL_REPOSITORY_REQUESTS"] = repository_limit
    os.environ["AGENT_EXPANDED_REPOSITORY_REQUESTS"] = repository_limit
    os.environ["AGENT_MAX_REPOSITORY_REQUESTS"] = repository_limit

    from agent.control_plane import discovery as discovery_module
    from agent.discovery import search_environment as search_environment_module
    from agent.web import app as web_app

    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    web_app._runs_dir = output_root

    print(f"WORKTREE_ROOT={ROOT}", flush=True)
    print(f"DISCOVERY_MODULE={Path(discovery_module.__file__).resolve()}", flush=True)
    print(
        f"SEARCH_ENVIRONMENT_MODULE={Path(search_environment_module.__file__).resolve()}",
        flush=True,
    )
    print(f"WEB_APP_MODULE={Path(web_app.__file__).resolve()}", flush=True)

    body = {
        "prompt": args.prompt,
        "runtime": "openai_agents",
        "source": "remote",
        "repository": "pride",
        "goal": args.goal,
        "species": ["human"],
        "species_policy": "include_only",
        "constraint_provenance": {
            "repository": "user",
            "goal": "user" if args.goal != "general" else "inferred",
            "species": "user",
            "species_policy": "user",
        },
        "max_projects": args.max_projects,
        "max_candidate_projects": args.max_candidate_projects,
        "max_files": 500,
        "max_files_per_project": 100,
        "discovery_mode": "multi_agent",
        "use_memory": False,
    }
    record = web_app._run_web_discovery(
        body,
        report=lambda message: print(f"REPORT {message}", flush=True),
    )
    result_path = output_root / "quality_audit_result.json"
    result_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"RESULT_PATH={result_path}", flush=True)
    print(
        json.dumps(
            {
                "status": record.get("status"),
                "run_id": record.get("run_id"),
                "project_count": record.get("project_count"),
                "file_count": record.get("file_count"),
                "output_dir": record.get("output_dir"),
            },
            ensure_ascii=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
