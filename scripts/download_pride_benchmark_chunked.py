from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


 # Keep each TLS connection short enough that the PRIDE/Clash path does not
 # reset long-running transfers.  The range endpoint supports resumable
 # chunks, so a failed chunk can be retried independently.
CHUNK_SIZE = 64 * 1024 * 1024


def safe_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value.strip())
    return cleaned.strip("._") or "unknown"


class StatusWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()

    def write(self, payload: dict[str, object]) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            event = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **payload}
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def curl_range(url: str, start: int, end: int, target: Path, expected: int, status: StatusWriter, label: str, proxy: str | None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size == expected:
        return
    if target.exists() and target.stat().st_size != expected:
        target.unlink()
    command = [
        "curl.exe",
        "--location",
        "--fail",
        "--silent",
        "--show-error",
        "--retry",
        "8",
        "--retry-delay",
        "5",
        "--retry-all-errors",
        "--connect-timeout",
        "30",
        "--http1.1",
        "--max-time",
        "900",
        "--range",
        f"{start}-{end}",
        "--output",
        str(target),
        url,
    ]
    if proxy:
        command[-1:-1] = ["--proxy", proxy]
    status.write({"event": "chunk_started", "label": label, "start": start, "end": end})
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        detail = (result.stderr or "curl failed").strip()[-1000:]
        raise RuntimeError(f"{label} range {start}-{end}: curl exit {result.returncode}: {detail}")
    actual = target.stat().st_size if target.exists() else 0
    if actual != expected:
        raise RuntimeError(f"{label} range {start}-{end}: size {actual} != expected {expected}")
    status.write({"event": "chunk_completed", "label": label, "start": start, "end": end, "bytes": actual})


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"project_accession", "file_name", "download_url", "expected_size_bytes"}
    if not rows or not required.issubset(rows[0]):
        raise RuntimeError("manifest is empty or missing download columns")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--proxy", default=None, help="HTTP proxy, for example http://127.0.0.1:7897")
    args = parser.parse_args()
    rows = load_rows(args.manifest)
    destination = args.destination.resolve()
    cache = args.cache.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    expected_total = sum(int(row["expected_size_bytes"]) for row in rows)
    free = shutil.disk_usage(cache).free
    if free < int(expected_total * 1.15):
        raise RuntimeError(f"local cache has {free} free bytes, but {expected_total} are required")
    status = StatusWriter(destination / "download_status.jsonl")
    status.write({"event": "chunked_batch_started", "files": len(rows), "expected_total_bytes": expected_total, "chunk_size": CHUNK_SIZE, "workers": args.workers})

    tasks: list[tuple[dict[str, str], int, int, Path, str]] = []
    existing: list[dict[str, object]] = []
    for row in rows:
        project = safe_name(row["project_accession"])
        name = Path(row["file_name"]).name
        expected = int(row["expected_size_bytes"])
        url = row["download_url"]
        final = destination / project / name
        if final.exists() and final.is_file() and final.stat().st_size == expected:
            existing.append({"project_accession": project, "file_name": name, "path": str(final), "bytes": expected, "status": "already_verified"})
            continue
        chunk_dir = cache / project / (name + ".chunks")
        for start in range(0, expected, CHUNK_SIZE):
            end = min(expected - 1, start + CHUNK_SIZE - 1)
            index = start // CHUNK_SIZE
            chunk = chunk_dir / f"{index:04d}.part"
            tasks.append((row, start, end, chunk, f"{project}/{name}"))

    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        future_map = {
            pool.submit(curl_range, row["download_url"], start, end, chunk, end - start + 1, status, label, args.proxy): (row, start, end, chunk, label)
            for row, start, end, chunk, label in tasks
            if not (chunk.exists() and chunk.stat().st_size == end - start + 1)
        }
        for future in as_completed(future_map):
            try:
                future.result()
            except Exception as exc:  # noqa: BLE001 - report all failed ranges
                row, start, end, chunk, label = future_map[future]
                failures.append(f"{label} range {start}-{end}: {exc}")
                status.write({"event": "chunk_failed", "label": label, "start": start, "end": end, "error": str(exc)})
    if failures:
        (destination / "download_summary.json").write_text(json.dumps({"failed_chunks": failures}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 1

    results: list[dict[str, object]] = list(existing)
    for row in rows:
        project = safe_name(row["project_accession"])
        name = Path(row["file_name"]).name
        expected = int(row["expected_size_bytes"])
        chunk_dir = cache / project / (name + ".chunks")
        final = destination / project / name
        if final.exists() and final.is_file() and final.stat().st_size == expected:
            # This file was verified before the ranged run; it has no cache
            # directory and must not enter the assembly pass.
            continue
        assembled = cache / project / (name + ".assembled.part")
        assembled.parent.mkdir(parents=True, exist_ok=True)
        with assembled.open("wb") as output:
            for start in range(0, expected, CHUNK_SIZE):
                chunk = chunk_dir / f"{start // CHUNK_SIZE:04d}.part"
                with chunk.open("rb") as source:
                    shutil.copyfileobj(source, output, length=16 * 1024 * 1024)
        if assembled.stat().st_size != expected:
            raise RuntimeError(f"{project}/{name}: assembled size mismatch")
        final = destination / project / name
        final.parent.mkdir(parents=True, exist_ok=True)
        target_part = final.with_name(final.name + ".chunked.part")
        shutil.copyfile(assembled, target_part)
        if target_part.stat().st_size != expected:
            raise RuntimeError(f"{project}/{name}: NAS copy size mismatch")
        os.replace(target_part, final)
        status.write({"event": "download_completed", "label": f"{project}/{name}", "bytes": expected, "path": str(final)})
        results.append({"project_accession": project, "file_name": name, "path": str(final), "bytes": expected, "status": "downloaded"})

    for filename in ("dataset_manifest.csv", "dataset_manifest.json", "quality_report.json", "selected_projects_review.csv"):
        source = args.manifest.parent / filename
        if source.exists():
            shutil.copy2(source, destination / filename)
    summary = {"manifest": str(args.manifest.resolve()), "destination": str(destination), "cache": str(cache), "expected_total_bytes": expected_total, "completed_files": len(results), "failed_chunks": [], "results": results}
    (destination / "download_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    status.write({"event": "chunked_batch_finished", "completed_files": len(results), "failed_chunks": 0})
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
