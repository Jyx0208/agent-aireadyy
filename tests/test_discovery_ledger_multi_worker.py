from __future__ import annotations

import multiprocessing
from pathlib import Path
from queue import Empty

from agent.discovery.production_authority import DurableAuthorityLedger


def _reserve_worker(path: str, output: object) -> None:
    ledger = DurableAuthorityLedger(path)
    output.put(
        ledger.reserve(
            "repair_idempotency",
            "shared-operation",
            "sha256:shared-operation",
            binding={"run_id": "multi-worker"},
        )
    )


def _consume_worker(path: str, output: object) -> None:
    ledger = DurableAuthorityLedger(path)
    output.put(
        ledger.consume_many(
            [
                ("metric_observation", "before", "sha256:before"),
                ("metric_observation", "after", "sha256:after"),
            ]
        )
    )


def _run_workers(target: object, ledger_path: Path, count: int = 6) -> list[bool]:
    context = multiprocessing.get_context("spawn")
    output = context.Queue()
    workers = [
        context.Process(target=target, args=(str(ledger_path), output))
        for _ in range(count)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)
        assert worker.exitcode == 0
    values: list[bool] = []
    for _ in workers:
        try:
            values.append(bool(output.get(timeout=5)))
        except Empty as exc:  # pragma: no cover - diagnostic on process failure
            raise AssertionError("worker did not report a ledger result") from exc
    return values


def test_only_one_process_can_reserve_shared_idempotency_key(tmp_path: Path) -> None:
    ledger_path = tmp_path / "shared-authority.sqlite"
    DurableAuthorityLedger(ledger_path)

    results = _run_workers(_reserve_worker, ledger_path)

    assert results.count(True) == 1
    assert results.count(False) == 5


def test_metric_pair_is_consumed_once_across_processes_and_restart(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "shared-authority.sqlite"
    ledger = DurableAuthorityLedger(ledger_path)
    assert ledger.reserve(
        "metric_observation", "before", "sha256:before"
    )
    assert ledger.reserve("metric_observation", "after", "sha256:after")

    results = _run_workers(_consume_worker, ledger_path)

    assert results.count(True) == 1
    assert results.count(False) == 5
    restarted = DurableAuthorityLedger(ledger_path)
    assert restarted.consume_many(
        [
            ("metric_observation", "before", "sha256:before"),
            ("metric_observation", "after", "sha256:after"),
        ]
    ) is False
