from __future__ import annotations

import ctypes
import os
import shutil
import time
from pathlib import Path
from typing import Any


def _percent(used: float | int | None, total: float | int | None) -> float | None:
    if used is None or total is None or total <= 0:
        return None
    return round(max(0.0, min(100.0, (float(used) / float(total)) * 100.0)), 1)


def _cpu_percent_from_snapshots(previous: tuple[int, ...] | None, current: tuple[int, ...] | None) -> float | None:
    if not previous or not current or len(previous) < 4 or len(current) < 4:
        return None
    prev_idle = previous[3] + (previous[4] if len(previous) > 4 else 0)
    curr_idle = current[3] + (current[4] if len(current) > 4 else 0)
    prev_non_idle = sum(previous[i] for i in (0, 1, 2, 5, 6, 7) if i < len(previous))
    curr_non_idle = sum(current[i] for i in (0, 1, 2, 5, 6, 7) if i < len(current))
    total_delta = (curr_idle + curr_non_idle) - (prev_idle + prev_non_idle)
    idle_delta = curr_idle - prev_idle
    if total_delta <= 0:
        return None
    return _percent(total_delta - idle_delta, total_delta)


def _read_linux_cpu_snapshot(path: Path = Path("/proc/stat")) -> tuple[int, ...] | None:
    try:
        first_line = path.read_text(encoding="utf-8").splitlines()[0]
    except Exception:
        return None
    parts = first_line.split()
    if not parts or parts[0] != "cpu":
        return None
    try:
        return tuple(int(value) for value in parts[1:])
    except ValueError:
        return None


def _cpu_percent_linux(sample_seconds: float = 0.05) -> float | None:
    previous = _read_linux_cpu_snapshot()
    if previous is None:
        return None
    time.sleep(sample_seconds)
    return _cpu_percent_from_snapshots(previous, _read_linux_cpu_snapshot())


class _FileTime(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32)]


def _filetime_to_int(value: _FileTime) -> int:
    return (int(value.dwHighDateTime) << 32) + int(value.dwLowDateTime)


def _windows_cpu_snapshot() -> tuple[int, int, int] | None:
    if os.name != "nt":
        return None
    idle = _FileTime()
    kernel = _FileTime()
    user = _FileTime()
    try:
        ok = ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
    except Exception:
        return None
    if not ok:
        return None
    return (_filetime_to_int(idle), _filetime_to_int(kernel), _filetime_to_int(user))


def _cpu_percent_windows(sample_seconds: float = 0.05) -> float | None:
    previous = _windows_cpu_snapshot()
    if previous is None:
        return None
    time.sleep(sample_seconds)
    current = _windows_cpu_snapshot()
    if current is None:
        return None
    prev_idle, prev_kernel, prev_user = previous
    curr_idle, curr_kernel, curr_user = current
    total_delta = (curr_kernel - prev_kernel) + (curr_user - prev_user)
    idle_delta = curr_idle - prev_idle
    if total_delta <= 0:
        return None
    return _percent(total_delta - idle_delta, total_delta)


def _load_average() -> float | None:
    try:
        return float(os.getloadavg()[0])
    except (AttributeError, OSError):
        return None


def _effective_cpu_cores(logical_cores: int | None) -> float | None:
    quota = _read_int_file(Path("/sys/fs/cgroup/cpu.max"), split_index=0)
    period = _read_int_file(Path("/sys/fs/cgroup/cpu.max"), split_index=1)
    if quota and period and quota > 0 and period > 0:
        return round(max(0.1, quota / period), 2)
    quota = _read_int_file(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"))
    period = _read_int_file(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us"))
    if quota and period and quota > 0 and period > 0:
        return round(max(0.1, quota / period), 2)
    return float(logical_cores) if logical_cores else None


def _collect_cpu() -> dict[str, Any]:
    logical_cores = os.cpu_count()
    effective_cores = _effective_cpu_cores(logical_cores)
    load_1m = _load_average()
    load_percent = _cpu_percent_windows() if os.name == "nt" else _cpu_percent_linux()
    if load_percent is None and load_1m is not None and effective_cores:
        load_percent = _percent(load_1m, effective_cores)
    return {
        "logical_cores": logical_cores,
        "effective_cores": effective_cores,
        "load_1m": round(load_1m, 2) if load_1m is not None else None,
        "load_percent": load_percent,
    }


def _read_int_file(path: Path, *, split_index: int = 0) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip().split()
    except Exception:
        return None
    if split_index >= len(raw) or raw[split_index].lower() == "max":
        return None
    try:
        return int(raw[split_index])
    except ValueError:
        return None


def _memory_from_meminfo(values: dict[str, int]) -> dict[str, Any]:
    total_kb = values.get("MemTotal")
    if not total_kb:
        return {
            "total_bytes": None,
            "used_bytes": None,
            "available_bytes": None,
            "used_percent": None,
            "scope": "host",
        }
    available_kb = values.get("MemAvailable")
    if available_kb is None:
        available_kb = values.get("MemFree", 0) + values.get("Buffers", 0) + values.get("Cached", 0)
    used_kb = max(0, total_kb - available_kb)
    total = total_kb * 1024
    available = max(0, available_kb * 1024)
    used = used_kb * 1024
    return {
        "total_bytes": total,
        "used_bytes": used,
        "available_bytes": available,
        "used_percent": _percent(used, total),
        "scope": "host",
    }


def _parse_meminfo(path: Path = Path("/proc/meminfo")) -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return values
    for line in lines:
        key, separator, rest = line.partition(":")
        if not separator:
            continue
        parts = rest.strip().split()
        if not parts:
            continue
        try:
            values[key] = int(parts[0])
        except ValueError:
            continue
    return values


def _memory_from_cgroup() -> dict[str, Any] | None:
    candidates = [
        (Path("/sys/fs/cgroup/memory.max"), Path("/sys/fs/cgroup/memory.current")),
        (Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"), Path("/sys/fs/cgroup/memory/memory.usage_in_bytes")),
    ]
    for limit_path, usage_path in candidates:
        total = _read_int_file(limit_path)
        used = _read_int_file(usage_path)
        if total is None or used is None or total <= 0 or total >= 1 << 60:
            continue
        available = max(0, total - used)
        return {
            "total_bytes": total,
            "used_bytes": used,
            "available_bytes": available,
            "used_percent": _percent(used, total),
            "scope": "container",
        }
    return None


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _memory_windows() -> dict[str, Any] | None:
    if os.name != "nt":
        return None
    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(_MemoryStatusEx)
    try:
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    except Exception:
        return None
    if not ok:
        return None
    total = int(status.ullTotalPhys)
    available = int(status.ullAvailPhys)
    used = max(0, total - available)
    return {
        "total_bytes": total,
        "used_bytes": used,
        "available_bytes": available,
        "used_percent": _percent(used, total),
        "scope": "host",
    }


def _collect_memory() -> dict[str, Any]:
    return (
        _memory_from_cgroup()
        or _memory_windows()
        or _memory_from_meminfo(_parse_meminfo())
    )


def _collect_disk(root: Path) -> dict[str, Any]:
    target = root
    if not target.exists():
        target = target.parent if target.parent.exists() else Path.cwd()
    try:
        usage = shutil.disk_usage(target)
    except Exception:
        return {
            "total_bytes": None,
            "used_bytes": None,
            "free_bytes": None,
            "used_percent": None,
            "path": str(root),
        }
    return {
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "used_percent": _percent(usage.used, usage.total),
        "path": str(target),
    }


def collect_system_metrics(root: str | Path = ".") -> dict[str, Any]:
    try:
        cpu = _collect_cpu()
    except Exception:
        cpu = {"logical_cores": os.cpu_count(), "effective_cores": None, "load_1m": None, "load_percent": None}
    try:
        memory = _collect_memory()
    except Exception:
        memory = {"total_bytes": None, "used_bytes": None, "available_bytes": None, "used_percent": None, "scope": "unknown"}
    return {
        "cpu": cpu,
        "memory": memory,
        "disk": _collect_disk(Path(root)),
    }
