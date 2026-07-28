import { useEffect, useMemo, useState } from "react";
import {
  Button,
  Checkbox,
  InlineNotification,
  NumberInput,
  ProgressBar,
  Select,
  SelectItem,
  Tag,
  TextArea,
  TextInput,
  Tile,
} from "@carbon/react";
import { Download, Play, Renew } from "@carbon/icons-react";

import { getBatch, preflight, startBatch, terminalWorkflowStatus, type WorkflowRecord } from "./workflow-api";

type Props = {
  batchId: string;
  onBatchId: (batchId: string) => void;
  onChanged?: () => void;
  /** Prefill from discovery L1 usable file list (one input per line). */
  initialInputs?: string;
  /** Bumps on each discovery → batch handoff so re-seed of the same text still applies. */
  initialInputsToken?: number;
  initialInputRecords?: WorkflowRecord[];
  initialSource?: {
    jobId?: string;
    discoveryId?: string;
    batchIndex?: number;
  };
};

type BatchProgress = {
  stage?: string;
  stage_label?: string;
  percent?: number | null;
  message?: string;
  failed_stage?: string;
  download?: {
    label?: string;
    downloaded_bytes?: number;
    total_bytes?: number | null;
    speed_bps?: number | null;
    eta_seconds?: number | null;
    complete?: boolean;
  };
  updated_at?: string;
};

function formatBytes(value?: number | null): string {
  const n = Number(value || 0);
  if (!Number.isFinite(n) || n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v >= 10 || i === 0 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
}

function formatSpeed(bps?: number | null): string {
  if (bps == null || !Number.isFinite(Number(bps)) || Number(bps) <= 0) return "—";
  return `${formatBytes(Number(bps))}/s`;
}

function formatEta(seconds?: number | null): string {
  if (seconds == null || !Number.isFinite(Number(seconds)) || Number(seconds) < 0) return "—";
  const s = Math.round(Number(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (m < 60) return `${m}m ${r}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function statusTagType(status: string): "red" | "green" | "blue" | "gray" | "purple" | "magenta" {
  if (status === "failed") return "red";
  if (status === "completed") return "green";
  if (status === "running") return "blue";
  if (status === "needs_review" || status === "blocked") return "magenta";
  return "gray";
}

function shortName(input: string): string {
  const text = String(input || "").trim();
  if (!text) return "—";
  try {
    const url = new URL(text);
    const parts = url.pathname.split("/").filter(Boolean);
    return parts[parts.length - 1] || text;
  } catch {
    const parts = text.split(/[/\\]/).filter(Boolean);
    return parts[parts.length - 1] || text;
  }
}

export function BatchPanel({
  batchId,
  onBatchId,
  onChanged,
  initialInputs,
  initialInputsToken,
  initialInputRecords,
  initialSource,
}: Props) {
  const [inputs, setInputs] = useState("");
  const [submitter, setSubmitter] = useState("local-user");
  const [repository, setRepository] = useState("pride");
  const [runMode, setRunMode] = useState("parameters");
  const [jobs, setJobs] = useState(3);
  const [record, setRecord] = useState<WorkflowRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [seedNotice, setSeedNotice] = useState("");
  const [filter, setFilter] = useState<"all" | "running" | "failed" | "completed" | "queued">("all");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [inputRecords, setInputRecords] = useState<WorkflowRecord[]>([]);
  const [deleteSourcesAfterSuccess, setDeleteSourcesAfterSuccess] = useState(false);

  useEffect(() => {
    const text = String(initialInputs || "").trim();
    if (!text) return;
    setInputs(text);
    setInputRecords(Array.isArray(initialInputRecords) ? initialInputRecords : []);
    const lines = text.split(/\r?\n/).map((line) => line.trim()).filter((line) => line && !line.startsWith("#"));
    setSeedNotice(`已从发现结果预填 ${lines.length} 条可用输入（L1），可直接启动参数推断或继续编辑。`);
  }, [initialInputs, initialInputsToken, initialInputRecords]);

  useEffect(() => {
    if (!batchId) {
      setRecord(null);
      return;
    }
    const controller = new AbortController();
    const load = async () => {
      try {
        setRecord(await getBatch(batchId, controller.signal));
      } catch (reason) {
        if (!(reason instanceof DOMException && reason.name === "AbortError")) setError(String(reason));
      }
    };
    void load();
    if (terminalWorkflowStatus(record?.status)) return () => controller.abort();
    // Faster poll while downloads may be active.
    const hasDownload = Array.isArray(record?.items)
      && (record?.items as WorkflowRecord[]).some((item) => {
        const progress = (item as WorkflowRecord).progress as BatchProgress | undefined;
        return item.status === "running" && progress?.stage === "download";
      });
    const timer = window.setInterval(() => void load(), hasDownload ? 1000 : 1500);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [batchId, record?.status, record?.updated_at]);

  const start = async () => {
    const items = inputs.split(/\r?\n/).map((value) => value.trim()).filter((value) => value && !value.startsWith("#"));
    if (!items.length || !submitter.trim()) {
      setError("请至少填写一个批量输入和提交人。");
      return;
    }
    const payload = {
      inputs: items,
      submitter: submitter.trim(),
      repository,
      run_mode: runMode,
      resource_policy: "balanced",
      jobs,
      fasta_preference: "project",
      ui_language: "zh-CN",
      input_records: inputRecords,
      delete_source_files_after_success: deleteSourcesAfterSuccess,
      source_discovery_job_id: initialSource?.jobId || undefined,
      source_discovery_id: initialSource?.discoveryId || undefined,
      source_batch_index: initialSource?.batchIndex || undefined,
    };
    setBusy(true);
    setError("");
    try {
      const check = await preflight(payload);
      if (check.status === "blocked") throw new Error((check.blocking_issues || ["Preflight blocked"]).join("；"));
      const next = await startBatch(payload);
      onBatchId(next.batch_id);
      setRecord(next);
      onChanged?.();
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  };

  const items = Array.isArray(record?.items) ? (record?.items as WorkflowRecord[]) : [];
  const summary = (record?.summary as WorkflowRecord | undefined) || {};
  const completed = Number(record?.completed_items ?? summary.completed ?? items.filter((i) => i.status === "completed").length);
  const failed = Number(record?.failed_items ?? summary.failed ?? items.filter((i) => i.status === "failed").length);
  const running = Number(record?.running_items ?? summary.running ?? items.filter((i) => i.status === "running").length);
  const queued = Number(record?.queued_items ?? summary.queued ?? items.filter((i) => i.status === "queued" || !i.status).length);
  const needsReview = Number(record?.needs_review_items ?? summary.needs_review ?? 0);
  const total = Number(record?.item_count ?? items.length) || 0;
  const percent = Number(summary.percent ?? (total ? ((completed + failed + needsReview) * 100) / total : 0));
  const focusMessage = String(summary.focus_message || "");
  const cleanupReleased = Number(summary.source_cleanup_released_bytes || 0);
  const cleanupCompleted = Number(summary.source_cleanup_completed || 0);
  const cleanupFailed = Number(summary.source_cleanup_failed || 0);

  const filteredItems = useMemo(() => {
    if (filter === "all") return items;
    return items.filter((item) => {
      const status = String(item.status || "queued");
      if (filter === "queued") return status === "queued" || status === "pending" || !status;
      return status === filter;
    });
  }, [items, filter]);

  return (
    <div className="workspace-stack workflow-panel">
      {error && <InlineNotification kind="error" title="批量任务失败" subtitle={error} onCloseButtonClick={() => setError("")} />}
      {seedNotice && (
        <InlineNotification
          kind="success"
          lowContrast
          title="已送入批量参数规划"
          subtitle={seedNotice}
          onCloseButtonClick={() => setSeedNotice("")}
        />
      )}
      <Tile>
        <div className="panel-heading">
          <div>
            <p className="eyebrow">B3 · BATCH EXCEL</p>
            <h2>批量参数规划与运行</h2>
            <p className="empty-copy">
              每行一个项目、文件或 URL；沿用原工作流的 Preflight、并行任务和 Excel/Audit 导出。发现页「送入批量参数规划」会预填可用文件列表。
            </p>
          </div>
        </div>
        <TextArea
          id="batch-inputs"
          rows={7}
          labelText="批量输入"
          helperText="空行和以 # 开头的行会被忽略。来自发现 L1 的列表会自动填入。"
          value={inputs}
          onChange={(event) => setInputs(event.target.value)}
        />
        <div className="form-grid form-grid--four">
          <TextInput id="batch-submitter" labelText="提交人" value={submitter} onChange={(event) => setSubmitter(event.target.value)} />
          <Select id="batch-repository" labelText="Repository" value={repository} onChange={(event) => setRepository(event.target.value)}>
            <SelectItem value="pride" text="PRIDE" />
            <SelectItem value="iprox" text="iProX" />
          </Select>
          <Select id="batch-run-mode" labelText="运行模式" value={runMode} onChange={(event) => setRunMode(event.target.value)}>
            <SelectItem value="parameters" text="只推断参数" />
            <SelectItem value="prepare" text="准备输入包" />
            <SelectItem value="full" text="完整工作流" />
          </Select>
          <NumberInput id="batch-jobs" label="并行数" min={1} max={16} value={jobs} onChange={(_event, state) => setJobs(Number(state.value || 1))} />
        </div>
        {initialSource?.batchIndex ? (
          <p className="empty-copy">
            来源：发现任务 {initialSource.jobId || initialSource.discoveryId || "—"} · 文件批次 {initialSource.batchIndex}
          </p>
        ) : null}
        <Checkbox
          id="batch-delete-source-files"
          labelText="每个文件处理成功后删除本地源文件"
          helperText="只删除本批次目录内的下载和转换文件；失败、需复核和取消项保留，结果与审计不删除。"
          checked={deleteSourcesAfterSuccess}
          onChange={(_event, state) => setDeleteSourcesAfterSuccess(Boolean(state.checked))}
        />
        <Button renderIcon={Play} disabled={busy || !inputs.trim()} onClick={() => void start()}>
          启动批量任务
        </Button>
      </Tile>

      {record && (
        <Tile>
          <div className="panel-heading">
            <div>
              <p className="eyebrow">BATCH {batchId}</p>
              <h2>批量监控</h2>
            </div>
            <Tag type={statusTagType(String(record.status || "unknown"))}>{String(record.status || "unknown")}</Tag>
          </div>

          <div className="batch-status-bar" style={{ display: "grid", gap: "0.75rem", marginBottom: "1rem" }}>
            <ProgressBar
              label="整批进度"
              helperText={`${completed + failed + needsReview}/${total || 0} 已结束 · 运行 ${running} · 排队 ${queued} · 失败 ${failed}`}
              value={Number.isFinite(percent) ? Math.max(0, Math.min(100, percent)) : 0}
              max={100}
              size="big"
            />
            <div className="metric-strip">
              <span>
                项目数<strong>{String(total)}</strong>
              </span>
              <span>
                已完成<strong>{String(completed)}</strong>
              </span>
              <span>
                运行中<strong>{String(running)}</strong>
              </span>
              <span>
                排队<strong>{String(queued)}</strong>
              </span>
              <span>
                失败<strong>{String(failed)}</strong>
              </span>
              {needsReview > 0 && (
                <span>
                  需复核<strong>{String(needsReview)}</strong>
                </span>
              )}
            </div>
            {focusMessage && (
              <p className="empty-copy" style={{ margin: 0 }}>
                <strong>当前焦点：</strong>
                {focusMessage}
              </p>
            )}
            {Boolean(record.delete_source_files_after_success) && (
              <p className="empty-copy" style={{ margin: 0 }}>
                <strong>源文件清理：</strong>
                已清理 {cleanupCompleted} 项 · 释放 {formatBytes(cleanupReleased)}
                {cleanupFailed ? ` · 清理失败 ${cleanupFailed} 项` : ""}
              </p>
            )}
          </div>

          <div className="button-row" style={{ flexWrap: "wrap", gap: "0.5rem" }}>
            <Button kind="tertiary" renderIcon={Renew} onClick={() => void getBatch(batchId).then(setRecord).catch((reason) => setError(String(reason)))}>
              刷新
            </Button>
            {Boolean(record.can_download) && (
              <Button kind="secondary" renderIcon={Download} href={`/api/batches/${encodeURIComponent(batchId)}/download`}>
                下载 Excel
              </Button>
            )}
            {Boolean(record.can_download_audit) && (
              <Button kind="tertiary" href={`/api/batches/${encodeURIComponent(batchId)}/audit.zip`}>
                下载 Audit ZIP
              </Button>
            )}
            <Select id="batch-filter" labelText="筛选" size="sm" value={filter} onChange={(e) => setFilter(e.target.value as typeof filter)}>
              <SelectItem value="all" text="全部" />
              <SelectItem value="running" text="运行中" />
              <SelectItem value="queued" text="排队" />
              <SelectItem value="failed" text="失败" />
              <SelectItem value="completed" text="已完成" />
            </Select>
          </div>

          {filteredItems.length > 0 && (
            <div className="run-list" style={{ display: "grid", gap: "0.65rem", marginTop: "1rem" }}>
              {filteredItems.map((item, index) => {
                const key = String(item.index ?? item.task_id ?? item.input ?? index);
                const status = String(item.status || "queued");
                const progress = (item.progress || {}) as BatchProgress;
                const download = progress.download;
                const open = Boolean(expanded[key]);
                const title = shortName(String(item.input || item.input_value || `Item ${index + 1}`));
                const stageLabel = String(progress.stage_label || progress.stage || status);
                const message = String(progress.message || item.error_summary || item.error || "");
                const pct =
                  download && download.total_bytes
                    ? Math.max(0, Math.min(100, Number(progress.percent ?? ((Number(download.downloaded_bytes || 0) / Number(download.total_bytes)) * 100))))
                    : progress.percent != null
                      ? Number(progress.percent)
                      : null;
                return (
                  <div
                    key={key}
                    style={{
                      border: "1px solid var(--cds-border-subtle-01, #e0e0e0)",
                      borderRadius: 8,
                      padding: "0.75rem",
                      background: "var(--cds-layer-01, #fff)",
                    }}
                  >
                    <div style={{ display: "flex", gap: "0.75rem", alignItems: "flex-start", justifyContent: "space-between" }}>
                      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", minWidth: 0, flex: 1 }}>
                        <Tag size="sm" type={statusTagType(status)}>
                          {status}
                        </Tag>
                        <div style={{ minWidth: 0 }}>
                          <div style={{ fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            #{String(item.index ?? index + 1)} · {title}
                          </div>
                          <div className="empty-copy" style={{ margin: 0 }}>
                            {stageLabel}
                            {message ? ` · ${message}` : ""}
                          </div>
                        </div>
                      </div>
                      <Button
                        kind="ghost"
                        size="sm"
                        onClick={() => setExpanded((prev) => ({ ...prev, [key]: !prev[key] }))}
                      >
                        {open ? "收起" : "详情"}
                      </Button>
                    </div>

                    {(status === "running" || status === "failed" || pct != null) && (
                      <div style={{ marginTop: "0.5rem" }}>
                        <ProgressBar
                          label={download ? `下载 ${download.label || ""}` : stageLabel}
                          helperText={
                            download
                              ? `${formatBytes(download.downloaded_bytes)} / ${download.total_bytes ? formatBytes(download.total_bytes) : "?"} · ${formatSpeed(download.speed_bps)} · ETA ${formatEta(download.eta_seconds)}`
                              : message || status
                          }
                          value={pct == null ? undefined : pct}
                          max={100}
                          size="small"
                          status={status === "failed" ? "error" : status === "completed" ? "finished" : "active"}
                        />
                      </div>
                    )}

                    {status === "failed" && (
                      <p className="empty-copy" style={{ color: "var(--cds-support-error, #da1e28)", margin: "0.35rem 0 0" }}>
                        错误：{String(item.error_summary || item.error || "未知错误")}
                        {item.failed_stage || progress.failed_stage ? `（阶段：${String(item.failed_stage || progress.failed_stage)}）` : ""}
                      </p>
                    )}

                    {open && (
                      <div style={{ marginTop: "0.75rem" }}>
                        <div className="empty-copy" style={{ marginBottom: "0.35rem" }}>
                          输入：{String(item.input || "")}
                        </div>
                        {Array.isArray(item.log_tail) && item.log_tail.length > 0 ? (
                          <pre
                            className="json-view"
                            style={{ maxHeight: 220, overflow: "auto", fontSize: 12, whiteSpace: "pre-wrap" }}
                          >
                            {(item.log_tail as string[]).slice(-30).join("\n")}
                          </pre>
                        ) : (
                          <p className="empty-copy">暂无日志尾</p>
                        )}
                        {item.error && (
                          <pre className="json-view" style={{ maxHeight: 120, overflow: "auto", fontSize: 12, whiteSpace: "pre-wrap" }}>
                            {String(item.error)}
                          </pre>
                        )}
                        {Boolean(item.source_cleanup) && (
                          <p className="empty-copy">
                            源文件清理：{String((item.source_cleanup as WorkflowRecord).status || "unknown")}
                            {Number((item.source_cleanup as WorkflowRecord).released_bytes || 0) > 0
                              ? ` · 释放 ${formatBytes(Number((item.source_cleanup as WorkflowRecord).released_bytes || 0))}`
                              : ""}
                          </p>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          <details style={{ marginTop: "1rem" }}>
            <summary>批量事件与原始 JSON</summary>
            <pre className="json-view">{JSON.stringify(record, null, 2)}</pre>
          </details>
        </Tile>
      )}
    </div>
  );
}
