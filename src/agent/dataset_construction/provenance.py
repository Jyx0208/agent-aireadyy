from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from prov.model import ProvDocument
from rocrate.rocrate import ROCrate

from agent.dataset_construction.models import DatasetCatalog, LeakageAudit, SplitSuite


def write_prov_document(
    root: Path,
    *,
    release_id: str,
    catalog: DatasetCatalog,
    suite: SplitSuite,
    audits: dict[str, LeakageAudit],
) -> Path:
    """Write interoperable W3C PROV describing the complete release build."""

    destination = root / "provenance" / "prov.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    document = ProvDocument()
    document.add_namespace("dataset", "https://guomics.example/dataset-construction/")
    software = document.agent(
        "dataset:leakage-aware-agent",
        {"prov:type": "prov:SoftwareAgent", "prov:label": "Leakage-aware dataset construction"},
    )
    source = document.entity(
        "dataset:source-batch",
        {"prov:location": catalog.source_batch_dir, "prov:label": "Existing Batch output"},
    )
    build = document.activity(f"dataset:build-{release_id}")
    release = document.entity(
        f"dataset:release-{release_id}",
        {
            "prov:label": release_id,
            "dataset:observationCount": len(catalog.observations),
            "dataset:protocolCount": len(suite.protocols),
        },
    )
    document.used(build, source)
    document.wasAssociatedWith(build, software)
    document.wasGeneratedBy(release, build)
    for protocol, plan in suite.protocols.items():
        split_entity = document.entity(
            f"dataset:split-{protocol}",
            {
                "prov:label": protocol,
                "dataset:splitStatus": plan.status,
                "dataset:auditStatus": audits[protocol].status,
                "dataset:solver": plan.solver or "not-run",
            },
        )
        document.wasGeneratedBy(split_entity, build)
        document.wasDerivedFrom(split_entity, source)
        document.wasDerivedFrom(release, split_entity)
    document.serialize(destination=str(destination), format="json")
    return destination


def write_ro_crate(
    root: Path,
    *,
    release_id: str,
    task_spec: dict[str, Any],
) -> Path:
    """Describe every frozen release artifact as an RO-Crate data entity."""

    destination = root / "ro-crate-metadata.json"
    crate = ROCrate()
    crate.name = f"Leakage-aware proteomics dataset release {release_id}"
    crate.description = (
        "Immutable dataset release with model-observation catalog, split manifests, "
        "independent leakage audits, checksums, and W3C PROV provenance."
    )
    crate.root_dataset["identifier"] = release_id
    crate.root_dataset["taskSpec"] = json.dumps(
        task_spec,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path == destination:
            continue
        crate.add_file(
            source=path,
            dest_path=path.relative_to(root).as_posix(),
            properties={"name": path.name},
            record_size=True,
        )
    crate.metadata.write(root)
    return destination
