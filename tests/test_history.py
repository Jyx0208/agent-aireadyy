from __future__ import annotations

from agent.web.history import (
    history_timestamp,
    history_project_key,
    merge_project_history_records,
    with_history_identity,
)


def test_history_project_key_prefers_stable_output_dir_over_task_id():
    record = {
        "task_id": "first-random-task",
        "output_dir": "runs/example.raw",
        "input_value": "different.raw",
    }

    assert history_project_key(record) == "example"


def test_merge_project_history_records_keeps_one_item_per_project_and_aliases_task_ids():
    records = [
        {
            "task_id": "old-task",
            "output_dir": "runs/example",
            "input_value": "example.raw",
            "status": "failed",
            "updated_at": "2026-05-09T12:00:00+08:00",
        },
        {
            "task_id": "new-task",
            "output_dir": "runs/example",
            "input_value": "example.raw",
            "status": "completed",
            "updated_at": "2026-05-09T12:30:00+08:00",
        },
    ]

    merged = merge_project_history_records(records)

    assert len(merged) == 1
    assert merged[0]["task_id"] == "new-task"
    assert merged[0]["status"] == "completed"
    assert merged[0]["project_key"] == "example"
    assert merged[0]["history_time"] == "2026-05-09T12:30:00+08:00"
    assert merged[0]["task_ids"] == ["old-task", "new-task"]


def test_merge_project_history_records_preserves_repeated_project_runs():
    records = [
        {
            "task_id": "first-task",
            "project_key": "example",
            "output_dir": "example",
            "input_value": "example.raw",
            "status": "failed",
            "updated_at": "2026-05-09T12:00:00+08:00",
        },
        {
            "task_id": "second-task",
            "project_key": "example",
            "output_dir": "example__20260509-123000__second",
            "input_value": "example.raw",
            "status": "completed",
            "updated_at": "2026-05-09T12:30:00+08:00",
        },
    ]

    merged = merge_project_history_records(records)

    assert len(merged) == 2
    assert [item["task_id"] for item in merged] == ["first-task", "second-task"]
    assert {item["project_key"] for item in merged} == {"example"}
    assert {item["history_id"] for item in merged} == {"example", "example__20260509-123000__second"}


def test_with_history_identity_adds_project_key_and_history_time():
    record = with_history_identity(
        {
            "task_id": "task-a",
            "input_value": "20190524_EXP1_Evo2_DBJ_LFQprot.raw",
            "finished_at": "2026-05-09T12:23:50+08:00",
        }
    )

    assert record["project_key"] == "20190524_EXP1_Evo2_DBJ_LFQprot"
    assert record["history_time"] == "2026-05-09T12:23:50+08:00"
    assert record["task_ids"] == ["task-a"]


def test_history_time_prefers_task_start_over_update_and_finish_time():
    record = with_history_identity(
        {
            "task_id": "task-a",
            "input_value": "example.raw",
            "created_at": "2026-05-09T11:59:00+08:00",
            "started_at": "2026-05-09T12:00:00+08:00",
            "finished_at": "2026-05-09T12:30:00+08:00",
            "updated_at": "2026-05-09T13:00:00+08:00",
        }
    )

    assert record["history_time"] == "2026-05-09T12:00:00+08:00"
    assert history_timestamp(record) == history_timestamp({"started_at": "2026-05-09T12:00:00+08:00"})


def test_merge_project_history_records_sorts_repeated_runs_by_started_time():
    records = [
        {
            "task_id": "started-first",
            "project_key": "example",
            "output_dir": "example",
            "input_value": "example.raw",
            "started_at": "2026-05-09T12:00:00+08:00",
            "updated_at": "2026-05-09T13:30:00+08:00",
        },
        {
            "task_id": "started-second",
            "project_key": "example",
            "output_dir": "example__20260509-123000__second",
            "input_value": "example.raw",
            "started_at": "2026-05-09T12:30:00+08:00",
            "updated_at": "2026-05-09T12:40:00+08:00",
        },
    ]

    merged = merge_project_history_records(records)

    assert [item["task_id"] for item in merged] == ["started-first", "started-second"]
    assert [item["history_time"] for item in merged] == [
        "2026-05-09T12:00:00+08:00",
        "2026-05-09T12:30:00+08:00",
    ]


def test_merge_project_history_records_does_not_replace_real_task_with_synthetic_file_scan():
    records = [
        {
            "task_id": "review-task",
            "output_dir": "runs/example",
            "input_value": "example.raw",
            "status": "blocked",
            "updated_at": "2026-05-09T12:30:00+08:00",
            "file_count": 3,
        },
        {
            "result_id": "example",
            "name": "example",
            "input_value": "example",
            "status": "completed",
            "updated_at": "2026-05-09T13:30:00+08:00",
            "file_count": 10,
        },
    ]

    merged = merge_project_history_records(records)

    assert len(merged) == 1
    assert merged[0]["task_id"] == "review-task"
    assert merged[0]["status"] == "blocked"
    assert merged[0]["history_time"] == "2026-05-09T12:30:00+08:00"
