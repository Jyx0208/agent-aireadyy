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
    assert 'value="iprox"' not in html
    assert "iProX" not in html
    assert "IPX" not in html
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


def test_frontend_template_uses_light_operational_app_shell():
    html = _html()

    assert "--bg:#f6f8fb" in html
    assert "--surface:#ffffff" in html
    assert "--primary:#4f46e5" in html
    assert "--success:#059669" in html
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
