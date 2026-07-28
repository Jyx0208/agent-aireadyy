import { useCallback, useEffect, useState } from "react";
import {
  Button,
  Checkbox,
  ComposedModal,
  InlineNotification,
  ModalBody,
  ModalFooter,
  ModalHeader,
  Tag,
  Tile,
} from "@carbon/react";
import { Download, Renew, TrashCan } from "@carbon/icons-react";

import {
  deleteHistoryItem,
  getHistory,
  previewHistoryDelete,
  type WorkflowRecord,
} from "./workflow-api";

type Props = {
  refreshKey?: number;
  onOpenTask: (id: string) => void;
  onOpenBatch: (id: string) => void;
  onOpenDiscovery: (item: WorkflowRecord) => void | Promise<void>;
};

const idOf = (item: WorkflowRecord) => String(item.task_id || item.batch_id || item.discovery_id || item.result_id || item.history_id || "");
const labelOf = (item: WorkflowRecord) => String(item.display_name || item.input_value || item.name || item.run_label || idOf(item) || "未命名运行");
const formatBytes = (value: unknown) => {
  let n = Number(value || 0);
  if (!Number.isFinite(n) || n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let unit = 0;
  while (n >= 1024 && unit < units.length - 1) {
    n /= 1024;
    unit += 1;
  }
  return `${unit === 0 || n >= 10 ? n.toFixed(0) : n.toFixed(1)} ${units[unit]}`;
};

export function HistoryPanel({ refreshKey, onOpenTask, onOpenBatch, onOpenDiscovery }: Props) {
  const [active, setActive] = useState<WorkflowRecord[]>([]);
  const [results, setResults] = useState<WorkflowRecord[]>([]);
  const [summary, setSummary] = useState<WorkflowRecord>({});
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [deleteItem, setDeleteItem] = useState<WorkflowRecord | null>(null);
  const [deletePreview, setDeletePreview] = useState<WorkflowRecord | null>(null);
  const [includeLinked, setIncludeLinked] = useState(false);

  const load = useCallback(async (refresh = false) => {
    setBusy(true);
    setError("");
    try {
      const payload = await getHistory(refresh);
      setActive(payload.active_tasks || []);
      setResults(payload.results || []);
      setSummary(payload.summary || {});
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }, []);
  useEffect(() => { void load(false); }, [load, refreshKey]);

  const open = (item: WorkflowRecord) => {
    const id = idOf(item);
    if (!id) return;
    if (item.kind === "batch") onOpenBatch(String(item.batch_id || item.result_id || id));
    else if (item.kind === "discovery") void onOpenDiscovery(item);
    else onOpenTask(id);
  };

  const deleteIdentity = (item: WorkflowRecord) => {
    const kind = String(item.kind || "");
    const id = kind === "discovery"
      ? String(item.job_id || item.discovery_id || item.run_id || "")
      : String(item.batch_id || item.result_id || "");
    return { kind, id };
  };

  const showDelete = async (item: WorkflowRecord, linked = false) => {
    const { kind, id } = deleteIdentity(item);
    if (!kind || !id) return;
    setBusy(true);
    setError("");
    try {
      const preview = await previewHistoryDelete(kind, id, linked);
      setDeleteItem(item);
      setIncludeLinked(linked);
      setDeletePreview(preview);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  };

  const confirmDelete = async () => {
    if (!deleteItem || !deletePreview) return;
    const { kind, id } = deleteIdentity(deleteItem);
    setBusy(true);
    setError("");
    try {
      const result = await deleteHistoryItem(
        kind,
        id,
        String(deletePreview.confirmation_id || ""),
        includeLinked,
      );
      setNotice(`已释放 ${formatBytes(result.released_bytes)}；删除 ${Array.isArray(result.deleted) ? result.deleted.length : 0} 个任务。`);
      setDeleteItem(null);
      setDeletePreview(null);
      await load(true);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  };

  const all = [...active, ...results];
  return <div className="workspace-stack workflow-panel">
    {error && <InlineNotification kind="error" title="运行历史操作失败" subtitle={error} onCloseButtonClick={() => setError("")} />}
    {notice && <InlineNotification kind="success" title="磁盘空间已更新" subtitle={notice} onCloseButtonClick={() => setNotice("")} />}
    <Tile>
      <div className="panel-heading">
        <div>
          <p className="eyebrow">OPERATIONS</p>
          <h2>历史任务与磁盘空间</h2>
          <p className="empty-copy">重新打开发现与批量结果不会重跑任务；删除前会展示准确范围和预计释放空间。</p>
        </div>
        <Button kind="tertiary" renderIcon={Renew} disabled={busy} onClick={() => void load(true)}>刷新</Button>
      </div>
      <div className="metric-strip">
        <span>总计<strong>{String(summary.total || all.length)}</strong></span>
        <span>运行中<strong>{String(summary.active || active.length)}</strong></span>
        <span>失败<strong>{String(summary.failed || 0)}</strong></span>
        <span>磁盘占用<strong>{formatBytes(summary.storage_bytes)}</strong></span>
      </div>
      {all.length ? <div className="history-list">{all.map((item, index) => {
        const id = idOf(item);
        const kind = String(item.kind || "task");
        const downloadable = Boolean(item.can_download);
        const openAvailable = item.open_available !== false;
        const deletableKind = kind === "discovery" || kind === "batch";
        const download = kind === "batch"
          ? `/api/batches/${encodeURIComponent(String(item.batch_id || item.result_id || id))}/download`
          : kind === "discovery"
            ? `/api/discovery/${encodeURIComponent(String(item.discovery_id || id))}/download?file=discovery_run_bundle_zip`
            : `/api/results/${encodeURIComponent(String(item.result_id || id))}/download`;
        return <div className="history-row" key={`${kind}:${id}:${index}`}>
          <div>
            <div className="history-title">{labelOf(item)}</div>
            <div className="history-meta">
              {kind} · {String(item.status || "unknown")} · {formatBytes(item.size_bytes)}
              {item.project_count != null ? ` · ${String(item.project_count)} 项目` : ""}
              {item.file_count != null ? ` · ${String(item.file_count)} 文件` : ""}
              {item.result_available === false
                ? ` · 结果文件已不存在（历史记录曾报告 ${formatBytes(item.recorded_size_bytes)}）`
                : ""}
            </div>
          </div>
          <div className="button-row">
            <Tag size="sm" type={item.status === "failed" ? "red" : item.status === "completed" ? "green" : "blue"}>{String(item.status || "unknown")}</Tag>
            <Button size="sm" kind="ghost" disabled={!openAvailable} title={openAvailable ? "" : "结果文件已不存在"} onClick={() => open(item)}>打开</Button>
            {downloadable && <Button size="sm" kind="ghost" renderIcon={Download} href={download}>下载</Button>}
            {deletableKind && (
              <Button
                size="sm"
                kind="danger--ghost"
                renderIcon={TrashCan}
                disabled={!Boolean(item.deletable) || busy}
                title={String(item.delete_block_reason || "")}
                onClick={() => void showDelete(item)}
              >
                删除
              </Button>
            )}
          </div>
        </div>;
      })}</div> : <p className="empty-copy">暂无任务。可以先从数据发现、单文件或批量工作流开始。</p>}
    </Tile>

    {deleteItem && deletePreview ? (
      <ComposedModal open onClose={() => { setDeleteItem(null); setDeletePreview(null); }} size="sm">
        <ModalHeader label="DISK CLEANUP" title="确认删除历史任务" closeModal={() => { setDeleteItem(null); setDeletePreview(null); }} />
        <ModalBody>
          <p>将删除：{labelOf(deleteItem)}</p>
          <p><strong>预计释放 {formatBytes(deletePreview.estimated_bytes)}</strong></p>
          <p className="empty-copy">删除后任务结果不可恢复；用户原始文件和共享缓存不会被删除。</p>
          {deleteItem.kind === "discovery" ? (
            <Checkbox
              id="delete-linked-batches"
              labelText="同时删除由此发现批次创建的关联批量任务"
              checked={includeLinked}
              onChange={(_event, state) => void showDelete(deleteItem, Boolean(state.checked))}
            />
          ) : null}
          {Array.isArray(deletePreview.targets) ? (
            <ul>{(deletePreview.targets as WorkflowRecord[]).map((target, index) => (
              <li key={`${String(target.kind)}:${String(target.id)}:${index}`}>
                {String(target.kind)} {String(target.id)} · {formatBytes(target.size_bytes)}
              </li>
            ))}</ul>
          ) : null}
        </ModalBody>
        <ModalFooter>
          <Button kind="secondary" onClick={() => { setDeleteItem(null); setDeletePreview(null); }}>取消</Button>
          <Button kind="danger" disabled={busy || !Boolean(deletePreview.deletable)} onClick={() => void confirmDelete()}>确认删除</Button>
        </ModalFooter>
      </ComposedModal>
    ) : null}
  </div>;
}
