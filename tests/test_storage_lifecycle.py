from __future__ import annotations

import json
from pathlib import Path

import pytest

import agent.web.app as web_app
from agent.web.storage_lifecycle import (
    clean_item_source_assets,
    delete_managed_tree,
    managed_child,
    path_size_bytes,
)


def test_clean_item_source_assets_only_removes_task_local_downloads_and_prepared(
    tmp_path: Path,
) -> None:
    item = tmp_path / "batches" / "batch-1" / "items" / "001"
    downloads = item / "assets" / "downloads"
    prepared = item / "assets" / "prepared"
    logs = item / "logs"
    downloads.mkdir(parents=True)
    prepared.mkdir(parents=True)
    logs.mkdir(parents=True)
    (downloads / "source.raw").write_bytes(b"x" * 11)
    (prepared / "source.mzML").write_bytes(b"y" * 13)
    (logs / "runtime.log").write_text("keep", encoding="utf-8")
    (item / "parameter_audit.json").write_text("{}", encoding="utf-8")

    receipt = clean_item_source_assets(item)

    assert receipt["status"] == "completed"
    assert receipt["released_bytes"] == 24
    assert not downloads.exists()
    assert not prepared.exists()
    assert (logs / "runtime.log").exists()
    assert (item / "parameter_audit.json").exists()


def test_managed_child_and_delete_refuse_root_and_path_escape(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    root.mkdir()
    with pytest.raises(ValueError):
        managed_child(root, "../outside")
    with pytest.raises(ValueError):
        delete_managed_tree(root, root)


def test_delete_managed_tree_reports_released_bytes(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    target = root / "discovery-1"
    target.mkdir(parents=True)
    (target / "manifest.json").write_bytes(b"z" * 17)

    receipt = delete_managed_tree(root, target)

    assert receipt["status"] == "completed"
    assert receipt["released_bytes"] == 17
    assert not target.exists()
    assert path_size_bytes(target) == 0


def test_batch_cleanup_retains_failed_item_and_cleans_completed_item(
    tmp_path: Path,
) -> None:
    failed_item = tmp_path / "failed"
    (failed_item / "assets" / "downloads").mkdir(parents=True)
    (failed_item / "assets" / "downloads" / "source.raw").write_bytes(b"x")

    retained = web_app._batch_item_source_cleanup(
        requested=True,
        output_dir=failed_item,
        terminal_status="failed",
    )
    assert retained["status"] == "retained"
    assert (failed_item / "assets" / "downloads" / "source.raw").exists()

    cleaned = web_app._batch_item_source_cleanup(
        requested=True,
        output_dir=failed_item,
        terminal_status="completed",
    )
    assert cleaned["status"] == "completed"
    assert cleaned["released_bytes"] == 1


def test_history_batch_delete_requires_preview_and_releases_space(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    batch_id = "batchdelete1"
    batch_dir = web_app._batch_dir(batch_id)
    batch_dir.mkdir(parents=True)
    (batch_dir / "payload.bin").write_bytes(b"x" * 23)
    batch = {
        "batch_id": batch_id,
        "status": "completed",
        "output_dir": str(batch_dir),
        "items": [],
    }
    web_app._write_batch_manifest(batch)

    preview = web_app._history_delete_preview(
        "batch",
        batch_id,
        include_linked_batches=False,
    )
    result = web_app._execute_history_delete(
        "batch",
        batch_id,
        {
            "confirmation_id": preview["confirmation_id"],
            "include_linked_batches": False,
        },
    )

    assert result["status"] == "completed"
    assert result["released_bytes"] >= 23
    assert not batch_dir.exists()


def test_stale_discovery_history_can_be_removed_from_index(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    discovery_id = "discovery_stale_1"
    web_app._write_history_index(
        [
            {
                "kind": "discovery",
                "history_id": f"discovery-{discovery_id}",
                "discovery_id": discovery_id,
                "run_id": discovery_id,
                "result_id": discovery_id,
                "project_key": f"discovery-{discovery_id}",
                "status": "completed",
                "size_bytes": 1000,
                "can_download": True,
            }
        ]
    )

    preview = web_app._history_delete_preview(
        "discovery",
        discovery_id,
        include_linked_batches=False,
    )
    decorated = web_app._decorate_history_item(
        web_app._find_history_record(discovery_id) or {}
    )
    assert preview["deletable"] is True
    assert preview["estimated_bytes"] == 0
    assert preview["targets"][0]["result_available"] is False
    assert decorated["size_bytes"] == 0
    assert decorated["recorded_size_bytes"] == 1000
    assert decorated["open_available"] is False
    assert decorated["can_download"] is False
    decorated_again = web_app._decorate_history_item(decorated)
    assert decorated_again["recorded_size_bytes"] == 1000

    result = web_app._execute_history_delete(
        "discovery",
        discovery_id,
        {
            "confirmation_id": preview["confirmation_id"],
            "include_linked_batches": False,
        },
    )
    assert result["released_bytes"] == 0
    assert not web_app._find_history_record(discovery_id)


def test_rebuilds_discovery_batches_from_frozen_manifests(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    discovery_id = "discovery_batches_1"
    batch_dir = (
        web_app._discovery_root_dir()
        / discovery_id
        / "verified_batches"
        / "batch_001"
    )
    batch_dir.mkdir(parents=True)
    manifest = {
        "run_id": discovery_id,
        "request": {
            "query_terms": ["immunopeptidomics"],
            "partial_delivery_batch_size": 500,
        },
        "projects": [
            {
                "project_accession": "PXD000001",
                "project_title": "Human immunopeptidome",
            }
        ],
        "files": [
            {
                "project_accession": "PXD000001",
                "project_title": "Human immunopeptidome",
                "file_accession_or_path": "PXD000001/sample.raw",
                "file_name": "sample.raw",
                "download_url": "https://example.test/sample.raw",
                "file_type": ".raw",
                "file_role": "raw_acquisition",
            }
        ],
        "summary": {
            "artifact_type": "verified_file_batch",
            "delivery_unit": "file",
            "terminal": True,
        },
    }
    (batch_dir / "dataset_manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    job = {
        "job_id": "job-1",
        "body": {"_execution_discovery_id": discovery_id},
        "result_batches": [],
    }

    batches = web_app._discovery_result_batches(job)
    handoff = web_app._discovery_batch_handoff(job, 1)

    assert len(batches) == 1
    assert batches[0]["file_count"] == 1
    assert batches[0]["terminal"] is True
    assert batches[0]["status"] == "ready"
    assert batches[0]["published_at"]
    assert handoff["file_count"] == 1
    assert handoff["inputs"] == ["https://example.test/sample.raw"]


def test_handoff_of_second_full_batch_contains_exactly_its_500_files(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    discovery_id = "discovery_full_batches"
    for batch_index in (1, 2):
        batch_dir = (
            web_app._discovery_root_dir()
            / discovery_id
            / "verified_batches"
            / f"batch_{batch_index:03d}"
        )
        batch_dir.mkdir(parents=True)
        start = (batch_index - 1) * 500
        files = [
            {
                "project_accession": "PXD000001",
                "project_title": "Human immunopeptidome",
                "file_accession_or_path": f"PXD000001/file-{index}.raw",
                "file_name": f"file-{index}.raw",
                "download_url": f"https://example.test/file-{index}.raw",
                "file_type": ".raw",
                "file_role": "raw_acquisition",
            }
            for index in range(start, start + 500)
        ]
        (batch_dir / "dataset_manifest.json").write_text(
            json.dumps(
                {
                    "run_id": discovery_id,
                    "request": {
                        "query_terms": ["immunopeptidomics"],
                        "partial_delivery_batch_size": 500,
                    },
                    "projects": [
                        {
                            "project_accession": "PXD000001",
                            "project_title": "Human immunopeptidome",
                        }
                    ],
                    "files": files,
                    "summary": {
                        "artifact_type": "verified_file_batch",
                        "delivery_unit": "file",
                        "terminal": False,
                    },
                }
            ),
            encoding="utf-8",
        )
    job = {
        "job_id": "job-full-batches",
        "body": {"_execution_discovery_id": discovery_id},
        "result_batches": [],
    }

    handoff = web_app._discovery_batch_handoff(job, 2)

    assert handoff["file_count"] == 500
    assert len(handoff["input_records"]) == 500
    assert len(set(handoff["inputs"])) == 500
    assert handoff["inputs"][0].endswith("file-500.raw")
    assert all("file-0.raw" not in value for value in handoff["inputs"])


def test_history_delete_rejects_scope_changed_after_preview(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    batch_id = "batchscope1"
    batch_dir = web_app._batch_dir(batch_id)
    batch_dir.mkdir(parents=True)
    web_app._write_batch_manifest(
        {
            "batch_id": batch_id,
            "status": "completed",
            "output_dir": str(batch_dir),
            "items": [],
        }
    )
    preview = web_app._history_delete_preview(
        "batch",
        batch_id,
        include_linked_batches=False,
    )
    original_targets = web_app._history_delete_targets

    def changed_targets(kind, identifier, *, include_linked_batches):
        targets = original_targets(
            kind,
            identifier,
            include_linked_batches=include_linked_batches,
        )
        return [
            *targets,
            {
                "kind": "batch",
                "id": "unexpected-new-batch",
                "status": "completed",
                "path": str(web_app._batch_dir("unexpected-new-batch")),
                "size_bytes": 0,
            },
        ]

    monkeypatch.setattr(web_app, "_history_delete_targets", changed_targets)
    with pytest.raises(ValueError, match="scope changed"):
        web_app._execute_history_delete(
            "batch",
            batch_id,
            {
                "confirmation_id": preview["confirmation_id"],
                "include_linked_batches": False,
            },
        )
    assert batch_dir.exists()
