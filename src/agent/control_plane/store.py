from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from agent.control_plane.models import AgentEvent, AgentRunRecord, ToolExecutionRecord, utc_now_iso


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def tool_idempotency_key(run_id: str, tool_name: str, arguments: dict[str, Any]) -> str:
    raw = f"{run_id}\n{tool_name}\n{canonical_json(arguments)}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class AgentRunStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id TEXT PRIMARY KEY,
                    workflow TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    sdk_state_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES agent_runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_events_run ON agent_events(run_id, sequence);
                CREATE TABLE IF NOT EXISTS agent_tool_calls (
                    idempotency_key TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    output_json TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES agent_runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_tool_calls_run ON agent_tool_calls(run_id, created_at);
                """
            )

    def save_run(self, run: AgentRunRecord) -> AgentRunRecord:
        updated = run.model_copy(update={"updated_at": utc_now_iso()})
        payload = updated.model_dump(mode="json", exclude={"sdk_state_json"})
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_runs (
                    run_id, workflow, status, payload_json, sdk_state_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    workflow = excluded.workflow,
                    status = excluded.status,
                    payload_json = excluded.payload_json,
                    sdk_state_json = excluded.sdk_state_json,
                    updated_at = excluded.updated_at
                """,
                (
                    updated.run_id,
                    updated.workflow,
                    updated.status,
                    canonical_json(payload),
                    updated.sdk_state_json,
                    updated.created_at,
                    updated.updated_at,
                ),
            )
        return updated

    def load_run(self, run_id: str) -> AgentRunRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json, sdk_state_json FROM agent_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        payload["sdk_state_json"] = row["sdk_state_json"]
        return AgentRunRecord.model_validate(payload)

    def append_event(self, run_id: str, event_type: str, payload: dict[str, Any] | None = None) -> AgentEvent:
        created_at = utc_now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO agent_events (run_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (run_id, event_type, canonical_json(payload or {}), created_at),
            )
            sequence = int(cursor.lastrowid)
        return AgentEvent(
            sequence=sequence,
            run_id=run_id,
            event_type=event_type,
            payload=payload or {},
            created_at=created_at,
        )

    def list_events(self, run_id: str) -> list[AgentEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT sequence, event_type, payload_json, created_at FROM agent_events WHERE run_id = ? ORDER BY sequence",
                (run_id,),
            ).fetchall()
        return [
            AgentEvent(
                sequence=int(row["sequence"]),
                run_id=run_id,
                event_type=str(row["event_type"]),
                payload=json.loads(row["payload_json"]),
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def claim_tool_call(
        self,
        *,
        run_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple[ToolExecutionRecord, bool]:
        key = tool_idempotency_key(run_id, tool_name, arguments)
        now = utc_now_iso()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM agent_tool_calls WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            if row is not None:
                connection.commit()
                return self._tool_record(row), False
            record = ToolExecutionRecord(
                idempotency_key=key,
                run_id=run_id,
                tool_name=tool_name,
                status="started",
                arguments=arguments,
                created_at=now,
                updated_at=now,
            )
            connection.execute(
                """
                INSERT INTO agent_tool_calls (
                    idempotency_key, run_id, tool_name, status, arguments_json,
                    output_json, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    run_id,
                    tool_name,
                    record.status,
                    canonical_json(arguments),
                    canonical_json({}),
                    None,
                    now,
                    now,
                ),
            )
            connection.commit()
            return record, True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def complete_tool_call(
        self,
        idempotency_key: str,
        output: dict[str, Any],
        *,
        status: str = "completed",
        error: str | None = None,
    ) -> ToolExecutionRecord:
        now = utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE agent_tool_calls
                SET status = ?, output_json = ?, error = ?, updated_at = ?
                WHERE idempotency_key = ?
                """,
                (status, canonical_json(output), error, now, idempotency_key),
            )
            row = connection.execute(
                "SELECT * FROM agent_tool_calls WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown tool call: {idempotency_key}")
        return self._tool_record(row)

    def save_sdk_state(self, run_id: str, sdk_state_json: str | None) -> AgentRunRecord:
        run = self.load_run(run_id)
        if run is None:
            raise KeyError(f"Unknown agent run: {run_id}")
        return self.save_run(run.model_copy(update={"sdk_state_json": sdk_state_json}))

    @staticmethod
    def _tool_record(row: sqlite3.Row) -> ToolExecutionRecord:
        return ToolExecutionRecord(
            idempotency_key=str(row["idempotency_key"]),
            run_id=str(row["run_id"]),
            tool_name=str(row["tool_name"]),
            status=str(row["status"]),
            arguments=json.loads(row["arguments_json"]),
            output=json.loads(row["output_json"]),
            error=row["error"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
