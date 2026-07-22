import { useState } from "react";
import { Button, InlineNotification, Select, SelectItem, TextArea, TextInput, Tile } from "@carbon/react";
import { DataBase, Play } from "@carbon/icons-react";

import { runAiReady, type AiReadyAction, type WorkflowRecord } from "./workflow-api";

const lines = (value: string) => value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);

export function AiReadyPanel({ onChanged }: { onChanged?: () => void }) {
  const [taskType, setTaskType] = useState("psm_scoring");
  const [searchDir, setSearchDir] = useState("");
  const [searchResults, setSearchResults] = useState("");
  const [peaklists, setPeaklists] = useState("");
  const [agentRunDir, setAgentRunDir] = useState("");
  const [buildDir, setBuildDir] = useState("");
  const [result, setResult] = useState<WorkflowRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const run = async (action: AiReadyAction) => {
    setBusy(true); setError("");
    const payload: WorkflowRecord = action === "validate-build"
      ? { build_dir: buildDir.trim(), task_type: taskType }
      : action === "build-from-agent-run" || action === "mini-e2e"
        ? { agent_run_dir: agentRunDir.trim(), task_types: [taskType], peaklists: lines(peaklists) }
        : { search_dir: searchDir.trim(), search_results: lines(searchResults), peaklists: lines(peaklists), task_types: [taskType] };
    try { setResult(await runAiReady(action, payload)); onChanged?.(); }
    catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  };

  const downloads = result?.downloads && typeof result.downloads === "object" ? result.downloads as Record<string, unknown> : {};
  return <div className="workspace-stack workflow-panel">
    {error && <InlineNotification kind="error" title="AI-ready 构建失败" subtitle={error} onCloseButtonClick={() => setError("")} />}
    <Tile>
      <div className="panel-heading"><div><p className="eyebrow">A4 · AI-READY BUILD</p><h2>构建训练数据</h2><p className="empty-copy">把搜索结果、Peaklist 或已完成的 Agent Run 转成任务表、数据集配方与可验证构建。</p></div></div>
      <div className="form-grid form-grid--two">
        <Select id="ai-task-type" labelText="下游任务" value={taskType} onChange={(event) => setTaskType(event.target.value)}><SelectItem value="psm_scoring" text="PSM scoring"/><SelectItem value="rt_prediction" text="RT prediction"/><SelectItem value="fragment_intensity_prediction" text="Fragment intensity"/><SelectItem value="denovo" text="De novo"/><SelectItem value="ptm_denovo" text="PTM de novo"/><SelectItem value="chimeric_interpretation" text="Chimeric interpretation"/></Select>
        <TextInput id="ai-search-dir" labelText="搜索结果目录" value={searchDir} onChange={(event) => setSearchDir(event.target.value)} />
        <TextArea id="ai-search-results" rows={4} labelText="搜索结果文件（每行一个）" value={searchResults} onChange={(event) => setSearchResults(event.target.value)} />
        <TextArea id="ai-peaklists" rows={4} labelText="Peaklist 文件（每行一个）" value={peaklists} onChange={(event) => setPeaklists(event.target.value)} />
        <TextInput id="ai-agent-run" labelText="Agent Run 目录" value={agentRunDir} onChange={(event) => setAgentRunDir(event.target.value)} />
        <TextInput id="ai-build-dir" labelText="已有 Build 目录" value={buildDir} onChange={(event) => setBuildDir(event.target.value)} />
      </div>
      <div className="button-row"><Button renderIcon={DataBase} disabled={busy || (!searchDir.trim() && !searchResults.trim())} onClick={() => void run("profile-inputs")}>分析输入</Button><Button kind="secondary" renderIcon={Play} disabled={busy || !agentRunDir.trim()} onClick={() => void run("build-from-agent-run")}>从 Agent Run 构建</Button><Button kind="tertiary" disabled={busy || !agentRunDir.trim()} onClick={() => void run("mini-e2e")}>Mini E2E 验证</Button><Button kind="tertiary" disabled={busy || !buildDir.trim()} onClick={() => void run("validate-build")}>验证现有 Build</Button></div>
    </Tile>
    {result && <Tile><div className="panel-heading"><div><p className="eyebrow">BUILD RESULT</p><h2>{String(result.status || result.ai_ready_outcome || "AI-ready 结果")}</h2></div></div><div className="button-row">{Object.entries(downloads).map(([name, url]) => typeof url === "string" && <Button key={name} kind="secondary" href={url}>{name}</Button>)}</div><details open><summary>构建摘要</summary><pre className="json-view">{JSON.stringify(result, null, 2)}</pre></details></Tile>}
  </div>;
}
