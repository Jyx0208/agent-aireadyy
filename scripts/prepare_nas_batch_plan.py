from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a NAS-local PRIDE batch processing plan from a frozen manifest.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("data_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    return parser.parse_args()


def processing_lane(file_name: str, acquisition_mode: str) -> tuple[str, str]:
    lower = file_name.lower()
    if acquisition_mode.casefold() == "dia":
        return "dia_deferred", "Current Discovery/MSDT DDA lane does not process DIA; route to DIA-native workflow."
    if lower.endswith(".d.zip"):
        return "tims_extract", "Extract Bruker .d archive, then run tims-compatible spectrum preparation."
    if lower.endswith(".mzxml"):
        return "mzxml_convert", "Convert mzXML to mzML before search and spectrum preparation."
    if lower.endswith(".mzml"):
        return "mzml_reuse", "Reuse mzML as the search/spectrum-preparation input."
    if lower.endswith(".raw"):
        return "raw_convert", "Convert vendor RAW to mzML with ProteoWizard/msconvert before search."
    return "unsupported_format", "No approved local preparation route for this extension."


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name in ("prepared", "search", "msdt", "qc", "logs", "releases"):
        (args.output_dir / name).mkdir(exist_ok=True)
    with args.manifest.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError(f"empty manifest: {args.manifest}")

    items: list[dict[str, object]] = []
    for row in rows:
        project = row["project_accession"]
        file_name = Path(row["file_name"]).name
        source = args.data_dir / project / file_name
        expected = int(row["expected_size_bytes"])
        actual = source.stat().st_size if source.is_file() else 0
        lane, action = processing_lane(file_name, row.get("acquisition_mode", ""))
        items.append(
            {
                "item_id": row.get("ms_run_id") or f"{project}:{file_name}",
                "project_accession": project,
                "source_file": file_name,
                "source_path": str(source),
                "expected_size_bytes": expected,
                "actual_size_bytes": actual,
                "size_verified": actual == expected,
                "file_type": row.get("file_type", ""),
                "acquisition_mode": row.get("acquisition_mode", ""),
                "species": row.get("canonical_species") or row.get("species", ""),
                "instrument_families": row.get("instrument_families", ""),
                "fragmentation_methods": row.get("fragmentation_methods", ""),
                "laboratory_names": row.get("laboratory_names", ""),
                "processing_lane": lane,
                "preparation_action": action,
                "status": "ready_for_preflight" if actual == expected else "awaiting_transfer",
            }
        )

    payload = {
        "batch_id": f"pride-benchmark-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_manifest": str(args.manifest.resolve()),
        "data_dir": str(args.data_dir.resolve()),
        "output_dir": str(args.output_dir.resolve()),
        "items": items,
        "processing_policy": {
            "raw_inputs_are_not_training_labels": True,
            "require_search_and_qc_before_dataset_construction": True,
            "preserve_dia_as_separate_lane": True,
            "do_not_publish_until_independent_audit": True,
        },
    }
    (args.output_dir / "batch_processing_plan.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (args.output_dir / "batch_processing_plan.csv").open("w", encoding="utf-8", newline="") as handle:
        fieldnames = list(items[0])
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(items)
    print(json.dumps({"batch_id": payload["batch_id"], "items": len(items), "verified": sum(bool(item["size_verified"]) for item in items), "output_dir": str(args.output_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
