from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import Field, field_validator, model_validator

from agent.discovery.constraints import ConstraintAssessment
from agent.models import JsonModel


ProjectJudgmentStatus = Literal[
    "provisional",
    "needs_investigation",
    "evidence_backed",
    "rejected",
]
ProjectHardGate = Literal["pass", "unknown", "fail"]
ProjectDecision = Literal["include", "investigate", "exclude"]
ProjectJudgmentNextAction = Literal[
    "inspect_project_evidence",
    "investigate_missing_evidence",
    "include_in_manifest",
    "exclude_project",
]
ProjectEvidenceStage = Literal["search", "inspection"]


class ProjectJudgmentInput(JsonModel):
    project_accession: str = Field(min_length=1, max_length=120)
    grade: int | None = Field(default=None, ge=0, le=3)
    status: ProjectJudgmentStatus
    hard_gate: ProjectHardGate
    confidence: float = Field(ge=0.0, le=1.0)
    decision: ProjectDecision
    missing_information: list[str] = Field(default_factory=list)
    next_action: ProjectJudgmentNextAction
    explanation: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=30)
    constraint_assessments: list[ConstraintAssessment] = Field(default_factory=list, max_length=100)
    rubric_version: str = Field(default="project-fit/v2", min_length=1, max_length=80)
    limitations: list[str] = Field(default_factory=list, max_length=30)
    target_file_count: int = Field(default=0, ge=0)
    evidence_stage: ProjectEvidenceStage

    @field_validator("project_accession")
    @classmethod
    def normalize_accession(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("evidence_refs", "limitations")
    @classmethod
    def normalize_string_lists(cls, values: list[str]) -> list[str]:
        return list(
            dict.fromkeys(
                " ".join(str(value or "").split()).strip()[:240]
                for value in values
                if str(value or "").strip()
            )
        )

    @model_validator(mode="after")
    def validate_semantics(self) -> "ProjectJudgmentInput":
        if self.evidence_stage == "search" and self.status == "evidence_backed":
            raise ValueError("search-stage judgments cannot be evidence-backed")
        if self.decision == "include" and not is_qualified_project_judgment(self):
            raise ValueError("include requires inspection-backed grade 2-3 with a passing hard gate")
        if self.hard_gate == "fail" and (
            self.status != "rejected" or self.decision != "exclude"
        ):
            raise ValueError("hard-gate failure requires explicit rejection")
        if self.grade is None and (
            self.hard_gate != "unknown" or self.decision != "investigate"
        ):
            raise ValueError("unknown grade must remain an investigation")
        if self.decision == "investigate" and not self.missing_information:
            raise ValueError("investigation requires missing_information")
        if self.decision == "include" and self.next_action != "include_in_manifest":
            raise ValueError("include requires next_action include_in_manifest")
        if self.decision == "exclude" and self.next_action != "exclude_project":
            raise ValueError("exclude requires next_action exclude_project")
        if self.decision == "investigate" and self.next_action not in {
            "inspect_project_evidence",
            "investigate_missing_evidence",
        }:
            raise ValueError("investigate requires an investigation next_action")
        if (
            self.evidence_stage == "inspection"
            and self.status == "evidence_backed"
            and not self.evidence_refs
        ):
            raise ValueError("inspection-backed judgments require evidence_refs")
        ids = [item.constraint_id.casefold() for item in self.constraint_assessments]
        if len(ids) != len(set(ids)):
            raise ValueError("constraint assessments must be unique per constraint_id")
        return self


def is_qualified_project_judgment(judgment: ProjectJudgmentInput) -> bool:
    return (
        judgment.evidence_stage == "inspection"
        and judgment.status == "evidence_backed"
        and judgment.hard_gate == "pass"
        and judgment.grade is not None
        and judgment.grade >= 2
        and judgment.decision == "include"
    )


def summarize_project_judgments(
    judgments: dict[str, ProjectJudgmentInput],
    *,
    target_project_count: int,
) -> dict[str, object]:
    grade_counts = Counter(
        "unknown" if judgment.grade is None else str(judgment.grade)
        for judgment in judgments.values()
    )
    qualified = sum(is_qualified_project_judgment(item) for item in judgments.values())
    investigate = sum(item.decision == "investigate" for item in judgments.values())
    rejected = sum(item.decision == "exclude" for item in judgments.values())
    stage = "inspection" if any(
        item.evidence_stage == "inspection" for item in judgments.values()
    ) else "search"
    return {
        "evidence_stage": stage,
        "assessed_projects": len(judgments),
        "qualified_projects": qualified,
        "qualified_target": int(target_project_count),
        "investigate_projects": investigate,
        "rejected_projects": rejected,
        "grade_counts": {
            key: int(grade_counts.get(key, 0))
            for key in ("0", "1", "2", "3", "unknown")
        },
        "quality_target_reached": qualified >= int(target_project_count),
    }
