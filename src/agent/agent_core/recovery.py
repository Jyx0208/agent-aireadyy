from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field

from agent.agent_core.recovery_policy import RecoveryDecision, recommend_recovery
from agent.errors import redact_secrets
from agent.execution.outputs import ExecutionFailureEvent
from agent.models import JsonModel
from agent.utils import write_json


class RecoveryTask(JsonModel):
    task_id: str
    input_file: str
    repository: str
    project_accession: str | None = None
    output_dir: str
    run_mode: str


class RecoveryEvidence(JsonModel):
    kind: str
    path: str | None = None
    marker: str | None = None
    excerpt: str
    category: str | None = None


class RecoveryFailure(JsonModel):
    error_id: str
    stage: str
    category: str
    retryable: bool
    detected_at: str
    detected_by: str
    public_message: str
    operator_hint: str
    evidence: list[RecoveryEvidence] = Field(default_factory=list)


class RecoveryAttempt(JsonModel):
    attempt_id: str
    action: str
    started_at: str
    finished_at: str | None = None
    status: str = "skipped"
    parameters: dict[str, Any] = Field(default_factory=dict)
    safety_checks: list[dict[str, Any]] = Field(default_factory=list)
    created_artifacts: list[str] = Field(default_factory=list)
    removed_artifacts: list[str] = Field(default_factory=list)


class RecoverySection(JsonModel):
    decision: str
    allowed_action: str | None = None
    requires_human: bool = True
    attempts: list[RecoveryAttempt] = Field(default_factory=list)
    next_manual_actions: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)


class RecoveryIntegrity(JsonModel):
    redaction_applied: bool = True
    idempotency_key: str
    source_branch: str | None = None
    source_commit: str | None = None


class RecoveryAudit(JsonModel):
    schema_version: str = "recovery-audit/v1"
    task: RecoveryTask
    failure: RecoveryFailure
    artifacts: dict[str, str | None] = Field(default_factory=dict)
    recovery: RecoverySection
    integrity: RecoveryIntegrity


def _redact(value: Any) -> str:
    return redact_secrets(value).replace("[redacted-api-key]", "[redacted]")


def _event_evidence(event: ExecutionFailureEvent) -> RecoveryEvidence:
    return RecoveryEvidence(
        kind=event.evidence_kind,
        path=str(event.path) if event.path is not None else None,
        marker=_redact(event.marker) if event.marker else None,
        excerpt=_redact(event.reason),
        category=event.category,
    )


def _primary_event(events: list[ExecutionFailureEvent]) -> ExecutionFailureEvent:
    priority = {
        "missing_pin": 10,
        "missing_msdt_output": 20,
        "insufficient_memory": 30,
        "fragpipe_oom": 30,
        "conversion_failure": 40,
        "mzml_empty_or_corrupt": 45,
        "download_failure": 50,
        "network": 50,
        "timeout": 50,
        "docker_unavailable": 60,
        "process_failed": 90,
    }
    return sorted(events, key=lambda item: priority.get(item.category, 100))[0]


def _retryable(decision: RecoveryDecision) -> bool:
    return bool(not decision.requires_human and decision.decision in {"retry_scheduled", "auto_attempted"})


def _idempotency_key(task_id: str, stage: str, events: list[ExecutionFailureEvent]) -> str:
    payload = "|".join([task_id, stage, *[f"{event.category}:{event.reason}" for event in events]])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_recovery_audit(
    *,
    task_id: str,
    input_file: str,
    output_dir: str | Path,
    run_mode: str,
    repository: str,
    project_accession: str | None,
    stage: str,
    events: list[ExecutionFailureEvent],
    artifacts: dict[str, str | Path | None] | None = None,
    current_threads: int | None = None,
    requested_action: str | None = None,
    detected_by: str = "agent.recovery",
) -> RecoveryAudit:
    if not events:
        events = [
            ExecutionFailureEvent(
                category="unknown",
                reason="Failure was reported without structured recovery evidence.",
                evidence_kind="status_transition",
            )
        ]
    primary = _primary_event(events)
    decision = recommend_recovery(
        category=primary.category,
        current_threads=current_threads,
        requested_action=requested_action,
    )
    now = datetime.now(UTC).isoformat()
    attempt_action = decision.allowed_action or "mark_review_required"
    recovery_attempt = RecoveryAttempt(
        attempt_id=f"R{hashlib.sha1(f'{task_id}:{primary.category}:{attempt_action}'.encode('utf-8')).hexdigest()[:8]}",
        action=attempt_action,
        started_at=now,
        finished_at=now,
        status="skipped" if decision.requires_human else "scheduled",
        parameters=decision.parameters,
        safety_checks=decision.safety_checks,
    )
    return RecoveryAudit(
        task=RecoveryTask(
            task_id=task_id,
            input_file=input_file,
            repository=repository,
            project_accession=project_accession,
            output_dir=str(output_dir),
            run_mode=run_mode,
        ),
        failure=RecoveryFailure(
            error_id=hashlib.sha1(f"{task_id}:{stage}:{primary.category}:{now}".encode("utf-8")).hexdigest()[:12],
            stage=stage,
            category=primary.category,
            retryable=_retryable(decision),
            detected_at=now,
            detected_by=detected_by,
            public_message=_redact(primary.reason),
            operator_hint=(
                "Automatic computational recovery is scheduled."
                if not decision.requires_human
                else "Manual review is required before retrying this failure."
            ),
            evidence=[_event_evidence(event) for event in events],
        ),
        artifacts={
            key: str(value) if value is not None else None
            for key, value in (artifacts or {}).items()
        },
        recovery=RecoverySection(
            decision=decision.decision,
            allowed_action=decision.allowed_action,
            requires_human=decision.requires_human,
            attempts=[recovery_attempt],
            next_manual_actions=decision.next_manual_actions,
            parameters=decision.parameters,
        ),
        integrity=RecoveryIntegrity(
            idempotency_key=_idempotency_key(task_id, stage, events),
        ),
    )


def write_recovery_audit(output_dir: str | Path, audit: RecoveryAudit) -> Path:
    return write_json(Path(output_dir) / "recovery_audit.json", audit)
