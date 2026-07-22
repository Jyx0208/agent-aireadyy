import { useCallback, useEffect, useState } from "react";
import { Button, InlineNotification, Tag, Tile } from "@carbon/react";
import { Download, Renew } from "@carbon/icons-react";

import { getHistory, type WorkflowRecord } from "./workflow-api";

type Props = {
  refreshKey?: number;
  onOpenTask: (id: string) => void;
  onOpenBatch: (id: string) => void;
};

const idOf = (item: WorkflowRecord) => String(item.task_id || item.batch_id || item.discovery_id || item.result_id || item.history_id || "");
const labelOf = (item: WorkflowRecord) => String(item.display_name || item.input_value || item.name || item.run_label || idOf(item) || "未命名运行");

export function HistoryPanel({ refreshKey, onOpenTask, onOpenBatch }: Props) {
  const [active, setActive] = useState<WorkflowRecord[]>([]);
  const [results, setResults] = useState<WorkflowRecord[]>([]);
  const [summary, setSummary] = useState<WorkflowRecord>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (refresh = false) => {
    setBusy(true); setError("");
    try { const payload = await getHistory(refresh); setActive(payload.active_tasks || []); setResults(payload.results || []); setSummary(payload.summary || {}); }
    catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  }, []);
  useEffect(() => { void load(false); }, [load, refreshKey]);

  const open = (item: WorkflowRecord) => {
    const id = idOf(item); if (!id) return;
    if (item.kind === "batch") onOpenBatch(id); else if (item.kind !== "discovery") onOpenTask(id);
  };
  const all = [...active, ...results];
  return <div className="workspace-stack workflow-panel">
    {error && <InlineNotification kind="error" title="读取运行历史失败" subtitle={error} onCloseButtonClick={() => setError("")} />}
    <Tile>
      <div className="panel-heading"><div><p className="eyebrow">OPERATIONS</p><h2>运行历史与结果</h2><p className="empty-copy">统一查看单文件、批量、Discovery 和 AI-ready 运行；历史来自服务端，不依赖浏览器缓存。</p></div><Button kind="tertiary" renderIcon={Renew} disabled={busy} onClick={() => void load(true)}>刷新</Button></div>
      <div className="metric-strip"><span>总计<strong>{String(summary.total || all.length)}</strong></span><span>运行中<strong>{String(summary.active || active.length)}</strong></span><span>失败<strong>{String(summary.failed || 0)}</strong></span><span>可下载<strong>{String(summary.downloadable || 0)}</strong></span></div>
      {all.length ? <div className="history-list">{all.map((item, index) => {
        const id = idOf(item); const kind = String(item.kind || "task"); const downloadable = Boolean(item.can_download);
        const download = kind === "batch" ? `/api/batches/${encodeURIComponent(id)}/download` : kind === "discovery" ? `/api/discovery/${encodeURIComponent(String(item.discovery_id || id))}/download?file=discovery_run_bundle_zip` : `/api/results/${encodeURIComponent(String(item.result_id || id))}/download`;
        return <div className="history-row" key={`${kind}:${id}:${index}`}><div><div className="history-title">{labelOf(item)}</div><div className="history-meta">{kind} · {String(item.submitter || "未填写提交人")} · {String(item.status || "unknown")}</div></div><div className="button-row"><Tag size="sm" type={item.status === "failed" ? "red" : item.status === "completed" ? "green" : "blue"}>{String(item.status || "unknown")}</Tag>{kind !== "discovery" && <Button size="sm" kind="ghost" onClick={() => open(item)}>打开</Button>}{downloadable && <Button size="sm" kind="ghost" renderIcon={Download} href={download}>下载</Button>}</div></div>;
      })}</div> : <p className="empty-copy">暂无任务。可以先从数据发现、单文件或批量工作流开始。</p>}
    </Tile>
  </div>;
}
