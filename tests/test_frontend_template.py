from __future__ import annotations

from pathlib import Path


TEMPLATE = Path("src/agent/web/templates/index.html")


def _html() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def test_frontend_template_exposes_accessible_landmarks_and_live_regions():
    html = _html()

    assert "<title>Task-aware AI-ready Data Agent</title>" in html
    assert "<h1>Task-aware AI-ready Data Agent</h1>" in html
    assert "Task-aware AI-ready Data Agent v0.3.1" in html
    assert "<title>PRIDE AI-ready Agent</title>" not in html
    assert 'class="skip-link"' in html
    assert 'href="#mainContent"' in html
    assert '<main class="container app-shell" id="mainContent"' in html
    assert 'aria-live="polite"' in html
    assert 'aria-describedby="runModeHelp"' in html
    assert 'role="status"' in html
    assert "/api/history?fast=1" in html


def test_frontend_template_labels_primary_form_controls():
    html = _html()

    for control_id in [
        "inputValue",
        "submitter",
        "repositorySelect",
        "reviewedFasta",
        "cfgApiKey",
        "cfgBaseUrl",
        "cfgModel",
        "cfgTimeout",
    ]:
        assert f'id="{control_id}"' in html
        assert f'for="{control_id}"' in html


def test_frontend_template_has_reusable_ui_components_and_button_loading_state():
    html = _html()

    assert "const UI={" in html
    assert "emptyState(message)" in html
    assert "taskRow(item,options={})" in html
    assert "reviewItem(item)" in html
    assert "setButtonBusy(button,busy,label)" in html
    assert "aria-busy" in html
    assert "aria-pressed" in html
    assert "aria-checked" in html


def test_frontend_template_exposes_batch_excel_workflow():
    html = _html()

    assert 'id="batchInput"' in html
    assert 'for="batchInput"' in html
    assert 'id="batchSubmitter"' in html
    assert 'for="batchSubmitter"' in html
    assert 'id="batchRepository"' in html
    assert 'for="batchRepository"' in html
    assert 'id="batchJobs"' in html
    assert 'id="batchStartBtn"' in html
    assert "startBatch()" in html
    assert "downloadBatchExcel" in html
    assert "downloadBatchAudit" in html
    assert "/api/batches/parameters" in html
    assert "/api/batches/" in html


def test_frontend_template_exposes_batch_run_mode_and_resource_policy():
    html = _html()

    assert 'data-run-mode="prepare"' in html
    assert 'id="singleResourcePolicy"' in html
    assert 'for="singleResourcePolicy"' in html
    assert 'id="batchRunMode"' in html
    assert 'for="batchRunMode"' in html
    assert 'id="batchResourcePolicy"' in html
    assert 'for="batchResourcePolicy"' in html
    assert "resource_policy:document.getElementById('singleResourcePolicy').value" in html
    assert "reviewed_fasta:document.getElementById('reviewedFasta').value" in html
    assert "run_mode:document.getElementById('batchRunMode').value" in html
    assert "resource_policy:document.getElementById('batchResourcePolicy').value" in html
    assert "runPreflight(payload)" in html
    assert "/api/preflight" in html
    assert "Prepare input package" in html


def test_frontend_template_disables_full_workflow_by_default_for_demo_server():
    html = _html()

    assert 'data-run-mode="full" onclick="setRunMode(\'full\')" data-i18n="modeFull" disabled aria-disabled="true"' in html
    assert '<option value="full" data-i18n="modeFull" disabled>Full workflow</option>' in html
    assert "let fullWorkflowEnabled=false" in html


def test_frontend_template_exposes_repository_selection_for_single_and_batch():
    html = _html()

    assert 'id="repositorySelect"' in html
    assert 'id="batchRepository"' in html
    assert 'value="auto"' in html
    assert 'value="pride"' in html
    assert 'value="massive"' in html
    assert 'value="iprox"' in html
    assert "iProX" in html
    assert "IPX" in html
    assert "repository:document.getElementById('repositorySelect').value" in html
    assert "repository:document.getElementById('batchRepository').value" in html
    assert "repositoryLabel" in html


def test_frontend_template_uses_workflow_tabs_and_status_cards():
    html = _html()

    assert 'role="tablist"' in html
    assert 'data-workflow-tab="single"' in html
    assert 'data-workflow-tab="batch"' in html
    assert 'data-workflow-tab="discovery"' in html
    assert 'id="singleTaskPanel"' in html
    assert 'id="batchTaskPanel"' in html
    assert 'id="discoveryTaskPanel"' in html
    assert 'class="status-cards system-summary"' in html
    assert "setWorkflowTab(" in html


def test_frontend_template_exposes_discovery_workflow():
    html = _html()

    assert 'id="discoveryStartBtn"' in html
    assert 'id="discoveryCancelBtn"' in html
    assert 'id="discoveryLogActivity"' in html
    assert 'id="discoveryLogTools"' in html
    assert 'id="discoveryLogRaw"' in html
    assert 'for="discoverySpecies"' in html
    assert 'for="discoveryMaxProjects"' in html
    assert 'id="discoveryUseMemory"' in html
    assert 'id="discoveryTaskType"' in html
    assert 'id="discoveryAgentic" checked' in html
    assert 'id="discoveryAgenticRounds"' in html
    assert 'id="discoveryRuntime" value="workflow"' in html
    assert 'id="discoveryRuntimeWorkflow"' in html
    assert 'id="discoveryRuntimeAgent"' in html
    assert "setDiscoveryRuntime('openai_agents')" in html
    assert 'id="discoveryAgentControls" hidden' in html
    assert 'id="discoveryAgentBudgetAutonomous"' in html
    assert 'id="discoveryAgentCredentialStatus"' in html
    assert "discoveryAgentServerKeyReady" in html
    assert "discoveryAgentServerKeyNeeded" in html
    assert "discoveryAgentWebKeyReady" in html
    assert "runtime:currentDiscoveryRuntime" in html
    assert "agent_budget_mode:" not in html
    assert "agent_max_rounds:" not in html
    assert "agent_max_turns:" not in html
    assert "agent_max_tool_calls:" not in html
    assert "state==='completed_with_review'?'needs_review'" in html
    assert "llm_config:collectConfig()" in html
    assert 'id="discoveryAgentResult"' in html
    assert "renderDiscoveryAgentResult(data)" in html
    assert "agents_discovery_report_md" in html
    assert "agents_discovery_events_json" in html
    assert "agents_discovery_summary_json" in html
    assert "agents_discovery_budget_json" in html
    assert 'id="discoveryPrompt"' in html
    assert 'id="discoveryResults"' in html
    assert "startDiscovery()" in html
    assert "pollDiscoveryJob()" in html
    assert "cancelDiscoveryJob()" in html
    assert "renderDiscoveryJobLogs" in html
    assert "safeRenderDiscoveryJobLogs" in html
    assert "appendDiscoveryLogRows" in html
    assert "discoveryLogSequences" in html
    assert "lastDiscoveryLogsFingerprint" not in html
    assert "const shouldFollow=box.scrollHeight-box.scrollTop-box.clientHeight<=16;" in html
    assert "discoverySubmitting" in html
    assert "正在提交 Discovery 请求" in html
    assert "safeRenderDiscoveryJobLogs([{ts:new Date().toISOString(),level:'info',message:t('discoverySubmitting')}])" in html
    assert "llm_config:collectConfig()" in html
    assert "downloadDiscoveryFile" in html
    assert "/api/discovery/jobs" in html
    assert "/api/discovery" in html
    assert "dataset_manifest_csv" in html
    assert "dataset_manifest_usable_csv" in html
    assert "batch_inputs_valid" in html
    assert "batch_inputs_usable" in html
    assert "quality_report" in html
    assert "dataset_manifest_task_ready_csv" in html
    assert "batch_inputs_task_ready" in html
    assert "task_ai_readiness_matrix_csv" in html
    assert "data_value_ranking_csv" in html
    assert "data_value_report_md" in html
    assert "downloadUsableManifestCsv" in html
    assert "downloadValidBatchInputs" in html
    assert "downloadQualityReport" in html
    assert "discoveryTrust" in html
    assert "discoveryUsable" in html
    assert "discoveryValidity" in html
    assert "discoveryTaskReady" in html
    assert "discoveryAgenticSummary" in html
    assert "validity_status" in html
    assert "validity_reasons" in html
    assert "task_readiness_status" in html
    assert "label_source_status" in html
    assert "spectra_requirement_status" in html
    assert "metadata_requirement_status" in html
    assert "ai_ready_target_schema" in html
    assert "fragmentation_methods" in html
    assert "diversity_tags" in html
    assert 'id="discoveryReviewBtn"' in html
    assert "saveDiscoveryReviews()" in html
    assert "collectDiscoveryReviews()" in html
    assert "/review" in html
    assert 'data-discovery-review="decision"' in html
    assert "DISCOVERY_VALIDITY_GROUPS=['valid','weak_keep','needs_review','exclude']" in html
    assert "discovery-file-group" in html
    assert "discoveryValidityBucket(file)" in html
    assert "discoveryGroup_valid" in html
    assert "discoveryGroup_weak_keep" in html
    assert "discoveryGroup_needs_review" in html
    assert "discoveryGroup_exclude" in html
    assert "discoverySetSelect('aiReadyTaskType',discoveryTaskType)" in html
    assert "batchRunMode&&fullWorkflowEnabled" in html
    assert "batchRunMode.value='full'" in html
    assert "task_ai_readiness_score" in html
    assert "data_value_score" in html


def test_frontend_template_exposes_immunopeptidomics_discovery_fields():
    html = _html()

    assert 'value="general"' in html
    assert "General data search" in html
    assert "Discovery target" in html
    assert 'value="immunopeptidomics"' in html
    assert "[hidden]{display:none!important}" in html
    assert "Immunopeptidomics / HLA ligandome" in html
    assert 'id="discoveryPtmRow"' in html
    assert 'id="discoveryPtm" multiple size="6"' in html
    assert "Hold Ctrl/Command to select multiple PTM types." in html
    assert "ptm_types:discoverySelectedPtmTypes()" in html
    assert "function discoverySetMultiSelect" in html
    assert "function discoverySelectedPtmTypes" in html
    assert "function discoveryAddPtmSelection" in html
    assert 'id="discoveryGoal" onchange="toggleDiscoveryGoalControls()"' in html
    assert "function toggleDiscoveryGoalControls()" in html
    assert "function discoverySelectedPtmType()" in html
    assert "ptm_type:discoverySelectedPtmType()" in html
    assert "toggleDiscoveryGoalControls();" in html
    assert "hla ligandome" in html.lower()
    assert "immunopeptide_scope" in html
    assert "hla_class" in html
    assert "hla_alleles" in html
    assert "immunopeptide_enrichment_methods" in html


def test_frontend_template_has_clean_chinese_i18n_override():
    html = _html()

    assert "const CLEAN_ZH_I18N" in html
    assert "蛋白质组 AI 数据工作台" in html
    assert "数据发现" in html
    assert "AI-ready 构建" in html
    assert "运行 Discovery" in html
    assert "发送到 Batch" in html
    assert "Discovery 期间使用 LLM 规划查询" in html
    assert "Object.assign(I18N.zh,CLEAN_ZH_I18N)" in html


def test_frontend_template_simplifies_ai_ready_build_into_three_stage_flow():
    html = _html()

    assert 'class="ai-ready-flow"' in html
    assert "1. Input source" in html
    assert "2. Task and build" in html
    assert "3. Results and next step" in html
    assert "1. 输入来源" in html
    assert "2. 任务与构建" in html
    assert "3. 结果与下一步" in html
    assert 'name="aiReadySource" value="agent_run" checked' in html
    assert 'name="aiReadySource" value="local_search"' in html
    assert 'name="aiReadySource" value="existing_build"' in html
    assert 'data-ai-ready-source-panel="agent_run"' in html
    assert 'data-ai-ready-source-panel="local_search" hidden' in html
    assert 'data-ai-ready-source-panel="existing_build" hidden' in html
    assert "function toggleAiReadySource()" in html
    assert "toggleAiReadySource();" in html
    assert "Manual TSV/MGF paths" in html
    assert "Advanced build tools" in html
    assert "Advanced reports and repository checks" in html
    assert "手动 TSV/MGF 路径" in html
    assert "高级构建工具" in html
    assert "高级报告与 repository 检查" in html


def test_frontend_template_connects_full_batch_to_ai_ready_build():
    html = _html()

    assert "function eligibleBatchAgentRunDirs(batch)" in html
    assert "if(mode!=='full')return []" in html
    assert "function batchItemHasAiReadyUsableRun(item)" in html
    assert "function workflowOutcomeText(outcome)" in html
    assert "function aiReadyOutcomeText(outcome)" in html
    assert "Partial outputs usable" in html
    assert "Completed from partial outputs" in html
    assert "workflowOutcome==='failed_with_usable_partial_outputs'" in html
    assert "item&&item.usable_partial_outputs" in html
    assert "buildAiReadyFromBatch()" in html
    assert "completed or usable partial batch run(s)" in html
    assert "batchAiReadyAvailable" in html
    assert "batchAiReadyRecipeNext" in html
    assert "data.output_dir&&!data.dataset_recipe" in html
    assert "aiReadyRecipeBatchDir" in html
    assert "function showBatchNeedsApiKey()" in html
    assert "batchNeedsApiKeyAfterHandoff" in html
    assert "use Build AI-ready" in html or "AI-ready 构建" in html


def test_frontend_template_guides_discovery_to_batch_to_build_loop():
    html = _html()

    assert 'id="discoveryNextStep"' in html
    assert "function renderDiscoveryNextStep()" in html
    assert "discoveryNextStepBatch" in html
    assert "discoveryBatchReadyNext" in html
    assert "Send selected to Batch" in html
    assert "Review / evidence" in html
    assert "discovery-row-details" in html
    assert "QC / Review" in html


def test_frontend_template_allows_open_species_general_discovery():
    html = _html()

    assert 'placeholder="optional; e.g. human, mouse"' in html
    assert "Species preference" in html
    assert "优先填写物种，保留其他" in html
    assert "Please provide positive project/file limits." in html
    assert "请确保项目和文件数量限制为正数。" in html
    assert "if(payload.max_projects<1||payload.max_files<1)" in html
    assert "!payload.species.length" not in html


def test_frontend_template_exposes_repository_smoke_and_agent_harness():
    html = _html()

    assert 'id="aiReadyRepositorySmokeRepository"' in html
    assert 'id="aiReadyRepositorySmokeInput"' in html
    assert 'id="aiReadyIproxIndexProjects"' in html
    assert 'id="aiReadyIproxIndexYears"' in html
    assert 'id="aiReadyIproxIndexDir"' in html
    assert 'id="aiReadyIproxIndexBtn"' in html
    assert 'id="aiReadyHarnessCaseFile"' in html
    assert 'id="aiReadyRepositorySmokeBtn"' in html
    assert 'id="aiReadyHarnessBtn"' in html
    assert "refreshIproxIndex()" in html
    assert "runRepositorySmoke()" in html
    assert "runAgentHarness()" in html
    assert "/api/ai-ready/refresh-iprox-index" in html
    assert "/api/ai-ready/repository-smoke" in html
    assert "/api/ai-ready/agent-harness" in html
    assert "iprox_index_dir:iproxIndexDir" in html
    assert "tests/fixtures/agent_harness_cases.json" in html


def test_frontend_template_exposes_recipe_split_and_gap_generation():
    html = _html()

    assert 'id="aiReadyRecipeBatchDir"' in html
    assert 'id="aiReadyRecipeDiscoveryManifest"' in html
    assert 'id="aiReadyRecipeRepositoryAudit"' in html
    assert 'id="aiReadyRecipeSplitStrategy"' in html
    assert 'value="project_disjoint"' in html
    assert 'value="lab_disjoint"' in html
    assert 'value="protein_disjoint"' in html
    assert "repository_audit:repositoryAudit" in html
    assert "split_strategy:splitStrategy" in html
    assert 'id="aiReadyRecipeBtn"' in html
    assert "makeDatasetRecipe()" in html
    assert "/api/ai-ready/make-dataset-recipe" in html
    assert 'id="aiReadyModelLoopRecipeDir"' in html
    assert 'id="aiReadyModelLoopAdapter"' in html
    assert 'value="xuanjinovo_template"' in html
    assert 'value="massnet_eval"' in html
    assert 'value="casanovo_eval"' in html
    assert 'id="aiReadyModelLoopMetricsFile"' in html
    assert "JSON / CSV / TSV / log" in html
    assert "xuanjinovo_eval_metrics.tsv" in html
    assert 'id="aiReadyModelLoopAdapterCommand"' in html
    assert "adapter:adapterCommand?'external_command':adapter" in html
    assert 'id="aiReadyRepositoryPlan"' in html
    assert "function renderModelRepositoryPlan(data,stat)" in html
    assert "model_informed_repository_plan" in html
    assert "Model-informed repository plan" in html
    assert "planned_repositories" in html
    assert 'id="aiReadyDiscoveryRequests"' in html
    assert "function renderModelDiscoveryRequests(data)" in html
    assert "function applyModelDiscoveryRequest(index)" in html
    assert "function applyModelDiscoveryRequestFallback(request,message)" in html
    assert "/api/ai-ready/model-informed-discovery-payload" in html
    assert "applyDiscoveryGoalFields(payload)" in html
    assert "currentModelDiscoveryRequests" in html
    assert "Next discovery requests" in html
    assert "setWorkflowTab('discovery')" in html
    assert "discoverySetSelect('discoveryRepository'" in html
    assert "model_informed_discovery_requests" in html
    assert 'id="aiReadyModelLoopBtn"' in html
    assert 'id="aiReadyDataScientistModelLoopDir"' in html
    assert 'id="aiReadyRepositorySmokeDirs"' in html
    assert 'id="aiReadyDataScientistReportBtn"' in html
    assert 'id="aiReadyGuidanceAlignmentBtn"' in html
    assert 'id="aiReadyDataScientistLoopBtn"' in html
    assert "runDatasetModelLoop()" in html
    assert "makeDataScientistAgentReport()" in html
    assert "makeGuidanceAlignmentReport()" in html
    assert "runDataScientistAgentLoop()" in html
    assert "repository_smoke_dirs:repositorySmokeDirs" in html
    assert "/api/ai-ready/model-loop" in html
    assert "/api/ai-ready/data-scientist-report" in html
    assert "/api/ai-ready/guidance-alignment" in html
    assert "/api/ai-ready/data-scientist-loop" in html
    assert "coverage gap" in html.lower()
    assert "evidence graph" in html.lower()


def test_frontend_template_exposes_device_performance_metrics():
    html = _html()

    for metric_id in ["metricCpu", "metricMemory", "metricDisk"]:
        assert f'id="{metric_id}"' in html

    assert "formatPercent(" in html
    assert "renderSystemMetrics(" in html
    assert "d.system_metrics" in html
    assert "systemCpu" in html
    assert "systemMemory" in html
    assert "systemDisk" in html


def test_frontend_template_renders_performance_metrics_as_animated_gauges():
    html = _html()

    assert ".gauge-dial" in html
    assert ".gauge-needle" in html
    assert ".gauge-card" in html
    assert "prefers-reduced-motion" in html
    assert "gaugeRotation(" in html
    assert "gaugeTone(" in html
    assert "gaugeCard(label,value,meta,percent)" in html
    assert "UI.gaugeCard(t('systemCpu')" in html
    assert "aria-label=\"'+esc(label)+' '+esc(value)+'\"" in html


def test_frontend_template_polls_performance_metrics_without_overlapping_requests():
    html = _html()

    assert "const HEALTH_REFRESH_MS=2000" in html
    assert "let healthPollActive=false" in html
    assert "async function pollHealth()" in html
    assert "if(healthPollActive)return" in html
    assert "setTimeout(pollHealth,HEALTH_REFRESH_MS)" in html
    assert "setInterval(loadHealth,5000)" not in html


def test_frontend_template_uses_unclipped_semicircle_gauges():
    html = _html()

    assert ".gauge-dial{position:relative;width:112px;height:70px;overflow:visible" in html
    assert 'viewBox="0 0 120 72"' in html
    assert 'class="gauge-svg"' in html
    assert 'class="gauge-arc gauge-low"' in html
    assert 'class="gauge-arc gauge-mid"' in html
    assert 'class="gauge-arc gauge-high"' in html
    assert "bottom:-42px" not in html
    assert ".gauge-dial{position:relative;height:50px;overflow:hidden" not in html
    assert "conic-gradient(" not in html
    assert "gaugeRotation(percent){const v=gaugePercent(percent);return ((v===null?0:v)*1.8-90).toFixed(1)}" in html


def test_frontend_template_uses_light_operational_app_shell():
    html = _html()

    assert "--bg:#f1f5f9" in html
    assert "--surface:#ffffff" in html
    assert "--primary:#6366f1" in html
    assert "--success:#10b981" in html
    assert 'class="container app-shell"' in html
    assert 'class="surface workbench-card"' in html
    assert 'class="right-rail operations-rail"' in html
    assert 'class="surface api-config-card"' in html
    assert 'class="surface history-card"' in html
    assert "system-summary" in html


def test_frontend_template_uses_inline_error_alerts_not_browser_alerts():
    html = _html()

    assert 'id="formAlert"' in html
    assert 'role="alert"' in html
    assert 'aria-live="assertive"' in html
    assert "showFormAlert(message)" in html
    assert "clearFormAlert()" in html
    assert "alert(" not in html


def test_frontend_template_localizes_workflow_copy():
    html = _html()

    for key in [
        "singleTabTitle",
        "singleTabDesc",
        "batchTabTitle",
        "batchTabDesc",
        "singleSetupTitle",
        "singleSetupHelp",
        "workflowReady",
    ]:
        assert f'data-i18n="{key}"' in html
        assert f"{key}:" in html

    assert "Parameter planning" in html
    assert "批量 Excel 报表" in html


def test_frontend_template_has_responsive_accessible_progress_and_panels():
    html = _html()

    assert 'aria-label="Pipeline progress"' in html
    assert 'role="listitem"' in html
    assert 'class="section-heading"' in html
    assert "@media(max-width:1100px)" in html
    assert "@media(max-width:720px)" in html


def test_frontend_template_keeps_agent_reasoning_panel_stable_and_unclipped():
    html = _html()

    assert ".run-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(380px,420px)" in html
    assert ".run-main{min-width:0}" in html
    assert ".side-panels{display:flex;flex-direction:column;gap:14px;position:sticky;top:24px;align-self:start;min-width:0;height:calc(100vh - 48px);max-height:calc(100vh - 48px);min-height:0;overflow:visible" in html
    assert ".review-panel{flex:0 1 280px;min-height:0;max-height:min(280px,34vh);overflow-y:auto" in html
    assert ".agent-reasoning-panel{flex:1 1 360px;min-height:280px;min-width:0;display:flex;flex-direction:column" in html
    assert ".agent-reasoning-body{display:none;flex:1 1 auto;min-height:0;padding:14px 16px;overflow:auto" in html
    assert "@keyframes slideDown{from{opacity:0;transform:translateY(-4px)}to{opacity:1;transform:translateY(0)}}" in html
    assert "@media(max-width:1180px)" in html
    assert ".side-panels{position:static;height:auto;max-height:none;overflow:visible}" in html


def test_frontend_template_maps_agent_gate_labels_to_user_copy():
    html = _html()

    assert "const GATE_LABEL_KEYS={auto_accept:'agentGateAllowed',evidence_gated_accept:'agentGateAllowed',review_required:'agentGateReview',blocked:'agentGateBlocked',allowed:'agentGateAllowed'}" in html
    assert "const labelKey=GATE_LABEL_KEYS[gate]" in html
    assert "gate.replace(/_/g,'')" not in html
    assert "_formatGate(plan.execution_gate)" in html


def test_frontend_template_separates_project_match_and_metadata_confidence():
    html = _html()

    assert "agentResolutionConfidence:'Resolution confidence'" in html
    assert "agentFileMatchScore:'File match score'" in html
    assert "agentMetadataConsistency:'Metadata consistency'" in html
    assert "agentResolutionConfidence:'解析置信度'" in html
    assert "agentFileMatchScore:'文件匹配分数'" in html
    assert "agentMetadataConsistency:'元数据一致性'" in html
    assert "obs.selected_project.resolution_confidence" in html
    assert "obs.selected_project.match_score" in html
    assert "obs.selected_project.metadata_consistency" in html
    assert "agentMatchConfidence" not in html


def test_frontend_template_has_useful_history_empty_state_and_batch_actions():
    html = _html()

    assert "emptyHistory(message,primaryAction,secondaryAction)" in html
    assert "historyEmptyTitle" in html
    assert "historyEmptyBody" in html
    assert "historyEmptySingleAction" in html
    assert "historyEmptyBatchAction" in html
    assert "attachBatch(id)" in html
    assert "downloadBatchExcelById(id)" in html
    assert "item.kind==='batch'" in html


def test_frontend_template_has_production_ui_component_helpers():
    html = _html()

    assert "statusPill(status,label)" in html
    assert "metricCard(label,value,meta)" in html
    assert "helperText(title,body)" in html
    assert "batchItem(item)" in html
    assert "batchEvent(event)" in html
    assert 'id="batchLogBox"' in html
    assert "renderBatchEvents" in html
    assert "role=\"tabpanel\"" in html
