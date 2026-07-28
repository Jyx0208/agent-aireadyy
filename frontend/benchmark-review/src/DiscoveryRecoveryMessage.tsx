import type { GenericItem, MessageResponse, MessageState } from "@carbon/ai-chat";
import type { ReactNode } from "react";

export type RecoveryAction =
  | "view_results"
  | "revise_strategy"
  | "research_current_card"
  | "reset_dialogue";

export type DiscoveryRecoveryOutcome = "done" | "failed";

export type DiscoveryRecoveryPayload = {
  kind: "discovery_recovery";
  jobId: string;
  discoveryId: string;
  /** Intent snapshot key captured when the terminal job finished (card generation). */
  cardGeneration: string;
  outcome: DiscoveryRecoveryOutcome;
  hasResults: boolean;
  summary: string;
};

export type RecoveryChipHandler = (
  action: RecoveryAction,
  payload: DiscoveryRecoveryPayload,
) => void;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  value != null && typeof value === "object" && !Array.isArray(value);

export function isDiscoveryRecoveryPayload(value: unknown): value is DiscoveryRecoveryPayload {
  if (!isRecord(value) || value.kind !== "discovery_recovery") return false;
  if (typeof value.jobId !== "string" || !value.jobId.trim()) return false;
  if (typeof value.discoveryId !== "string") return false;
  if (typeof value.cardGeneration !== "string" || !value.cardGeneration.trim()) return false;
  if (value.outcome !== "done" && value.outcome !== "failed") return false;
  if (typeof value.hasResults !== "boolean") return false;
  if (typeof value.summary !== "string") return false;
  return true;
}

export function discoveryRecoveryMessage(
  id: string,
  payload: DiscoveryRecoveryPayload,
  requestId?: string,
): MessageResponse {
  const item: GenericItem = {
    response_type: "user_defined",
    user_defined: payload,
  } as GenericItem;
  return { id, request_id: requestId, output: { generic: [item] } };
}

const CHIPS: Array<{
  action: RecoveryAction;
  label: string;
  needsResults?: boolean;
}> = [
  { action: "view_results", label: "查看本轮结果", needsResults: true },
  { action: "revise_strategy", label: "先改策略再搜" },
  { action: "research_current_card", label: "按当前卡重新搜索" },
  { action: "reset_dialogue", label: "重置对话" },
];

export function DiscoveryRecoveryMessage({
  payload,
  onAction,
}: {
  payload: DiscoveryRecoveryPayload;
  onAction?: RecoveryChipHandler;
}): ReactNode {
  const title = payload.outcome === "failed" ? "本轮未完成 — 可继续" : "本轮已结束 — 可继续";
  return (
    <section
      className="discovery-recovery"
      data-testid="discovery-recovery"
      data-job-id={payload.jobId}
      data-outcome={payload.outcome}
      aria-label={title}
    >
      <p className="discovery-recovery__eyebrow">
        {payload.outcome === "failed" ? "失败恢复" : "完成检查点"}
      </p>
      <h3 className="discovery-recovery__title">{title}</h3>
      {payload.summary ? (
        <p className="discovery-recovery__summary">{payload.summary}</p>
      ) : null}
      <div className="discovery-recovery__chips" role="group" aria-label="恢复操作">
        {CHIPS.map((chip) => {
          const disabled = Boolean(chip.needsResults && !payload.hasResults);
          return (
            <button
              key={chip.action}
              type="button"
              className="discovery-recovery__chip"
              data-recovery-action={chip.action}
              disabled={disabled || !onAction}
              title={
                disabled
                  ? "本轮没有可打开的结果（无 job 绑定的 L1/审查交付）"
                  : undefined
              }
              onClick={() => onAction?.(chip.action, payload)}
            >
              {chip.label}
            </button>
          );
        })}
      </div>
      <p className="discovery-recovery__hint">
        重新搜索会开新会话并再次确认；确认前不会访问 PRIDE。不会与进行中的任务双开。
      </p>
    </section>
  );
}

export function renderDiscoveryRecoveryUserDefined(
  state: { messageItem?: GenericItem | null; messageState?: MessageState | string },
  onAction?: RecoveryChipHandler,
): ReactNode {
  const payload = state.messageItem?.user_defined;
  return isDiscoveryRecoveryPayload(payload)
    ? <DiscoveryRecoveryMessage payload={payload} onAction={onAction} />
    : null;
}

/** Human-readable short error; never dump multi-MB raw context into the bubble. */
export function formatRecoveryFailureDetail(error: unknown, maxLen = 280): string {
  const raw = String(error || "").trim() || "数据发现失败，请查看任务日志了解原因。";
  const collapsed = raw.replace(/\s+/g, " ");
  if (collapsed.length <= maxLen) return collapsed;
  return `${collapsed.slice(0, maxLen - 1)}…`;
}
