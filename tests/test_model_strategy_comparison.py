from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agent.ai_ready.model_strategy_comparison import compare_dataset_model_strategies
from agent.cli import app


def _write_case(tmp_path: Path) -> Path:
    agent_metrics = tmp_path / "agent_metrics.json"
    random_metrics = tmp_path / "random_metrics.json"
    keyword_metrics = tmp_path / "keyword_metrics.json"
    agent_metrics.write_text(
        json.dumps(
            {
                "heldout_project": {"accuracy": 0.82},
                "heldout_instrument": {"accuracy": 0.78},
                "heldout_organism": {"accuracy": 0.76},
                "train": {"accuracy": 0.90},
                "total_rows": 1200,
            }
        ),
        encoding="utf-8",
    )
    random_metrics.write_text(
        json.dumps(
            {
                "heldout_project": {"accuracy": 0.70},
                "heldout_instrument": {"accuracy": 0.68},
                "heldout_organism": {"accuracy": 0.67},
                "train": {"accuracy": 0.93},
                "total_rows": 1200,
            }
        ),
        encoding="utf-8",
    )
    keyword_metrics.write_text(
        json.dumps(
            {
                "heldout_project": {"accuracy": 0.74},
                "heldout_instrument": {"accuracy": 0.72},
                "heldout_organism": {"accuracy": 0.69},
                "train": {"accuracy": 0.91},
                "total_rows": 1200,
            }
        ),
        encoding="utf-8",
    )
    case = tmp_path / "strategy_case.json"
    case.write_text(
        json.dumps(
            {
                "goal": "compare agent-selected dataset with baselines",
                "task_type": "denovo",
                "primary_metric": "accuracy",
                "higher_is_better": True,
                "strategies": [
                    {"strategy": "agent_data_value", "metrics_file": str(agent_metrics)},
                    {"strategy": "random_baseline", "metrics_file": str(random_metrics)},
                    {"strategy": "repository_keyword_baseline", "metrics_file": str(keyword_metrics)},
                ],
            }
        ),
        encoding="utf-8",
    )
    return case


def test_compare_dataset_model_strategies_reports_agent_delta(tmp_path: Path):
    case_file = _write_case(tmp_path)

    result = compare_dataset_model_strategies(case_file=case_file, output_dir=tmp_path / "comparison")

    assert result.status == "ready"
    assert result.best_baseline_strategy == "repository_keyword_baseline"
    assert result.agent_minus_best_baseline is not None
    assert result.agent_minus_best_baseline > 0
    assert result.interpretation == "agent_selected_dataset_outperforms_best_baseline_on_heldout_metrics"
    summary = json.loads(Path(result.files["model_strategy_comparison_json"]).read_text(encoding="utf-8"))
    assert summary["strategy_rows"][0]["primary_metric"] == "accuracy"
    assert Path(result.files["model_strategy_comparison_csv"]).exists()
    assert "Model Strategy Comparison" in Path(result.files["model_strategy_comparison_md"]).read_text(encoding="utf-8")


def test_compare_dataset_model_strategies_cli(tmp_path: Path):
    case_file = _write_case(tmp_path)
    output_dir = tmp_path / "cli_comparison"

    result = CliRunner().invoke(
        app,
        [
            "compare-dataset-model-strategies",
            "--case-file",
            str(case_file),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ready"
    assert payload["interpretation"] == "agent_selected_dataset_outperforms_best_baseline_on_heldout_metrics"
    assert (output_dir / "model_strategy_comparison.json").exists()
