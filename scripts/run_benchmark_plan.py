from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.audit.review import build_task_state_snapshot, write_task_state
from agent.errors import build_error_record, write_error_record
from agent.input.normalizer import normalize_input, safe_output_stem
from agent.orchestrator.pipeline import AgentService
from agent.utils import write_json
from export_benchmark_excel import ResultSource, summarize_source, write_xlsx


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


class FileReporter:
    def __init__(self, output_dir: Path) -> None:
        self.path = output_dir / "logs" / "runtime.log"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def __call__(self, message: Any) -> None:
        if isinstance(message, dict):
            text = json.dumps(message, ensure_ascii=False)
        else:
            text = str(message)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(text + "\n")


def _read_file_list(path: Path) -> list[str]:
    items: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        item = raw.strip()
        if item and not item.startswith("#"):
            items.append(item)
    return items


def _output_dir_for_item(output_root: Path, item: str) -> Path:
    stem = safe_output_stem(item) or Path(item).stem
    return output_root / stem


def _write_error(output_dir: Path, item: str, exc: BaseException) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    task_id = ""
    try:
        task_id = normalize_input(item).task_id
    except Exception:
        task_id = f"task-{safe_output_stem(item)}"
    error = build_error_record(exc, stage="planning", input_file=item)
    write_error_record(output_dir / "error.json", error)
    write_task_state(
        output_dir / "task_state.json",
        build_task_state_snapshot(
            task_id=task_id,
            status="failed",
            stage="planning",
            source_file=Path(item).name,
            project_accession=None,
            notes=[error["public_message"], error["operator_hint"]],
        ),
    )


def run_one(item: str, output_root: Path) -> tuple[str, str, str]:
    output_dir = _output_dir_for_item(output_root, item)
    output_dir.mkdir(parents=True, exist_ok=True)
    service: AgentService | None = None
    try:
        reporter = FileReporter(output_dir)
        service = AgentService(reporter=reporter)
        task = normalize_input(item)
        resolution = service.resolve_project(task.original_input)
        write_json(output_dir / "project_resolution.json", resolution)
        primary = resolution.primary_project
        if primary is None or primary.match_type not in {"exact", "stem"} or primary.match_score < 90:
            reason = (
                "No exact PRIDE project match found."
                if primary is None
                else (
                    f"Non-exact PRIDE project match: {primary.project_accession}, "
                    f"match_type={primary.match_type}, score={primary.match_score}, matched_file={primary.matched_file}"
                )
            )
            raise RuntimeError(reason)
        if resolution.needs_review:
            raise RuntimeError(f"Ambiguous PRIDE project match: {resolution.resolution_reason}")
        result = service.plan_dda_run_from_pride(task=task, output_dir=output_dir)
        service.write_task_bundle(
            output_dir,
            result.resolution,
            result.context,
            result.attributes,
            result.plan,
            asset=result.asset,
        )
        if (output_dir / "error.json").exists():
            (output_dir / "error.json").unlink()
        status = "needs_review" if result.plan.needs_review else "resolved"
        return item, status, str(output_dir)
    except Exception as exc:
        _write_error(output_dir, item, exc)
        return item, "failed", str(output_dir)
    finally:
        if service is not None:
            try:
                service.pride_client.close()
            except Exception:
                pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run PRIDE benchmark parameter planning in parallel and export Excel.")
    parser.add_argument("file_list", type=Path, help="Text file with one PRIDE file name per line.")
    parser.add_argument("--output-root", default=Path("benchmark_runs"), type=Path, help="Where plan-only run folders are written.")
    parser.add_argument("--excel", default=Path("benchmark_results.xlsx"), type=Path, help="Output Excel file.")
    parser.add_argument("--jobs", default=3, type=int, help="Parallel workers. Keep moderate to avoid LLM/API rate limits.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    _load_dotenv(REPO_ROOT / ".env")
    items = _read_file_list(args.file_list)
    if not items:
        raise SystemExit(f"No input files found in {args.file_list}")

    args.output_root.mkdir(parents=True, exist_ok=True)
    jobs = max(1, min(args.jobs, len(items)))
    print(f"Planning {len(items)} files with {jobs} parallel workers. Output root: {args.output_root}")

    results: list[tuple[str, str, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        future_to_item = {pool.submit(run_one, item, args.output_root): item for item in items}
        for future in concurrent.futures.as_completed(future_to_item):
            item = future_to_item[future]
            try:
                result = future.result()
            except Exception as exc:
                output_dir = _output_dir_for_item(args.output_root, item)
                _write_error(output_dir, item, exc)
                result = (item, "failed", str(output_dir))
            results.append(result)
            print(f"[{result[1]}] {result[0]} -> {result[2]}")

    sources = [ResultSource(label=item, path=_output_dir_for_item(args.output_root, item)) for item in items]
    rows = [summarize_source(source) for source in sources]
    write_xlsx(rows, args.excel)

    counts: dict[str, int] = {}
    for _, status, _ in results:
        counts[status] = counts.get(status, 0) + 1
    print(f"Wrote Excel: {args.excel}")
    print(f"Status counts: {counts}")


if __name__ == "__main__":
    main()
