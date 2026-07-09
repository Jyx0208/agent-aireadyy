from __future__ import annotations

from pathlib import Path

from agent.ai_ready.model_adapters import load_model_metrics_file


def test_load_model_metrics_file_normalizes_xuanjinovo_tsv(tmp_path: Path) -> None:
    path = tmp_path / "xuanjinovo_eval.tsv"
    path.write_text(
        "\n".join(
            [
                "metric\tvalue\tsplit",
                "peptide recall\t78.5\toverall",
                "aa_precision\t91.2\theldout_project",
                "seq_acc\t0.64\tphosphotyrosine",
            ]
        ),
        encoding="utf-8",
    )

    metrics = load_model_metrics_file(path, adapter="xuanjinovo_template", task_type="denovo")

    assert metrics["metric_adapter_template"] == "xuanjinovo_eval"
    assert metrics["primary_metric"] == "sequence_accuracy"
    assert metrics["peptide_recall"] == 0.785
    assert metrics["slices"]["heldout_project"]["amino_acid_precision"] == 0.912
    assert metrics["slices"]["phosphotyrosine"]["sequence_accuracy"] == 0.64


def test_load_model_metrics_file_normalizes_massnet_log(tmp_path: Path) -> None:
    path = tmp_path / "massnet_eval.log"
    path.write_text(
        """
        MassNet evaluation
        cosine similarity: 83.4%
        pearson = 0.76
        loss: 0.31
        """,
        encoding="utf-8",
    )

    metrics = load_model_metrics_file(path, adapter="massnet", task_type="fragment_intensity_prediction")

    assert metrics["metric_adapter_template"] == "massnet_eval"
    assert metrics["primary_metric"] == "cosine_similarity"
    assert metrics["cosine_similarity"] == 0.834
    assert metrics["pearson"] == 0.76
    assert metrics["loss"] == 0.31
