from __future__ import annotations

from agent.runtime.system_metrics import (
    _cpu_percent_from_snapshots,
    _memory_from_meminfo,
    _percent,
)


def test_percent_returns_rounded_percentage_and_none_for_missing_total():
    assert _percent(25, 200) == 12.5
    assert _percent(1, 3) == 33.3
    assert _percent(10, 0) is None


def test_cpu_percent_from_snapshots_uses_non_idle_delta():
    previous = (100, 0, 50, 850, 0, 0, 0, 0, 0, 0)
    current = (150, 0, 70, 880, 0, 0, 0, 0, 0, 0)

    assert _cpu_percent_from_snapshots(previous, current) == 70.0


def test_memory_from_meminfo_uses_mem_available_when_present():
    metrics = _memory_from_meminfo(
        {
            "MemTotal": 16_384,
            "MemFree": 1_024,
            "MemAvailable": 4_096,
            "Buffers": 512,
            "Cached": 2_048,
        }
    )

    assert metrics == {
        "total_bytes": 16_384 * 1024,
        "used_bytes": 12_288 * 1024,
        "available_bytes": 4_096 * 1024,
        "used_percent": 75.0,
        "scope": "host",
    }
