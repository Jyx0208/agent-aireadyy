import type { GenericItem, MessageResponse, MessageState } from "@carbon/ai-chat";
import { ProgressBar, Tag } from "@carbon/react";
import type { ReactNode } from "react";

import {
  CodexTimeline,
  isTimelineEvent,
  tl,
  type TimelineEvent,
} from "./CodexTimeline";
import { humanizeJobProgress } from "./grill-tree";
import {
  honestDiscoveryStatus,
  type DiscoveryJob,
  type WorkflowRecord,
} from "./workflow-api";

export type DiscoveryRunStatus = "queued" | "running" | "completed" | "failed" | "blocked" | "cancelled";

export type DiscoveryRunMetrics = {
  projects: number;
  repositoryHits: number;
  files: number;
  reviews: number;
  /** Verified downloadable files; primary delivery count for the workbench. */
  selectedProjects?: number;
  usableFiles: number;
  strictValidFiles: number;
  directUsableFiles?: number;
  inheritedUsableFiles?: number;
  pendingFiles?: number;
  inspectedProjects: number;
  judgmentQualifiedProjects: number;
  buildReadyProjects: number;
  buildReadyFiles: number;
  blockerCounts: Record<string, number>;
};

export type DiscoveryMilestone = {
  id: string;
  text: string;
};

export type DiscoveryRunProvenance = {
  authorityMode: string;
  authorityKeyId: string;
  builderPreflightStatus: string;
  builderPreflightRef: string;
};

export type VerifiedProjectBatchLink = {
  batchIndex: number;
  projectCount: number;
  fileCount: number;
  cumulativeFileCount?: number;
  deliveryUnit?: string;
  status?: string;
  publishedAt?: string;
  terminal?: boolean;
  downloadUrl: string;
};

export type SearchQueryTrace = {
  id: string;
  round: number;
  query: string;
  depth: number;
  startOffset: number;
  role: string;
  status: "planned" | "running" | "completed" | "failed" | "skipped";
  executedSeeds: string[];
  rawResultCount: number;
  newCandidateCount: number;
  pagesCompleted: number;
  maxPages: number;
  activeSeed: string;
  currentSeedResultCount: number;
  error: string;
  skipReason: string;
};

export type DiscoveryTermExecution = {
  term: string;
  termIndex: number;
  termCount: number;
  role: string;
  status: "pending" | "running" | "completed" | "failed";
  chunksCompleted: number;
  rawResultCount: number;
  newCandidateCount: number;
  exhausted: boolean;
  failureReason: string;
  reviewedProjectCount: number;
};

export type DiscoveryExecutionState = {
  phase: string;
  activeTermIndex: number;
  candidateCount: number;
  reviewedProjectCount: number;
  pendingReviewCount: number;
  reviewWorkers: number;
  allTermsExhausted: boolean;
  completionReady: boolean;
  terms: DiscoveryTermExecution[];
};

export type ProjectReviewTrace = {
  projectAccession: string;
  title: string;
  status: "queued" | "reading" | "inspected" | "included" | "investigate" | "excluded" | "failed";
  stage: string;
  detail: string;
  matchedIntentTerms: string[];
  queryHits: string[];
  species: string[];
  acquisitionMode: string;
  selectedFileCount: number;
  retrievalScore: number | null;
  grade: number | null;
  confidence: number | null;
  decision: string;
  explanation: string;
  evidenceRefs: string[];
  evidenceDetails: string[];
  missingInformation: string[];
  limitations: string[];
  steps: string[];
  rawFileCount: number;
  usableFileCount: number;
  excludedFileCount: number;
  fileRoleCounts: Record<string, number>;
  filterReasonCounts: Record<string, number>;
  lastEventSequence: number;
  concludedSequence: number;
};

export type DiscoveryRunView = {
  jobId: string;
  status: DiscoveryRunStatus;
  statusLabel: string;
  statusDetail: string;
  summary: string;
  headline: string;
  milestones: DiscoveryMilestone[];
  metrics: DiscoveryRunMetrics;
  provenance: DiscoveryRunProvenance;
  progressPercent: number | null;
  technicalEvents: TimelineEvent[];
  rawLogCount: number;
  discoveryId: string;
  error: string;
  qualityIssues: string[];
  resultBatches: VerifiedProjectBatchLink[];
  executionState: DiscoveryExecutionState | null;
  searchTrace: SearchQueryTrace[];
  reviewTrace: ProjectReviewTrace[];
  resumable: boolean;
};

export type DiscoveryProgressPayload = {
  kind: "discovery_progress";
  jobId: string;
  status: DiscoveryRunStatus;
  statusLabel: string;
  summary: string;
  headline: string;
  milestones: DiscoveryMilestone[];
  metrics: DiscoveryRunMetrics;
  provenance?: DiscoveryRunProvenance;
  progressPercent: number | null;
  technicalEvents: TimelineEvent[];
  rawLogCount: number;
  resultBatches?: VerifiedProjectBatchLink[];
  executionState?: DiscoveryExecutionState;
  searchTrace?: SearchQueryTrace[];
  reviewTrace?: ProjectReviewTrace[];
  resumable?: boolean;
};

const RUN_STATUSES = new Set<DiscoveryRunStatus>([
  "queued",
  "running",
  "completed",
  "failed",
  "blocked",
  "cancelled",
]);

const STATUS_LABEL: Record<DiscoveryRunStatus, string> = {
  queued: "已排队",
  running: "搜索中",
  completed: "已完成",
  failed: "失败",
  blocked: "已交付候选清单",
  cancelled: "已取消",
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  value != null && typeof value === "object" && !Array.isArray(value);

function normalizeStatus(value: unknown): DiscoveryRunStatus {
  const normalized = String(value || "queued").toLowerCase();
  if (normalized === "interrupted" || normalized === "durability_failed") return "failed";
  return RUN_STATUSES.has(normalized as DiscoveryRunStatus)
    ? (normalized as DiscoveryRunStatus)
    : "queued";
}

function readCount(value: unknown): number {
  if (value == null || value === "") return 0;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? Math.round(parsed) : 0;
}

function firstCount(...values: unknown[]): number {
  return readCount(values.find((value) => value != null && value !== ""));
}

function readCountMap(value: unknown): Record<string, number> {
  if (!isRecord(value)) return {};
  return Object.fromEntries(
    Object.entries(value)
      .map(([code, count]) => [code, readCount(count)] as const)
      .filter(([code, count]) => code.trim().length > 0 && count > 0),
  );
}

function readProgressPercent(value: unknown): number | null {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return null;
  return Math.max(0, Math.min(100, parsed));
}

function readOptionalText(value: unknown): string {
  return typeof value === "string" ? value.trim().slice(0, 500) : "";
}

function readTextList(value: unknown, limit = 30): string[] {
  return Array.isArray(value)
    ? value.map(readOptionalText).filter(Boolean).slice(0, limit)
    : [];
}

function readExecutionState(value: unknown): DiscoveryExecutionState | null {
  if (!isRecord(value) || value.schema_version !== "discovery-execution/v1") {
    return null;
  }
  const terms = Array.isArray(value.terms)
    ? value.terms.filter(isRecord).map((term) => ({
        term: readOptionalText(term.term),
        termIndex: readCount(term.term_index),
        termCount: readCount(term.term_count),
        role: readOptionalText(term.role),
        status: (
          ["pending", "running", "completed", "failed"].includes(String(term.status))
            ? String(term.status)
            : "pending"
        ) as DiscoveryTermExecution["status"],
        chunksCompleted: readCount(term.chunks_completed),
        rawResultCount: readCount(term.raw_result_count),
        newCandidateCount: readCount(term.new_candidate_count),
        exhausted: term.exhausted === true,
        failureReason: readOptionalText(term.failure_reason),
        reviewedProjectCount: readCount(term.reviewed_project_count),
      })).filter((term) => term.term.length > 0)
    : [];
  return {
    phase: readOptionalText(value.phase),
    activeTermIndex: readCount(value.active_term_index),
    candidateCount: readCount(value.candidate_count),
    reviewedProjectCount: readCount(value.reviewed_project_count),
    pendingReviewCount: readCount(value.pending_review_count),
    reviewWorkers: readCount(value.review_workers),
    allTermsExhausted: value.all_terms_exhausted === true,
    completionReady: value.completion_ready === true,
    terms,
  };
}

const SEARCH_ROLE_LABELS: Record<string, string> = {
  primary_theme: "核心主题",
  theme_synonym: "主题近义词",
  secondary_theme: "次级主题",
  general: "扩展检索词",
  exact_accession: "指定项目",
};

const FILE_ROLE_LABELS: Record<string, string> = {
  raw_acquisition: "原始采集文件",
  converted_peaklist: "转换峰表",
  search_result: "鉴定结果",
  report_table: "结果表/报告",
  metadata: "元数据",
  unknown: "未识别文件",
};

const FILTER_REASON_LABELS: Record<string, string> = {
  acquisition_hard_constraint_conflict: "采集模式与“仅 DDA”硬约束冲突",
  mixed_acquisition_project: "混合采集项目与“仅 DDA”硬约束冲突",
  file_name_assay_context_conflict: "文件名显示为非免疫肽实验",
  species_hard_constraint_conflict: "物种与仅限人类的硬约束冲突",
  labeling_hard_constraint_conflict: "标记方式与硬约束冲突",
  unsupported_file_type: "文件类型不受支持",
  unsupported_file_role: "不是采集文件或峰表",
};

function searchRoleLabel(role: string): string {
  return SEARCH_ROLE_LABELS[role] || role || "未分类";
}

function skipReasonLabel(reason: string): string {
  if (reason.startsWith("waiting_for_confirmed_theme:")) {
    const expectedTheme = reason.split(":", 2)[1] || "";
    return `已自动跳过越序检索；请先继续主题词“${expectedTheme}”，完成后再处理当前词`;
  }
  if (reason === "repository_seed_exhausted") return "这个精确检索词已经读到仓库末尾";
  if (reason === "repository_seed_already_searched_at_equal_or_greater_depth") {
    return "这个检索词此前已经读取到相同或更深位置";
  }
  if (reason === "search_request_budget_reserved_for_inspection") {
    return "本轮请求额度已预留给项目审查";
  }
  return reason;
}

function filterReasonLabel(reason: string): string {
  if (reason.startsWith("unsupported_file_role:")) {
    const role = reason.split(":", 2)[1] || "unknown";
    if (role === "report_table" || role === "search_result" || role === "metadata") {
      return "结果表或报告文件，不是采集/峰表";
    }
    return `${FILE_ROLE_LABELS[role] || role}，不是可交付的采集/峰表`;
  }
  return FILTER_REASON_LABELS[reason] || reason.replaceAll("_", " ");
}

function searchErrorLabel(error: string): string {
  const orderViolation = error.match(
    /open_ended_theme_order_violation:\s*expected\s+(.+?);\s*search/i,
  );
  if (orderViolation) {
    return `检索顺序保护触发，当前应处理主题词“${orderViolation[1]}”。旧版本会把重复提交的已耗尽词也判为失败；新版本已改为安全跳过。`;
  }
  return error;
}

function projectExplanation(item: ProjectReviewTrace): string {
  if (item.explanation !== "no usable acquisition/peaklist file candidates after filtering") {
    return item.explanation;
  }
  if (Object.keys(item.filterReasonCounts || {}).length) {
    return "没有文件通过采集/峰表筛选；逐项过滤原因见下方。";
  }
  return "没有文件通过采集/峰表筛选；这是旧版审查事件，未记录逐项过滤原因。";
}

function buildSearchTrace(job: DiscoveryJob): SearchQueryTrace[] {
  const trace: SearchQueryTrace[] = [];
  let active: SearchQueryTrace[] = [];
  let batchNumber = 0;
  for (const rawLog of job.logs || []) {
    if (!isRecord(rawLog)) continue;
    const type = readOptionalText(rawLog.type);
    const payload = isRecord(rawLog.payload) ? rawLog.payload : {};
    if (type === "candidate_search_started") {
      batchNumber += 1;
      const action = isRecord(payload.action) ? payload.action : {};
      const queries = Array.isArray(action.queries) ? action.queries.filter(isRecord) : [];
      active = queries.map((query, queryIndex) => ({
        id: `search-${batchNumber}-${queryIndex}-${readOptionalText(query.query)}`,
        round: batchNumber,
        query: readOptionalText(query.query),
        depth: readCount(query.depth),
        startOffset: 0,
        role: readOptionalText(query.budget_role) || "general",
        status: "planned" as const,
        executedSeeds: [],
        rawResultCount: 0,
        newCandidateCount: 0,
        pagesCompleted: 0,
        maxPages: 0,
        activeSeed: "",
        currentSeedResultCount: 0,
        error: "",
        skipReason: "",
      })).filter((query) => query.query.length > 0);
      trace.push(...active);
      continue;
    }
    if (type === "repository_theme_order_corrected") {
      const requested = readTextList(payload.requested_queries);
      const executed = readOptionalText(payload.executed_query);
      const query = active.find((item) => requested.includes(item.query))
        || [...active].reverse().find((item) => item.status === "planned");
      if (query && executed && !query.executedSeeds.includes(executed)) {
        query.executedSeeds.push(executed);
      }
      continue;
    }
    if (type.startsWith("repository_query_")) {
      const parentQuery = readOptionalText(payload.query);
      const query = active.find(
        (item) => item.query === parentQuery || item.executedSeeds.includes(parentQuery),
      );
      if (!query) continue;
      const seed = readOptionalText(payload.executed_query);
      if (seed && !query.executedSeeds.includes(seed)) query.executedSeeds.push(seed);
      query.depth = readCount(payload.depth) || query.depth;
      query.role = readOptionalText(payload.role) || query.role;
      if (type === "repository_query_started") {
        query.status = "running";
        query.activeSeed = seed;
        query.pagesCompleted = 0;
        query.maxPages = readCount(payload.max_pages);
        query.startOffset = readCount(payload.start_offset);
        query.currentSeedResultCount = 0;
        query.error = "";
        query.skipReason = "";
      } else if (type === "repository_query_page_completed") {
        query.status = "running";
        query.activeSeed = seed;
        query.pagesCompleted = Math.max(
          0,
          readCount(payload.page) - readCount(payload.start_page),
        );
        query.maxPages = readCount(payload.max_pages) || query.maxPages;
        query.currentSeedResultCount = readCount(payload.cumulative_count);
      } else if (type === "repository_query_completed") {
        query.rawResultCount += readCount(payload.raw_result_count);
        query.newCandidateCount += readCount(payload.new_candidate_count);
        query.currentSeedResultCount = 0;
      } else if (type === "repository_query_failed") {
        query.status = "failed";
        query.error = readOptionalText(payload.error) || "repository query failed";
      } else if (type === "repository_query_skipped") {
        query.status = "skipped";
        query.skipReason = readOptionalText(payload.reason) || "query skipped";
      }
      continue;
    }
    if (type === "candidate_search_failed") {
      const error = readOptionalText(payload.error) || "candidate search failed";
      for (const query of active) {
        if (query.status !== "completed") {
          query.status = "failed";
          query.error = error;
        }
      }
      continue;
    }
    if (type === "candidate_search_completed") {
      const observation = isRecord(payload.observation) ? payload.observation : {};
      const yields = Array.isArray(observation.query_yields)
        ? observation.query_yields.filter(isRecord)
        : [];
      for (const query of active) {
        const matches = yields.filter((item) => {
          const yieldedQuery = readOptionalText(item.query);
          return yieldedQuery === query.query || query.executedSeeds.includes(yieldedQuery);
        });
        query.rawResultCount = matches.reduce(
          (sum, item) => sum + readCount(item.raw_result_count),
          0,
        );
        query.newCandidateCount = matches.reduce(
          (sum, item) => sum + readCount(item.new_candidate_count),
          0,
        );
        const error = matches.map((item) => readOptionalText(item.error)).find(Boolean) || "";
        const executed = matches.filter((item) => !readOptionalText(item.skipped_reason) && !readOptionalText(item.error));
        const skipReason = matches.map((item) => readOptionalText(item.skipped_reason)).find(Boolean) || "";
        query.status = error && !executed.length ? "failed" : executed.length ? "completed" : "skipped";
        query.error = executed.length ? "" : error;
        query.skipReason = executed.length ? "" : skipReason;
        for (const item of matches) {
          const seed = readOptionalText(item.executed_query);
          if (seed && !query.executedSeeds.includes(seed)) query.executedSeeds.push(seed);
        }
      }
    }
  }
  return trace;
}

function buildProjectReviewTrace(job: DiscoveryJob): ProjectReviewTrace[] {
  const projects = new Map<string, ProjectReviewTrace>();
  const ensure = (rawAccession: unknown) => {
    const projectAccession = readOptionalText(rawAccession).toUpperCase();
    if (!projectAccession) return null;
    const existing = projects.get(projectAccession);
    if (existing) return existing;
    const created: ProjectReviewTrace = {
      projectAccession,
      title: "",
      status: "queued",
      stage: "等待读取 metadata",
      detail: "",
      matchedIntentTerms: [],
      queryHits: [],
      species: [],
      acquisitionMode: "",
      selectedFileCount: 0,
      retrievalScore: null,
      grade: null,
      confidence: null,
      decision: "",
      explanation: "",
      evidenceRefs: [],
      evidenceDetails: [],
      missingInformation: [],
      limitations: [],
      steps: [],
      rawFileCount: 0,
      usableFileCount: 0,
      excludedFileCount: 0,
      fileRoleCounts: {},
      filterReasonCounts: {},
      lastEventSequence: 0,
      concludedSequence: 0,
    };
    projects.set(projectAccession, created);
    return created;
  };

  for (const [logIndex, rawLog] of (job.logs || []).entries()) {
    if (!isRecord(rawLog)) continue;
    const eventSequence = readCount(rawLog.sequence) || logIndex + 1;
    const type = readOptionalText(rawLog.type);
    const payload = isRecord(rawLog.payload) ? rawLog.payload : {};
    if (type === "candidate_inspection_started") {
      const action = isRecord(payload.action) ? payload.action : {};
      for (const accession of readTextList(action.accessions, 100)) {
        const project = ensure(accession);
        if (project) project.lastEventSequence = eventSequence;
      }
      continue;
    }
    if (type === "candidate_search_completed") {
      const observation = isRecord(payload.observation) ? payload.observation : {};
      const previews = Array.isArray(observation.previews)
        ? observation.previews.filter(isRecord)
        : [];
      for (const preview of previews) {
        const project = ensure(preview.project_accession);
        if (!project) continue;
        project.lastEventSequence = Math.max(project.lastEventSequence, eventSequence);
        project.title = readOptionalText(preview.title);
        project.stage = "候选预览：等待项目级 metadata 审查";
        project.matchedIntentTerms = readTextList(preview.matched_intent_terms);
        project.queryHits = readTextList(preview.query_hits);
        project.species = readTextList(preview.species);
        project.acquisitionMode = readOptionalText(preview.acquisition_mode);
        const retrievalScore = preview.project_score == null
          ? Number.NaN
          : Number(preview.project_score);
        project.retrievalScore = Number.isFinite(retrievalScore) ? retrievalScore : null;
      }
      continue;
    }
    if (type === "job_message") {
      const message = readOptionalText(rawLog.message);
      const accession = message.match(/PXD\d+/i)?.[0];
      const project = accession ? ensure(accession) : null;
      if (!project) continue;
      project.lastEventSequence = eventSequence;
      project.detail = message;
      if (/^Inspecting project/i.test(message)) {
        project.status = "reading";
        project.stage = "正在读取项目 metadata";
      } else if (/metadata scored/i.test(message)) {
        project.status = "reading";
        project.stage = "metadata 已解析，正在核对文件";
        const score = message.match(/retrieval score\s+([0-9.]+)/i)?.[1];
        project.retrievalScore = score == null ? null : Number(score);
      } else if (/Listing files|fetched \d+ file/i.test(message)) {
        project.status = "reading";
        project.stage = "正在读取项目文件清单";
      } else if (/checking SDRF|downloading SDRF|loaded \d+ SDRF/i.test(message)) {
        project.status = "reading";
        project.stage = "正在核对 SDRF metadata";
      } else if (/kept \d+ file candidate/i.test(message)) {
        project.status = "inspected";
        project.stage = "metadata 与文件初审完成";
      } else if (/Excluded project/i.test(message)) {
        project.status = "excluded";
        project.stage = "被科学条件排除";
        project.explanation = message.split(":").slice(1).join(":").trim();
      } else if (/metadata failed|parsing failed|File listing failed/i.test(message)) {
        project.status = "failed";
        project.stage = "读取失败";
      }
      if (["inspected", "excluded", "failed"].includes(project.status)) {
        project.concludedSequence = eventSequence;
      }
      if (!project.steps.includes(message)) project.steps.push(message);
      project.steps = project.steps.slice(-30);
      continue;
    }
    if (type === "candidate_inspection_completed") {
      const observation = isRecord(payload.observation) ? payload.observation : {};
      const assessments = Array.isArray(observation.project_assessments)
        ? observation.project_assessments.filter(isRecord)
        : [];
      for (const assessment of assessments) {
        const project = ensure(assessment.project_accession);
        if (!project) continue;
        project.lastEventSequence = eventSequence;
        project.concludedSequence = eventSequence;
        project.title = readOptionalText(assessment.project_title);
        project.status = project.status === "excluded" ? "excluded" : "inspected";
        project.stage = "项目级 metadata 与文件证据已读取";
        project.matchedIntentTerms = readTextList(assessment.matched_intent_terms);
        project.queryHits = readTextList(assessment.query_hits);
        project.species = readTextList(assessment.species);
        project.acquisitionMode = readOptionalText(assessment.acquisition_mode);
        project.selectedFileCount = readCount(assessment.selected_file_count);
        project.evidenceRefs = readTextList(assessment.available_evidence_refs);
        const sdrf = isRecord(assessment.sdrf) ? assessment.sdrf : {};
        const warningCounts = isRecord(assessment.file_evidence_warning_counts)
          ? assessment.file_evidence_warning_counts
          : {};
        project.evidenceDetails = [
          readOptionalText(assessment.project_description_excerpt)
            ? `项目描述：${readOptionalText(assessment.project_description_excerpt)}`
            : "",
          readOptionalText(assessment.sample_processing_excerpt)
            ? `样本处理：${readOptionalText(assessment.sample_processing_excerpt)}`
            : "",
          readOptionalText(assessment.data_processing_excerpt)
            ? `数据处理：${readOptionalText(assessment.data_processing_excerpt)}`
            : "",
          readTextList(assessment.selected_file_examples, 8).length
            ? `文件示例：${readTextList(assessment.selected_file_examples, 8).join("、")}`
            : "",
          readOptionalText(sdrf.status)
            ? `SDRF：${readOptionalText(sdrf.status)}${
                readCount(sdrf.row_count) ? `，${readCount(sdrf.row_count)} 行` : ""
              }`
            : "",
          Object.keys(warningCounts).length
            ? `证据警告：${Object.entries(warningCounts)
                .slice(0, 8)
                .map(([key, value]) => `${key}×${readCount(value)}`)
                .join("、")}`
            : "",
        ].filter(Boolean);
        project.steps.push("项目级 metadata 与文件证据汇总完成");
        project.steps = [...new Set(project.steps)].slice(-30);
      }
      const outcomes = Array.isArray(observation.inspection_outcomes)
        ? observation.inspection_outcomes.filter(isRecord)
        : [];
      for (const outcome of outcomes) {
        const project = ensure(outcome.project_accession);
        if (!project) continue;
        project.lastEventSequence = eventSequence;
        project.concludedSequence = eventSequence;
        const category = readOptionalText(outcome.category);
        const reason = readOptionalText(outcome.reason);
        const error = readOptionalText(outcome.error);
        project.detail = [reason, error].filter(Boolean).join("：");
        project.explanation = project.explanation || project.detail;
        project.rawFileCount = readCount(outcome.raw_file_count);
        project.usableFileCount = readCount(outcome.usable_file_count);
        project.excludedFileCount = readCount(outcome.excluded_file_count);
        project.fileRoleCounts = readCountMap(outcome.file_role_counts);
        project.filterReasonCounts = readCountMap(outcome.filter_reason_counts);
        if (category === "usable_files") {
          project.status = "inspected";
          project.stage = "项目文件审查完成，有可用文件";
        } else if (category === "scientific_exclusion") {
          project.status = "excluded";
          project.stage = "因科学条件不符而排除";
        } else if (category === "no_usable_files") {
          project.status = "excluded";
          project.stage = "未发现可用文件";
        } else if (category === "not_inspected") {
          project.status = "failed";
          project.stage = "本轮未完成审查";
        } else {
          project.status = "failed";
          project.stage = `项目审查失败${readOptionalText(outcome.stage) ? `：${readOptionalText(outcome.stage)}` : ""}`;
        }
        const outcomeStep = `${project.stage}${project.detail ? `：${project.detail}` : ""}`;
        if (!project.steps.includes(outcomeStep)) project.steps.push(outcomeStep);
        project.steps = project.steps.slice(-30);
      }
      continue;
    }
    if (type === "project_judgments_recorded") {
      const judgments = Array.isArray(payload.judgments)
        ? payload.judgments.filter(isRecord)
        : [];
      for (const judgment of judgments) {
        const project = ensure(judgment.project_accession);
        if (!project) continue;
        project.lastEventSequence = eventSequence;
        project.concludedSequence = eventSequence;
        const grade = judgment.grade;
        const confidence = Number(judgment.confidence);
        project.grade = typeof grade === "number" && Number.isFinite(grade) ? grade : null;
        project.confidence = Number.isFinite(confidence) ? confidence : null;
        project.decision = readOptionalText(judgment.decision);
        project.status =
          project.decision === "include"
            ? "included"
            : project.decision === "exclude"
              ? "excluded"
              : "investigate";
        project.stage = "项目级证据评分已记录";
        project.explanation = readOptionalText(judgment.explanation);
        project.evidenceRefs = readTextList(judgment.evidence_refs);
        project.missingInformation = readTextList(judgment.missing_information);
        project.limitations = readTextList(judgment.limitations);
        project.steps.push(
          `项目级证据评分已记录：${project.grade == null ? "未知" : `${project.grade}/3`}，结论 ${project.decision || "未知"}`,
        );
        project.steps = [...new Set(project.steps)].slice(-30);
      }
    }
  }
  const concludedStatuses = new Set([
    "inspected",
    "included",
    "investigate",
    "excluded",
    "failed",
  ]);
  const displayRank = (project: ProjectReviewTrace) => {
    if (project.status === "reading") return 0;
    if (concludedStatuses.has(project.status)) return 1;
    return 2;
  };
  return [...projects.values()].sort((left, right) => {
    const rankDifference = displayRank(left) - displayRank(right);
    if (rankDifference) return rankDifference;
    if (displayRank(left) < 2) {
      return (
        right.concludedSequence - left.concludedSequence
        || right.lastEventSequence - left.lastEventSequence
        || left.projectAccession.localeCompare(right.projectAccession)
      );
    }
    return (
      (right.retrievalScore ?? Number.NEGATIVE_INFINITY)
      - (left.retrievalScore ?? Number.NEGATIVE_INFINITY)
      || left.projectAccession.localeCompare(right.projectAccession)
    );
  });
}

function progressEventToTimeline(event: ReturnType<typeof humanizeJobProgress>["progressEvents"][number]): TimelineEvent {
  if (event.kind === "think") return tl.think(event.text || "Agent 正在评估下一步", event.id);
  if (event.kind === "action") return tl.action(event.text || "Agent 已完成一个阶段", event.id);
  return tl.tool(event.name || "数据发现", event.status || "ok", event.detail, undefined, event.id);
}

function buildMilestones(
  humanSteps: string[],
  headline: string,
  status: DiscoveryRunStatus,
): DiscoveryMilestone[] {
  const unique: string[] = [];
  for (const step of humanSteps) {
    const value = String(step || "").trim();
    if (value && !unique.includes(value)) unique.push(value);
  }

  if (unique.length < 2) {
    const statusStep =
      status === "completed"
        ? "候选检索与审查已结束"
        : status === "failed"
          ? "运行已停止，等待查看失败原因"
          : status === "blocked"
            ? "检索已结束：已整理可用文件清单，可继续批量参数规划"
          : status === "cancelled"
            ? "本轮搜索已停止"
            : "已接收并锁定本轮确认策略";
    if (!unique.includes(statusStep)) unique.unshift(statusStep);
  }
  if (unique.length < 2 && headline && !unique.includes(headline)) unique.push(headline);

  return unique.slice(-3).map((text, index) => ({
    id: `milestone-${Math.max(0, unique.length - 3) + index}-${text.slice(0, 24)}`,
    text,
  }));
}

/**
 * The single display projection shared by chat progress and the context rail.
 * Rendering components never parse raw discovery records independently.
 */
export function buildDiscoveryRunView(job: DiscoveryJob): DiscoveryRunView {
  const record = (job.record || {}) as WorkflowRecord;
  const recordSummary = isRecord(record.summary) ? record.summary : {};
  const completion = isRecord(record.business_completion) ? record.business_completion : {};
  const completionProgress = isRecord(completion.progress) ? completion.progress : {};
  const publicationAuthority = isRecord(record.publication_authority)
    ? record.publication_authority
    : isRecord(recordSummary.publication_authority)
      ? recordSummary.publication_authority
      : {};
  const audit = isRecord(record.latest_discovery_audit)
    ? record.latest_discovery_audit
    : isRecord(recordSummary.latest_discovery_audit)
      ? recordSummary.latest_discovery_audit
      : {};
  const qualityIssues = Array.isArray(audit.issues)
    ? audit.issues
        .filter(isRecord)
        .map((issue) => String(issue.summary || issue.code || "").trim())
        .filter(Boolean)
        .slice(0, 4)
    : [];
  const serverStatus = normalizeStatus(job.status || record.status);
  const attemptFinishedWithoutAudit = (job.logs || []).some((log) =>
    ["discovery_quality_repair_completed", "repair_attempt_finished"].includes(
      String(log.type || ""),
    ),
  );
  const status = normalizeStatus(
    honestDiscoveryStatus(serverStatus, record, attemptFinishedWithoutAudit),
  );
  const auditCounts = isRecord(audit.counts) ? audit.counts : {};
  const executionState = readExecutionState(job.execution_state);
  const searchTrace = buildSearchTrace(job);
  const latestBatchMatch = searchTrace.at(-1)?.id.match(/^search-(\d+)-/);
  const latestBatchPrefix = latestBatchMatch ? `search-${latestBatchMatch[1]}-` : "";
  const repositoryHits = searchTrace
    .filter((item) => !latestBatchPrefix || item.id.startsWith(latestBatchPrefix))
    .reduce(
      (sum, item) =>
        sum + item.rawResultCount + (item.status === "running" ? item.currentSeedResultCount : 0),
      0,
    );
  const liveDeduplicatedCandidates = searchTrace.reduce(
    (sum, item) => sum + item.newCandidateCount,
    0,
  );
  const finalProjectCount = firstCount(
      completionProgress.candidate_projects,
      recordSummary.candidate_projects,
      record.project_count,
    );
  const runIsActive = status === "queued" || status === "running";
  const metrics: DiscoveryRunMetrics = {
    projects: runIsActive
      ? Math.max(
          finalProjectCount,
          liveDeduplicatedCandidates,
          executionState?.candidateCount ?? 0,
        )
      : finalProjectCount,
    repositoryHits,
    files: firstCount(
      completionProgress.candidate_files,
      recordSummary.candidate_files,
      record.file_count,
    ),
    reviews: firstCount(recordSummary.needs_review_files, record.needs_review_files),
    usableFiles: firstCount(
      auditCounts.usable_files,
      recordSummary.usable_files,
      record.usable_files,
      completionProgress.usable_files,
    ),
    strictValidFiles: firstCount(
      auditCounts.strict_valid_files,
      recordSummary.strict_valid_files,
      record.strict_valid_files,
    ),
    directUsableFiles: firstCount(
      auditCounts.direct_usable_files,
      recordSummary.direct_usable_files,
    ),
    inheritedUsableFiles: firstCount(
      auditCounts.inherited_usable_files,
      recordSummary.inherited_usable_files,
    ),
    pendingFiles: firstCount(
      auditCounts.pending_files,
      recordSummary.pending_files,
      recordSummary.needs_review_files,
    ),
    inspectedProjects: firstCount(
      completionProgress.reviewed_projects,
      recordSummary.reviewed_projects,
      recordSummary.inspected_projects,
      recordSummary.assessable_inspections,
      record.review_count,
    ),
    judgmentQualifiedProjects: firstCount(
      completionProgress.judgment_qualified_projects,
      recordSummary.judgment_qualified_projects,
      recordSummary.judgment_qualified,
    ),
    buildReadyProjects: firstCount(
      completionProgress.build_ready_projects,
      completion.build_ready_projects,
      recordSummary.build_ready_projects,
    ),
    buildReadyFiles: firstCount(
      completionProgress.build_ready_files,
      completion.build_ready_files,
      recordSummary.build_ready_files,
    ),
    blockerCounts: readCountMap(
      completionProgress.blocker_counts ?? recordSummary.blocker_counts,
    ),
  };
  // L1 success metric: usable file list (not build-ready package count).
  metrics.selectedProjects = metrics.usableFiles;
  const human = humanizeJobProgress({
    ...job,
    status,
    record: { ...record, project_count: metrics.projects, file_count: metrics.files },
  });
  const statusDetail = String(
    record.status_message ||
      record.phase_label ||
      (Array.isArray(completion.limitations) ? completion.limitations[0] : "") ||
      "",
  ).trim();
  const technicalEvents = human.progressEvents.map(progressEventToTimeline);
  const resultBatches = (Array.isArray(job.result_batches) ? job.result_batches : [])
    .filter(isRecord)
    .map((batch) => ({
      batchIndex: readCount(batch.batch_index),
      projectCount: readCount(batch.project_count),
      fileCount: readCount(batch.file_count),
      cumulativeFileCount: readCount(batch.cumulative_verified_file_count),
      deliveryUnit: readOptionalText(batch.delivery_unit) || "project",
      status: readOptionalText(batch.status) || "ready",
      publishedAt: readOptionalText(batch.published_at),
      terminal: Boolean(batch.terminal),
      downloadUrl: readOptionalText(batch.download_url),
    }))
    .filter((batch) => batch.batchIndex > 0 && batch.downloadUrl.length > 0);
  const reviewTrace = buildProjectReviewTrace(job);
  const concludedReviewProjects = reviewTrace.filter((item) =>
    ["inspected", "included", "investigate", "excluded", "failed"].includes(item.status),
  ).length;
  const qualifiedReviewProjects = reviewTrace.filter(
    (item) => item.status === "included",
  ).length;
  metrics.inspectedProjects = Math.max(metrics.inspectedProjects, concludedReviewProjects);
  metrics.inspectedProjects = Math.max(
    metrics.inspectedProjects,
    executionState?.reviewedProjectCount ?? 0,
  );
  metrics.judgmentQualifiedProjects = Math.max(
    metrics.judgmentQualifiedProjects,
    qualifiedReviewProjects,
  );

  return {
    jobId: String(job.job_id || "").trim(),
    status,
    statusLabel: STATUS_LABEL[status],
    statusDetail,
    summary: human.summary,
    headline: human.headline,
    milestones: buildMilestones(human.humanSteps, human.headline, status),
    metrics,
    provenance: {
      authorityMode: readOptionalText(
        publicationAuthority.authority_mode ?? record.authority_mode,
      ),
      authorityKeyId: readOptionalText(publicationAuthority.key_id ?? record.authority_key_id),
      builderPreflightStatus: readOptionalText(
        record.publication_builder_preflight_status ?? recordSummary.publication_builder_preflight_status,
      ),
      builderPreflightRef: readOptionalText(
        record.publication_builder_preflight_ref ?? recordSummary.publication_builder_preflight_ref,
      ),
    },
    progressPercent: readProgressPercent(record.progress_percent),
    technicalEvents,
    rawLogCount: human.rawLogCount,
    discoveryId: String(record.discovery_id || job.discovery_id || "").trim(),
    error: String(job.error || record.error || "").trim(),
    qualityIssues,
    resultBatches,
    executionState,
    searchTrace,
    reviewTrace,
    resumable: job.resumable === true,
  };
}

export function toDiscoveryProgressPayload(view: DiscoveryRunView): DiscoveryProgressPayload {
  const provenance = Object.values(view.provenance).some(Boolean)
    ? view.provenance
    : undefined;
  return {
    kind: "discovery_progress",
    jobId: view.jobId,
    status: view.status,
    statusLabel: view.statusLabel,
    summary: view.summary,
    headline: view.headline,
    milestones: view.milestones,
    metrics: view.metrics,
    provenance,
    progressPercent: view.progressPercent,
    technicalEvents: view.technicalEvents,
    rawLogCount: view.rawLogCount,
    resultBatches: view.resultBatches,
    executionState: view.executionState || undefined,
    searchTrace: view.searchTrace,
    reviewTrace: view.reviewTrace,
    resumable: view.resumable,
  };
}

const isNonNegativeNumber = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value) && value >= 0;

export function isDiscoveryProgressPayload(value: unknown): value is DiscoveryProgressPayload {
  if (!isRecord(value) || value.kind !== "discovery_progress") return false;
  if (typeof value.jobId !== "string") return false;
  if (typeof value.status !== "string" || !RUN_STATUSES.has(value.status as DiscoveryRunStatus)) return false;
  if (typeof value.statusLabel !== "string" || typeof value.summary !== "string" || typeof value.headline !== "string") {
    return false;
  }
  if (!Array.isArray(value.milestones) || value.milestones.length > 3) return false;
  if (!value.milestones.every((item) => isRecord(item) && typeof item.id === "string" && typeof item.text === "string")) {
    return false;
  }
  if (!isRecord(value.metrics)) return false;
  if (![
    value.metrics.projects,
    value.metrics.repositoryHits,
    value.metrics.files,
    value.metrics.reviews,
    value.metrics.inspectedProjects,
    value.metrics.judgmentQualifiedProjects,
    value.metrics.buildReadyProjects,
    value.metrics.buildReadyFiles,
  ].every(isNonNegativeNumber)) return false;
  if (value.metrics.selectedProjects != null && !isNonNegativeNumber(value.metrics.selectedProjects)) return false;
  if (value.metrics.usableFiles != null && !isNonNegativeNumber(value.metrics.usableFiles)) return false;
  if (value.metrics.strictValidFiles != null && !isNonNegativeNumber(value.metrics.strictValidFiles)) return false;
  if (
    !isRecord(value.metrics.blockerCounts) ||
    !Object.entries(value.metrics.blockerCounts).every(
      ([code, count]) => code.trim().length > 0 && isNonNegativeNumber(count),
    )
  ) return false;
  if (
    value.provenance != null &&
    (!isRecord(value.provenance) ||
      ![
        value.provenance.authorityMode,
        value.provenance.authorityKeyId,
        value.provenance.builderPreflightStatus,
        value.provenance.builderPreflightRef,
      ].every((item) => typeof item === "string"))
  ) {
    return false;
  }
  if (
    value.progressPercent != null &&
    (typeof value.progressPercent !== "number" ||
      !Number.isFinite(value.progressPercent) ||
      value.progressPercent < 0 ||
      value.progressPercent > 100)
  ) {
    return false;
  }
  if (!Array.isArray(value.technicalEvents) || !value.technicalEvents.every(isTimelineEvent)) return false;
  if (
    value.resultBatches != null &&
    (!Array.isArray(value.resultBatches) ||
    !value.resultBatches.every(
      (batch) =>
        isRecord(batch) &&
        isNonNegativeNumber(batch.batchIndex) &&
        isNonNegativeNumber(batch.projectCount) &&
        isNonNegativeNumber(batch.fileCount) &&
        typeof batch.downloadUrl === "string",
    ))
  ) return false;
  if (
    value.executionState != null &&
    (!isRecord(value.executionState) ||
      typeof value.executionState.phase !== "string" ||
      !isNonNegativeNumber(value.executionState.activeTermIndex) ||
      !isNonNegativeNumber(value.executionState.candidateCount) ||
      !isNonNegativeNumber(value.executionState.reviewedProjectCount) ||
      !isNonNegativeNumber(value.executionState.pendingReviewCount) ||
      !isNonNegativeNumber(value.executionState.reviewWorkers) ||
      typeof value.executionState.allTermsExhausted !== "boolean" ||
      typeof value.executionState.completionReady !== "boolean" ||
      !Array.isArray(value.executionState.terms) ||
      !value.executionState.terms.every(
        (term) =>
          isRecord(term) &&
          typeof term.term === "string" &&
          isNonNegativeNumber(term.termIndex) &&
          isNonNegativeNumber(term.termCount) &&
          typeof term.role === "string" &&
          ["pending", "running", "completed", "failed"].includes(String(term.status)) &&
          isNonNegativeNumber(term.chunksCompleted) &&
          isNonNegativeNumber(term.rawResultCount) &&
          isNonNegativeNumber(term.newCandidateCount) &&
          typeof term.exhausted === "boolean" &&
          typeof term.failureReason === "string" &&
          isNonNegativeNumber(term.reviewedProjectCount),
      ))
  ) return false;
  if (
    value.searchTrace != null &&
    (!Array.isArray(value.searchTrace) ||
      !value.searchTrace.every(
        (entry) =>
          isRecord(entry) &&
          typeof entry.id === "string" &&
          typeof entry.query === "string" &&
          isNonNegativeNumber(entry.depth) &&
          typeof entry.role === "string" &&
          ["planned", "running", "completed", "failed", "skipped"].includes(String(entry.status)) &&
          Array.isArray(entry.executedSeeds) &&
          entry.executedSeeds.every((seed) => typeof seed === "string") &&
          isNonNegativeNumber(entry.rawResultCount) &&
          isNonNegativeNumber(entry.newCandidateCount) &&
          isNonNegativeNumber(entry.pagesCompleted) &&
          isNonNegativeNumber(entry.maxPages) &&
          typeof entry.activeSeed === "string" &&
          isNonNegativeNumber(entry.currentSeedResultCount) &&
          typeof entry.error === "string" &&
          typeof entry.skipReason === "string",
      ))
  ) return false;
  if (
    value.reviewTrace != null &&
    (!Array.isArray(value.reviewTrace) ||
      !value.reviewTrace.every(
        (entry) =>
          isRecord(entry) &&
          typeof entry.projectAccession === "string" &&
          typeof entry.title === "string" &&
          typeof entry.status === "string" &&
          typeof entry.stage === "string" &&
          typeof entry.detail === "string" &&
          Array.isArray(entry.evidenceRefs) &&
          Array.isArray(entry.evidenceDetails) &&
          Array.isArray(entry.steps),
      ))
  ) return false;
  return isNonNegativeNumber(value.rawLogCount);
}

function tagType(status: DiscoveryRunStatus): "blue" | "green" | "red" | "gray" | "magenta" {
  if (status === "completed") return "green";
  if (status === "failed") return "red";
  if (status === "blocked") return "magenta";
  if (status === "cancelled") return "gray";
  return "blue";
}

export function DiscoveryProgressMessage({ payload, onCancel, onResume }: { payload: DiscoveryProgressPayload; onCancel?: (jobId: string) => void; onResume?: (jobId: string) => void }) {
  const active = payload.status === "queued" || payload.status === "running";
  const showProgress = active || payload.progressPercent != null;
  const executionState = payload.executionState || null;
  const searchTrace = payload.searchTrace || [];
  const reviewTrace = payload.reviewTrace || [];
  const searchRounds = [...new Set(searchTrace.map((item) => item.round))].sort(
    (left, right) => left - right,
  );
  const latestSearchRound = searchRounds.at(-1);
  const uniqueSearchTermCount = new Set(
    searchTrace.map((item) => item.query.trim().toLocaleLowerCase()).filter(Boolean),
  ).size;
  const concludedReviewCount = reviewTrace.filter((item) =>
    ["inspected", "included", "investigate", "excluded", "failed"].includes(item.status)
  ).length;
  const visibleReviewTrace = reviewTrace.slice(0, 100);
  const currentReview = reviewTrace.find((item) => item.status === "reading");
  const currentQuery = [...searchTrace].reverse().find((item) =>
    item.status === "running" || item.status === "planned"
  );
  const currentTask = currentReview
    ? `正在审查 ${currentReview.projectAccession}：${currentReview.stage}`
    : currentQuery
      ? `第 ${currentQuery.round} 轮：正在处理 ${currentQuery.query}`
      : active
        ? "正在根据检索与审查结果决定下一步"
        : payload.status === "failed"
          ? "任务已停止，请查看失败与阻塞原因"
          : "本轮检索与项目审查已经结束";
  const activeTerm = executionState?.terms.find(
    (term) => term.termIndex === executionState.activeTermIndex,
  );
  const authoritativeTask = executionState?.phase === "reviewing" && activeTerm
    ? `正在审查“${activeTerm.term}”发现的项目：已审 ${executionState.reviewedProjectCount} 个，${executionState.reviewWorkers || 4} 路并行`
    : executionState?.phase === "searching" && activeTerm
      ? `正在完整检索主题词 ${activeTerm.termIndex}/${activeTerm.termCount}：“${activeTerm.term}”（内部自动翻页至耗尽）`
      : executionState?.phase === "finalizing"
        ? "全部主题词已检索至耗尽，审查队列已清空，正在整理交付结果"
        : currentTask;
  const filterReasonSummary = reviewTrace.reduce<Record<string, number>>(
    (summary, item) => {
      for (const [reason, count] of Object.entries(item.filterReasonCounts || {})) {
        summary[reason] = (summary[reason] || 0) + count;
      }
      return summary;
    },
    {},
  );
  const topFilterReasons = Object.entries(filterReasonSummary)
    .sort((left, right) => right[1] - left[1])
    .slice(0, 6);
  const progressLabel =
    payload.progressPercent == null
      ? "总体进度（服务端暂未提供百分比）"
      : `总体进度 ${Math.round(payload.progressPercent)}%`;

  return (
    <section
      className={`discovery-progress discovery-progress--${payload.status}`}
      aria-label="数据发现进度"
      aria-live={active ? "polite" : "off"}
    >
      <div className="discovery-progress__header">
        <span className="discovery-progress__kicker">数据发现</span>
        <Tag size="sm" type={tagType(payload.status)}>{payload.statusLabel}</Tag>
        {active && onCancel ? (
          <button
            type="button"
            className="discovery-progress__cancel"
            data-testid="discovery-cancel"
            onClick={() => onCancel(payload.jobId)}
          >
            停止发现
          </button>
        ) : null}
        {!active && payload.resumable && onResume ? (
          <button
            type="button"
            className="discovery-progress__cancel"
            data-testid="discovery-resume"
            onClick={() => onResume(payload.jobId)}
          >
            从断点继续
          </button>
        ) : null}
      </div>
      <p className="discovery-progress__headline">{payload.headline}</p>
      <section className="discovery-progress__task" aria-label="当前任务与流程">
        <div>
          <strong>当前任务</strong>
          <span>{authoritativeTask}</span>
        </div>
        <ol>
          <li data-state={searchTrace.length ? "active" : "waiting"}>
            <span>1</span>
            <div>
              <strong>检索候选</strong>
              <small>
                {executionState
                  ? `${executionState.terms.filter((term) => term.status === "completed").length}/${executionState.terms.length} 个主题词已耗尽 · ${payload.metrics.projects} 个去重候选`
                  : `${searchRounds.length} 个旧版查询段 · ${payload.metrics.projects} 个去重候选`}
              </small>
            </div>
          </li>
          <li data-state={reviewTrace.length ? "active" : "waiting"}>
            <span>2</span>
            <div>
              <strong>项目级审查</strong>
              <small>{concludedReviewCount} 个已有结论 · 候选总数 {payload.metrics.projects}</small>
            </div>
          </li>
          <li data-state={(payload.resultBatches || []).length ? "active" : "waiting"}>
            <span>3</span>
            <div>
              <strong>分批交付</strong>
              <small>{(payload.resultBatches || []).length} 个已验证批次</small>
            </div>
          </li>
        </ol>
      </section>
      {showProgress ? (
        <ProgressBar
          label={progressLabel}
          value={payload.progressPercent ?? undefined}
          max={100}
          size="small"
          status={payload.status === "failed" ? "error" : payload.status === "completed" || payload.status === "blocked" ? "finished" : "active"}
        />
      ) : null}
      {payload.milestones.length ? (
        <ol className="discovery-progress__milestones" aria-label="最近里程碑">
          {payload.milestones.map((milestone, index) => (
            <li key={milestone.id} className={index === payload.milestones.length - 1 ? "is-current" : ""}>
              <span aria-hidden>{index === payload.milestones.length - 1 && active ? "●" : "✓"}</span>
              <span>{milestone.text}</span>
            </li>
          ))}
        </ol>
      ) : null}
      <dl className="discovery-progress__metrics">
        <div>
          <dt>候选项目（已去重）</dt>
          <dd>{payload.metrics.projects}</dd>
        </div>
        {active && payload.metrics.repositoryHits > 0 ? (
          <div>
            <dt>实时检索命中（含重复）</dt>
            <dd>{payload.metrics.repositoryHits}</dd>
          </div>
        ) : null}
        <div><dt>已审项目</dt><dd>{payload.metrics.inspectedProjects}</dd></div>
        <div><dt>判断合格</dt><dd>{payload.metrics.judgmentQualifiedProjects}</dd></div>
        <div><dt>可交付文件</dt><dd>{payload.metrics.usableFiles ?? payload.metrics.selectedProjects ?? 0}</dd></div>
        <div><dt>项目证据继承</dt><dd>{payload.metrics.inheritedUsableFiles ?? 0}</dd></div>
        <div><dt>等待复核文件</dt><dd>{payload.metrics.pendingFiles ?? 0}</dd></div>
      </dl>
      {(payload.resultBatches || []).length ? (
        <section className="discovery-progress__batches" aria-label="已验证文件批次">
          <p>已验证文件批次</p>
          <ul>
            {(payload.resultBatches || []).map((batch) => (
              <li key={batch.batchIndex}>
                <a href={batch.downloadUrl}>
                  批次 {batch.batchIndex}：{batch.fileCount} 个文件（来自 {batch.projectCount} 个项目）
                  {(batch.cumulativeFileCount ?? 0) > 0 ? ` · 累计 ${batch.cumulativeFileCount}` : ""}
                </a>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {executionState?.terms.length ? (
        <section className="discovery-progress__search-trace" aria-label="仓库检索过程">
          <div>
            <strong>仓库检索过程</strong>
            <span>
              {executionState.terms.filter((term) =>
                ["completed", "failed"].includes(term.status)
              ).length}
              /{executionState.terms.length} 个主题词已结束 · 每个词内部自动翻页至仓库耗尽
            </span>
          </div>
          <div className="discovery-progress__search-rounds">
            {executionState.terms.map((term) => (
              <details
                className="discovery-progress__search-round"
                key={`${term.termIndex}-${term.term}`}
                open={term.termIndex === executionState.activeTermIndex || term.status === "failed"}
              >
                <summary>
                  <span className="discovery-progress__round-label">
                    主题词 {term.termIndex}/{term.termCount}
                  </span>
                  <span className="discovery-progress__round-summary">
                    {term.status === "pending" ? "等待"
                      : term.status === "running" ? "检索或审查中"
                        : term.status === "completed" ? "已耗尽并完成审查"
                          : "失败，尚未耗尽"}
                  </span>
                </summary>
                <ol>
                  <li data-status={term.status === "pending" ? "planned" : term.status}>
                    <div>
                      <strong>{term.term}</strong>
                      <span>{searchRoleLabel(term.role)}</span>
                    </div>
                    <p>
                      内部分页段 {term.chunksCompleted} 个 · 原始返回 {term.rawResultCount}
                      {" · "}全局去重后新增 {term.newCandidateCount}
                    </p>
                    <p>
                      仓库状态：{term.exhausted ? "已明确读到末尾" : "尚未证明耗尽"}
                      {term.reviewedProjectCount > 0
                        ? ` · 累计已审 ${term.reviewedProjectCount} 个项目`
                        : ""}
                    </p>
                    {term.failureReason ? (
                      <p role="alert">失败原因：{term.failureReason}</p>
                    ) : null}
                  </li>
                </ol>
              </details>
            ))}
          </div>
        </section>
      ) : null}
      {!executionState && searchTrace.length ? (
        <section className="discovery-progress__search-trace" aria-label="仓库检索过程">
          <div>
            <strong>仓库检索过程</strong>
            <span>
              {searchTrace.filter((item) =>
                ["completed", "failed", "skipped"].includes(item.status)
              ).length}
              /{searchTrace.length} 个查询动作已结束 · 共 {searchRounds.length} 轮 · {uniqueSearchTermCount} 个主题词
            </span>
          </div>
          <div className="discovery-progress__search-rounds">
            {searchRounds.map((round) => {
              const items = searchTrace.filter((item) => item.round === round);
              const finished = items.filter((item) =>
                ["completed", "failed", "skipped"].includes(item.status)
              ).length;
              return (
                <details
                  className="discovery-progress__search-round"
                  key={round}
                  open={round === latestSearchRound}
                >
                  <summary>
                    <span className="discovery-progress__round-label">第 {round} 轮</span>
                    <span className="discovery-progress__round-summary">
                      {items.length} 个检索词 · {finished}/{items.length} 已结束
                    </span>
                  </summary>
                  <ol>
                    {items.map((item) => (
                      <li key={item.id} data-status={item.status}>
                        <div>
                          <strong>{item.query}</strong>
                          <span>{searchRoleLabel(item.role)} · {
                            item.status === "planned" ? "等待"
                              : item.status === "running" ? "检索中"
                                : item.status === "completed" ? "完成"
                                  : item.status === "skipped" ? "已跳过"
                                    : "失败"
                          }</span>
                        </div>
                        <p>
                          本轮最多追加 {item.depth} 条
                          {item.startOffset > 0 ? ` · 从第 ${item.startOffset + 1} 条继续` : " · 从头开始"}
                        </p>
                        {item.executedSeeds.length ? (
                          <p>提交给 PRIDE：{item.executedSeeds.join("；")}</p>
                        ) : null}
                        {item.status === "running" && item.maxPages ? (
                          <p>{`当前 ${item.activeSeed}：完成 ${item.pagesCompleted}/${item.maxPages} 页，本轮已返回 ${item.currentSeedResultCount}`}</p>
                        ) : null}
                        {item.status === "completed" || item.status === "running" ? (
                          <p>本轮原始返回 {item.rawResultCount} · 去重后新增 {item.newCandidateCount}</p>
                        ) : null}
                        {item.skipReason ? <p>未执行原因：{skipReasonLabel(item.skipReason)}</p> : null}
                        {item.error ? <p role="alert">失败原因：{searchErrorLabel(item.error)}</p> : null}
                      </li>
                    ))}
                  </ol>
                </details>
              );
            })}
          </div>
        </section>
      ) : null}
      {reviewTrace.length ? (
        <section className="discovery-progress__review-trace" aria-label="项目审查过程">
          <div>
            <strong>项目审查过程</strong>
            <span>
              {concludedReviewCount} 个已有结论 · 候选总数 {payload.metrics.projects}
              {reviewTrace.length > 100 ? " · 当前仅展示最近 100 个" : ""}
              {" · accession 全局去重 · 最多 4 路并行审查"}
            </span>
          </div>
          <ol>
            {visibleReviewTrace.map((item) => (
              <li key={item.projectAccession} data-status={item.status}>
                <div>
                  <strong>{item.projectAccession}</strong>
                  <span>{item.stage}</span>
                </div>
                {item.title ? <p>{item.title}</p> : null}
                {item.matchedIntentTerms.length || item.queryHits.length ? (
                  <p>
                    入选依据：
                    {item.matchedIntentTerms.length
                      ? `匹配主题 ${item.matchedIntentTerms.join("、")}`
                      : ""}
                    {item.queryHits.length
                      ? `${item.matchedIntentTerms.length ? "；" : ""}命中检索词 ${item.queryHits.join("、")}`
                      : ""}
                  </p>
                ) : null}
                {item.species.length || item.acquisitionMode || item.selectedFileCount ? (
                  <p>
                    metadata：
                    {[
                      item.species.length ? `物种 ${item.species.join("、")}` : "",
                      item.acquisitionMode ? `采集 ${item.acquisitionMode.toUpperCase()}` : "",
                      item.selectedFileCount ? `保留文件 ${item.selectedFileCount}` : "",
                    ].filter(Boolean).join("；")}
                  </p>
                ) : null}
                {item.retrievalScore != null ? (
                  <p>检索启发分 {item.retrievalScore}（仅用于安排审查顺序）</p>
                ) : null}
                {item.grade != null || item.decision ? (
                  <p>
                    {item.grade != null ? `项目级证据评分 ${item.grade}/3` : "项目级证据评分未知"}
                    {item.confidence != null ? ` · 置信度 ${Math.round(item.confidence * 100)}%` : ""}
                    {item.decision ? ` · 结论 ${item.decision}` : ""}
                  </p>
                ) : null}
                {item.explanation ? <p>判断理由：{projectExplanation(item)}</p> : null}
                {item.rawFileCount || item.excludedFileCount ? (
                  <p>
                    文件检查：原始记录 {item.rawFileCount}
                    {item.usableFileCount ? ` · 保留 ${item.usableFileCount}` : ""}
                    {item.excludedFileCount ? ` · 过滤 ${item.excludedFileCount}` : ""}
                  </p>
                ) : null}
                {Object.keys(item.fileRoleCounts || {}).length ? (
                  <p>
                    文件类型：
                    {Object.entries(item.fileRoleCounts || {})
                      .map(([role, count]) => `${FILE_ROLE_LABELS[role] || role} ${count}`)
                      .join(" · ")}
                  </p>
                ) : null}
                {Object.keys(item.filterReasonCounts || {}).length ? (
                  <ul className="discovery-progress__filter-reasons">
                    {Object.entries(item.filterReasonCounts || {})
                      .sort((left, right) => right[1] - left[1])
                      .map(([reason, count]) => (
                        <li key={`${item.projectAccession}-${reason}`}>
                          {filterReasonLabel(reason)} ×{count}
                        </li>
                      ))}
                  </ul>
                ) : null}
                {item.evidenceRefs.length ? <p>证据字段：{item.evidenceRefs.join("、")}</p> : null}
                {item.evidenceDetails.length ? (
                  <ul className="discovery-progress__evidence-details">
                    {item.evidenceDetails.map((detail, index) => (
                      <li key={`${item.projectAccession}-evidence-${index}`}>{detail}</li>
                    ))}
                  </ul>
                ) : null}
                {item.missingInformation.length ? <p>仍缺证据：{item.missingInformation.join("、")}</p> : null}
                {item.limitations.length ? <p>限制：{item.limitations.join("、")}</p> : null}
                {item.detail && item.status === "reading" ? <p>{item.detail}</p> : null}
                {item.steps.length ? (
                  <details>
                    <summary>查看逐步审查日志（{item.steps.length}）</summary>
                    <ol>
                      {item.steps.map((step, index) => (
                        <li key={`${item.projectAccession}-step-${index}`}>{step}</li>
                      ))}
                    </ol>
                  </details>
                ) : null}
              </li>
            ))}
          </ol>
          {reviewTrace.length > 100 ? (
            <p>当前仅展示最近 100 个；统计分母使用全部 {payload.metrics.projects} 个去重候选，完整审计保留在任务日志与结果文件中。</p>
          ) : null}
        </section>
      ) : null}
      {topFilterReasons.length ? (
        <section className="discovery-progress__failure-summary" aria-label="主要过滤与失败原因">
          <strong>主要过滤原因（文件级命中次数）</strong>
          <ul>
            {topFilterReasons.map(([reason, count]) => (
              <li key={reason}>{filterReasonLabel(reason)}：{count}</li>
            ))}
          </ul>
        </section>
      ) : null}
      {Object.keys(payload.metrics.blockerCounts).length ? (
        <div className="discovery-progress__blockers">
          <p>阻塞原因</p>
          <ul>
            {Object.entries(payload.metrics.blockerCounts).map(([code, count]) => (
              <li key={code}>{code.replaceAll("_", " ")}：{count}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <details className="discovery-progress__file-details">
        <summary>文件与兼容指标</summary>
        <dl>
          <div><dt>候选文件</dt><dd>{payload.metrics.files}</dd></div>
          <div><dt>可交付文件（总）</dt><dd>{payload.metrics.usableFiles ?? payload.metrics.selectedProjects ?? 0}</dd></div>
          <div><dt>直接文件证据</dt><dd>{payload.metrics.directUsableFiles ?? 0}</dd></div>
          <div><dt>项目证据继承</dt><dd>{payload.metrics.inheritedUsableFiles ?? 0}</dd></div>
          <div><dt>build-ready（参考）</dt><dd>{payload.metrics.buildReadyProjects}/{payload.metrics.buildReadyFiles}</dd></div>
          <div><dt>待复核</dt><dd>{payload.metrics.reviews}</dd></div>
          {payload.provenance?.authorityMode ? (
            <div><dt>Authority 模式</dt><dd>{payload.provenance.authorityMode}</dd></div>
          ) : null}
          {payload.provenance?.authorityKeyId ? (
            <div><dt>Authority key ID</dt><dd>{payload.provenance.authorityKeyId}</dd></div>
          ) : null}
          {payload.provenance?.builderPreflightStatus ? (
            <div>
              <dt>Builder preflight</dt>
              <dd>{payload.provenance.builderPreflightStatus}（兼容预检，不等于 dry-run 接受）</dd>
            </div>
          ) : null}
          {payload.provenance?.builderPreflightRef ? (
            <div><dt>Preflight 引用</dt><dd>{payload.provenance.builderPreflightRef}</dd></div>
          ) : null}
        </dl>
      </details>
      <CodexTimeline
        events={payload.technicalEvents}
        summary={`技术轨迹 · ${payload.rawLogCount} 条运行事件`}
        streaming={active}
      />
    </section>
  );
}

export function discoveryProgressMessage(
  id: string,
  view: DiscoveryRunView,
  requestId?: string,
): MessageResponse {
  const item: GenericItem = {
    response_type: "user_defined",
    user_defined: toDiscoveryProgressPayload(view),
  } as GenericItem;
  return { id, request_id: requestId, output: { generic: [item] } };
}

export function renderDiscoveryProgressUserDefined(
  state: { messageItem?: GenericItem | null; messageState?: MessageState | string },
  onCancel?: (jobId: string) => void,
  onResume?: (jobId: string) => void,
): ReactNode {
  const payload = state.messageItem?.user_defined;
  return isDiscoveryProgressPayload(payload) ? <DiscoveryProgressMessage payload={payload} onCancel={onCancel} onResume={onResume} /> : null;
}
