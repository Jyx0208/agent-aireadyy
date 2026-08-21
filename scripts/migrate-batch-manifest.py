"""Repair and relocate a persisted batch manifest during Windows deployment."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


ACTIVE_STATUSES = {"queued", "running"}


def _load_manifest(path: Path, batch_id: str) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if str(payload.get("batch_id") or "") != batch_id:
        raise ValueError(
            f"batch manifest identity mismatch: expected {batch_id}, "
            f"found {payload.get('batch_id')}"
        )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--recovery", type=Path, required=True)
    parser.add_argument("--target-dir", type=Path, required=True)
    args = parser.parse_args()

    try:
        manifest = _load_manifest(args.manifest, args.batch_id)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        manifest = _load_manifest(args.recovery, args.batch_id)

    now = datetime.now().astimezone().isoformat()
    manifest["output_dir"] = str(args.target_dir)
    manifest["excel_path"] = str(args.target_dir / "benchmark_results.xlsx")
    manifest["requested_run_mode"] = (
        manifest.get("requested_run_mode") or manifest.get("run_mode") or "full"
    )

    for item in manifest.get("items") or []:
        leaf = Path(str(item.get("output_dir") or "")).name
        if not leaf:
            leaf = f"{int(item.get('index') or 0):03d}_item"
        item["output_dir"] = str(args.target_dir / "items" / leaf)
        if str(item.get("status") or "") == "running":
            item["status"] = "interrupted"
            item["finished_at"] = item.get("finished_at") or now
            item["error_summary"] = (
                item.get("error_summary")
                or "Batch item was interrupted by service maintenance; existing files were preserved."
            )

    if str(manifest.get("status") or "") in ACTIVE_STATUSES:
        manifest["status"] = "interrupted"
        manifest["interrupted"] = True
        manifest["finished_at"] = manifest.get("finished_at") or now
        manifest["updated_at"] = now
        errors = [str(value) for value in manifest.get("errors") or [] if str(value)]
        message = (
            "Batch was interrupted by the batch-control deployment; "
            "completed artifacts were preserved."
        )
        if message not in errors:
            errors.append(message)
        manifest["errors"] = errors

    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.manifest.with_suffix(args.manifest.suffix + ".tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(args.manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
