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
import type { DiscoveryJob, WorkflowRecord } from "./workflow-api";

export type DiscoveryRunStatus = "queued" | "running" | "completed" | "failed" | "blocked" | "cancelled";

export type DiscoveryRunMetrics = {
  projects: number;
  files: number;
  reviews: number;
  selectedProjects?: number;
};

export type DiscoveryMilestone = {
  id: string;
  text: string;
};

export type DiscoveryRunView = {
  status: DiscoveryRunStatus;
  statusLabel: string;
  statusDetail: string;
  summary: string;
  headline: string;
  milestones: DiscoveryMilestone[];
  metrics: DiscoveryRunMetrics;
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
  blocked: "质量未通过",
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

function readProgressPercent(value: unknown): number | null {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return null;
  return Math.max(0, Math.min(100, parsed));
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
            ? "检索已结束，但交付质量闸门未通过"
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
  const status = normalizeStatus(job.status || record.status);
  const human = humanizeJobProgress(job);
  const statusDetail = String(record.status_message || record.phase_label || "").trim();
  const technicalEvents = human.progressEvents.map(progressEventToTimeline);

  return {
    status,
    statusLabel: STATUS_LABEL[status],
    statusDetail,
    summary: human.summary,
    headline: human.headline,
    milestones: buildMilestones(human.humanSteps, human.headline, status),
    metrics: {
      projects: readCount(record.project_count),
      files: readCount(record.file_count),
      reviews: readCount(record.review_count ?? recordSummary.needs_review_files),
      selectedProjects: readCount(
        recordSummary.selected_projects ?? recordSummary.delivery_eligible_projects,
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
  return {
    kind: "discovery_progress",
    status: view.status,
    statusLabel: view.statusLabel,
    summary: view.summary,
    headline: view.headline,
    milestones: view.milestones,
    metrics: view.metrics,
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
  if (![value.metrics.projects, value.metrics.files, value.metrics.reviews].every(isNonNegativeNumber)) return false;
  if (value.metrics.selectedProjects != null && !isNonNegativeNumber(value.metrics.selectedProjects)) return false;
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
          status={payload.status === "failed" || payload.status === "blocked" ? "error" : payload.status === "completed" ? "finished" : "active"}
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
        <div><dt>候选项目</dt><dd>{payload.metrics.projects}</dd></div>
        <div><dt>候选文件</dt><dd>{payload.metrics.files}</dd></div>
        <div><dt>通过交付</dt><dd>{payload.metrics.selectedProjects ?? 0}</dd></div>
        <div><dt>待复核</dt><dd>{payload.metrics.reviews}</dd></div>
      </dl>
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
