from __future__ import annotations

from hashlib import sha256
from typing import Iterable, Literal

from pydantic import Field, field_validator, model_validator

from agent.models import JsonModel


FileReviewStatus = Literal["unreviewed", "queued", "reviewing", "reviewed", "error"]
FileDecision = Literal["include", "investigate", "exclude"]
FileReasonStatus = Literal["not_needed", "pending", "generating", "ready", "error"]
FileHardGate = Literal["pass", "unknown", "fail"]
FileSelectionRole = Literal["primary_input", "required_companion", "evidence_only"]
FileJudgmentSource = Literal["llm", "rule", "project_legacy"]


def stable_file_id(
    repository: str,
    project_accession: str,
    native_id: str,
) -> str:
    """Return a stable, compact identity for one repository file."""

    normalized = "\0".join(
        (
            str(repository or "unknown").strip().casefold(),
            str(project_accession or "").strip().upper(),
            str(native_id or "").strip(),
        )
    )
    digest = sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return f"file_{digest}"


class FileEvidenceCheck(JsonModel):
    file_id: str
    status: Literal["pass", "fail"]
    cited_refs: list[str] = Field(default_factory=list)
    missing_refs: list[str] = Field(default_factory=list)


class FileJudgmentInput(JsonModel):
    file_id: str = Field(min_length=6, max_length=80)
    project_accession: str = Field(min_length=1, max_length=120)
    file_name: str = Field(min_length=1, max_length=1000)
    family_id: str | None = Field(default=None, max_length=100)
    selection_role: FileSelectionRole = "primary_input"
    review_status: FileReviewStatus = "reviewed"
    decision: FileDecision
    grade: int | None = Field(default=None, ge=0, le=3)
    hard_gate: FileHardGate
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list, max_length=50)
    limitations: list[str] = Field(default_factory=list, max_length=30)
    missing_information: list[str] = Field(default_factory=list, max_length=30)
    companion_file_ids: list[str] = Field(default_factory=list, max_length=100)
    reason_outline: list[str] = Field(default_factory=list, max_length=12)
    reason_text: str | None = Field(default=None, max_length=4000)
    reason_status: FileReasonStatus = "pending"
    reason_scope: Literal["file", "project_legacy"] = "file"
    judgment_source: FileJudgmentSource = "llm"
    strategy_hash: str = Field(default="", max_length=128)
    evidence_hash: str = Field(default="", max_length=128)
    model_id: str = Field(default="", max_length=160)
    judgment_version: str = Field(default="file-fit/v1", min_length=1, max_length=80)

    @field_validator("project_accession")
    @classmethod
    def normalize_accession(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator(
        "evidence_refs",
        "limitations",
        "missing_information",
        "companion_file_ids",
        "reason_outline",
    )
    @classmethod
    def normalize_lists(cls, values: list[str]) -> list[str]:
        return list(
            dict.fromkeys(
                " ".join(str(value or "").split()).strip()
                for value in values
                if str(value or "").strip()
            )
        )

    @model_validator(mode="after")
    def validate_decision(self) -> "FileJudgmentInput":
        if self.decision == "include" and (
            self.review_status != "reviewed"
            or self.grade is None
            or self.grade < 2
            or self.hard_gate != "pass"
            or not self.evidence_refs
        ):
            raise ValueError(
                "include requires a reviewed grade 2-3 file, a passing hard gate, "
                "and evidence references"
            )
        if self.decision == "investigate" and not self.missing_information:
            raise ValueError("investigate requires missing_information")
        if self.hard_gate == "fail" and self.decision != "exclude":
            raise ValueError("a failed hard gate must exclude the file")
        if self.reason_status == "ready" and not str(self.reason_text or "").strip():
            raise ValueError("ready reason status requires reason_text")
        if (
            self.review_status == "reviewed"
            and self.decision in {"exclude", "investigate"}
            and (
                self.reason_status != "ready"
                or not str(self.reason_text or "").strip()
            )
        ):
            raise ValueError(
                "reviewed excluded and investigate files require a concise reason_text"
            )
        return self


def check_file_evidence(
    judgment: FileJudgmentInput,
    available_refs: Iterable[str],
) -> FileEvidenceCheck:
    available = {str(ref) for ref in available_refs if str(ref).strip()}
    missing = [ref for ref in judgment.evidence_refs if ref not in available]
    return FileEvidenceCheck(
        file_id=judgment.file_id,
        status="fail" if missing else "pass",
        cited_refs=list(judgment.evidence_refs),
        missing_refs=missing,
    )


def is_file_selection_ready(
    judgment: FileJudgmentInput,
    evidence_check: FileEvidenceCheck,
) -> bool:
    return bool(
        judgment.review_status == "reviewed"
        and judgment.decision == "include"
        and judgment.grade is not None
        and judgment.grade >= 2
        and judgment.hard_gate == "pass"
        and judgment.reason_status == "ready"
        and str(judgment.reason_text or "").strip()
        and evidence_check.status == "pass"
    )


def summarize_file_judgments(
    judgments: Iterable[FileJudgmentInput],
) -> dict[str, int]:
    items = list(judgments)
    return {
        "total": len(items),
        "unreviewed": sum(item.review_status == "unreviewed" for item in items),
        "queued": sum(item.review_status == "queued" for item in items),
        "reviewing": sum(item.review_status == "reviewing" for item in items),
        "reviewed": sum(item.review_status == "reviewed" for item in items),
        "selected": sum(item.decision == "include" for item in items),
        "investigate": sum(item.decision == "investigate" for item in items),
        "excluded": sum(item.decision == "exclude" for item in items),
        "errors": sum(item.review_status == "error" for item in items),
        "reasons_ready": sum(item.reason_status == "ready" for item in items),
    }
