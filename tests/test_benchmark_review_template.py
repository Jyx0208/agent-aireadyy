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
    assert 'id="deleteJobButton"' in html
    assert 'id="jobLiveStatus"' in html
    assert "function deleteSelectedJob()" in html
    assert 'method: "DELETE"' in html
    assert "只删除任务记录和该任务专属文件，不删除候选池及已写入的共享评审结果" in html
    assert "任务正在运行，请先取消任务" in html
    assert "实时监控中 · 每 2 秒更新 · 上次刷新" in html
    assert "实时监控已停止 · 最后刷新" in html
    assert "jobLog.scrollTop = jobLog.scrollHeight" in html
    assert "selectedJobId = null" in html
    assert "await refreshJobs({ autoSelectActive: false })" in html
    assert 'id="candidateMachineStatus"' in html
    assert 'id="candidateMachineTraceButton"' in html
    assert 'id="machineTraceScroll"' in html
    assert 'class="machine-trace-scroll"' in html
    assert "overflow-y: auto" in html
    assert "max-height: calc(100vh - 24px)" in html
    assert 'data-queue="graded"' in html
    assert "function updateQueueCounts()" in html
    assert "graded: `已评分 ${gradedCount}`" in html
    assert 'activeQueue === "graded" ? isGraded(candidate)' in html
    assert "查看机器评审轨迹（仅开发者）" in html
    assert "function renderCandidateMachineStatus(candidate)" in html
    assert "function machineReviewProjection(candidate)" in html
    assert "机器正在评审当前候选" in html
    assert "当前候选等待机器评审" in html
    assert "机器评审失败" in html
    assert "当前候选未评审" in html
    assert "当前候选未完成评审" in html
    assert "机器评审排队中" in html
    assert 'const deletionBlocked = job.status === "running"' in html
    assert 'selectedJobDetail.status === "running"' in html
    assert "当前展示为历史模型任务/复核轮次记录" in html
    assert "专家最终票 1 票 · 最终 ${text(runs[0] && runs[0].grade)} 分" in html
    assert "每位专家只计 1 票" in html
    assert "currentJudgmentIds.has" in html
    assert "同模型内部复核 ${runRounds} 轮（仅作稳定性审计）" in html
    assert "内部复核轮次未记录" in html
    assert "不自动算作独立专家票" in html
    assert "模型任务 ${index + 1} 最终结论 · 1 票" in html
    assert "旧记录：模型身份未记录/不可验证" in html
    assert "复核轮次 ${roundIndex + 1}" in html
    assert "当前累计 ${votes.length} 票" not in html
    assert "activeJobSummary || selectedJobDetail" in html
    assert "|| activeJobSummary || null" in html
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
    assert 'id="poolBuilderModelSelect"' in html
    assert "先拉取可用模型" in html
    assert 'id="poolBuilderModelInput"' in html
    assert "或手填模型" in html
    assert 'id="poolBuilderApiKeyInput"' in html
    assert 'id="poolBuilderTimeoutInput"' in html
    assert 'id="poolBuilderFetchModelsButton"' in html
    assert "一键拉取可用模型" in html
    assert 'id="poolBuilderCheckButton"' in html
    assert "测试 API 连接" in html
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
    assert "/api/benchmark-review/build-llm-config/models" in html
    assert "/api/benchmark-review/build-llm-config/check" in html
    assert 'method: "PUT"' in html
    assert "function selectedPoolBuilderModel()" in html
    assert 'byId("poolBuilderModelInput").value.trim() || byId("poolBuilderModelSelect").value' in html
    assert "function collectPoolBuilderConfig()" in html
    assert "if (apiKey) config.api_key = apiKey;" in html
    assert 'byId("poolBuilderFetchModelsButton").disabled = busy' in html
    assert 'byId("poolBuilderCheckButton").disabled = busy' in html
    assert 'byId("poolBuilderSaveButton").disabled = busy' in html
    assert 'select.dataset.modelListLoaded = complete ? "true" : "false"' in html
    assert "setPoolBuilderModelOptions(config.model ? [config.model] : [], config.model, false)" in html
    assert "setPoolBuilderModelOptions(models, selected, true)" in html
    assert "if (!manualModel && selectedModel)" in html
    assert "已拉取 ${models.length} 个可用模型" in html
    assert "API 连接测试成功" in html
    assert "API 连接测试失败" in html
    assert "function poolBuilderErrorMessage(error)" in html
    assert "请填写 API Key，或先保存一套可复用的建池模型配置。" in html
    assert "Base URL 已改变，请填写该接口对应的 API Key。" in html
    assert "请填写建池模型的 Base URL。" in html
    assert "请选择或手填建池模型。" in html
    assert "当前提供商必须使用 OpenAI-compatible API 协议和 Base URL。" in html
    assert "${poolBuilderErrorMessage(error)}" in html
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
