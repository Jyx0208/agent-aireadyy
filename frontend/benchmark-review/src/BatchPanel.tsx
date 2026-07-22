import { useEffect, useState } from "react";
import { Button, InlineNotification, NumberInput, Select, SelectItem, Tag, TextArea, TextInput, Tile } from "@carbon/react";
import { Download, Play, Renew } from "@carbon/icons-react";

import { getBatch, preflight, startBatch, terminalWorkflowStatus, type WorkflowRecord } from "./workflow-api";

type Props = { batchId: string; onBatchId: (batchId: string) => void; onChanged?: () => void };

export function BatchPanel({ batchId, onBatchId, onChanged }: Props) {
  const [inputs, setInputs] = useState("");
  const [submitter, setSubmitter] = useState("local-user");
  const [repository, setRepository] = useState("pride");
  const [runMode, setRunMode] = useState("parameters");
  const [jobs, setJobs] = useState(3);
  const [record, setRecord] = useState<WorkflowRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!batchId) { setRecord(null); return; }
    const controller = new AbortController();
    const load = async () => {
      try { setRecord(await getBatch(batchId, controller.signal)); }
      catch (reason) { if (!(reason instanceof DOMException && reason.name === "AbortError")) setError(String(reason)); }
    };
    void load();
    if (terminalWorkflowStatus(record?.status)) return () => controller.abort();
    const timer = window.setInterval(() => void load(), 1500);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [batchId, record?.status]);

  const start = async () => {
    const items = inputs.split(/\r?\n/).map((value) => value.trim()).filter((value) => value && !value.startsWith("#"));
    if (!items.length || !submitter.trim()) { setError("请至少填写一个批量输入和提交人。"); return; }
    const payload = { inputs: items, submitter: submitter.trim(), repository, run_mode: runMode, resource_policy: "balanced", jobs, fasta_preference: "project", ui_language: "zh-CN", input_records: [] };
    setBusy(true); setError("");
    try {
      const check = await preflight(payload);
      if (check.status === "blocked") throw new Error((check.blocking_issues || ["Preflight blocked"]).join("；"));
      const next = await startBatch(payload);
      onBatchId(next.batch_id); setRecord(next); onChanged?.();
    } catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  };

  const items = Array.isArray(record?.items) ? record.items as WorkflowRecord[] : [];
  return <div className="workspace-stack workflow-panel">
    {error && <InlineNotification kind="error" title="批量任务失败" subtitle={error} onCloseButtonClick={() => setError("")} />}
    <Tile>
      <div className="panel-heading"><div><p className="eyebrow">B3 · BATCH EXCEL</p><h2>批量参数规划与运行</h2><p className="empty-copy">每行一个项目、文件或 URL；沿用原工作流的 Preflight、并行任务和 Excel/Audit 导出。</p></div></div>
      <TextArea id="batch-inputs" rows={7} labelText="批量输入" helperText="空行和以 # 开头的行会被忽略。" value={inputs} onChange={(event) => setInputs(event.target.value)} />
      <div className="form-grid form-grid--four">
        <TextInput id="batch-submitter" labelText="提交人" value={submitter} onChange={(event) => setSubmitter(event.target.value)} />
        <Select id="batch-repository" labelText="Repository" value={repository} onChange={(event) => setRepository(event.target.value)}><SelectItem value="pride" text="PRIDE"/><SelectItem value="iprox" text="iProX"/></Select>
        <Select id="batch-run-mode" labelText="运行模式" value={runMode} onChange={(event) => setRunMode(event.target.value)}><SelectItem value="parameters" text="只推断参数"/><SelectItem value="prepare" text="准备输入包"/><SelectItem value="full" text="完整工作流"/></Select>
        <NumberInput id="batch-jobs" label="并行数" min={1} max={16} value={jobs} onChange={(_event, state) => setJobs(Number(state.value || 1))}/>
      </div>
      <Button renderIcon={Play} disabled={busy || !inputs.trim()} onClick={() => void start()}>启动批量任务</Button>
    </Tile>
    {record && <Tile>
      <div className="panel-heading"><div><p className="eyebrow">BATCH {batchId}</p><h2>批量监控</h2></div><Tag type={record.status === "failed" ? "red" : record.status === "completed" ? "green" : "blue"}>{String(record.status || "unknown")}</Tag></div>
      <div className="metric-strip"><span>项目数<strong>{String(record.item_count ?? items.length)}</strong></span><span>已完成<strong>{String(record.completed_count ?? items.filter((item) => item.status === "completed").length)}</strong></span><span>失败<strong>{String(record.failed_count ?? items.filter((item) => item.status === "failed").length)}</strong></span></div>
      <div className="button-row"><Button kind="tertiary" renderIcon={Renew} onClick={() => void getBatch(batchId).then(setRecord).catch((reason) => setError(String(reason)))}>刷新</Button>{Boolean(record.can_download) && <Button kind="secondary" renderIcon={Download} href={`/api/batches/${encodeURIComponent(batchId)}/download`}>下载 Excel</Button>}{Boolean(record.can_download_audit) && <Button kind="tertiary" href={`/api/batches/${encodeURIComponent(batchId)}/audit.zip`}>下载 Audit ZIP</Button>}</div>
      {items.length > 0 && <div className="run-list">{items.map((item, index) => <div key={String(item.task_id || item.input || index)}><Tag size="sm" type={item.status === "failed" ? "red" : item.status === "completed" ? "green" : "gray"}>{String(item.status || "queued")}</Tag><span>{String(item.input || item.input_value || `Item ${index + 1}`)}</span></div>)}</div>}
      <details><summary>批量事件与详情</summary><pre className="json-view">{JSON.stringify(record, null, 2)}</pre></details>
    </Tile>}
  </div>;
}
