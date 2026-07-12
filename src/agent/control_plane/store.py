from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from agent.control_plane.models import (
    AgentEvent,
    AgentRunRecord,
    BudgetDecision,
    SearchGrant,
    SearchProposalRecord,
    ToolExecutionRecord,
    utc_now_iso,
)


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def tool_idempotency_key(run_id: str, tool_name: str, arguments: dict[str, Any]) -> str:
    raw = f"{run_id}\n{tool_name}\n{canonical_json(arguments)}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class AgentRunStore:
    def __init__(
        self,
        path: str | Path,
        event_listener: Callable[[AgentEvent], None] | None = None,
    ) -> None:
        self.path = Path(path)
        self.event_listener = event_listener
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
                CREATE TABLE IF NOT EXISTS agent_search_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    query_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES agent_runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_search_proposals_run
                ON agent_search_proposals(run_id, created_at);
                CREATE TABLE IF NOT EXISTS agent_budget_decisions (
                    proposal_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES agent_runs(run_id),
                    FOREIGN KEY(proposal_id) REFERENCES agent_search_proposals(proposal_id)
                );
                CREATE TABLE IF NOT EXISTS agent_search_grants (
                    grant_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    proposal_id TEXT NOT NULL,
                    query_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES agent_runs(run_id),
                    FOREIGN KEY(proposal_id) REFERENCES agent_search_proposals(proposal_id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_search_grants_run
                ON agent_search_grants(run_id, created_at);
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
        event = AgentEvent(
            sequence=sequence,
            run_id=run_id,
            event_type=event_type,
            payload=payload or {},
            created_at=created_at,
        )
        self._notify_event(event)
        return event

    def _notify_event(self, event: AgentEvent) -> None:
        if self.event_listener is None:
            return
        try:
            self.event_listener(event)
        except Exception:
            return

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

    def save_search_proposal(self, proposal: SearchProposalRecord) -> SearchProposalRecord:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_search_proposals (
                    proposal_id, run_id, query_hash, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(proposal_id) DO UPDATE SET
                    run_id = excluded.run_id,
                    query_hash = excluded.query_hash,
                    payload_json = excluded.payload_json,
                    created_at = excluded.created_at
                """,
                (
                    proposal.proposal_id,
                    proposal.run_id,
                    proposal.query_hash,
                    canonical_json(proposal.model_dump(mode="json")),
                    proposal.created_at,
                ),
            )
        return proposal

    def load_search_proposal(self, proposal_id: str) -> SearchProposalRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM agent_search_proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
        if row is None:
            return None
        return SearchProposalRecord.model_validate(json.loads(row["payload_json"]))

    def list_search_proposals(self, run_id: str) -> list[SearchProposalRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM agent_search_proposals
                WHERE run_id = ? ORDER BY created_at, proposal_id
                """,
                (run_id,),
            ).fetchall()
        return [SearchProposalRecord.model_validate(json.loads(row["payload_json"])) for row in rows]

    def save_budget_decision(self, run_id: str, decision: BudgetDecision) -> BudgetDecision:
        created_at = utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_budget_decisions (proposal_id, run_id, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(proposal_id) DO UPDATE SET
                    run_id = excluded.run_id,
                    payload_json = excluded.payload_json,
                    created_at = excluded.created_at
                """,
                (
                    decision.proposal_id,
                    run_id,
                    canonical_json(decision.model_dump(mode="json")),
                    created_at,
                ),
            )
        return decision

    def load_budget_decision(self, proposal_id: str) -> BudgetDecision | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM agent_budget_decisions WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
        if row is None:
            return None
        return BudgetDecision.model_validate(json.loads(row["payload_json"]))

    def issue_search_grant(self, grant: SearchGrant) -> SearchGrant:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_search_grants (
                    grant_id, run_id, proposal_id, query_hash, status, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(grant_id) DO NOTHING
                """,
                (
                    grant.grant_id,
                    grant.run_id,
                    grant.proposal_id,
                    grant.query_hash,
                    grant.status,
                    canonical_json(grant.model_dump(mode="json")),
                    grant.created_at,
                    grant.updated_at,
                ),
            )
            row = connection.execute(
                "SELECT payload_json FROM agent_search_grants WHERE grant_id = ?",
                (grant.grant_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("search_grant_not_persisted")
        return SearchGrant.model_validate(json.loads(row["payload_json"]))

    def load_search_grant(self, grant_id: str) -> SearchGrant | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM agent_search_grants WHERE grant_id = ?",
                (grant_id,),
            ).fetchone()
        if row is None:
            return None
        return SearchGrant.model_validate(json.loads(row["payload_json"]))

    def list_search_grants(self, run_id: str) -> list[SearchGrant]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM agent_search_grants
                WHERE run_id = ? ORDER BY created_at, grant_id
                """,
                (run_id,),
            ).fetchall()
        return [SearchGrant.model_validate(json.loads(row["payload_json"])) for row in rows]

    def consume_search_grant(self, run_id: str, grant_id: str, query_hash: str) -> SearchGrant:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json, status, run_id, query_hash FROM agent_search_grants WHERE grant_id = ?",
                (grant_id,),
            ).fetchone()
            if row is None:
                raise ValueError("search_grant_not_found")
            if str(row["run_id"]) != run_id:
                raise ValueError("search_grant_run_mismatch")
            if str(row["query_hash"]) != query_hash:
                raise ValueError("search_grant_query_mismatch")
            if str(row["status"]) != "issued":
                raise ValueError(f"grant_already_{row['status']}")
            grant = SearchGrant.model_validate(json.loads(row["payload_json"]))
            consumed = grant.model_copy(update={"status": "consumed", "updated_at": utc_now_iso()})
            connection.execute(
                """
                UPDATE agent_search_grants
                SET status = ?, payload_json = ?, updated_at = ? WHERE grant_id = ?
                """,
                (
                    consumed.status,
                    canonical_json(consumed.model_dump(mode="json")),
                    consumed.updated_at,
                    grant_id,
                ),
            )
            connection.commit()
            return consumed
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def increment_dynamic_usage(
        self,
        run_id: str,
        *,
        query_units: int = 0,
        repository_requests: int = 0,
        search_batches: int = 0,
        budget_reviews: int = 0,
        enforce_limits: bool = True,
    ) -> AgentRunRecord:
        deltas = {
            "query_units": query_units,
            "repository_requests": repository_requests,
            "search_batches": search_batches,
            "budget_reviews": budget_reviews,
        }
        if any(delta < 0 for delta in deltas.values()):
            raise ValueError("dynamic_usage_deltas_must_be_non_negative")

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json, sdk_state_json FROM agent_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown agent run: {run_id}")
            run = self._run_record(row)
            usage = run.dynamic_usage.model_copy(
                update={field: getattr(run.dynamic_usage, field) + delta for field, delta in deltas.items()}
            )
            if enforce_limits:
                limits = run.dynamic_limits
                if usage.query_units > limits.max_query_units:
                    raise ValueError("query_unit_budget_exhausted")
                if usage.repository_requests > limits.max_repository_requests:
                    raise ValueError("hard_repository_request_limit")
                started_at = datetime.fromisoformat(usage.started_at)
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=UTC)
                elapsed_seconds = (datetime.now(UTC) - started_at).total_seconds()
                if elapsed_seconds > limits.max_elapsed_seconds:
                    raise ValueError("elapsed_time_budget_exhausted")
            updated = run.model_copy(update={"dynamic_usage": usage, "updated_at": utc_now_iso()})
            connection.execute(
                "UPDATE agent_runs SET payload_json = ?, updated_at = ? WHERE run_id = ?",
                (
                    canonical_json(updated.model_dump(mode="json", exclude={"sdk_state_json"})),
                    updated.updated_at,
                    run_id,
                ),
            )
            connection.commit()
            return updated
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def increment_tool_call_count(self, run_id: str) -> AgentRunRecord:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json, sdk_state_json FROM agent_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown agent run: {run_id}")
            run = self._run_record(row)
            next_count = run.tool_call_count + 1
            if next_count > run.budget.max_tool_calls:
                raise ValueError("tool_call_budget_exhausted")
            updated = run.model_copy(update={"tool_call_count": next_count, "updated_at": utc_now_iso()})
            connection.execute(
                "UPDATE agent_runs SET payload_json = ?, updated_at = ? WHERE run_id = ?",
                (
                    canonical_json(updated.model_dump(mode="json", exclude={"sdk_state_json"})),
                    updated.updated_at,
                    run_id,
                ),
            )
            connection.commit()
            return updated
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def increment_model_usage(
        self,
        run_id: str,
        *,
        requests: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
    ) -> AgentRunRecord:
        values = (requests, input_tokens, output_tokens, total_tokens)
        if any(value < 0 for value in values):
            raise ValueError("model_usage_deltas_must_be_non_negative")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json, sdk_state_json FROM agent_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown agent run: {run_id}")
            run = self._run_record(row)
            updated = run.model_copy(
                update={
                    "model_requests": run.model_requests + requests,
                    "model_input_tokens": run.model_input_tokens + input_tokens,
                    "model_output_tokens": run.model_output_tokens + output_tokens,
                    "model_total_tokens": run.model_total_tokens + total_tokens,
                    "updated_at": utc_now_iso(),
                }
            )
            connection.execute(
                "UPDATE agent_runs SET payload_json = ?, updated_at = ? WHERE run_id = ?",
                (
                    canonical_json(updated.model_dump(mode="json", exclude={"sdk_state_json"})),
                    updated.updated_at,
                    run_id,
                ),
            )
            connection.commit()
            return updated
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

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

    @staticmethod
    def _run_record(row: sqlite3.Row) -> AgentRunRecord:
        payload = json.loads(row["payload_json"])
        payload["sdk_state_json"] = row["sdk_state_json"]
        return AgentRunRecord.model_validate(payload)
