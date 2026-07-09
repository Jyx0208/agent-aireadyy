from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agent.ai_ready.curation_memory import apply_curation_decisions_to_memory
from agent.cli import app
from agent.discovery.memory import DiscoveryMemory


def _write_queue(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "curation_id": "curation:ptm",
                        "repository": "pride",
                        "repository_strategy": "multi_repository",
                        "planned_repositories": ["pride", "massive", "iprox"],
                        "project_accession": "PXDTEST001",
                        "source_file": "sample_a.mzML",
                        "task_type": "ptm_denovo",
                        "curation_type": "confirm_ptm_semantics",
                        "reason": "semantic_metadata_low_confidence",
                    },
                    {
                        "curation_id": "curation:leakage",
                        "repository": "pride",
                        "project_accession": "PXDTEST002",
                        "source_file": "sample_b.mzML",
                        "task_type": "denovo",
                        "curation_type": "check_leakage_risk",
                        "reason": "potential_leakage_risk",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def test_apply_curation_decisions_writes_discovery_memory(tmp_path: Path) -> None:
    queue = _write_queue(tmp_path / "curation_queue.json")
    decisions = tmp_path / "decisions.csv"
    decisions.write_text(
        "curation_id,decision,note\n"
        "curation:ptm,reject,PTM evidence is not convincing\n"
        "curation:leakage,keep,split reviewed\n",
        encoding="utf-8",
    )

    result = apply_curation_decisions_to_memory(
        curation_queue=queue,
        decisions_csv=decisions,
        output_dir=tmp_path / "update",
        memory_dir=tmp_path / "memory",
        run_id="curation_test",
    )

    assert result.status == "updated"
    assert result.imported_decision_count == 2
    assert result.skipped_count == 0
    assert Path(result.files["curation_memory_update_json"]).exists()
    memory = DiscoveryMemory(tmp_path / "memory")
    stored = memory.load_review_decisions()
    assert len(stored) == 2
    assert stored[0].decision == "reject"
    assert stored[0].reason == "wrong_ptm"
    assert "PTM evidence" in stored[0].note
    assert "repository_strategy=multi_repository" in stored[0].note
    assert "planned_repositories=pride,massive,iprox" in stored[0].note
    assert stored[1].decision == "keep"
    assert stored[1].reason == "correct"
    summary = json.loads(Path(result.files["curation_memory_update_json"]).read_text(encoding="utf-8"))
    assert summary["imported_decisions"][0]["repository_strategy"] == "multi_repository"
    assert summary["imported_decisions"][0]["planned_repositories"] == ["pride", "massive", "iprox"]
    csv_text = Path(result.files["curation_memory_update_csv"]).read_text(encoding="utf-8")
    assert "planned_repositories" in csv_text
    assert "pride;massive;iprox" in csv_text
    md_text = Path(result.files["curation_memory_update_md"]).read_text(encoding="utf-8")
    assert "Planned repositories" in md_text
    assert "pride, massive, iprox" in md_text


def test_apply_curation_decisions_default_review_and_cli(tmp_path: Path) -> None:
    queue = _write_queue(tmp_path / "curation_queue.json")
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "apply-curation-decisions",
            "--curation-queue",
            str(queue),
            "--output-dir",
            str(tmp_path / "update"),
            "--memory-dir",
            str(tmp_path / "memory"),
            "--default-decision",
            "needs_review",
            "--run-id",
            "cli_curation",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "updated"
    assert payload["imported_decision_count"] == 2
    stored = DiscoveryMemory(tmp_path / "memory").load_review_decisions()
    assert {decision.decision for decision in stored} == {"needs_review"}
