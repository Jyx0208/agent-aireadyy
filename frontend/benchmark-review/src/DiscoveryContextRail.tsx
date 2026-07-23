import { useState } from "react";
import {
  Button,
  ComposedModal,
  ModalBody,
  ModalHeader,
  ProgressBar,
  Tag,
  Tile,
} from "@carbon/react";
import { DataBase, Download } from "@carbon/icons-react";

import { buildDiscoveryRunView, type DiscoveryRunStatus, type DiscoveryRunView } from "./DiscoveryProgressMessage";
import { IntentSpecPanel } from "./IntentSpecPanel";
import type { GrillPhase, IntentSpec } from "./intent-spec";
import type { DiscoveryJob } from "./workflow-api";

type Props = {
  spec: IntentSpec;
  phase: GrillPhase;
  job: DiscoveryJob | null;
  onConfirm: () => void;
  onApplyDefaults: () => void;
  onNavigate: (tabIndex: number) => void;
  /** Load discovery L1 usable file list into batch planner. */
  onSeedBatchInputs?: (text: string) => void;
};

function tagType(status: DiscoveryRunStatus): "blue" | "green" | "red" | "gray" | "magenta" | "purple" {
  if (status === "completed") return "green";
  if (status === "failed") return "red";
  if (status === "blocked") return "purple";
  if (status === "cancelled") return "gray";
  return "blue";
}

async function fetchUsableBatchInputs(discoveryId: string): Promise<string> {
  const id = encodeURIComponent(discoveryId);
  const urls = [
    `/api/discovery/${id}/download?file=batch_inputs_usable`,
    `/api/discovery/${id}/download?file=batch_inputs`,
  ];
  for (const url of urls) {
    try {
      const response = await fetch(url);
      if (!response.ok) continue;
      const text = (await response.text()).trim();
      if (text) return text;
    } catch {
      // try next
    }
  }
  throw new Error("未找到可用文件列表（batch_inputs_usable）。可下载完整运行包后手动整理。");
}

function RunStatusCard({
  view,
  onOpenResult,
}: {
  view: DiscoveryRunView | null;
  onOpenResult: () => void;
}) {
  const active = view?.status === "queued" || view?.status === "running";
  const showProgress = Boolean(view && (active || view.progressPercent != null));
  const usable = view?.metrics.usableFiles ?? view?.metrics.selectedProjects ?? 0;

  return (
    <Tile className="discovery-run-card">
      <div className="section-title discovery-run-card__title">
        <DataBase size={20} />
        <h2>当前数据发现</h2>
      </div>
      {!view ? (
        <p className="empty-copy">策略确认后，这里只显示真实运行状态、关键计数和结果入口。</p>
      ) : (
        <>
          <div className="run-status">
            <Tag size="sm" type={tagType(view.status)}>{view.statusLabel}</Tag>
            <span>{view.statusDetail || view.summary}</span>
          </div>
          {showProgress ? (
            <ProgressBar
              label={
                view.progressPercent == null
                  ? "总体进度（服务端暂未提供百分比）"
                  : `总体进度 ${Math.round(view.progressPercent)}%`
              }
              value={view.progressPercent ?? undefined}
              max={100}
              size="small"
              status={
                view.status === "failed"
                  ? "error"
                  : view.status === "completed" || view.status === "blocked"
                    ? "finished"
                    : "active"
              }
            />
          ) : null}
          <dl className="context-metrics">
            <div><dt>项目</dt><dd>{view.metrics.projects}</dd></div>
            <div><dt>文件线索</dt><dd>{view.metrics.files}</dd></div>
            <div><dt>可用文件 L1</dt><dd>{usable}</dd></div>
            <div><dt>待复核</dt><dd>{view.metrics.reviews}</dd></div>
          </dl>

          {view.status === "completed" || view.status === "blocked" ? (
            <div className="discovery-result-entry">
              <p className="eyebrow">L1 可用清单</p>
              <h3>{view.status === "blocked" ? "可用文件清单已就绪" : "发现结果已就绪"}</h3>
              <p>
                {view.status === "blocked"
                  ? `约 ${usable} 条可用文件（valid + weak_keep）。可下载清单或一键送入批量参数规划。`
                  : view.summary}
              </p>
              <Button size="sm" onClick={onOpenResult}>查看结果与送入批量</Button>
            </div>
          ) : null}
          {view.status === "failed" ? (
            <div className="discovery-run-error" role="alert">
              <strong>这轮没有完成</strong>
              <p>{view.error || "可在左侧展开技术轨迹查看失败阶段。"}</p>
            </div>
          ) : null}
          {view.status === "cancelled" ? (
            <p className="empty-copy">已取消本轮搜索；可以继续修改策略后重新确认。</p>
          ) : null}
        </>
      )}
    </Tile>
  );
}

function ResultModal({
  view,
  open,
  onClose,
  onNavigate,
  onSeedBatchInputs,
}: {
  view: DiscoveryRunView | null;
  open: boolean;
  onClose: () => void;
  onNavigate: (tabIndex: number) => void;
  onSeedBatchInputs?: (text: string) => void;
}) {
  const [seedBusy, setSeedBusy] = useState(false);
  const [seedError, setSeedError] = useState("");

  if (!open || !view || !["completed", "blocked"].includes(view.status)) return null;
  const base = view.discoveryId
    ? `/api/discovery/${encodeURIComponent(view.discoveryId)}/download?file=`
    : "";
  const usable = view.metrics.usableFiles ?? view.metrics.selectedProjects ?? 0;
  const strictValid = view.metrics.strictValidFiles ?? 0;

  const sendToBatch = async () => {
    if (!view.discoveryId) {
      setSeedError("缺少 discovery_id，无法拉取列表。");
      return;
    }
    setSeedBusy(true);
    setSeedError("");
    try {
      const text = await fetchUsableBatchInputs(view.discoveryId);
      onSeedBatchInputs?.(text);
      onClose();
      onNavigate(2);
    } catch (reason) {
      setSeedError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSeedBusy(false);
    }
  };

  return (
    <ComposedModal open onClose={onClose} size="lg">
      <ModalHeader
        label="DATA DISCOVERY"
        title={view.status === "blocked" ? "可用文件清单与说明" : "发现结果"}
        iconDescription="关闭结果"
        closeModal={onClose}
      />
      <ModalBody hasScrollingContent>
        <p className="discovery-result-summary">{view.summary}</p>
        <dl className="result-modal-metrics">
          <div><dt>候选项目</dt><dd>{view.metrics.projects}</dd></div>
          <div><dt>可用文件 L1</dt><dd>{usable}</dd></div>
          <div><dt>严格 valid</dt><dd>{strictValid}</dd></div>
          <div><dt>文件线索</dt><dd>{view.metrics.files}</dd></div>
        </dl>

        <section className="result-modal-section">
          <h3>当前不足（说明，不阻挡 L1 使用）</h3>
          {view.qualityIssues.length ? (
            <div className="discovery-audit-summary">
              <ul>{view.qualityIssues.map((issue) => <li key={issue}>{issue}</li>)}</ul>
              <p className="empty-copy">
                以上多指严格 build-ready / 元数据完整度。你要的可用文件列表仍可下载，并送入批量参数规划与标准化构建。
              </p>
            </div>
          ) : (
            <p className="empty-copy">未单独列出阻塞项；请以下载清单为准。</p>
          )}
        </section>

        <section className="result-modal-section">
          <h3>下载与批量</h3>
          {base ? (
            <div className="result-download-grid">
              <Button size="sm" renderIcon={Download} href={`${base}batch_inputs_usable`}>
                可用批量输入 L1
              </Button>
              <Button size="sm" kind="secondary" renderIcon={Download} href={`${base}dataset_manifest_usable_csv`}>
                可用数据清单 CSV
              </Button>
              <Button size="sm" kind="secondary" renderIcon={Download} href={`${base}agents_discovery_report_md`}>
                Agent 审查报告
              </Button>
              <Button size="sm" kind="tertiary" renderIcon={Download} href={`${base}discovery_run_bundle_zip`}>
                完整运行包
              </Button>
            </div>
          ) : (
            <p className="empty-copy">结果正在归档，下载入口尚未生成。</p>
          )}
          {seedError ? <p className="empty-copy" role="alert">{seedError}</p> : null}
          <div className="button-row" style={{ marginTop: "0.75rem" }}>
            <Button
              size="sm"
              kind="primary"
              disabled={seedBusy || !view.discoveryId || !onSeedBatchInputs}
              onClick={() => void sendToBatch()}
            >
              {seedBusy ? "正在加载列表…" : "送入批量参数规划"}
            </Button>
            <Button size="sm" kind="tertiary" onClick={() => { onClose(); onNavigate(2); }}>
              仅打开批量页
            </Button>
          </div>
        </section>
      </ModalBody>
    </ComposedModal>
  );
}

export function DiscoveryContextRail({
  spec,
  phase,
  job,
  onConfirm,
  onApplyDefaults,
  onNavigate,
  onSeedBatchInputs,
}: Props) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [resultOpen, setResultOpen] = useState(false);
  const view = job ? buildDiscoveryRunView(job) : null;
  const openResult = () => {
    setMobileOpen(false);
    setResultOpen(true);
  };

  const content = (
    <>
      <IntentSpecPanel
        spec={spec}
        phase={phase}
        busy={phase === "running"}
        onConfirm={onConfirm}
        onApplyDefaults={onApplyDefaults}
      />
      <RunStatusCard view={view} onOpenResult={openResult} />
    </>
  );

  return (
    <>
      <div className="context-rail-mobile-trigger">
        <Button
          kind="secondary"
          size="sm"
          aria-expanded={mobileOpen}
          onClick={() => setMobileOpen(true)}
        >
          查看策略与运行状态
        </Button>
      </div>
      <aside className="run-rail" aria-label="策略与运行上下文">
        {content}
      </aside>

      {mobileOpen ? (
        <ComposedModal open onClose={() => setMobileOpen(false)} size="lg">
          <ModalHeader
            label="DISCOVERY CONTEXT"
            title="策略与运行状态"
            iconDescription="关闭上下文"
            closeModal={() => setMobileOpen(false)}
          />
          <ModalBody hasScrollingContent>
            <div className="context-modal-stack">{content}</div>
          </ModalBody>
        </ComposedModal>
      ) : null}

      <ResultModal
        view={view}
        open={resultOpen}
        onClose={() => setResultOpen(false)}
        onNavigate={onNavigate}
        onSeedBatchInputs={onSeedBatchInputs}
      />
    </>
  );
}
