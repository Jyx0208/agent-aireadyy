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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download a frozen PRIDE benchmark manifest with resume and size checks.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--status", type=Path, default=None, help="local status log; avoids making NAS logging a download dependency")
    parser.add_argument("--proxy", default=None, help="HTTP proxy, for example http://127.0.0.1:7897")
    return parser.parse_args()


def safe_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value.strip())
    return cleaned.strip("._") or "unknown"


def load_rows(manifest: Path) -> list[dict[str, str]]:
    with manifest.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError(f"manifest is empty: {manifest}")
    required = {"project_accession", "file_name", "download_url", "expected_size_bytes"}
    missing = sorted(required - set(rows[0]))
    if missing:
        raise RuntimeError(f"manifest is missing columns: {', '.join(missing)}")
    return rows


class StatusWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()

    def write(self, payload: dict[str, object]) -> None:
        payload = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **payload}
        with self.lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def run_curl(url: str, part: Path, expected: int, status: StatusWriter, label: str, proxy: str | None = None) -> None:
    part.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, 4):
        if part.exists() and part.stat().st_size > expected:
            part.rename(part.with_name(part.name + f".oversize-{int(time.time())}"))
        command = [
            "curl.exe",
            "--location",
            "--fail",
            "--silent",
            "--show-error",
            "--retry",
            "8",
            "--retry-delay",
            "10",
            "--retry-all-errors",
            "--connect-timeout",
            "30",
            "--continue-at",
            "-",
            "--output",
            str(part),
            url,
        ]
        if proxy:
            command[-1:-1] = ["--proxy", proxy]
        status.write({"event": "download_started", "label": label, "attempt": attempt, "part_bytes": part.stat().st_size if part.exists() else 0})
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if result.returncode == 33 and part.exists():
            # The server declined a range request; restart this one file only.
            part.unlink()
            continue
        if result.returncode != 0:
            detail = (result.stderr or "curl failed").strip()[-1000:]
            status.write({"event": "download_attempt_failed", "label": label, "attempt": attempt, "returncode": result.returncode, "error": detail})
            if attempt == 3:
                raise RuntimeError(f"{label}: curl exit {result.returncode}: {detail}")
            continue
        actual = part.stat().st_size if part.exists() else 0
        if actual != expected:
            status.write({"event": "size_mismatch", "label": label, "attempt": attempt, "actual_bytes": actual, "expected_bytes": expected})
            if attempt == 3:
                raise RuntimeError(f"{label}: size {actual} != expected {expected}")
            continue
        return
    raise RuntimeError(f"{label}: exhausted download attempts")


def download_one(row: dict[str, str], destination: Path, status: StatusWriter, proxy: str | None = None) -> dict[str, object]:
    project = safe_name(row["project_accession"])
    name = Path(row["file_name"]).name
    final = destination / project / name
    part = final.with_name(final.name + ".part")
    expected = int(row["expected_size_bytes"])
    label = f"{project}/{name}"
    if final.exists() and final.is_file() and final.stat().st_size == expected:
        status.write({"event": "already_verified", "label": label, "bytes": expected})
        return {"project_accession": project, "file_name": name, "path": str(final), "status": "already_verified", "bytes": expected}
    if final.exists() and final.is_file():
        final.rename(final.with_name(final.name + f".invalid-{int(time.time())}"))
    run_curl(row["download_url"], part, expected, status, label, proxy=proxy)
    os.replace(part, final)
    actual = final.stat().st_size
    status.write({"event": "download_completed", "label": label, "bytes": actual, "path": str(final)})
    return {"project_accession": project, "file_name": name, "path": str(final), "status": "downloaded", "bytes": actual}


def main() -> int:
    args = parse_args()
    rows = load_rows(args.manifest)
    destination = args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    expected_total = sum(int(row["expected_size_bytes"]) for row in rows)
    free = shutil.disk_usage(destination).free
    if free < int(expected_total * 1.15):
        raise RuntimeError(f"destination has {free} free bytes, but {expected_total} are required")
    for filename in ("dataset_manifest.csv", "dataset_manifest.json", "quality_report.json", "selected_projects_review.csv"):
        source = args.manifest.parent / filename
        if source.exists():
            shutil.copy2(source, destination / filename)
    status_path = args.status or (Path.cwd() / ".codex_tmp" / "pride_download_status.jsonl")
    status = StatusWriter(status_path)
    status.write({"event": "download_batch_started", "files": len(rows), "expected_total_bytes": expected_total, "workers": args.workers})
    results: list[dict[str, object]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(rows)))) as pool:
        futures = [pool.submit(download_one, row, destination, status, args.proxy) for row in rows]
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001 - report all failed files before stopping
                errors.append(str(exc))
                status.write({"event": "download_failed", "error": str(exc)})
    results.sort(key=lambda item: (str(item.get("project_accession")), str(item.get("file_name"))))
    summary = {
        "manifest": str(args.manifest.resolve()),
        "destination": str(destination),
        "expected_total_bytes": expected_total,
        "completed_files": len(results),
        "failed_files": len(errors),
        "results": results,
        "errors": errors,
    }
    (destination / "download_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        shutil.copy2(status_path, destination / "download_status.jsonl")
    except OSError:
        # The NAS may briefly disconnect after the data itself is written. The
        # authoritative log remains on the local disk in that case.
        pass
    status.write({"event": "download_batch_finished", "completed_files": len(results), "failed_files": len(errors)})
    if errors:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
