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
  files: number;
  reviews: number;
  /** L1 usable files (valid + weak_keep); primary delivery count for the workbench. */
  selectedProjects?: number;
  usableFiles: number;
  strictValidFiles: number;
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

export type DiscoveryRunView = {
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
};

export type DiscoveryProgressPayload = {
  kind: "discovery_progress";
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

function readBlockerCounts(value: unknown): Record<string, number> {
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
  const metrics: DiscoveryRunMetrics = {
    projects: firstCount(
      completionProgress.candidate_projects,
      recordSummary.candidate_projects,
      record.project_count,
    ),
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
    blockerCounts: readBlockerCounts(
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

  return {
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
  };
}

export function toDiscoveryProgressPayload(view: DiscoveryRunView): DiscoveryProgressPayload {
  const provenance = Object.values(view.provenance).some(Boolean)
    ? view.provenance
    : undefined;
  return {
    kind: "discovery_progress",
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
  };
}

const isNonNegativeNumber = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value) && value >= 0;

export function isDiscoveryProgressPayload(value: unknown): value is DiscoveryProgressPayload {
  if (!isRecord(value) || value.kind !== "discovery_progress") return false;
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
  return isNonNegativeNumber(value.rawLogCount);
}

function tagType(status: DiscoveryRunStatus): "blue" | "green" | "red" | "gray" | "magenta" {
  if (status === "completed") return "green";
  if (status === "failed") return "red";
  if (status === "blocked") return "magenta";
  if (status === "cancelled") return "gray";
  return "blue";
}

export function DiscoveryProgressMessage({ payload }: { payload: DiscoveryProgressPayload }) {
  const active = payload.status === "queued" || payload.status === "running";
  const showProgress = active || payload.progressPercent != null;
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
      </div>
      <p className="discovery-progress__headline">{payload.headline}</p>
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
        <div><dt>检索项目</dt><dd>{payload.metrics.projects}</dd></div>
        <div><dt>已审项目</dt><dd>{payload.metrics.inspectedProjects}</dd></div>
        <div><dt>判断合格</dt><dd>{payload.metrics.judgmentQualifiedProjects}</dd></div>
        <div><dt>可用文件 L1</dt><dd>{payload.metrics.usableFiles ?? payload.metrics.selectedProjects ?? 0}</dd></div>
      </dl>
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
          <div><dt>严格 valid</dt><dd>{payload.metrics.strictValidFiles ?? 0}</dd></div>
          <div><dt>可用文件 L1</dt><dd>{payload.metrics.usableFiles ?? payload.metrics.selectedProjects ?? 0}</dd></div>
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
): ReactNode {
  const payload = state.messageItem?.user_defined;
  return isDiscoveryProgressPayload(payload) ? <DiscoveryProgressMessage payload={payload} /> : null;
}
