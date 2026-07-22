import { useCallback, useEffect, useState } from "react";
import { Button, InlineNotification, PasswordInput, Select, SelectItem, TextInput, Tile } from "@carbon/react";
import { Renew, Save } from "@carbon/icons-react";

import { withoutBlankSecret, workflowJson, type WorkflowRecord } from "./workflow-api";

type Draft = { provider: string; api_key: string; base_url: string; model: string; timeout: string };
const providerUrls: Record<string, string> = { openai: "https://api.openai.com/v1", deepseek: "https://api.deepseek.com", openrouter: "https://openrouter.ai/api/v1", siliconflow: "https://api.siliconflow.cn/v1", openai_compatible: "" };

export function SettingsPanel() {
  const [draft, setDraft] = useState<Draft>({ provider: "openai_compatible", api_key: "", base_url: "", model: "", timeout: "120" });
  const [savedKey, setSavedKey] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    try {
      const data = await workflowJson<WorkflowRecord & { api_key_set?: boolean; base_url?: string; model?: string; timeout?: string }>("/api/config");
      setSavedKey(Boolean(data.api_key_set));
      setDraft((value) => ({ ...value, api_key: "", base_url: String(data.base_url || ""), model: String(data.model || ""), timeout: String(data.timeout || "120") }));
    } catch (reason) { setError(String(reason)); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const action = async (kind: "models" | "check" | "save") => {
    setBusy(true); setError(""); setMessage("");
    const config = withoutBlankSecret(draft);
    try {
      if (kind === "models") {
        const data = await workflowJson<WorkflowRecord & { models?: string[]; selected?: string }>("/api/llm/models", { method: "POST", body: JSON.stringify({ llm_config: config }) });
        setModels(data.models || []); if (!draft.model && data.selected) setDraft((value) => ({ ...value, model: String(data.selected) })); setMessage(`已获取 ${(data.models || []).length} 个模型。`);
      } else if (kind === "check") {
        await workflowJson("/api/llm/check", { method: "POST", body: JSON.stringify({ llm_config: config }) }); setMessage("模型连接可用。");
      } else {
        await workflowJson("/api/llm/config", { method: "PUT", body: JSON.stringify({ llm_config: config }) }); setDraft((value) => ({ ...value, api_key: "" })); setMessage("运行模型配置已保存。"); await load();
      }
    } catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  };

  return <div className="workspace-stack settings-narrow">
    {error && <InlineNotification kind="error" title="模型配置失败" subtitle={error} onCloseButtonClick={() => setError("")} />}
    {message && <InlineNotification lowContrast kind="success" title={message} onCloseButtonClick={() => setMessage("")} />}
    <Tile>
      <p className="eyebrow">AGENT RUNTIME MODEL</p><h2>蛋白质组学 Agent 模型</h2><p className="empty-copy">供数据发现、参数推断、文件处理规划与 AI-ready 构建共同使用。API Key 只保存在服务端配置中。</p>
      <div className="form-grid form-grid--two">
        <Select id="runtime-provider" labelText="Provider / 协议" value={draft.provider} onChange={(event) => { const provider = event.target.value; setDraft((value) => ({ ...value, provider, base_url: Object.hasOwn(providerUrls, provider) ? providerUrls[provider] : value.base_url })); }}>{Object.keys(providerUrls).map((provider) => <SelectItem key={provider} value={provider} text={provider}/>)}</Select>
        <PasswordInput id="runtime-key" labelText={savedKey ? "API Key（留空保留已保存值）" : "API Key"} value={draft.api_key} onChange={(event) => setDraft({ ...draft, api_key: event.target.value })}/>
        <TextInput id="runtime-url" labelText="Base URL" value={draft.base_url} onChange={(event) => setDraft({ ...draft, base_url: event.target.value })}/>
        <TextInput id="runtime-timeout" labelText="Timeout" value={draft.timeout} onChange={(event) => setDraft({ ...draft, timeout: event.target.value })}/>
        <TextInput id="runtime-model" labelText="Model" value={draft.model} onChange={(event) => setDraft({ ...draft, model: event.target.value })}/>
        <Select id="runtime-models" labelText="接口返回模型" value={models.includes(draft.model) ? draft.model : ""} onChange={(event) => setDraft({ ...draft, model: event.target.value })}><SelectItem value="" text="手动填写或先获取"/>{models.map((model) => <SelectItem key={model} value={model} text={model}/>)}</Select>
      </div>
      <div className="button-row"><Button kind="secondary" renderIcon={Renew} disabled={busy} onClick={() => void action("models")}>获取模型</Button><Button kind="tertiary" disabled={busy || !draft.model} onClick={() => void action("check")}>检查连接</Button><Button renderIcon={Save} disabled={busy || !draft.base_url || !draft.model || (!savedKey && !draft.api_key)} onClick={() => void action("save")}>保存</Button></div>
    </Tile>
  </div>;
}
