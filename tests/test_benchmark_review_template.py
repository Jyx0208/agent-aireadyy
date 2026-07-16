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
    assert 'id="serverPoolSelect"' in html
    assert 'id="openServerPoolButton"' in html
    assert 'id="importServerButton"' in html
    assert 'id="reviewMode"' in html
    assert "/api/expert-review/pools" in html
    assert "project_accession" not in html
    assert "openai_agents" not in html


def test_benchmark_review_template_has_repair_contract_markers() -> None:
    html = TEMPLATE.read_text(encoding="utf-8")

    assert 'id="developerToken"' in html
    assert 'type="password"' in html
    assert "X-Expert-Review-Token" in html
    assert 'id="candidateEmptyState"' in html
    assert "function currentCandidate()" in html
    assert 'role="log"' in html
    assert 'setAttribute("role", "progressbar")' in html
    assert 'aria-live="polite"' in html
    assert "PAGE_LIMIT = 500" in html
    assert "offset += page.length" in html
    assert "while (total === null || offset < total)" in html
    assert "清除当前候选评分" in html
    assert "未回退到客户端副本" in html
    assert 'id="profileEditorSelect"' in html
    assert 'id="profileSelect"' in html
    assert "developer_allowed" in html
    assert 'id="poolBuildPanel"' in html
    assert 'id="poolBuildPrompt"' in html
    assert 'id="startPoolBuildButton"' in html
    assert 'id="poolBuildAction"' in html
    assert "/api/benchmark-review/builds" in html
    assert "build_and_review" in html
    assert 'id="profileProviderInput"' in html
    assert 'id="profileModelFamilyInput"' in html
    assert 'id="profileResolvedModelInput"' in html
    assert 'id="profileEndpointIdentityInput"' in html
    assert 'id="profileIdentityVerificationInput"' in html
    assert 'id="profileEnabledInput"' in html
    assert "requested_model_id" in html
    assert '<option value="verified">' not in html
    assert "请先在开发者工具中配置并选择专家 Profile" not in html
    assert 'review: action === "build_and_review" ? { profile_id:' not in html


def test_benchmark_review_template_avoids_known_unsafe_fallbacks() -> None:
    html = TEMPLATE.read_text(encoding="utf-8")

    assert "title.innerHTML" not in html
    assert "catch (e) {}" not in html
    assert '|| "dummy"' not in html
    assert 'localStorage.setItem("developerToken"' not in html
    assert "localStorage.setItem('developerToken'" not in html
