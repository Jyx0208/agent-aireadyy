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
};

function tagType(status: DiscoveryRunStatus): "blue" | "green" | "red" | "gray" | "magenta" {
  if (status === "completed") return "green";
  if (status === "failed") return "red";
  if (status === "blocked") return "magenta";
  if (status === "cancelled") return "gray";
  return "blue";
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
              status={view.status === "failed" || view.status === "blocked" ? "error" : view.status === "completed" ? "finished" : "active"}
            />
          ) : null}
          <dl className="context-metrics">
            <div><dt>项目</dt><dd>{view.metrics.projects}</dd></div>
            <div><dt>文件</dt><dd>{view.metrics.files}</dd></div>
            <div><dt>通过交付</dt><dd>{view.metrics.selectedProjects ?? 0}</dd></div>
            <div><dt>待复核</dt><dd>{view.metrics.reviews}</dd></div>
          </dl>

          {view.status === "completed" ? (
            <div className="discovery-result-entry">
              <p className="eyebrow">RESULT READY</p>
              <h3>发现结果已就绪</h3>
              <p>{view.summary}</p>
              <Button size="sm" onClick={onOpenResult}>查看结果</Button>
            </div>
          ) : null}
          {view.status === "failed" ? (
            <div className="discovery-run-error" role="alert">
              <strong>这轮没有完成</strong>
              <p>{view.error || "可在左侧展开技术轨迹查看失败阶段。"}</p>
            </div>
          ) : null}
          {view.status === "blocked" ? (
            <div className="discovery-run-error" role="status">
              <strong>候选已保留，但没有结果通过交付质量闸门</strong>
              <p>{view.summary}</p>
              <Button size="sm" kind="tertiary" onClick={onOpenResult}>查看审计</Button>
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
}: {
  view: DiscoveryRunView | null;
  open: boolean;
  onClose: () => void;
  onNavigate: (tabIndex: number) => void;
}) {
  if (!open || !view || !["completed", "blocked"].includes(view.status)) return null;
  const base = view.discoveryId
    ? `/api/discovery/${encodeURIComponent(view.discoveryId)}/download?file=`
    : "";

  return (
    <ComposedModal open onClose={onClose} size="lg">
      <ModalHeader
        label="DATA DISCOVERY"
        title={view.status === "blocked" ? "质量审计与候选证据" : "发现结果"}
        iconDescription="关闭结果"
        closeModal={onClose}
      />
      <ModalBody hasScrollingContent>
        <p className="discovery-result-summary">{view.summary}</p>
        <dl className="result-modal-metrics">
          <div><dt>{view.status === "blocked" ? "候选项目" : "入选项目"}</dt><dd>{view.metrics.projects}</dd></div>
          <div><dt>通过交付</dt><dd>{view.metrics.selectedProjects ?? 0}</dd></div>
          <div><dt>文件线索</dt><dd>{view.metrics.files}</dd></div>
          <div><dt>待复核</dt><dd>{view.metrics.reviews}</dd></div>
        </dl>

        <section className="result-modal-section">
          <h3>审查与下载</h3>
          {view.qualityIssues.length ? (
            <div className="discovery-audit-summary">
              <h4>{view.status === "blocked" ? "未通过的主要原因" : "质量说明"}</h4>
              <ul>{view.qualityIssues.map((issue) => <li key={issue}>{issue}</li>)}</ul>
            </div>
          ) : null}
          {base ? (
            <div className="result-download-grid">
              <Button size="sm" renderIcon={Download} href={`${base}agents_discovery_report_md`}>
                Agent 审查报告
              </Button>
              <Button size="sm" kind="secondary" renderIcon={Download} href={`${base}dataset_manifest_csv`}>
                数据清单 CSV
              </Button>
              <Button size="sm" kind="secondary" renderIcon={Download} href={`${base}project_judgments_table_csv`}>
                项目评分与理由
              </Button>
              <Button size="sm" kind="tertiary" renderIcon={Download} href={`${base}discovery_run_bundle_zip`}>
                完整运行包
              </Button>
            </div>
          ) : (
            <p className="empty-copy">结果正在归档，下载入口尚未生成。</p>
          )}
        </section>

        {view.status === "completed" ? (
          <section className="result-modal-section">
            <h3>继续处理</h3>
            <div className="button-row">
              <Button size="sm" kind="secondary" onClick={() => { onClose(); onNavigate(1); }}>
                单文件处理
              </Button>
              <Button size="sm" kind="tertiary" onClick={() => { onClose(); onNavigate(2); }}>
                批量处理
              </Button>
              <Button size="sm" kind="tertiary" onClick={() => { onClose(); onNavigate(3); }}>
                AI-ready 构建
              </Button>
            </div>
          </section>
        ) : (
          <p className="empty-copy">候选尚未通过交付门槛，不能进入后续处理；请先查看审计并让 Agent 修复或调整策略。</p>
        )}
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
      />
    </>
  );
}
