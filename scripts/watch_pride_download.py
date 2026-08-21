from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show live progress and throughput for a PRIDE benchmark download.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--pid-file", type=Path, default=None)
    parser.add_argument("--cache", type=Path, default=None, help="chunk cache used by the ranged downloader")
    return parser.parse_args()


def expected_total(manifest: Path) -> int:
    with manifest.open("r", encoding="utf-8-sig", newline="") as handle:
        return sum(int(row["expected_size_bytes"]) for row in csv.DictReader(handle))


def bytes_on_disk(destination: Path, cache: Path | None = None) -> int:
    total = 0
    # Only count data files and their resumable .part files.  This avoids
    # counting copied manifests, summaries, and status logs.
    for path in destination.glob("PXD*/*"):
        try:
            if path.is_file() and (
                (path.name.endswith(".part") and cache is None)
                or (not path.name.endswith(".part") and ".invalid-" not in path.name and ".oversize-" not in path.name)
            ):
                total += path.stat().st_size
        except OSError:
            # A NAS can briefly reject a stat while reconnecting; retain the
            # last good sample rather than terminating the monitor.
            continue
    if cache and cache.exists():
        # Count cached ranges for files that have not yet been assembled into
        # their final destination.  Once a final file is complete, its cache
        # is ignored so progress is never double-counted.
        for path in cache.rglob("*.part"):
            try:
                if path.name.endswith(".assembled.part"):
                    relative = path.relative_to(cache)
                    project = relative.parts[0]
                    name = path.name[: -len(".assembled.part")]
                elif path.parent.name.endswith(".chunks"):
                    relative = path.relative_to(cache)
                    project = relative.parts[0]
                    name = path.parent.name[: -len(".chunks")]
                else:
                    continue
                final = destination / project / name
                if not final.exists() or final.stat().st_size == 0:
                    total += path.stat().st_size
            except (OSError, ValueError):
                continue
    return total


def fmt_bytes(value: float) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.2f} {unit}"
        amount /= 1024
    return f"{amount:.2f} TB"


def fmt_duration(seconds: float) -> str:
    if seconds <= 0 or seconds == float("inf"):
        return "--"
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days:
        return f"{days}d {hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def process_alive(pid_file: Path | None) -> bool:
    if pid_file is None or not pid_file.exists():
        return True
    try:
        pid = int(pid_file.read_text(encoding="ascii").strip())
        if os.name == "nt":
            # os.kill(pid, 0) is not a reliable existence check on Windows
            # (it can return WinError 87 for a live process).  OpenProcess is
            # a read-only check and works for the hidden background workers.
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def main() -> int:
    args = parse_args()
    total = expected_total(args.manifest)
    previous = bytes_on_disk(args.destination, args.cache)
    previous_time = time.monotonic()
    while True:
        time.sleep(max(1.0, args.interval))
        current = bytes_on_disk(args.destination, args.cache)
        now = time.monotonic()
        elapsed = max(now - previous_time, 1e-6)
        speed = max(0, current - previous) / elapsed
        percent = min(100.0, current / total * 100) if total else 100.0
        remaining = max(total - current, 0)
        eta = remaining / speed if speed > 0 else float("inf")
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"[{stamp}] 已下载 {fmt_bytes(current)} / {fmt_bytes(total)} "
            f"({percent:6.2f}%) | 速度 {fmt_bytes(speed)}/s | ETA {fmt_duration(eta)}",
            flush=True,
        )
        previous, previous_time = current, now
        if not process_alive(args.pid_file) and (args.destination / "download_summary.json").exists():
            print("下载进程已结束；以上为最终监控结果。", flush=True)
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
