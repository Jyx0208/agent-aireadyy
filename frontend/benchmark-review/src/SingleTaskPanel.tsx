import { useEffect, useMemo, useState } from "react";
import {
  Button,
  InlineNotification,
  ProgressBar,
  Select,
  SelectItem,
  Tag,
  TextInput,
  Tile,
} from "@carbon/react";
import { Download, Play, Renew } from "@carbon/icons-react";

import { getSingleTask, preflight, startSingleTask, submitTaskReview, terminalWorkflowStatus, type WorkflowRecord } from "./workflow-api";

type Props = { taskId: string; onTaskId: (taskId: string) => void; onChanged?: () => void };
type ReviewOption = { field?: string; label?: string; values?: string[] };

export function SingleTaskPanel({ taskId, onTaskId, onChanged }: Props) {
  const [input, setInput] = useState("");
  const [submitter, setSubmitter] = useState("local-user");
  const [repository, setRepository] = useState("pride");
  const [runMode, setRunMode] = useState("parameters");
  const [resourcePolicy, setResourcePolicy] = useState("balanced");
  const [record, setRecord] = useState<WorkflowRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reviewDraft, setReviewDraft] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!taskId) { setRecord(null); return; }
    const controller = new AbortController();
    const load = async () => {
      try { setRecord(await getSingleTask(taskId, controller.signal)); }
      catch (reason) { if (!(reason instanceof DOMException && reason.name === "AbortError")) setError(String(reason)); }
    };
    void load();
    if (terminalWorkflowStatus(record?.status)) return () => controller.abort();
    const timer = window.setInterval(() => void load(), 1500);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [taskId, record?.status]);

  const reviewOptions = useMemo(() => {
    const summary = record?.review_summary as { review_options?: ReviewOption[] } | undefined;
    return summary?.review_options || [];
  }, [record]);

  const start = async () => {
    if (!input.trim() || !submitter.trim()) { setError("请填写 PRIDE 项目/文件和提交人。"); return; }
    setBusy(true); setError("");
    const payload = {
      input_value: input.trim(), submitter: submitter.trim(), repository,
      fasta_preference: "project", reviewed_fasta: "", run_mode: runMode,
      resource_policy: resourcePolicy, ui_language: "zh-CN",
    };
    try {
      const check = await preflight(payload);
      if (check.status === "blocked") throw new Error((check.blocking_issues || ["Preflight blocked"]).join("；"));
      const next = await startSingleTask(payload);
      onTaskId(next.task_id); setRecord(next); onChanged?.();
    } catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  };

  const submitReview = async () => {
    if (!taskId) return;
    setBusy(true); setError("");
    try { setRecord(await submitTaskReview(taskId, reviewDraft)); onChanged?.(); }
    catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  };

  const step = Number(record?.step || 0);
  return <div className="workspace-stack workflow-panel">
    {error && <InlineNotification kind="error" title="单文件任务失败" subtitle={error} onCloseButtonClick={() => setError("")} />}
    <Tile>
      <div className="panel-heading"><div><p className="eyebrow">P2 · SINGLE FILE</p><h2>单文件处理</h2><p className="empty-copy">解析项目与文件，推断搜索参数，按需要准备输入包或运行完整流程。</p></div></div>
      <div className="form-grid form-grid--two">
        <TextInput id="single-input" labelText="PRIDE accession、文件名或 URL" value={input} onChange={(event) => setInput(event.target.value)} />
        <TextInput id="single-submitter" labelText="提交人" value={submitter} onChange={(event) => setSubmitter(event.target.value)} />
        <Select id="single-repository" labelText="Repository" value={repository} onChange={(event) => setRepository(event.target.value)}><SelectItem value="pride" text="PRIDE"/><SelectItem value="iprox" text="iProX"/></Select>
        <Select id="single-run-mode" labelText="运行模式" value={runMode} onChange={(event) => setRunMode(event.target.value)}><SelectItem value="parameters" text="只推断参数"/><SelectItem value="prepare" text="准备输入包"/><SelectItem value="full" text="完整工作流"/></Select>
        <Select id="single-resource-policy" labelText="资源策略" value={resourcePolicy} onChange={(event) => setResourcePolicy(event.target.value)}><SelectItem value="balanced" text="Balanced"/><SelectItem value="conservative" text="Conservative"/><SelectItem value="performance" text="Performance"/></Select>
      </div>
      <div className="button-row"><Button renderIcon={Play} disabled={busy || !input.trim()} onClick={() => void start()}>启动单文件任务</Button>{taskId && <Button kind="tertiary" renderIcon={Renew} onClick={() => void getSingleTask(taskId).then(setRecord).catch((reason) => setError(String(reason)))}>刷新</Button>}</div>
    </Tile>
    {record && <Tile>
      <div className="panel-heading"><div><p className="eyebrow">RUN {taskId}</p><h2>任务监控</h2></div><Tag type={record.status === "failed" ? "red" : record.status === "completed" ? "green" : "blue"}>{String(record.status || "unknown")}</Tag></div>
      <ProgressBar label={`阶段 ${step}/5`} value={Math.max(0, Math.min(100, step * 20))} max={100}/>
      <div className="metric-strip"><span>模式<strong>{String(record.run_mode || runMode)}</strong></span><span>队列位置<strong>{String(record.queue_position || "—")}</strong></span><span>可下载<strong>{record.can_download ? "是" : "否"}</strong></span></div>
      {reviewOptions.length > 0 && <div className="review-block"><h3>需要人工复核</h3><div className="form-grid form-grid--two">{reviewOptions.map((option) => <Select key={option.field} id={`review-${option.field}`} labelText={option.label || option.field || "选项"} value={reviewDraft[option.field || ""] || ""} onChange={(event) => setReviewDraft((value) => ({ ...value, [option.field || ""]: event.target.value }))}><SelectItem value="" text="请选择"/>{(option.values || []).map((value) => <SelectItem key={value} value={value} text={value}/>)}</Select>)}</div><Button size="sm" onClick={() => void submitReview()} disabled={busy || Object.keys(reviewDraft).length === 0}>提交复核并继续</Button></div>}
      <div className="button-row">{Boolean(record.can_download) && <Button kind="secondary" renderIcon={Download} href={`/api/tasks/${encodeURIComponent(taskId)}/download`}>下载结果 ZIP</Button>}<Button kind="tertiary" href={`/api/tasks/${encodeURIComponent(taskId)}/agent-audit`}>Agent 审计</Button></div>
      <details><summary>任务详情与日志</summary><pre className="json-view">{JSON.stringify(record, null, 2)}</pre></details>
    </Tile>}
  </div>;
}
