from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import Field

from agent.errors import redact_secrets
from agent.models import JsonModel
from agent.utils import write_json


class RecoverySignal(JsonModel):
    category: str
    priority: str
    source: str
    evidence: str
    recommended_action: str
    auto_executable: bool = False
    requires_human: bool = True


class AgentRecoveryReport(JsonModel):
    schema_version: str = "agent-recovery-report/v1"
    status: str
    workflow_outcome: str = "unknown"
    usable_partial_outputs: bool = False
    run_dir: str
    primary_issue: str | None = None
    recommended_next_step: str | None = None
    signals: list[RecoverySignal] = Field(default_factory=list)
    artifact_paths: dict[str, str] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class RecoveryRule:
    category: str
    priority: str
    patterns: tuple[str, ...]
    recommended_action: str
    auto_executable: bool = False
    requires_human: bool = True


RECOVERY_RULES: tuple[RecoveryRule, ...] = (
    RecoveryRule(
        category="corrupt_raw_file",
        priority="P0",
        patterns=(
            r"corrupt raw file",
            r"rawfileimpl::ctor",
            r"error processing file .*\.raw",
        ),
        recommended_action="skip this RAW; prefer repository mzML or choose another clean Thermo RAW",
        auto_executable=False,
        requires_human=False,
    ),
    RecoveryRule(
        category="conversion_failed",
        priority="P0",
        patterns=(
            r"conversion_failure",
            r"asset preparation failed",
            r"local asset preparation failed",
            r"msconvert",
            r"proteowizard",
            r"docker_path_mapping_failed",
            r"conversion_failed",
        ),
        recommended_action="retry with allowed converter fallback or move RAW conversion to Linux/server if fallback already failed",
        auto_executable=True,
        requires_human=False,
    ),
    RecoveryRule(
        category="review_gate_blocked",
        priority="P0",
        patterns=(
            r"review_gate_blocked",
            r"requires review",
            r"multi-species",
            r"multiple species",
            r"species_uncertain",
            r"file-level species",
            r"file-level sdrf",
            r"fasta.*placeholder",
            r"workflow[_\s-]*missing",
            r"missing workflow",
            r"workflow[_\s-]*(?:file|path)?[_\s-]*not[_\s-]*found",
            r"project[_\s-]*resolution.*(?:failed|uncertain|needs[_\s-]*review|requires[_\s-]*review)",
        ),
        recommended_action="do_not_bypass_review_gate; require file-level evidence or choose a cleaner candidate",
        auto_executable=False,
        requires_human=True,
    ),
    RecoveryRule(
        category="resource_oom",
        priority="P1",
        patterns=(
            r"fragpipe_oom",
            r"insufficient memory",
            r"outofmemory",
            r"out of memory",
            r"java heap",
            r"gc overhead",
            r"\bkilled\b",
        ),
        recommended_action="retry with lower threads/RAM or select a smaller file",
        auto_executable=True,
        requires_human=False,
    ),
    RecoveryRule(
        category="msdt_feature_missing",
        priority="P1",
        patterns=(
            r"Usecols do not match columns",
            r"delta_RT_loess",
            r"unweighted_spectral_entropy",
        ),
        recommended_action=(
            "treat FragPipe search outputs as reusable partial results; retry clean MSDT only with an "
            "MSBooster-compatible workflow/config, or continue with task-specific AI-ready exporters"
        ),
        auto_executable=False,
        requires_human=False,
    ),
    RecoveryRule(
        category="low_psm_msbooster",
        priority="P1",
        patterns=(
            r"not enough high quality PSMs",
            r"not enough target PSMs",
            r"RT regression using 0 PSMs",
            r"MSBooster",
            r"low_psm_msbooster",
        ),
        recommended_action=(
            "treat as low-confidence full/search output; export only from existing usable results "
            "or switch to a cleaner candidate"
        ),
        auto_executable=False,
        requires_human=False,
    ),
    RecoveryRule(
        category="zero_psm",
        priority="P1",
        patterns=(
            r"(?<!using )\b0\s+PSMs?\b",
            r"no target PSMs",
            r"zero_psm",
        ),
        recommended_action="do not mark training-ready; inspect species/FASTA/enzyme/workflow or switch candidate",
        auto_executable=False,
        requires_human=True,
    ),
    RecoveryRule(
        category="missing_search_results",
        priority="P1",
        patterns=(
            r"needs_search_results",
            r"missing_search_results",
            r"missing_pin",
            r"Missing required output: FragPipe PIN",
            r"miss mzml_fp_pin_path",
        ),
        recommended_action="run/repair search output first, or point AI-ready Build to an existing psm.tsv/peptide.tsv/pin",
        auto_executable=False,
        requires_human=False,
    ),
    RecoveryRule(
        category="missing_peaklist",
        priority="P1",
        patterns=(
            r"needs_peaklist",
            r"missing_peaklist",
            r"source_parquet_not_found",
            r"no_spectra_written",
        ),
        recommended_action="generate MGF from MSDT/rawspectrum parquet or provide a matching peaklist",
        auto_executable=True,
        requires_human=False,
    ),
    RecoveryRule(
        category="spectrum_mismatch",
        priority="P2",
        patterns=(r"spectrum_not_matched", r"spectrum mismatch", r"scan.*not matched"),
        recommended_action="repair spectrum id/title/scan matching or regenerate MGF with richer TITLE fields",
        auto_executable=False,
        requires_human=False,
    ),
    RecoveryRule(
        category="missing_target_decoy",
        priority="P2",
        patterns=(r"needs_target_decoy_labels", r"missing_target_decoy", r"target_decoy_missing"),
        recommended_action="rerun/locate search results with target-decoy labels before PSM scoring export",
        auto_executable=False,
        requires_human=True,
    ),
    RecoveryRule(
        category="missing_modified_sequence",
        priority="P2",
        patterns=(r"needs_modified_sequence_labels", r"missing_modified_sequence"),
        recommended_action="use PTM-localized search results; do not fabricate PTM de novo labels",
        auto_executable=False,
        requires_human=True,
    ),
    RecoveryRule(
        category="no_multi_peptide_assignment",
        priority="P2",
        patterns=(r"no_multi_peptide_assignment", r"multi_peptide_assignment_missing"),
        recommended_action="keep chimeric export conservative; use chimeric-aware labels/search results",
        auto_executable=False,
        requires_human=False,
    ),
    RecoveryRule(
        category="download_slow_or_failed",
        priority="P3",
        patterns=(
            r"download_slow",
            r"download_failed",
            r"download failure",
            r"timeout",
            r"rate_limited",
            r"remote_service",
        ),
        recommended_action="stop slow candidate and select a smaller cached mzML/RAW or retry bounded download later",
        auto_executable=True,
        requires_human=False,
    ),
    RecoveryRule(
        category="input_too_large",
        priority="P3",
        patterns=(r"estimated_output_too_large", r"download_too_large", r"input file.*too large"),
        recommended_action="skip oversized input and choose a smaller validation candidate",
        auto_executable=True,
        requires_human=False,
    ),
)


ARTIFACT_NAMES = {
    "task_state.json",
    "review_queue.json",
    "run_manifest.json",
    "recovery_audit.json",
    "task_history.json",
    "mini_e2e_summary.json",
    "mini_e2e_report.md",
    "ai_ready_validation_report.json",
    "ai_ready_build_summary.json",
    "agentic_dataset_build_summary.json",
    "agentic_dataset_build_report.md",
    "real_smoke_summary.json",
    "real_smoke_report.md",
    "runtime.log",
    "run.log",
    "filter.log",
}


def analyze_agent_recovery(run_dir: str | Path, output_dir: str | Path | None = None) -> dict[str, Path]:
    run_dir = Path(run_dir)
    if not run_dir.exists():
        raise FileNotFoundError(f"Run directory does not exist: {run_dir}")
    output_root = Path(output_dir) if output_dir is not None else run_dir
    output_root.mkdir(parents=True, exist_ok=True)
    report = build_agent_recovery_report(run_dir)
    json_path = output_root / "agent_recovery_report.json"
    md_path = output_root / "agent_recovery_report.md"
    write_json(json_path, report.model_dump(mode="json"))
    md_path.write_text(_markdown_report(report), encoding="utf-8")
    return {"agent_recovery_report_json": json_path, "agent_recovery_report_md": md_path}


def build_agent_recovery_report(run_dir: str | Path) -> AgentRecoveryReport:
    run_dir = Path(run_dir)
    artifacts = _collect_artifacts(run_dir)
    partial_outputs = _partial_output_paths(run_dir)
    declared_status = _declared_workflow_status(artifacts)
    if declared_status == "completed":
        return AgentRecoveryReport(
            status="no_recovery_needed",
            workflow_outcome="completed",
            usable_partial_outputs=False,
            run_dir=str(run_dir),
            primary_issue=None,
            recommended_next_step="No known recovery blocker detected.",
            signals=[],
            artifact_paths={name: str(path) for name, path in artifacts.items()},
            summary={
                "signal_count": 0,
                "priority_counts": {},
                "category_counts": {},
                "auto_executable_count": 0,
                "requires_human_count": 0,
                "partial_outputs": {name: str(path) for name, path in partial_outputs.items()},
                "workflow_outcome": "completed",
                "usable_partial_outputs": False,
            },
        )
    signals = _classify_artifacts(artifacts, partial_outputs=partial_outputs)
    primary = signals[0] if signals else None
    workflow_outcome = _workflow_outcome(signals, artifacts=artifacts, partial_outputs=partial_outputs)
    usable_partial_outputs = workflow_outcome == "failed_with_usable_partial_outputs"
    return AgentRecoveryReport(
        status="needs_action" if signals else "no_recovery_needed",
        workflow_outcome=workflow_outcome,
        usable_partial_outputs=usable_partial_outputs,
        run_dir=str(run_dir),
        primary_issue=primary.category if primary else None,
        recommended_next_step=primary.recommended_action if primary else "No known recovery blocker detected.",
        signals=signals,
        artifact_paths={name: str(path) for name, path in artifacts.items()},
        summary={
            "signal_count": len(signals),
            "priority_counts": _count_by(signals, "priority"),
            "category_counts": _count_by(signals, "category"),
            "auto_executable_count": sum(1 for signal in signals if signal.auto_executable),
            "requires_human_count": sum(1 for signal in signals if signal.requires_human),
            "partial_outputs": {name: str(path) for name, path in partial_outputs.items()},
            "workflow_outcome": workflow_outcome,
            "usable_partial_outputs": usable_partial_outputs,
        },
    )


def _collect_artifacts(run_dir: Path) -> dict[str, Path]:
    artifacts: dict[str, Path] = {}
    for path in run_dir.rglob("*"):
        if not path.is_file() or path.name not in ARTIFACT_NAMES:
            continue
        relative = path.relative_to(run_dir).as_posix()
        artifacts.setdefault(relative, path)
    return artifacts


def _classify_artifacts(
    artifacts: dict[str, Path],
    *,
    partial_outputs: dict[str, Path],
) -> list[RecoverySignal]:
    signals: list[RecoverySignal] = []
    seen: set[tuple[str, str]] = set()
    for source, path in artifacts.items():
        text = _artifact_text(path)
        if not text:
            continue
        for rule in RECOVERY_RULES:
            if not any(re.search(pattern, text, re.IGNORECASE | re.DOTALL) for pattern in rule.patterns):
                continue
            key = (rule.category, source)
            if key in seen:
                continue
            seen.add(key)
            signals.append(
                RecoverySignal(
                    category=rule.category,
                    priority=rule.priority,
                    source=source,
                    evidence=_evidence_excerpt(text, rule.patterns),
                    recommended_action=rule.recommended_action,
                    auto_executable=rule.auto_executable,
                    requires_human=rule.requires_human,
                )
            )
    _add_partial_output_signal(signals, artifacts=artifacts, partial_outputs=partial_outputs)
    priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    category_order = {rule.category: index for index, rule in enumerate(RECOVERY_RULES)}
    category_order["partial_outputs_available"] = category_order.get("low_psm_msbooster", 999) - 0.5
    return sorted(signals, key=lambda item: (priority_order.get(item.priority, 99), category_order.get(item.category, 999), item.source))


def _partial_output_paths(run_dir: Path) -> dict[str, Path]:
    candidates = {
        "pin": ("fragpipe/**/*.pin", "fragpipe/**/*edited.pin"),
        "psm_tsv": ("fragpipe/**/psm.tsv", "fragpipe/**/*psm*.tsv"),
        "peptide_tsv": ("fragpipe/**/peptide.tsv", "fragpipe/**/*peptide*.tsv"),
        "rawspectrum_parquet": ("rawspectrum/**/*.parquet",),
        "msdt_parquet": ("msdt/**/*.parquet",),
    }
    outputs: dict[str, Path] = {}
    for role, patterns in candidates.items():
        for pattern in patterns:
            matches = sorted(path for path in run_dir.glob(pattern) if path.is_file() and path.stat().st_size > 0)
            if matches:
                outputs[role] = matches[0]
                break
    return outputs


def _workflow_outcome(
    signals: list[RecoverySignal],
    *,
    artifacts: dict[str, Path],
    partial_outputs: dict[str, Path],
) -> str:
    categories = {signal.category for signal in signals}
    if partial_outputs and "partial_outputs_available" in categories:
        return "failed_with_usable_partial_outputs"
    if "review_gate_blocked" in categories:
        return "blocked_by_review_gate"
    if "zero_psm" in categories:
        return "failed_no_training_labels"
    if "low_psm_msbooster" in categories and partial_outputs:
        return "partial_outputs_low_confidence"
    if categories:
        return "failed_or_blocked"
    status = _declared_workflow_status(artifacts)
    if status in {"completed", "failed", "blocked", "cancelled"}:
        return status
    return "no_known_issue"


def _declared_workflow_status(artifacts: dict[str, Path]) -> str:
    for source, path in artifacts.items():
        if not source.endswith(("task_state.json", "task_history.json", "run_manifest.json")):
            continue
        payload = _artifact_json(path)
        status = str(payload.get("status") or "").strip().lower()
        if status:
            return status
    return ""


def _add_partial_output_signal(
    signals: list[RecoverySignal],
    *,
    artifacts: dict[str, Path],
    partial_outputs: dict[str, Path],
) -> None:
    if not partial_outputs:
        return
    text_parts: list[str] = []
    for source, path in artifacts.items():
        if source.endswith(("task_state.json", "task_history.json", "filter.log", "run.log", "runtime.log")):
            text_parts.append(_artifact_text(path))
    text = "\n".join(text_parts)
    if not text:
        return
    failure_patterns = (
        r'"status"\s*:\s*"failed"',
        r"PhilosopherFilter",
        r"Process returned non-zero exit code",
        r"MSDT-Converter internal process exited non-zero",
        r"MSDT-Converter log marker",
    )
    if not any(re.search(pattern, text, re.IGNORECASE | re.DOTALL) for pattern in failure_patterns):
        return
    if any(signal.category == "partial_outputs_available" for signal in signals):
        return
    outputs = ", ".join(f"{name}={path.name}" for name, path in sorted(partial_outputs.items()))
    signals.append(
        RecoverySignal(
            category="partial_outputs_available",
            priority="P1",
            source="run_artifacts",
            evidence=f"Full workflow failed after producing reusable intermediate outputs: {outputs}",
            recommended_action=(
                "run conservative partial AI-ready export from existing PIN/PSM/MSDT/rawspectrum outputs; "
                "mark full workflow as partial instead of completed"
            ),
            auto_executable=True,
            requires_human=False,
        )
    )


def _artifact_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if path.suffix.lower() == ".json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            pass
        else:
            text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if len(text) > 240_000:
        text = text[:80_000] + "\n...[truncated]...\n" + text[-120_000:]
    return redact_secrets(text)


def _artifact_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _evidence_excerpt(text: str, patterns: tuple[str, ...]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        start = max(0, match.start() - 160)
        end = min(len(text), match.end() + 240)
        excerpt = text[start:end].replace("\r", " ").replace("\n", " ")
        return re.sub(r"\s+", " ", excerpt).strip()
    return re.sub(r"\s+", " ", text[:300]).strip()


def _count_by(signals: list[RecoverySignal], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for signal in signals:
        key = str(getattr(signal, field))
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _markdown_report(report: AgentRecoveryReport) -> str:
    lines = [
        "# Agent Recovery Report",
        "",
        f"- Status: `{report.status}`",
        f"- Workflow outcome: `{report.workflow_outcome}`",
        f"- Usable partial outputs: `{report.usable_partial_outputs}`",
        f"- Run dir: `{report.run_dir}`",
        f"- Primary issue: `{report.primary_issue or 'none'}`",
        f"- Recommended next step: {report.recommended_next_step or 'None'}",
        "",
        "## Priority Model",
        "",
        "- `P0`: safety/input hard blockers; do not bypass.",
        "- `P1`: full/search blockers that stop dataset construction.",
        "- `P2`: task-specific AI-ready label/input gaps.",
        "- `P3`: download/size operational issues.",
        "",
        "## Signals",
        "",
    ]
    if not report.signals:
        lines.append("- No known recovery signals detected.")
    for signal in report.signals:
        lines.extend(
            [
                f"### {signal.priority} `{signal.category}`",
                "",
                f"- Source: `{signal.source}`",
                f"- Auto executable: `{signal.auto_executable}`",
                f"- Requires human: `{signal.requires_human}`",
                f"- Recommended action: {signal.recommended_action}",
                f"- Evidence: {signal.evidence}",
                "",
            ]
        )
    return "\n".join(lines) + "\n"
