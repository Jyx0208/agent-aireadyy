from pathlib import Path


TEMPLATE = Path("src/agent/web/templates/benchmark_review.html")


def test_benchmark_review_template_supports_blind_scoring_and_export() -> None:
    html = TEMPLATE.read_text(encoding="utf-8")

    assert "Discovery Benchmark 盲评" in html
    assert 'id="poolFile"' in html
    assert 'id="reviewerId"' in html
    assert 'id="taskFilter"' in html
    assert 'data-grade="0"' in html
    assert 'data-grade="3"' in html
    assert 'id="reviewNotes"' in html
    assert 'id="exportButton"' in html
    assert "judgment_pool.reviewed.json" in html
    assert "localStorage" in html
    assert "project_accession" not in html
    assert "openai_agents" not in html
