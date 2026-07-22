import { useId, useMemo, useState, type ReactNode } from "react";
import type { GenericItem, MessageResponse, MessageState } from "@carbon/ai-chat";

export type TimelineToolStatus = "running" | "ok" | "fallback" | "error";

export type TimelineEvent =
  | { id?: string; type: "think"; text: string }
  | {
      id?: string;
      type: "tool";
      name: string;
      status: TimelineToolStatus;
      detail?: string;
      ms?: number;
    }
  | { id?: string; type: "action"; text: string };

export type CodexTimelinePayload = {
  kind: "codex_timeline";
  events: TimelineEvent[];
  /** Technical detail is collapsed unless a caller explicitly opts in. */
  defaultOpen?: boolean;
  summary?: string;
};

const TOOL_STATUSES = new Set<TimelineToolStatus>(["running", "ok", "fallback", "error"]);

const STATUS_LABEL: Record<TimelineToolStatus, string> = {
  running: "进行中",
  ok: "完成",
  fallback: "已回退",
  error: "失败",
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  value != null && typeof value === "object" && !Array.isArray(value);

const isOptionalString = (value: unknown) => value == null || typeof value === "string";

export function isTimelineEvent(value: unknown): value is TimelineEvent {
  if (!isRecord(value) || !isOptionalString(value.id)) return false;
  if (value.type === "think" || value.type === "action") {
    return typeof value.text === "string" && value.text.trim().length > 0;
  }
  if (value.type !== "tool") return false;
  return (
    typeof value.name === "string" &&
    value.name.trim().length > 0 &&
    typeof value.status === "string" &&
    TOOL_STATUSES.has(value.status as TimelineToolStatus) &&
    isOptionalString(value.detail) &&
    (value.ms == null || (typeof value.ms === "number" && Number.isFinite(value.ms) && value.ms >= 0))
  );
}

function eventKey(event: TimelineEvent, index: number): string {
  if (event.id) return event.id;
  if (event.type === "tool") return `tool:${event.name}:${index}`;
  if (event.type === "action") return `action:${index}`;
  return `think:${index}`;
}

export function summarizeTimeline(events: TimelineEvent[]): string {
  const tools = events.filter((event): event is Extract<TimelineEvent, { type: "tool" }> => event.type === "tool");
  const thinks = events.filter((event) => event.type === "think");
  if (tools.length) {
    const failed = tools.filter((tool) => tool.status === "fallback" || tool.status === "error").length;
    if (failed) return `技术轨迹 · ${tools.length} 次工具调用（${failed} 次异常）`;
    return `技术轨迹 · ${tools.length} 次工具调用`;
  }
  if (thinks.length) return `技术轨迹 · ${thinks.length} 步`;
  return "技术轨迹";
}

export function CodexTimeline({
  events,
  defaultOpen = false,
  summary,
  streaming = false,
}: {
  events: TimelineEvent[];
  defaultOpen?: boolean;
  summary?: string;
  streaming?: boolean;
}): ReactNode {
  const title = summary || summarizeTimeline(events);
  const regionId = useId();
  // Deliberately uncontrolled after mount: polling must not reopen a timeline
  // that the user has chosen to collapse (or collapse one they opened).
  const [open, setOpen] = useState(defaultOpen);
  const visible = useMemo(() => events.filter(isTimelineEvent), [events]);

  if (!visible.length) return null;

  return (
    <div className={`codex-tl${streaming ? " codex-tl--streaming" : ""}`}>
      <button
        type="button"
        className="codex-tl__toggle"
        aria-controls={regionId}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="codex-tl__chevron" aria-hidden>
          {open ? "▾" : "▸"}
        </span>
        <span className="codex-tl__title">{title}</span>
        {streaming ? <span className="codex-tl__pulse" aria-label="进行中" /> : null}
      </button>
      {open ? (
        <div
          id={regionId}
          className="codex-tl__region"
          role="region"
          aria-label={`${title}详情`}
          tabIndex={0}
        >
          <ol className="codex-tl__list">
            {visible.map((event, index) => {
              const key = eventKey(event, index);
              if (event.type === "think") {
                return (
                  <li key={key} className="codex-tl__item codex-tl__item--think">
                    <span className="codex-tl__rail" aria-hidden />
                    <div className="codex-tl__body">
                      <p className="codex-tl__think">{event.text}</p>
                    </div>
                  </li>
                );
              }
              if (event.type === "action") {
                return (
                  <li key={key} className="codex-tl__item codex-tl__item--action">
                    <span className="codex-tl__rail" aria-hidden />
                    <div className="codex-tl__body">
                      <div className="codex-tl__action">{event.text}</div>
                    </div>
                  </li>
                );
              }
              return (
                <li key={key} className={`codex-tl__item codex-tl__item--tool codex-tl__item--${event.status}`}>
                  <span className="codex-tl__rail" aria-hidden />
                  <div className="codex-tl__body">
                    <div className="codex-tl__tool-card">
                      <div className="codex-tl__tool-head">
                        <span className="codex-tl__tool-name">{event.name}</span>
                        <span className={`codex-tl__badge codex-tl__badge--${event.status}`}>
                          {STATUS_LABEL[event.status]}
                          {event.ms != null ? ` · ${event.ms}ms` : ""}
                        </span>
                      </div>
                      {event.detail ? <pre className="codex-tl__tool-detail">{event.detail}</pre> : null}
                    </div>
                  </div>
                </li>
              );
            })}
          </ol>
        </div>
      ) : null}
    </div>
  );
}

export function isCodexTimelinePayload(value: unknown): value is CodexTimelinePayload {
  if (!isRecord(value) || value.kind !== "codex_timeline" || !Array.isArray(value.events)) return false;
  if (value.events.length > 200 || !value.events.every(isTimelineEvent)) return false;
  if (value.defaultOpen != null && typeof value.defaultOpen !== "boolean") return false;
  if (!isOptionalString(value.summary)) return false;
  return true;
}

export function timelineMessage(
  id: string,
  body: string,
  events: TimelineEvent[],
  options?: {
    requestId?: string;
    defaultOpen?: boolean;
    summary?: string;
  },
): MessageResponse {
  const generic: GenericItem[] = [];
  if (events.length) {
    generic.push({
      response_type: "user_defined",
      user_defined: {
        kind: "codex_timeline",
        events,
        defaultOpen: options?.defaultOpen,
        summary: options?.summary,
      } satisfies CodexTimelinePayload,
    } as GenericItem);
  }
  if (body.trim()) {
    generic.push({
      response_type: "text",
      text: body,
    } as GenericItem);
  }
  if (!generic.length) {
    generic.push({
      response_type: "text",
      text: "…",
    } as GenericItem);
  }
  return {
    id,
    request_id: options?.requestId,
    output: { generic },
  };
}

export function renderCodexUserDefined(
  state: { messageItem?: GenericItem | null; messageState?: MessageState | string },
): ReactNode {
  const payload = state.messageItem?.user_defined;
  if (!isCodexTimelinePayload(payload)) return null;
  const messageState = String(state.messageState || "").toLowerCase();
  const streaming = messageState === "partial" || messageState === "streaming";
  return (
    <CodexTimeline
      events={payload.events}
      defaultOpen={payload.defaultOpen}
      summary={payload.summary}
      streaming={streaming}
    />
  );
}

/** Friendly builders used by the chat turn pipeline. */
export const tl = {
  think: (text: string, id?: string): TimelineEvent => ({ type: "think", text, id }),
  action: (text: string, id?: string): TimelineEvent => ({ type: "action", text, id }),
  tool: (
    name: string,
    status: TimelineToolStatus,
    detail?: string,
    ms?: number,
    id?: string,
  ): TimelineEvent => ({ type: "tool", name, status, detail, ms, id }),
};
