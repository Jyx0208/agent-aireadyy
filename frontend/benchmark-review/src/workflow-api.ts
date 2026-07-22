import {
  decodeAgentTurnResponse,
  type AgentDialogueMessage,
  type AgentNextDecision,
  type AgentResolvedDecision,
  type AgentTurn,
} from "./agent-turn";

export type WorkflowRecord = Record<string, unknown> & {
  error?: string;
  ok?: boolean;
  status?: string;
};

/** Job lifecycle statuses — domain failures use status+error, not transport failures. */
export const DISCOVERY_JOB_STATUSES = ["queued", "running", "failed", "blocked", "cancelled", "completed"] as const;

export function isDiscoveryJobStatus(status: unknown): boolean {
  return DISCOVERY_JOB_STATUSES.includes(String(status || "").toLowerCase() as (typeof DISCOVERY_JOB_STATUSES)[number]);
}

export async function workflowJson<T extends WorkflowRecord>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(path, {
    ...options,
    headers: {
      ...(options.body != null ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });
  let payload: T;
  try {
    payload = (await response.json()) as T;
  } catch {
    throw new Error(`HTTP ${response.status}: invalid JSON response`);
  }
  // Discovery job bodies may carry status=failed with a domain error string.
  // That is a successful HTTP response of a job record — do not treat as transport failure.
  const jobOk =
    isDiscoveryJobStatus(payload.status) &&
    (path.includes("/api/discovery/jobs") || path.includes("/api/discovery/job"));
  if (!response.ok || payload.ok === false || (payload.error && !jobOk)) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

export const getHistory = (refresh = false, signal?: AbortSignal) =>
  workflowJson<WorkflowRecord & { active_tasks?: WorkflowRecord[]; results?: WorkflowRecord[]; summary?: WorkflowRecord }>(
    `/api/history?fast=${refresh ? "0" : "1"}${refresh ? "&refresh=1" : ""}`,
    { signal },
  );

export const preflight = (payload: WorkflowRecord) =>
  workflowJson<WorkflowRecord & { blocking_issues?: string[]; warnings?: string[] }>("/api/preflight", {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const startSingleTask = (payload: WorkflowRecord) =>
  workflowJson<WorkflowRecord & { task_id: string }>("/api/tasks", {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const getSingleTask = (taskId: string, signal?: AbortSignal) =>
  workflowJson<WorkflowRecord>(`/api/tasks/${encodeURIComponent(taskId)}`, { signal });

export const submitTaskReview = (taskId: string, overrides: Record<string, string>) =>
  workflowJson<WorkflowRecord>(`/api/tasks/${encodeURIComponent(taskId)}/review`, {
    method: "POST",
    body: JSON.stringify({ overrides }),
  });

export const startBatch = (payload: WorkflowRecord) =>
  workflowJson<WorkflowRecord & { batch_id: string }>("/api/batches/parameters", {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const getBatch = (batchId: string, signal?: AbortSignal) =>
  workflowJson<WorkflowRecord>(`/api/batches/${encodeURIComponent(batchId)}`, { signal });

export type DiscoveryJob = WorkflowRecord & {
  job_id?: string;
  logs?: WorkflowRecord[];
  record?: WorkflowRecord;
};

export type DiscoveryGoalParse = WorkflowRecord & {
  parser?: string;
  prompt?: string;
  fields?: WorkflowRecord;
  warnings?: string[];
  reasoning?: string;
};

/** Parse a free-text goal into structured discovery fields (LLM). Soft-fails to null caller-side. */
export const parseDiscoveryGoal = (
  prompt: string,
  current: WorkflowRecord = {},
  signal?: AbortSignal,
) =>
  workflowJson<DiscoveryGoalParse>("/api/discovery/parse-goal", {
    method: "POST",
    signal,
    body: JSON.stringify({
      prompt,
      output_language: "zh-CN",
      current: { output_language: "zh-CN", ...current },
    }),
  });

export type GrillTurnResult = AgentTurn;

/** One conversational grill turn (LLM phrasing + answer mapping). Soft-fails caller-side. */
export const grillTurn = async (
  payload: {
    user_message: string;
    phase?: string;
    turn_kind?: string;
    pending_question?: WorkflowRecord | null;
    pending_decision?: AgentNextDecision | null;
    decision_memory?: AgentResolvedDecision[];
    resolved_fields?: string[];
    session_id?: string;
    intent_snapshot?: WorkflowRecord;
    answered?: WorkflowRecord;
    local_summary?: string;
    allow_server_default?: boolean;
    gap_report?: WorkflowRecord | null;
    dialogue_history?: AgentDialogueMessage[];
  },
  signal?: AbortSignal,
) =>
  decodeAgentTurnResponse(await workflowJson<WorkflowRecord>("/api/discovery/grill-turn", {
    method: "POST",
    signal,
    body: JSON.stringify(payload),
  }));


/** Start discovery only with a full payload (Grill-confirmed). */
export const startDiscoveryJob = (payload: WorkflowRecord, signal?: AbortSignal) =>
  payload.grill_confirmed !== true
    ? Promise.reject(new Error("Explicit strategy confirmation is required before discovery."))
    : workflowJson<DiscoveryJob>("/api/discovery/jobs", {
        method: "POST",
        signal,
        body: JSON.stringify({
          runtime: "openai_agents",
          source: "remote",
          repository: "pride",
          output_language: "zh-CN",
          constraints_enabled: false,
          goal: "general",
          acquisition_mode: "unknown",
          labeling_strategy: "unknown",
          species: [],
          species_policy: "open",
          diversity_strategy: "balanced",
          use_memory: true,
          save_memory: true,
          hard_constraint_fields: ["repository"],
          constraint_provenance: { repository: "user" },
          idempotency_key: crypto.randomUUID(),
          ...payload,
        }),
      });

export const getDiscoveryJob = (jobId: string, detail = false, signal?: AbortSignal) =>
  workflowJson<DiscoveryJob>(`/api/discovery/jobs/${encodeURIComponent(jobId)}${detail ? "?detail=1" : ""}`, { signal });

export const cancelDiscoveryJob = (jobId: string) =>
  workflowJson<DiscoveryJob>(`/api/discovery/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });

export type AiReadyAction =
  | "profile-inputs"
  | "locate-inputs"
  | "build-from-agent-run"
  | "mini-e2e"
  | "validate-build";

export const runAiReady = (action: AiReadyAction, payload: WorkflowRecord) =>
  workflowJson<WorkflowRecord>(`/api/ai-ready/${action}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const terminalWorkflowStatus = (status: unknown) =>
  ["completed", "failed", "blocked", "cancelled"].includes(String(status || "").toLowerCase());

export const delay = (milliseconds: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const timeout = window.setTimeout(resolve, milliseconds);
    signal?.addEventListener(
      "abort",
      () => {
        window.clearTimeout(timeout);
        reject(new DOMException("Cancelled", "AbortError"));
      },
      { once: true },
    );
  });

export function withoutBlankSecret<T extends Record<string, unknown>>(payload: T): T {
  if (String(payload.api_key || "").trim()) return payload;
  const { api_key: _apiKey, ...rest } = payload;
  return rest as T;
}
