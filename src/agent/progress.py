from __future__ import annotations

from typing import Any


def _format_bytes(value: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    unit = units[0]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            break
        size /= 1024
    if unit == "B":
        return f"{int(size)} {unit}"
    return f"{size:.1f} {unit}"


def _format_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "--:--"
    total = int(seconds)
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def render_download_progress(event: dict[str, Any], width: int = 24) -> str:
    total = int(event.get("total") or 0)
    downloaded = int(event.get("downloaded") or 0)
    speed_bps = float(event.get("speed_bps") or 0.0)
    eta_seconds = event.get("eta_seconds")
    label = str(event.get("label") or "download")

    if total > 0:
        ratio = max(0.0, min(1.0, downloaded / total))
        percent = ratio * 100
        filled = int(width * ratio)
        bar = f"[{'#' * filled}{'-' * (width - filled)}]"
        amount = f"{_format_bytes(downloaded)}/{_format_bytes(total)}"
        pct = f"{percent:4.1f}%"
    else:
        bar = f"[{'#' * (width // 2)}{'-' * (width - width // 2)}]"
        amount = _format_bytes(downloaded)
        pct = "--.-%"

    speed = f"{_format_bytes(speed_bps)}/s" if speed_bps > 0 else "-- B/s"
    eta = f"ETA {_format_eta(eta_seconds)}"
    return f"{bar} {pct} {amount} {speed} {eta} {label}"
