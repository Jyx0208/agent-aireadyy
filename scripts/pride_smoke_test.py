from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import sys
import threading
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent.errors import build_error_record, write_error_record
from agent.input.normalizer import normalize_input, safe_output_stem
from agent.orchestrator.pipeline import AgentService
from agent.pride.client import PrideClient
from agent.pride.resolver import resolve_input_to_project
from agent.utils import write_json


MS_FILE_SUFFIXES = (
    ".raw",
    ".mzml",
    ".mzxml",
    ".mgf",
    ".wiff",
    ".d",
    ".d.zip",
    ".raw.zip",
    ".wiff.scan",
)


class DiskBudgetExceeded(RuntimeError):
    pass


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


def _json_line(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for file in path.rglob("*"):
        if file.is_file():
            try:
                total += file.stat().st_size
            except OSError:
                continue
    return total


class SmokeRunWriter:
    def __init__(self, output_root: Path, max_output_mb: float = 50.0) -> None:
        self.output_root = output_root
        self.max_output_bytes = int(max_output_mb * 1024 * 1024)
        self._lock = threading.Lock()
        self.output_root.mkdir(parents=True, exist_ok=True)

    def ensure_budget(self) -> None:
        used = _directory_size(self.output_root)
        if used > self.max_output_bytes:
            raise DiskBudgetExceeded(
                f"Smoke test output exceeded budget: {used / 1024 / 1024:.2f} MB > {self.max_output_bytes / 1024 / 1024:.2f} MB"
            )

    def write_record(self, record: dict[str, Any]) -> None:
        with self._lock:
            self.ensure_budget()
            with (self.output_root / "records.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(_json_line(record) + "\n")

    def write_error(self, record: dict[str, Any]) -> None:
        with self._lock:
            self.ensure_budget()
            with (self.output_root / "errors.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(_json_line(record) + "\n")

    def write_summary(self, summary: dict[str, Any]) -> None:
        with self._lock:
            self.ensure_budget()
            write_json(self.output_root / "summary.json", summary)


class FileReporter:
    def __init__(self, output_dir: Path) -> None:
        self.path = output_dir / "logs" / "runtime.log"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def __call__(self, message: Any) -> None:
        text = json.dumps(message, ensure_ascii=False) if isinstance(message, dict) else str(message)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(text + "\n")


def _is_mass_spec_file(file_name: str) -> bool:
    lowered = file_name.lower()
    return any(lowered.endswith(suffix) for suffix in MS_FILE_SUFFIXES)


def collect_pride_inputs(
    client: PrideClient,
    *,
    keywords: list[str],
    sample_size: int,
    projects_per_keyword: int = 10,
    files_per_project: int = 3,
    max_files_scan: int = 20,
) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        if len(selected) >= sample_size:
            break
        projects = client.search_projects(keyword, page_size=projects_per_keyword)
        for project in projects[:projects_per_keyword]:
            if len(selected) >= sample_size:
                break
            accession = str(project.get("accession") or "")
            if not accession:
                continue
            files = client.list_project_files(accession, page_size=min(max_files_scan, 100), max_files=max_files_scan)
            taken = 0
            for file_record in files:
                name = str(file_record.get("fileName") or "").strip()
                key = name.lower()
                if not name or key in seen or not _is_mass_spec_file(name):
                    continue
                selected.append(name)
                seen.add(key)
                taken += 1
                if len(selected) >= sample_size or taken >= files_per_project:
                    break
    return selected


def _status_from_resolution(resolution) -> str:
    if resolution.primary_project is None:
        return "unresolved"
    if resolution.needs_review:
        return "needs_review"
    return "resolved"


def _output_dir_for_item(output_root: Path, item: str) -> Path:
    return output_root / "plans" / (safe_output_stem(item) or Path(item).stem or "pride_input")


def run_resolution_item(item: str, output_root: Path, timeout: float, max_files_per_project: int) -> dict[str, Any]:
    with PrideClient(timeout=timeout) as client:
        resolution = resolve_input_to_project(client, item, max_files_per_project=max_files_per_project)
    primary = resolution.primary_project
    return {
        "input_file": item,
        "status": _status_from_resolution(resolution),
        "project": primary.project_accession if primary else "",
        "matched_file": primary.matched_file if primary else "",
        "match_type": primary.match_type if primary else "",
        "match_score": primary.match_score if primary else 0,
        "needs_review": resolution.needs_review,
        "reason": resolution.resolution_reason,
        "updated_at": datetime.now(UTC).isoformat(),
    }


def run_planning_item(item: str, output_root: Path, timeout: float, prefer_project_fasta: bool = False) -> dict[str, Any]:
    output_dir = _output_dir_for_item(output_root, item)
    output_dir.mkdir(parents=True, exist_ok=True)
    service: AgentService | None = None
    try:
        service = AgentService(pride_client=PrideClient(timeout=timeout), reporter=FileReporter(output_dir))
        task = normalize_input(item)
        result = service.plan_dda_run_from_pride(
            task=task,
            output_dir=output_dir,
            prefer_project_fasta=prefer_project_fasta,
        )
        service.write_task_bundle(output_dir, result.resolution, result.context, result.attributes, result.plan, asset=result.asset)
        primary = result.resolution.primary_project
        return {
            "input_file": item,
            "status": "needs_review" if result.plan.needs_review else "planned",
            "project": primary.project_accession if primary else "",
            "workflow": result.plan.fragpipe_workflow_path.name,
            "fasta": result.plan.fasta_path.name,
            "blocking_issues": result.plan.blocking_issues,
            "output_dir": str(output_dir),
            "updated_at": datetime.now(UTC).isoformat(),
        }
    finally:
        if service is not None:
            service.pride_client.close()


def _free_space_gb(path: Path) -> float:
    path.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(path).free / 1024 / 1024 / 1024


def _run_item(args: tuple[str, str, Path, float, int, bool]) -> dict[str, Any]:
    item, mode, output_root, timeout, max_files_per_project, prefer_project_fasta = args
    if mode == "planning":
        return run_planning_item(item, output_root, timeout, prefer_project_fasta=prefer_project_fasta)
    return run_resolution_item(item, output_root, timeout, max_files_per_project=max_files_per_project)


def run_smoke_test(
    *,
    output_root: Path,
    mode: str,
    keywords: list[str],
    sample_size: int,
    jobs: int,
    timeout: float,
    max_output_mb: float,
    min_free_gb: float,
    projects_per_keyword: int,
    files_per_project: int,
    max_files_scan: int,
    max_files_per_project: int,
    prefer_project_fasta: bool = False,
) -> dict[str, Any]:
    writer = SmokeRunWriter(output_root, max_output_mb=max_output_mb)
    if _free_space_gb(output_root) < min_free_gb:
        raise DiskBudgetExceeded(f"Free disk space is below {min_free_gb:.1f} GB; smoke test aborted before writing outputs.")
    with PrideClient(timeout=timeout) as client:
        inputs = collect_pride_inputs(
            client,
            keywords=keywords,
            sample_size=sample_size,
            projects_per_keyword=projects_per_keyword,
            files_per_project=files_per_project,
            max_files_scan=max_files_scan,
        )
    (output_root / "sampled_inputs.txt").write_text("\n".join(inputs) + ("\n" if inputs else ""), encoding="utf-8")

    status_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    tasks = [(item, mode, output_root, timeout, max_files_per_project, prefer_project_fasta) for item in inputs]
    workers = max(1, min(jobs, len(tasks) or 1))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_item = {pool.submit(_run_item, task): task[0] for task in tasks}
        for future in concurrent.futures.as_completed(future_to_item):
            item = future_to_item[future]
            writer.ensure_budget()
            try:
                record = future.result()
                status_counts[str(record.get("status") or "unknown")] += 1
                writer.write_record(record)
            except Exception as exc:
                error = build_error_record(exc, stage=mode, input_file=item)
                status_counts["failed"] += 1
                category_counts[str(error.get("category") or "unknown")] += 1
                writer.write_error(error)
                if mode == "planning":
                    write_error_record(_output_dir_for_item(output_root, item) / "error.json", error)

    summary = {
        "mode": mode,
        "total": len(inputs),
        "status_counts": dict(status_counts),
        "error_category_counts": dict(category_counts),
        "output_root": str(output_root),
        "output_size_mb": round(_directory_size(output_root) / 1024 / 1024, 3),
        "free_space_gb": round(_free_space_gb(output_root), 3),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    writer.write_summary(summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Large PRIDE metadata smoke test with strict disk safeguards.")
    parser.add_argument("--output-root", type=Path, default=Path("pride_smoke_runs"))
    parser.add_argument("--mode", choices=["resolution", "planning"], default="resolution")
    parser.add_argument("--keywords", default="lfq,tmt,phospho,dia,hela,ecoli")
    parser.add_argument("--sample-size", type=int, default=60)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--max-output-mb", type=float, default=50.0)
    parser.add_argument("--min-free-gb", type=float, default=2.0)
    parser.add_argument("--projects-per-keyword", type=int, default=10)
    parser.add_argument("--files-per-project", type=int, default=2)
    parser.add_argument("--max-files-scan", type=int, default=20)
    parser.add_argument("--max-files-per-project", type=int, default=300)
    parser.add_argument("--prefer-project-fasta", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    _load_dotenv(REPO_ROOT / ".env")
    keywords = [item.strip() for item in args.keywords.split(",") if item.strip()]
    summary = run_smoke_test(
        output_root=args.output_root,
        mode=args.mode,
        keywords=keywords,
        sample_size=max(1, args.sample_size),
        jobs=max(1, args.jobs),
        timeout=args.timeout,
        max_output_mb=args.max_output_mb,
        min_free_gb=args.min_free_gb,
        projects_per_keyword=max(1, args.projects_per_keyword),
        files_per_project=max(1, args.files_per_project),
        max_files_scan=max(1, args.max_files_scan),
        max_files_per_project=max(1, args.max_files_per_project),
        prefer_project_fasta=args.prefer_project_fasta,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
