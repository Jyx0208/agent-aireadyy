from agent.ai_ready.release_predicates import (
    evaluate_release,
    export_status_for_rows,
    exporter_status_from_rows,
)
from agent.assets.download_contract import publish_part_file
from agent.discovery.candidate_evidence_matrix import (
    CandidateEvidenceMatrix,
    CandidateEvidenceRow,
    EvidenceCell,
    scientific_stop_ready,
)
from agent.discovery.query_builder import prepare_pride_search_queries


def test_prepare_pride_search_queries_returns_multiple_seeds_for_compound_text():
    seeds = prepare_pride_search_queries(
        ["human phosphoproteomics DDA label free"],
        max_seeds_per_query=5,
    )
    assert len(seeds) >= 2


def test_export_status_zero_rows_not_completed():
    assert export_status_for_rows(0) == "export_empty"
    assert export_status_for_rows(3) == "completed"
    assert exporter_status_from_rows(0) == "export_empty"


def test_release_blocks_zero_rows_and_leakage_not_evaluated():
    decision = evaluate_release(
        horizon="pre_release",
        rows_out=0,
        parquet_exists=False,
        leakage_risk=None,
        task_type="rt_prediction",
        export_report={"rt_unit": "unknown"},
    )
    assert not decision.ok
    assert "zero_rows" in decision.blockers


def test_release_blocks_psm_without_decoy():
    decision = evaluate_release(
        horizon="ai_ready_table",
        rows_out=10,
        parquet_exists=True,
        leakage_risk={"status": "pass"},
        task_type="psm_scoring",
        export_report={"rows_out": 10, "target_count": 10, "decoy_count": 0},
        integrity_status="verified",
    )
    assert not decision.ok
    assert "psm_no_decoy" in decision.blockers


def test_cem_conjunction_not_corpus_union():
    # Three candidates each satisfy one hard cell only => no conjunction pass.
    def row(acc, cells):
        return CandidateEvidenceRow(
            accession=acc,
            cells={k: EvidenceCell(requirement_id=k, state=v) for k, v in cells.items()},
            hard_conjunction_pass=all(v == "PASS" for v in cells.values()),
            hard_fail=any(v == "FAIL" for v in cells.values()),
            hard_unknown=any(v == "UNKNOWN" for v in cells.values()),
            inspection_backed=True,
        )

    matrix = CandidateEvidenceMatrix(
        hard_requirement_ids=["human", "dda", "phospho"],
        rows={
            "A": row("A", {"human": "PASS", "dda": "FAIL", "phospho": "FAIL"}),
            "B": row("B", {"human": "FAIL", "dda": "PASS", "phospho": "FAIL"}),
            "C": row("C", {"human": "FAIL", "dda": "FAIL", "phospho": "PASS"}),
        },
        n_candidates=3,
        n_hard_conjunction_pass=0,
        n_hard_pass_inspected=0,
    )
    assert matrix.n_hard_conjunction_pass == 0
    ok, reason = scientific_stop_ready(matrix, target_hard_pass_inspected=1)
    assert not ok
    assert reason


def test_publish_part_checksum_mismatch(tmp_path):
    from agent.assets.download_contract import DownloadContractError
    part = tmp_path / "f.raw.part"
    final = tmp_path / "f.raw"
    part.write_bytes(b"abc")
    try:
        publish_part_file(part, final, expected_sha256=("0"*64))
        assert False, "expected DownloadContractError"
    except DownloadContractError as exc:
        assert exc.code == "checksum_mismatch"
    assert not final.exists()


def test_publish_part_success_unknown_checksum(tmp_path):
    part = tmp_path / "f.raw.part"
    final = tmp_path / "f.raw"
    part.write_bytes(b"abc")
    receipt = publish_part_file(part, final)
    assert receipt.published
    assert final.exists()
    assert receipt.status == "checksum_unknown"
