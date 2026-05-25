from __future__ import annotations

from pathlib import Path


TEMPLATE = Path("src/agent/web/templates/index.html")


def _html() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def test_frontend_template_exposes_accessible_landmarks_and_live_regions():
    html = _html()

    assert 'class="skip-link"' in html
    assert 'href="#mainContent"' in html
    assert '<main class="container app-shell" id="mainContent"' in html
    assert 'aria-live="polite"' in html
    assert 'aria-describedby="runModeHelp"' in html
    assert 'role="status"' in html


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
    assert 'id="singleTaskPanel"' in html
    assert 'id="batchTaskPanel"' in html
    assert 'class="status-cards system-summary"' in html
    assert "setWorkflowTab(" in html


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
    assert 'class="surface history-card operations-rail"' in html
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
