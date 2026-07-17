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
    assert 'id="poolBuildScale"' in html
    assert '<option value="curated">' in html
    assert '<option value="balanced">' in html
    assert '<option value="exhaustive">' in html
    assert 'id="poolBuildOutputLanguage"' in html
    assert '<option value="zh-CN">' in html
    assert 'scale_mode: scaleMode' in html
    assert 'output_language: outputLanguage' in html
    assert "build.prompt_parse" in html
    assert "progress.counts" in html
    assert "candidate_projects_seen" in html
    assert "selected_projects" in html
    assert "selected_files" in html
    assert "English query terms" in html
    assert 'className = "pool-build-steps"' in html
    assert 'className = "pool-build-now"' in html
    assert "启动模型评审" in html
    assert "形成专家共识" in html
    assert "专家共识仍在后台运行" in html
    assert "Build review pool" in html
    assert "build.review_progress" in html
    assert "已结束但存在失败" in html
    assert "模型评审任务已结束" in html
    assert "localizedJobStatus" in html
    assert "localizedJobLogMessage" in html
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


def test_benchmark_review_template_has_independent_pool_builder_llm_config() -> None:
    html = TEMPLATE.read_text(encoding="utf-8")
    config_section = html.split('id="poolBuilderConfigPanel"', 1)[1].split("</section>", 1)[0]

    assert "0. 评审池构建模型配置（先配置一次）" in html
    assert 'id="poolBuilderProviderInput"' in html
    assert 'id="poolBuilderBaseUrlInput"' in html
    assert 'id="poolBuilderModelInput"' in html
    assert 'id="poolBuilderApiKeyInput"' in html
    assert 'id="poolBuilderTimeoutInput"' in html
    assert 'id="poolBuilderSaveButton"' in html
    assert 'id="poolBuilderConfigStatus"' in html
    assert '<option value="openai_compatible">' in html
    assert '<option value="openai">' in html
    assert '<option value="google">' in html
    assert '<option value="xai">' in html
    assert '<option value="deepseek">' in html
    assert '<option value="anthropic">' not in config_section
    assert "OpenAI-compatible Base URL" in html
    assert "理解 Prompt、生成英文检索词" in html
    assert "/api/benchmark-review/build-llm-config" in html
    assert 'method: "PUT"' in html
    assert "const config = { provider, base_url: baseUrl, model, timeout };" in html
    assert "if (apiKey) config.api_key = apiKey;" in html
    assert "payload.profile" in html
    assert "API Key 留空时保留现有值" in html
    assert "已配置：${config.provider} · ${config.model}" in html
    assert 'id="poolBuildParserProfile"' not in html
    assert "parser_profile_id" not in html


def test_benchmark_review_template_avoids_known_unsafe_fallbacks() -> None:
    html = TEMPLATE.read_text(encoding="utf-8")

    assert "title.innerHTML" not in html
    assert "catch (e) {}" not in html
    assert '|| "dummy"' not in html
    assert 'localStorage.setItem("developerToken"' not in html
    assert "localStorage.setItem('developerToken'" not in html
