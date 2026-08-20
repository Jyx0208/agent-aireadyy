from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _positive_int(value: str | None, default: int) -> int:
    try:
        parsed = int(value or "")
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


@dataclass(frozen=True, slots=True)
class OperationsSettings:
    """Runtime locations and bounded operational defaults.

    The operations database and queue database intentionally use separate
    SQLite files so task-queue contention cannot delay read-only status APIs.
    """

    database_path: Path
    queue_path: Path
    artifact_root: Path
    worker_count: int = 4
    event_page_size: int = 100
    history_page_size: int = 25

    @classmethod
    def from_environment(cls) -> "OperationsSettings":
        runs_root = Path(os.getenv("AGENT_RUNS_DIR", "runs"))
        operations_root = Path(
            os.getenv("AGENT_OPERATIONS_DIR", str(runs_root / "_operations"))
        )
        return cls(
            database_path=Path(
                os.getenv(
                    "AGENT_OPERATIONS_DB",
                    str(operations_root / "operations.sqlite"),
                )
            ),
            queue_path=Path(
                os.getenv(
                    "AGENT_QUEUE_DB",
                    str(operations_root / "queue.sqlite"),
                )
            ),
            artifact_root=Path(
                os.getenv(
                    "AGENT_OPERATIONS_ARTIFACTS",
                    str(operations_root / "artifacts"),
                )
            ),
            worker_count=_positive_int(
                os.getenv("AGENT_DISCOVERY_WORKERS"),
                4,
            ),
            event_page_size=_positive_int(
                os.getenv("AGENT_EVENT_PAGE_SIZE"),
                100,
            ),
            history_page_size=_positive_int(
                os.getenv("AGENT_HISTORY_PAGE_SIZE"),
                25,
            ),
        )

    def ensure_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifact_root.mkdir(parents=True, exist_ok=True)
