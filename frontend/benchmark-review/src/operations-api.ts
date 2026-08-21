import { workflowJson, type WorkflowRecord } from "./workflow-api";

export type OperationsProgress = {
  current_term: string;
  term_total: number;
  term_completed: number;
  raw_hit_count: number;
  candidate_count: number;
  reviewed_count: number;
  pending_review_count: number;
  qualified_count: number;
  file_clue_count: number;
  usable_file_count: number;
  batch_count: number;
  worker_count: number;
};

export type OperationsError = {
  code?: string | null;
  message?: string | null;
};

export type OperationsJob = {
  job_id: string;
  job_type: string;
  status: string;
  phase: string;
  objective: string;
  repository: string;
  species: string;
  version: number;
  last_event_sequence: number;
  cancel_requested: boolean;
  resumable: boolean;
  progress: OperationsProgress;
  heartbeat_at?: string | null;
  created_at: string;
  started_at?: string | null;
  updated_at: string;
  finished_at?: string | null;
  archived_at?: string | null;
  error?: OperationsError | null;
  result?: WorkflowRecord;
};

export type DatasetConstructionRequest = {
  batch_dir: string;
  output_dir: string;
  release_id: string;
  task_spec: WorkflowRecord;
  ratios: [number, number, number];
  seed: number;
  policy?: WorkflowRecord;
  idempotency_key?: string;
};

export const submitDatasetConstructionJob = (payload: DatasetConstructionRequest) =>
  workflowJson<OperationsJob & WorkflowRecord>(
    "/api/ops/dataset-construction/jobs",
    { method: "POST", body: JSON.stringify(payload) },
  );

export const datasetArtifactUrl = (jobId: string, artifactKey: string) =>
  `/api/ops/jobs/${encodeURIComponent(jobId)}/artifacts/${encodeURIComponent(artifactKey)}`;

export type OperationsEvent = {
  id: number;
  sequence: number;
  job_id: string;
  type: string;
  level: string;
  actor: string;
  phase: string;
  message: string;
  payload: WorkflowRecord;
  created_at: string;
};

export type OperationsTerm = {
  position: number;
  term: string;
  role: string;
  status: string;
  cursor?: string | null;
  page_count: number;
  raw_count: number;
  unique_count: number;
  attempt_count: number;
  started_at?: string | null;
  updated_at: string;
  finished_at?: string | null;
  error?: OperationsError | null;
};

export type OperationsReview = {
  id: number;
  repository: string;
  accession: string;
  title: string;
  position: number;
  status: string;
  current_step: string;
  worker_slot?: number | null;
  decision: string;
  reason_code?: string | null;
  score?: number | null;
  confidence?: number | null;
  discovered_by_terms: string[];
  reasons: string[];
  evidence_summary: WorkflowRecord;
  metadata_summary: WorkflowRecord;
  file_clue_count: number;
  usable_file_count: number;
  elapsed_ms?: number | null;
  started_at?: string | null;
  updated_at: string;
  finished_at?: string | null;
};

export type OperationsFile = {
  id: number;
  repository: string;
  project_accession: string;
  native_id: string;
  file_name: string;
  logical_path: string;
  download_url: string;
  file_format: string;
  file_category: string;
  file_role: string;
  acquisition_mode: string;
  size_bytes?: number | null;
  status: string;
  eligible: boolean;
  reason_code?: string | null;
  reasons: string[];
  evidence: WorkflowRecord;
  updated_at: string;
};

export type OperationsBatch = {
  batch_id: string;
  job_id: string;
  batch_index: number;
  status: string;
  file_count: number;
  project_count: number;
  cumulative_file_count: number;
  checksum: string;
  terminal: boolean;
  created_at: string;
};

export type OperationsWorker = {
  slot: number;
  status: "busy" | "idle";
  project_accession?: string | null;
  step: string;
  started_at?: string | null;
};

export type OperationsHistoryItem = {
  history_id: string;
  kind: string;
  source_id: string;
  job_id?: string | null;
  status: string;
  status_group: string;
  display_name: string;
  objective: string;
  repository: string;
  species: string;
  project_count: number;
  file_count: number;
  size_bytes: number;
  open_available: boolean;
  deletable: boolean;
  metadata: WorkflowRecord;
  created_at: string;
  updated_at: string;
  archived_at?: string | null;
  deleted_at?: string | null;
};

export type OperationsPage<T> = {
  items: T[];
  page: number;
  page_size: number;
  total: number;
  pages: number;
  has_previous: boolean;
  has_next: boolean;
  summary?: WorkflowRecord;
};

const params = (values: Record<string, string | number | boolean | null | undefined>) => {
  const query = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") return;
    query.set(key, String(value));
  });
  return query.toString();
};

export const getOperationsJob = (jobId: string, signal?: AbortSignal) =>
  workflowJson<OperationsJob & WorkflowRecord>(
    `/api/ops/jobs/${encodeURIComponent(jobId)}`,
    { signal },
  );

export const getOperationsEvents = (
  jobId: string,
  after: number,
  limit = 200,
  signal?: AbortSignal,
) =>
  workflowJson<WorkflowRecord & { items: OperationsEvent[]; last_event_sequence: number }>(
    `/api/ops/jobs/${encodeURIComponent(jobId)}/events/page?${params({ after, limit })}`,
    { signal },
  );

export const getOperationsTerms = (jobId: string, signal?: AbortSignal) =>
  workflowJson<WorkflowRecord & { items: OperationsTerm[]; total: number }>(
    `/api/ops/jobs/${encodeURIComponent(jobId)}/terms`,
    { signal },
  );

export const getOperationsReviews = (
  jobId: string,
  options: {
    page: number;
    pageSize: number;
    status?: string;
    decision?: string;
    query?: string;
    sort?: string;
    direction?: "asc" | "desc";
  },
  signal?: AbortSignal,
) =>
  workflowJson<OperationsPage<OperationsReview> & WorkflowRecord>(
    `/api/ops/jobs/${encodeURIComponent(jobId)}/reviews?${params({
      page: options.page,
      page_size: options.pageSize,
      status: options.status,
      decision: options.decision,
      query: options.query,
      sort: options.sort,
      direction: options.direction,
    })}`,
    { signal },
  );

export const getOperationsWorkers = (jobId: string, signal?: AbortSignal) =>
  workflowJson<WorkflowRecord & { items: OperationsWorker[]; total: number; busy: number }>(
    `/api/ops/jobs/${encodeURIComponent(jobId)}/workers`,
    { signal },
  );

export const getOperationsFiles = (
  jobId: string,
  options: {
    page: number;
    pageSize: number;
    eligible?: boolean | null;
    projectAccession?: string;
    query?: string;
    sort?: string;
    direction?: "asc" | "desc";
  },
  signal?: AbortSignal,
) =>
  workflowJson<OperationsPage<OperationsFile> & WorkflowRecord>(
    `/api/ops/jobs/${encodeURIComponent(jobId)}/files?${params({
      page: options.page,
      page_size: options.pageSize,
      eligible: options.eligible,
      project_accession: options.projectAccession,
      query: options.query,
      sort: options.sort,
      direction: options.direction,
    })}`,
    { signal },
  );

export const getOperationsBatches = (jobId: string, signal?: AbortSignal) =>
  workflowJson<WorkflowRecord & { items: OperationsBatch[]; total: number }>(
    `/api/ops/jobs/${encodeURIComponent(jobId)}/batches`,
    { signal },
  );

export const cancelOperationsJob = (jobId: string) =>
  workflowJson<OperationsJob & WorkflowRecord>(
    `/api/ops/jobs/${encodeURIComponent(jobId)}/cancel`,
    { method: "POST" },
  );

export const resumeOperationsJob = (jobId: string) =>
  workflowJson<OperationsJob & WorkflowRecord>(
    `/api/ops/jobs/${encodeURIComponent(jobId)}/resume`,
    { method: "POST" },
  );

export const getOperationsHistory = (
  options: {
    page: number;
    pageSize: number;
    statusGroup?: string;
    kind?: string;
    query?: string;
    archived?: boolean;
    trash?: boolean;
    sort?: string;
    direction?: "asc" | "desc";
  },
  signal?: AbortSignal,
) =>
  workflowJson<OperationsPage<OperationsHistoryItem> & WorkflowRecord>(
    `/api/ops/history?${params({
      page: options.page,
      page_size: options.pageSize,
      status_group: options.statusGroup,
      kind: options.kind,
      query: options.query,
      archived: options.archived,
      trash: options.trash,
      sort: options.sort,
      direction: options.direction,
    })}`,
    { signal },
  );

export const archiveOperationsHistory = (historyId: string, archived: boolean) =>
  workflowJson<OperationsHistoryItem & WorkflowRecord>(
    `/api/ops/history/${encodeURIComponent(historyId)}/archive?${params({ archived })}`,
    { method: "POST" },
  );

export const markOperationsHistoryDeleted = (
  historyId: string,
  releasedBytes: number,
) =>
  workflowJson<OperationsHistoryItem & WorkflowRecord>(
    `/api/ops/history/${encodeURIComponent(historyId)}/deleted?${params({
      released_bytes: releasedBytes,
    })}`,
    { method: "POST" },
  );

export const operationsEventUrl = (jobId: string, after: number) =>
  `/api/ops/jobs/${encodeURIComponent(jobId)}/events?${params({ after })}`;

export const operationsTerminal = (status: unknown) =>
  ["completed", "failed", "blocked", "cancelled"].includes(
    String(status || "").toLowerCase(),
  );
