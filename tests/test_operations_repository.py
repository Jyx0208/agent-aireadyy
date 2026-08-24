from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter

import pytest
from sqlalchemy import func, insert, select

from agent.operations.config import OperationsSettings
from agent.operations.legacy import (
    import_legacy_discovery_summaries,
    import_legacy_history_index,
)
from agent.operations.repository import OperationsRepository
from agent.operations.models import (
    BatchFile,
    DeletionRequest,
    FileRecord,
    Job,
    JobEvent,
    ProjectReview,
)
from agent.operations.state import InvalidJobTransition
from agent.discovery.file_judgment import stable_file_id


def settings(tmp_path: Path) -> OperationsSettings:
    root = tmp_path / "operations"
    return OperationsSettings(
        database_path=root / "operations.sqlite",
        queue_path=root / "queue.sqlite",
        artifact_root=root / "artifacts",
        worker_count=4,
    )


def test_operations_state_events_and_indexed_evidence(tmp_path: Path):
    repository = OperationsRepository(settings(tmp_path))
    try:
        created = repository.create_job(
            job_id="discovery_job_test",
            payload={
                "objective": "检索 PRIDE 中所有人类免疫肽组学数据",
                "repository": "pride",
                "species": ["Homo sapiens"],
            },
            terms=[
                {"term": "immunopeptidomics", "role": "primary_theme"},
                {"term": "HLA ligandome", "role": "theme_synonym"},
            ],
            idempotency_key="operations-test",
        )
        assert created["status"] == "queued"
        assert created["progress"]["term_total"] == 2

        repository.transition_job(
            "discovery_job_test",
            "searching",
            phase="searching",
            reason="worker started",
            event_type="job_started",
        )
        repository.append_event(
            "discovery_job_test",
            event_type="repository_term_task_started",
            phase="searching",
            payload={
                "term": "immunopeptidomics",
                "term_index": 1,
                "term_count": 2,
            },
        )
        repository.append_event(
            "discovery_job_test",
            event_type="repository_term_chunk_completed",
            phase="searching",
            payload={
                "term": "immunopeptidomics",
                "term_index": 1,
                "page_count": 3,
                "raw_count": 305,
                "new_candidate_count": 305,
                "candidate_count": 305,
            },
        )
        repository.append_event(
            "discovery_job_test",
            event_type="repository_term_task_completed",
            phase="searching",
            payload={
                "term": "immunopeptidomics",
                "term_index": 1,
                "raw_count": 305,
                "unique_count": 305,
            },
        )
        repository.append_event(
            "discovery_job_test",
            event_type="candidate_inspection_started",
            phase="reviewing",
            payload={
                "action": {"accessions": ["PXD000001"]},
                "worker_slot": 1,
                "step": "metadata",
            },
        )
        repository.append_event(
            "discovery_job_test",
            event_type="candidate_inspection_completed",
            phase="reviewing",
            payload={
                "observation": {
                    "metrics": {
                        "candidate_count": 305,
                        "reviewed_project_count": 1,
                        "pending_review_count": 304,
                    },
                    "project_assessments": [
                        {
                            "project_accession": "PXD000001",
                            "project_title": "Human HLA ligandome",
                            "decision": "qualified",
                            "score": 91,
                            "confidence": 0.93,
                            "reasons": ["human immunopeptidomics metadata"],
                            "usable_file_count": 8,
                            "evidence": {"metadata": "HLA class I ligands"},
                        }
                    ],
                }
            },
        )

        snapshot = repository.get_job("discovery_job_test")
        assert snapshot is not None
        assert snapshot["status"] == "reviewing"
        assert snapshot["progress"]["candidate_count"] == 305
        assert snapshot["progress"]["reviewed_count"] == 1
        assert snapshot["progress"]["pending_review_count"] == 304
        assert len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8")) < 100_000

        terms = repository.list_terms("discovery_job_test")
        assert terms[0]["status"] == "completed"
        assert terms[0]["raw_count"] == 305

        reviews = repository.list_reviews(
            "discovery_job_test",
            page=1,
            page_size=25,
        )
        assert reviews.total == 1
        assert reviews.items[0]["decision"] == "qualified"
        assert reviews.items[0]["usable_file_count"] == 8

        events = repository.events_after(
            "discovery_job_test",
            after=0,
            limit=100,
        )
        assert [event["sequence"] for event in events] == list(
            range(1, len(events) + 1)
        )
        assert all(
            len(json.dumps(event, ensure_ascii=False).encode("utf-8")) < 16_384
            for event in events
        )
    finally:
        repository.close()


def test_repository_query_events_update_term_pages_and_failure_state(tmp_path: Path):
    repository = OperationsRepository(settings(tmp_path))
    try:
        repository.create_job(
            job_id="query-events",
            payload={"objective": "human immunopeptidomics"},
            terms=[
                {"term": "immunopeptidomics", "role": "primary_theme"},
                {"term": "HLA ligandome", "role": "theme_synonym"},
            ],
        )
        repository.transition_job(
            "query-events",
            "searching",
            phase="searching",
            reason="worker started",
        )
        repository.append_event(
            "query-events",
            event_type="repository_query_started",
            phase="searching",
            payload={
                "query": "immunopeptidomics",
                "role": "primary_theme",
                "page_number": 1,
                "page_size": 100,
            },
        )
        repository.append_event(
            "query-events",
            event_type="repository_query_page_completed",
            phase="searching",
            payload={
                "query": "immunopeptidomics",
                "role": "primary_theme",
                "page_number": 1,
                "page_result_count": 100,
                "pages_completed": 1,
                "cumulative_count": 100,
            },
        )
        repository.append_event(
            "query-events",
            event_type="repository_query_completed",
            phase="searching",
            payload={
                "query": "immunopeptidomics",
                "role": "primary_theme",
                "pages_completed": 1,
                "raw_result_count": 100,
                "new_candidate_count": 94,
                "duplicate_count": 6,
            },
        )
        repository.append_event(
            "query-events",
            event_type="repository_query_failed",
            phase="searching",
            level="error",
            payload={
                "query": "HLA ligandome",
                "role": "theme_synonym",
                "page_number": 1,
                "pages_completed": 0,
                "error": "TLS connection closed",
            },
        )

        terms = repository.list_terms("query-events")
        assert terms[0]["status"] == "completed"
        assert terms[0]["page_count"] == 1
        assert terms[0]["raw_count"] == 100
        assert terms[0]["unique_count"] == 94
        assert terms[1]["status"] == "failed"
        assert terms[1]["error"]["message"] == "TLS connection closed"
        snapshot = repository.get_job("query-events")
        assert snapshot is not None
        assert snapshot["progress"]["term_completed"] == 2
        assert snapshot["progress"]["raw_hit_count"] == 100
    finally:
        repository.close()


def test_operations_final_record_indexes_only_selected_files(tmp_path: Path):
    repository = OperationsRepository(settings(tmp_path))
    try:
        repository.create_job(
            job_id="mixed-project",
            payload={"objective": "mixed project", "repository": "pride"},
        )
        repository.sync_legacy_job(
            {
                "job_id": "mixed-project",
                "status": "completed",
                "body": {"objective": "mixed project", "repository": "pride"},
                "execution_state": {
                    "phase": "completed",
                    "candidate_count": 1,
                    "reviewed_project_count": 1,
                    "pending_review_count": 0,
                },
                "record": {
                    "projects": [
                        {
                            "project_accession": "PXD000002",
                            "title": "Mixed acquisition project",
                            "decision": "qualified",
                        }
                    ],
                    "files": [
                        {
                            "project_accession": "PXD000002",
                            "file_name": "selected.raw",
                            "file_accession_or_path": "selected.raw",
                            "file_role": "raw",
                            "status": "usable",
                            "eligible": True,
                        },
                        {
                            "project_accession": "PXD000002",
                            "file_name": "excluded.dia",
                            "file_accession_or_path": "excluded.dia",
                            "file_role": "raw",
                            "status": "excluded",
                            "eligible": False,
                            "reason_code": "acquisition_mode_mismatch",
                        },
                    ],
                },
            }
        )
        usable = repository.list_files(
            "mixed-project",
            page=1,
            page_size=25,
            eligible=True,
        )
        excluded = repository.list_files(
            "mixed-project",
            page=1,
            page_size=25,
            eligible=False,
        )
        assert [item["file_name"] for item in usable.items] == ["selected.raw"]
        assert [item["file_name"] for item in excluded.items] == ["excluded.dia"]
    finally:
        repository.close()


def test_file_review_filters_cursor_and_detail_reason(tmp_path: Path):
    repository = OperationsRepository(settings(tmp_path))
    try:
        files = []
        for index, decision in enumerate(("include", "exclude", "investigate"), start=1):
            native_id = f"sample-{index}.raw"
            files.append(
                {
                    "repository": "pride",
                    "project_accession": "PXD000003",
                    "file_accession_or_path": native_id,
                    "file_name": native_id,
                    "file_id": stable_file_id("pride", "PXD000003", native_id),
                    "review_status": "reviewed",
                    "decision": decision,
                    "reason_status": "ready",
                    "reason_scope": "file",
                    "reason_text": f"file-specific reason {index}",
                    "judgment_confidence": 0.9,
                }
            )
        repository.sync_legacy_job(
            {
                "job_id": "file-review-job",
                "status": "completed",
                "body": {"objective": "file review", "repository": "pride"},
                "record": {"files": files},
            }
        )
        repository.project_file_review_event(
            "file-review-job",
            "file_review_batch_started",
            {"items": [files[0]]},
        )
        repository.project_file_review_event(
            "file-review-job",
            "file_review_batch_completed",
            {
                "judgments": [
                    {
                        **files[0],
                        "review_status": "reviewed",
                        "decision": "include",
                        "reason_status": "ready",
                    }
                ]
            },
        )

        first = repository.list_files(
            "file-review-job",
            page_size=1,
            cursor=0,
            review_status="reviewed",
        )
        second = repository.list_files(
            "file-review-job",
            page_size=1,
            cursor=first.next_cursor,
            review_status="reviewed",
        )
        excluded = repository.list_files(
            "file-review-job",
            cursor=0,
            decision="exclude",
        )
        detail = repository.get_file("file-review-job", files[1]["file_id"])

        assert first.items[0]["file_id"] != second.items[0]["file_id"]
        assert first.items[0]["reason_text"] is None
        assert excluded.summary and excluded.summary["excluded"] == 1
        assert [item["decision"] for item in excluded.items] == ["exclude"]
        assert detail and detail["reason_text"] == "file-specific reason 2"
    finally:
        repository.close()


def test_invalid_terminal_transition_is_rejected(tmp_path: Path):
    repository = OperationsRepository(settings(tmp_path))
    try:
        repository.create_job(
            job_id="terminal-job",
            payload={"objective": "terminal"},
        )
        repository.transition_job(
            "terminal-job",
            "searching",
            phase="searching",
            reason="start",
        )
        repository.transition_job(
            "terminal-job",
            "finalizing",
            phase="finalizing",
            reason="freeze",
        )
        repository.transition_job(
            "terminal-job",
            "completed",
            phase="completed",
            reason="done",
        )
        with pytest.raises(InvalidJobTransition):
            repository.transition_job(
                "terminal-job",
                "searching",
                phase="searching",
                reason="illegal implicit rerun",
            )
    finally:
        repository.close()


def test_fixed_target_pipeline_completion_projects_to_finalizing(tmp_path: Path):
    repository = OperationsRepository(settings(tmp_path))
    try:
        repository.create_job(
            job_id="fixed-target-complete",
            payload={"objective": "find 20", "max_projects": 20},
            terms=[{"term": "immunopeptidomics", "role": "primary_theme"}],
        )
        repository.transition_job(
            "fixed-target-complete",
            "searching",
            phase="searching",
            reason="worker started",
        )
        repository.append_event(
            "fixed-target-complete",
            event_type="confirmed_theme_pipeline_completed",
            phase="failed",
            payload={
                "status": "completed",
                "target_reached": True,
                "all_terms_exhausted": False,
                "pending_review_count": 0,
                "qualified_project_count": 20,
            },
        )

        job = repository.get_job("fixed-target-complete")
        assert job is not None
        assert job["phase"] == "finalizing"
        assert job["progress"]["qualified_count"] == 20
    finally:
        repository.close()


def test_late_progress_event_cannot_roll_back_terminal_phase(tmp_path: Path):
    repository = OperationsRepository(settings(tmp_path))
    try:
        repository.create_job(
            job_id="terminal-phase",
            payload={"objective": "finish cleanly"},
        )
        repository.transition_job(
            "terminal-phase",
            "searching",
            phase="searching",
            reason="worker started",
        )
        repository.transition_job(
            "terminal-phase",
            "finalizing",
            phase="finalizing",
            reason="freezing results",
            error_code="old_failure",
            error_message="old failure",
        )
        repository.transition_job(
            "terminal-phase",
            "completed",
            phase="completed",
            reason="done",
        )

        repository.append_event(
            "terminal-phase",
            event_type="job_message",
            phase="finalizing",
            message="late informational event",
        )

        job = repository.get_job("terminal-phase")
        assert job is not None
        assert job["status"] == "completed"
        assert job["phase"] == "completed"
        assert job["error"] is None
    finally:
        repository.close()


def test_completed_legacy_sync_clears_error_from_earlier_attempt(tmp_path: Path):
    repository = OperationsRepository(settings(tmp_path))
    try:
        repository.sync_legacy_job(
            {
                "job_id": "completed-legacy",
                "status": "completed",
                "error": "Agent run already exists",
                "body": {"objective": "find 20"},
                "execution_state": {"phase": "completed"},
            }
        )

        job = repository.get_job("completed-legacy")
        assert job is not None
        assert job["status"] == "completed"
        assert job["phase"] == "completed"
        assert job["error"] is None
    finally:
        repository.close()


def test_active_legacy_sync_hides_stale_error_from_resumed_attempt(tmp_path: Path):
    repository = OperationsRepository(settings(tmp_path))
    try:
        repository.sync_legacy_job(
            {
                "job_id": "active-legacy-resume",
                "status": "failed",
                "error": "peer closed connection without sending complete message body",
                "body": {"objective": "find 20"},
                "execution_state": {"phase": "failed"},
            }
        )
        repository.transition_job(
            "active-legacy-resume",
            "queued",
            phase="queued",
            reason="resume requested",
            resumable=False,
        )
        active = repository.sync_legacy_job(
            {
                "job_id": "active-legacy-resume",
                "status": "running",
                "error": "peer closed connection without sending complete message body",
                "body": {"objective": "find 20"},
                "execution_state": {"phase": "finalizing"},
            }
        )

        assert active["status"] == "finalizing"
        assert active["error"] is None
    finally:
        repository.close()


def test_batch_delivery_survives_without_legacy_manifest(tmp_path: Path):
    repository = OperationsRepository(settings(tmp_path))
    try:
        repository.sync_legacy_job(
            {
                "job_id": "durable-batch",
                "status": "completed",
                "body": {"objective": "find files"},
                "execution_state": {"phase": "completed"},
                "record": {
                    "files": [
                        {
                            "repository": "pride",
                            "project_accession": "PXD000001",
                            "file_accession_or_path": "file-a",
                            "file_name": "a.raw",
                            "download_url": "https://example.test/a.raw",
                            "file_type": "raw",
                            "file_role": "acquisition",
                            "acquisition_mode": "dda",
                            "eligible": True,
                        },
                        {
                            "repository": "pride",
                            "project_accession": "PXD000002",
                            "file_accession_or_path": "file-b",
                            "file_name": "b.raw",
                            "download_url": "https://example.test/b.raw",
                            "file_type": "raw",
                            "file_role": "acquisition",
                            "acquisition_mode": "dda",
                            "eligible": True,
                        },
                    ]
                },
                "result_batches": [
                    {
                        "batch_index": 1,
                        "file_count": 2,
                        "project_count": 2,
                        "cumulative_verified_file_count": 2,
                        "manifest_path": "missing/dataset_manifest.json",
                        "file_identifiers": [
                            "pride:PXD000002:file-b",
                            "pride:PXD000001:file-a",
                        ],
                        "terminal": True,
                    }
                ],
            }
        )

        delivery = repository.get_batch_delivery("durable-batch", 1)
        assert delivery is not None
        assert delivery["missing_file_identifiers"] == []
        assert [item["file_name"] for item in delivery["files"]] == [
            "b.raw",
            "a.raw",
        ]
    finally:
        repository.close()


def test_legacy_import_skips_empty_garbage_and_keeps_material_runs(tmp_path: Path):
    runs = tmp_path / "runs"
    summaries = runs / "discovery_jobs" / "_history"
    summaries.mkdir(parents=True)
    (summaries / "empty.json").write_text(
        json.dumps(
            {
                "job_id": "empty",
                "status": "failed",
                "project_count": 0,
                "file_count": 0,
            }
        ),
        encoding="utf-8",
    )
    (summaries / "material.json").write_text(
        json.dumps(
            {
                "job_id": "material",
                "status": "completed",
                "display_name": "Human immunopeptidomics",
                "project_count": 231,
                "file_count": 5057,
            }
        ),
        encoding="utf-8",
    )
    (runs / "project_history.json").write_text(
        json.dumps(
            [
                {
                    "task_id": "garbage-task",
                    "status": "failed",
                    "input_value": "sample.raw",
                },
                {
                    "task_id": "_operations",
                    "status": "completed",
                    "input_value": "_operations",
                },
                {
                    "task_id": "material-task",
                    "status": "completed",
                    "input_value": "real.raw",
                    "file_count": 1,
                    "can_download": True,
                },
                {
                    "kind": "discovery",
                    "job_id": None,
                    "discovery_id": "agents_job_discovery_job_123",
                    "status": "blocked",
                    "display_name": "Discovery Â· immunopeptidomics",
                    "input_value": "immunopeptidomics",
                    "project_count": 12,
                    "file_count": 500,
                },
                {
                    "kind": "discovery",
                    "job_id": None,
                    "discovery_id": "empty-download",
                    "status": "failed",
                    "can_download": True,
                    "project_count": 0,
                    "file_count": 0,
                },
            ]
        ),
        encoding="utf-8",
    )
    repository = OperationsRepository(settings(tmp_path))
    try:
        discovery = import_legacy_discovery_summaries(repository, runs)
        history = import_legacy_history_index(repository, runs)
        assert discovery["imported"] == 1
        assert discovery["skipped"] == 1
        assert history["imported"] == 2
        assert history["skipped"] == 3
        page = repository.list_history(page=1, page_size=25)
        assert {item["source_id"] for item in page.items} == {
            "material",
            "material-task",
            "agents_job_discovery_job_123",
        }
        discovery = repository.get_job("agents_job_discovery_job_123")
        assert discovery is not None
        assert discovery["status"] == "blocked"
        assert discovery["progress"]["usable_file_count"] == 500
        migrated = next(
            item
            for item in page.items
            if item["source_id"] == "agents_job_discovery_job_123"
        )
        assert migrated["display_name"] == "Discovery · immunopeptidomics"
    finally:
        repository.close()


def test_stale_legacy_projection_cannot_regress_durable_job(tmp_path: Path):
    repository = OperationsRepository(settings(tmp_path))
    try:
        repository.create_job(
            job_id="durable-job",
            payload={"objective": "durable"},
        )
        repository.transition_job(
            "durable-job",
            "searching",
            phase="searching",
            reason="worker claimed",
        )
        repository.sync_legacy_job(
            {
                "job_id": "durable-job",
                "status": "queued",
                "body": {"objective": "stale disk snapshot"},
                "execution_state": {"phase": "queued"},
            }
        )
        assert repository.get_job("durable-job")["status"] == "searching"

        repository.transition_job(
            "durable-job",
            "finalizing",
            phase="finalizing",
            reason="freeze",
        )
        repository.transition_job(
            "durable-job",
            "completed",
            phase="completed",
            reason="done",
        )
        repository.sync_legacy_job(
            {
                "job_id": "durable-job",
                "status": "running",
                "body": {"objective": "stale active snapshot"},
                "execution_state": {"phase": "reviewing"},
            }
        )
        assert repository.get_job("durable-job")["status"] == "completed"
    finally:
        repository.close()


def test_batch_membership_is_deduplicated_and_capped_by_publisher(tmp_path: Path):
    repository = OperationsRepository(settings(tmp_path))
    try:
        repository.create_job(
            job_id="batch-job",
            payload={"objective": "deliver files"},
        )
        first_batch = [f"pride:PXD000001:file-{index:04d}.raw" for index in range(500)]
        repository.append_event(
            "batch-job",
            event_type="verified_project_batch_published",
            payload={
                "batch_index": 1,
                "file_count": 500,
                "project_count": 20,
                "cumulative_verified_file_count": 500,
                "file_identifiers": first_batch,
            },
        )
        repository.append_event(
            "batch-job",
            event_type="verified_project_batch_published",
            payload={
                "batch_index": 2,
                "file_count": 2,
                "project_count": 1,
                "cumulative_verified_file_count": 502,
                "file_identifiers": [
                    "pride:PXD000002:new-1.raw",
                    "pride:PXD000002:new-2.raw",
                ],
            },
        )
        batches = repository.list_batches("batch-job")
        assert [item["file_count"] for item in batches] == [500, 2]
        assert [item["cumulative_file_count"] for item in batches] == [500, 502]
        with repository.database.session() as session:
            assert session.scalar(select(func.count(BatchFile.id))) == 502
    finally:
        repository.close()


def test_history_index_handles_thousands_without_loading_run_files(tmp_path: Path):
    repository = OperationsRepository(settings(tmp_path))
    try:
        records = [
            (
                {
                    "status": "completed",
                    "display_name": f"Human immunopeptidomics {index}",
                    "project_count": index % 300,
                    "file_count": index % 5000,
                    "updated_at": f"2026-07-{(index % 28) + 1:02d}T12:00:00+00:00",
                },
                "discovery",
                f"history-{index:05d}",
                None,
                index * 1024,
            )
            for index in range(2_000)
        ]
        import_started = perf_counter()
        assert repository.upsert_history_records_bulk(records) == 2_000
        assert perf_counter() - import_started < 10

        query_started = perf_counter()
        page = repository.list_history(
            page=20,
            page_size=25,
            query="immunopeptidomics",
        )
        assert perf_counter() - query_started < 1
        assert page.total == 2_000
        assert len(page.items) == 25
    finally:
        repository.close()


def test_history_hides_execution_discovery_alias_when_canonical_job_exists(
    tmp_path: Path,
):
    repository = OperationsRepository(settings(tmp_path))
    try:
        job_id = "discovery_job_20260731_113056_4b04bc"
        repository.create_job(
            job_id=job_id,
            payload={"objective": "Human immunopeptidomics"},
        )
        repository.transition_job(
            job_id,
            "searching",
            phase="searching",
            reason="worker claimed",
        )
        repository.transition_job(
            job_id,
            "finalizing",
            phase="finalizing",
            reason="freeze delivery",
        )
        repository.append_event(
            job_id,
            event_type="verified_project_batch_published",
            phase="finalizing",
            payload={
                "batch_index": 1,
                "file_count": 500,
                "project_count": 8,
                "cumulative_verified_file_count": 500,
                "file_identifiers": [
                    f"pride:PXD000001:file-{index:04d}.raw"
                    for index in range(500)
                ],
            },
        )
        repository.transition_job(
            job_id,
            "completed",
            phase="completed",
            reason="done",
        )
        alias_id = f"agents_job_{job_id}"
        repository.upsert_history_record(
            {
                "status": "blocked",
                "display_name": "Execution wrapper",
                "project_count": 20,
                "file_count": 966,
                "updated_at": "2026-07-31T12:07:49+08:00",
            },
            kind="discovery",
            source_id=alias_id,
            history_id=f"discovery:{alias_id}",
        )

        page = repository.list_history(
            page=1,
            page_size=25,
            kind="discovery",
        )

        assert page.total == 1
        assert [item["source_id"] for item in page.items] == [job_id]
        assert repository.history_summary()["total"] == 1
    finally:
        repository.close()


def test_large_operations_fixture_stays_bounded_and_paged(tmp_path: Path):
    repository = OperationsRepository(settings(tmp_path))
    try:
        repository.create_job(
            job_id="scale-job",
            payload={"objective": "Human immunopeptidomics scale fixture"},
        )
        reviews = [
            {
                "job_id": "scale-job",
                "accession": f"PXD{index:06d}",
                "position": index,
                "status": "completed",
                "decision": "qualified",
            }
            for index in range(1, 766)
        ]
        files = [
            {
                "job_id": "scale-job",
                "project_accession": f"PXD{(index % 765) + 1:06d}",
                "native_id": f"file-{index:05d}.raw",
                "file_name": f"file-{index:05d}.raw",
                "eligible": index % 2 == 0,
                "status": "qualified" if index % 2 == 0 else "excluded",
            }
            for index in range(20_000)
        ]
        events = [
            {
                "job_id": "scale-job",
                "sequence": sequence,
                "event_type": "project_review_step",
                "actor": "scale-fixture",
                "phase": "reviewing",
                "message": f"Reviewed item {sequence - 1}",
                "payload": {"position": sequence - 1},
            }
            for sequence in range(2, 10_002)
        ]
        load_started = perf_counter()
        with repository.database.session() as session:
            session.execute(insert(ProjectReview), reviews)
            session.execute(insert(FileRecord), files)
            session.execute(insert(JobEvent), events)
            job = session.get(Job, "scale-job")
            assert job is not None
            job.candidate_count = 765
            job.reviewed_count = 765
            job.qualified_count = 765
            job.file_clue_count = 20_000
            job.usable_file_count = 10_000
            job.event_sequence = 10_001
            session.commit()
        assert perf_counter() - load_started < 30

        query_started = perf_counter()
        review_page = repository.list_reviews(
            "scale-job",
            page=31,
            page_size=25,
        )
        file_page = repository.list_files(
            "scale-job",
            page=200,
            page_size=100,
            eligible=None,
        )
        event_page = repository.events_after(
            "scale-job",
            after=9_900,
            limit=100,
        )
        assert perf_counter() - query_started < 1
        assert review_page.total == 765
        assert len(review_page.items) == 15
        assert file_page.total == 20_000
        assert len(file_page.items) == 100
        assert len(event_page) == 100
        snapshot = repository.get_job("scale-job")
        assert snapshot is not None
        assert len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8")) < 100_000
    finally:
        repository.close()


def test_history_delete_creates_a_durable_audit_receipt(tmp_path: Path):
    repository = OperationsRepository(settings(tmp_path))
    try:
        repository.upsert_history_record(
            {
                "status": "completed",
                "display_name": "finished discovery",
                "file_count": 10,
            },
            kind="discovery",
            source_id="delete-me",
            size_bytes=4096,
        )
        deleted = repository.mark_history_deleted(
            "discovery:delete-me",
            released_bytes=3072,
        )
        assert deleted["deleted_at"]
        with repository.database.session() as session:
            receipt = session.scalar(select(DeletionRequest))
            assert receipt is not None
            assert receipt.status == "completed"
            assert receipt.estimated_bytes == 4096
            assert receipt.released_bytes == 3072
    finally:
        repository.close()
