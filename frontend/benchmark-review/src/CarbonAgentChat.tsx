import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ChatCustomElement,
  BusEventType,
  MessageResponseTypes,
  MessageState,
  type ChatInstance,
  type MessageRequest,
  type MessageResponse,
  type PublicConfigMessaging,
} from "@carbon/ai-chat";

import {
  renderCodexUserDefined,
  timelineMessage,
  tl,
  type TimelineToolStatus,
  type TimelineEvent,
} from "./CodexTimeline";
import {
  buildDiscoveryRunView,
  discoveryProgressMessage,
  renderDiscoveryProgressUserDefined,
} from "./DiscoveryProgressMessage";

import {
  assessStrategyGaps,
  applyRecommendedDefaults,
  detectNextStepCommand,
  formatConfirmMessage,
  formatDoneMessage,
  intentSnapshotForLlm,
  isReadyForConfirm,
  toDiscoveryJobPayload,
} from "./grill-tree";
import {
  appendAgentDialogue,
  decodeAgentTurnResponse,
  formatAgentNextDecision,
  isStrategyFingerprint,
  reduceAgentTurn,
  reduceAgentUnavailable,
  type AgentDialogueMessage,
  type AgentNextDecision,
  type AgentResolvedDecision,
  type AgentSemanticVerification,
} from "./agent-turn";
import { createEmptyIntent, type GrillPhase, type IntentSpec } from "./intent-spec";
import {
  cancelDiscoveryJob,
  delay,
  getDiscoveryJob,
  grillTurn,
  startDiscoveryJob,
  type DiscoveryJob,
  type GrillTurnResult,
  type WorkflowRecord,
} from "./workflow-api";
import {
  freshDialogueSessionId,
  startDialogueSessionId,
  storeDialogueSessionId,
} from "./dialogue-session";
import {
  canonicalDiscoveryPayloadFingerprint,
  canonicalDiscoveryPayloadJson,
} from "./strategy-fingerprint";

export type GrillControls = {
  confirm: () => void;
  applyDefaults: () => void;
};

type Props = {
  onJob: (job: DiscoveryJob) => void;
  onIntentChange: (spec: IntentSpec) => void;
  onPhaseChange: (phase: GrillPhase) => void;
  onNavigate?: (tabIndex: number) => void;
  onRegisterControls?: (controls: GrillControls | null) => void;
  externalCommand?: "confirm" | "defaults" | null;
  onExternalCommandConsumed?: () => void;
};

const text = (value: unknown) => String(value || "").trim();
const terminal = (job: DiscoveryJob | null) =>
  ["completed", "failed", "blocked", "cancelled"].includes(String(job?.status || "").toLowerCase());

/** Slightly above the server's bounded turn deadline; never wait for multi-minute retries. */
export const AGENT_TURN_TIMEOUT_MS = 75_000;

type AgentTurnRequestContext = {
  sessionId: string;
  generation: number;
  intentSnapshot: WorkflowRecord;
  snapshotKey: string;
};

type ConfirmedAgentExecution = {
  request: AgentTurnRequestContext;
  agentStrategyFingerprint: string;
};

function joinAssistantParts(...parts: Array<string | null | undefined>): string {
  return parts
    .map((part) => String(part || "").trim())
    .filter(Boolean)
    .join("\n\n");
}

function jobMessage(job: DiscoveryJob, responseId: string, requestId?: string): MessageResponse {
  return discoveryProgressMessage(responseId, buildDiscoveryRunView(job), requestId);
}
function captureIntentSnapshot(spec: IntentSpec): Pick<AgentTurnRequestContext, "intentSnapshot" | "snapshotKey"> {
  const snapshotKey = JSON.stringify(intentSnapshotForLlm(spec));
  return {
    intentSnapshot: JSON.parse(snapshotKey) as WorkflowRecord,
    snapshotKey,
  };
}

function intentSnapshotKey(spec: IntentSpec): string {
  return JSON.stringify(intentSnapshotForLlm(spec));
}

function semanticVerificationTimelineEvent(
  verification: AgentSemanticVerification | null,
): TimelineEvent | null {
  if (!verification) return null;
  const status: TimelineToolStatus = verification.verdict === "rejected"
    ? "error"
    : verification.verdict === "unavailable" || verification.verdict === "budget_exhausted"
      ? "fallback"
      : "ok";
  const detail = [
    verification.verdict,
    verification.rationale,
    verification.error,
    ...verification.errors,
  ].filter(Boolean).join(" · ");
  return tl.tool("semantic-verification", status, detail || verification.verdict);
}

export function CarbonAgentChat({
  onJob,
  onIntentChange,
  onPhaseChange,
  onNavigate,
  onRegisterControls,
  externalCommand,
  onExternalCommandConsumed,
}: Props) {
  const welcomed = useRef(false);
  const instanceRef = useRef<ChatInstance | null>(null);
  const phaseRef = useRef<GrillPhase>("idle");
  const intentRef = useRef<IntentSpec>(createEmptyIntent());
  const runningRef = useRef(false);
  const dialogueHistoryRef = useRef<AgentDialogueMessage[]>([]);
  const pendingDecisionRef = useRef<AgentNextDecision | null>(null);
  const decisionMemoryRef = useRef<AgentResolvedDecision[]>([]);
  const [initialDialogueSessionId] = useState(() => startDialogueSessionId());
  const dialogueSessionIdRef = useRef<string>(initialDialogueSessionId);
  const dialogueGenerationRef = useRef(0);
  const inFlightGrillControllersRef = useRef(new Set<AbortController>());
  const restartHandlerRegisteredRef = useRef(false);

  const setPhase = useCallback(
    (phase: GrillPhase) => {
      phaseRef.current = phase;
      onPhaseChange(phase);
    },
    [onPhaseChange],
  );

  const invalidateGrillRequests = useCallback(() => {
    dialogueGenerationRef.current += 1;
    for (const controller of inFlightGrillControllersRef.current) controller.abort();
    inFlightGrillControllersRef.current.clear();
  }, []);

  const abandonDialogueSession = useCallback(() => {
    // An aborted HTTP request may still finish in the server worker and append
    // its rejected turn to SQLite. Move the live card to a fresh canonical
    // session before accepting another message; request-carried history and
    // decision memory preserve the accepted conversation without replaying the
    // late turn.
    invalidateGrillRequests();
    const nextSessionId = freshDialogueSessionId();
    dialogueSessionIdRef.current = nextSessionId;
    storeDialogueSessionId(nextSessionId);
  }, [invalidateGrillRequests]);

  const setIntent = useCallback(
    (spec: IntentSpec) => {
      const strategyChanged = intentSnapshotKey(spec) !== intentSnapshotKey(intentRef.current);
      if (strategyChanged && inFlightGrillControllersRef.current.size > 0) {
        // Any card mutation invalidates every turn captured from the previous
        // snapshot. Rotate the server-side SDK session as well as aborting the
        // HTTP request so a worker that finishes late cannot taint later turns.
        abandonDialogueSession();
      }
      intentRef.current = spec;
      onIntentChange(spec);
    },
    [abandonDialogueSession, onIntentChange],
  );

  const resetDialogueState = useCallback(() => {
    invalidateGrillRequests();
    const nextSessionId = freshDialogueSessionId();
    dialogueSessionIdRef.current = nextSessionId;
    storeDialogueSessionId(nextSessionId);
    dialogueHistoryRef.current = [];
    pendingDecisionRef.current = null;
    decisionMemoryRef.current = [];
    welcomed.current = false;
    setIntent(createEmptyIntent());
    setPhase("idle");
  }, [invalidateGrillRequests, setIntent, setPhase]);

  useEffect(() => () => invalidateGrillRequests(), [invalidateGrillRequests]);

  const isRequestIdentityCurrent = useCallback((request: AgentTurnRequestContext) => (
    request.generation === dialogueGenerationRef.current
    && request.sessionId === dialogueSessionIdRef.current
  ), []);

  const isRequestSnapshotCurrent = useCallback((request: AgentTurnRequestContext) => (
    isRequestIdentityCurrent(request)
    && request.snapshotKey === intentSnapshotKey(intentRef.current)
  ), [isRequestIdentityCurrent]);

  const pushAssistant = useCallback(async (body: string, id?: string) => {
    const instance = instanceRef.current;
    if (!instance) return;
    await instance.messaging.addMessage({
      id: id || `grill-${crypto.randomUUID()}`,
      output: { generic: [{ response_type: MessageResponseTypes.TEXT, text: body }] },
    });
  }, []);

  const runDiscovery = useCallback(
    async (
      confirmation: ConfirmedAgentExecution,
      requestId?: string,
      signal?: AbortSignal,
    ) => {
      if (runningRef.current) return;
      if (
        !isStrategyFingerprint(confirmation.agentStrategyFingerprint)
        || phaseRef.current !== "awaiting_confirm"
        || !isRequestSnapshotCurrent(confirmation.request)
      ) {
        await pushAssistant("确认绑定的策略快照已经失效；本次没有启动搜索，请重新确认当前策略。");
        return;
      }
      if (!intentRef.current.confirmed) {
        await pushAssistant("还差最后一步：确认右侧策略后我才会去 PRIDE 搜。");
        return;
      }

      const confirmed: IntentSpec = { ...intentRef.current, confirmed: true };
      setIntent(confirmed);
      if (confirmed.runHorizon === "plan_only") {
        setPhase("done");
        await pushAssistant(
          "搜索计划已经确认并保留；按你选择的“只做计划”，这一步没有访问 PRIDE，也没有启动数据发现任务。之后想执行时，直接告诉我把终点改成候选数据或候选审查即可。",
        );
        return;
      }
      if (["ai_ready_table", "pre_release", "full_release"].includes(confirmed.runHorizon)) {
        // These horizons require a downstream executor after a reviewed
        // discovery run. Never pretend that plain repository search fulfilled
        // a training-table or release request.
        setIntent({ ...confirmed, confirmed: false });
        setPhase("grilling");
        await pushAssistant(
          `“${confirmed.runHorizon}”需要先完成可审计的候选审查，再交给对应的下游执行器。当前页面不会偷偷降级成普通搜索；请先把本次终点改为“找到并审查候选”，完成后我再衔接下一阶段。`,
        );
        return;
      }
      setPhase("running");
      runningRef.current = true;

      let job: DiscoveryJob | null = null;
      const responseId = `discovery:${crypto.randomUUID()}`;
      const instance = instanceRef.current;

      try {
        const canonicalPayload = toDiscoveryJobPayload(confirmed) as unknown as WorkflowRecord;
        const strategyFingerprintPayload = canonicalDiscoveryPayloadJson(canonicalPayload);
        const strategyFingerprint = await canonicalDiscoveryPayloadFingerprint(canonicalPayload);
        const payload: WorkflowRecord = {
          ...canonicalPayload,
          strategy_fingerprint_payload: strategyFingerprintPayload,
          strategy_fingerprint: strategyFingerprint,
        };
        job = await startDiscoveryJob(payload, signal);
        onJob(job);

        const update = (state: MessageState) => {
          if (!instance) return Promise.resolve();
          return instance.messaging.upsertMessage(responseId, state, () =>
            jobMessage(job || {}, responseId, requestId),
          );
        };

        await update(MessageState.STREAMING);
        while (!terminal(job)) {
          await delay(1000, signal);
          job = await getDiscoveryJob(String(job.job_id), false, signal);
          onJob(job);
          await update(MessageState.STREAMING);
        }
        if (job.status === "completed" || job.status === "blocked") {
          job = await getDiscoveryJob(String(job.job_id), true, signal);
          onJob(job);
        }
        await update(MessageState.COMPLETE);

        if (job.status === "completed") {
          setPhase("done");
          await pushAssistant(formatDoneMessage(job, confirmed));
        } else if (job.status === "failed") {
          setPhase("failed");
          const detail =
            (typeof job.error === "string" && job.error.trim()) ||
            "数据发现失败，请查看任务日志了解原因。";
          await pushAssistant(`数据发现失败：${detail}`);
        } else if (job.status === "blocked") {
          setPhase("failed");
          const record = (job.record || {}) as WorkflowRecord;
          const summary = (record.summary || {}) as WorkflowRecord;
          const candidates = Number(summary.candidate_projects ?? record.project_count ?? 0);
          const selected = Number(summary.selected_projects ?? summary.delivery_eligible_projects ?? 0);
          const reason = text((record.agent as WorkflowRecord | undefined)?.stop_reason || job.error);
          await pushAssistant(
            `搜索和审查已经结束，但质量闸门没有放行结果：${candidates} 个候选，${selected} 个通过交付。` +
              (reason ? ` 原因：${reason}。` : "") +
              " 我保留了候选证据和完整审计记录；你可以查看审计，或调整策略后让我继续修复。",
          );
        } else {
          setPhase("done");
        }
      } catch (reason) {
        if (reason instanceof DOMException && reason.name === "AbortError" && job?.job_id) {
          onJob(await cancelDiscoveryJob(job.job_id));
          setPhase("idle");
          // rethrow abort so Carbon stop-button path completes cleanly
          throw reason;
        }
        setPhase("failed");
        await pushAssistant(
          `启动或运行数据发现失败：${reason instanceof Error ? reason.message : String(reason)}`,
        );
        // do not rethrow non-abort errors — avoid Carbon catastrophic "Something went wrong"
      } finally {
        runningRef.current = false;
      }
    },
    [isRequestSnapshotCurrent, onJob, pushAssistant, setIntent, setPhase],
  );

  const applyDefaultsCore = useCallback((): IntentSpec => {
    const filled = applyRecommendedDefaults(intentRef.current);
    pendingDecisionRef.current = null;
    setIntent(filled);
    setPhase("awaiting_confirm");
    return filled;
  }, [setIntent, setPhase]);

  const handleDefaults = useCallback(async () => {
    const filled = applyDefaultsCore();
    await pushAssistant(
      "行，缺口我先用稳妥默认补齐了——你仍可改。确认后才搜。\n\n" +
        formatConfirmMessage(filled),
    );
  }, [applyDefaultsCore, pushAssistant]);

  const handleConfirm = useCallback(async () => {
    if (phaseRef.current === "running") return;
    if (phaseRef.current !== "awaiting_confirm" || !isReadyForConfirm(intentRef.current)) {
      await pushAssistant(
        "当前策略还没有进入待确认状态；本次没有启动搜索。请先继续和 Agent 对齐策略，或用右侧默认设置补齐。",
      );
      return;
    }
    const capturedSnapshot = captureIntentSnapshot(intentRef.current);
    const request: AgentTurnRequestContext = {
      sessionId: dialogueSessionIdRef.current,
      generation: dialogueGenerationRef.current,
      ...capturedSnapshot,
    };
    const strategyFingerprint = await canonicalDiscoveryPayloadFingerprint(
      capturedSnapshot.intentSnapshot,
    );
    const trustedButtonTurn = decodeAgentTurnResponse({
      status: "completed",
      action: "confirm_strategy",
      assistant_message: "",
      strategy_fingerprint: strategyFingerprint,
      tool_calls: [{
        name: "confirm_strategy",
        arguments: { strategy_fingerprint: strategyFingerprint },
      }],
    });
    const reduction = reduceAgentTurn(intentRef.current, trustedButtonTurn, {
      phase: phaseRef.current,
    });
    if (!isRequestSnapshotCurrent(request) || !reduction.confirmationAccepted) {
      await pushAssistant(
        "The strategy changed before confirmation could be bound. Nothing was started; please review and confirm the current card again.",
      );
      return;
    }
    setIntent(reduction.spec);
    setPhase("awaiting_confirm");
    await runDiscovery({
      request,
      agentStrategyFingerprint: strategyFingerprint,
    });
  }, [isRequestSnapshotCurrent, pushAssistant, runDiscovery, setIntent, setPhase]);

  useEffect(() => {
    onRegisterControls?.({
      confirm: () => {
        void handleConfirm();
      },
      applyDefaults: () => {
        void handleDefaults();
      },
    });
    return () => onRegisterControls?.(null);
  }, [handleConfirm, handleDefaults, onRegisterControls]);

  useEffect(() => {
    if (!externalCommand) return;
    if (externalCommand === "confirm") void handleConfirm();
    if (externalCommand === "defaults") void handleDefaults();
    onExternalCommandConsumed?.();
  }, [externalCommand, handleConfirm, handleDefaults, onExternalCommandConsumed]);

  const addWelcomeMessage = useCallback(async (instance: ChatInstance) => {
    instanceRef.current = instance;
    if (!restartHandlerRegisteredRef.current) {
      instance.on({
        type: BusEventType.RESTART_CONVERSATION,
        handler: () => resetDialogueState(),
      });
      restartHandlerRegisteredRef.current = true;
    }
    if (welcomed.current) return;
    welcomed.current = true;
    await instance.messaging.addMessage({
      id: "pride-agent-welcome",
      output: {
        generic: [
          {
            response_type: MessageResponseTypes.TEXT,
            text:
              "你好，我是蛋白质组学数据 Agent——帮你在 PRIDE 里找对数据，而不是填表。\n\n" +
              "直接说目标就行，例如：人源免疫肽、先只要 20 个候选；或「DDA 做人源 RT 预测」。\n" +
              "术语不懂就问我；我会按你的任务给建议。右侧是实时策略，**你确认前我不会去搜**。\n" +
              "想省事可以说 **按推荐默认**。",
          },
        ],
      },
    });
  }, [resetDialogueState]);

  const lastGrillErrorRef = useRef("");
  const callGrillTurn = useCallback(
    async (
      prompt: string,
      opts: {
        phase: GrillPhase;
        turnKind: string;
        request: AgentTurnRequestContext;
        signal?: AbortSignal;
        timeoutMs?: number;
      },
    ): Promise<GrillTurnResult | null> => {
      const controller = new AbortController();
      inFlightGrillControllersRef.current.add(controller);
      const onOuterAbort = () => controller.abort();
      if (opts.signal?.aborted) controller.abort();
      else opts.signal?.addEventListener("abort", onOuterAbort);
      // DeepSeek 冷启动/JSON 补全偶发 >25s；给足余量，避免误报「超时」
      const timer = window.setTimeout(
        () => controller.abort(),
        opts.timeoutMs ?? AGENT_TURN_TIMEOUT_MS,
      );
      lastGrillErrorRef.current = "";
      try {
        const payload = {
          user_message: prompt,
          phase: opts.phase,
          turn_kind: opts.turnKind,
          pending_question: null,
          pending_decision: pendingDecisionRef.current,
          decision_memory: decisionMemoryRef.current,
          resolved_fields: intentRef.current.resolvedFields || [],
          session_id: opts.request.sessionId,
          intent_snapshot: opts.request.intentSnapshot,
          answered: intentRef.current.answered as unknown as WorkflowRecord,
          local_summary: "",
          allow_server_default: true,
          gap_report: assessStrategyGaps(intentRef.current) as unknown as WorkflowRecord,
          dialogue_history: dialogueHistoryRef.current,
          ...(opts.phase === "awaiting_confirm"
            ? { pending_strategy_snapshot: opts.request.intentSnapshot }
            : {}),
        } as Parameters<typeof grillTurn>[0] & {
          pending_strategy_snapshot?: WorkflowRecord;
        };
        const turn = await grillTurn(payload, controller.signal);
        if (controller.signal.aborted) {
          if (isRequestIdentityCurrent(opts.request)) abandonDialogueSession();
          return null;
        }
        return turn;
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err || "unknown");
        if (isRequestIdentityCurrent(opts.request)) {
          if (controller.signal.aborted) {
            abandonDialogueSession();
          } else {
            lastGrillErrorRef.current = msg.slice(0, 180);
          }
        }
        return null;
      } finally {
        window.clearTimeout(timer);
        opts.signal?.removeEventListener("abort", onOuterAbort);
        inFlightGrillControllersRef.current.delete(controller);
      }
    },
    [abandonDialogueSession, isRequestIdentityCurrent],
  );

  const messaging = useMemo<PublicConfigMessaging>(
    () => ({
      skipWelcome: true,
      messageTimeoutSecs: 0,
      messageLoadingIndicatorTimeoutSecs: 1,
      showStopButtonImmediately: true,
      async customSendMessage(request: MessageRequest, requestOptions, instance) {
        instanceRef.current = instance;
        const prompt = text(request.input.text);
        const responseId = `msg:${crypto.randomUUID()}`;
        const events: TimelineEvent[] = [];
        let lastBody = "";
        const reply = async (body: string, state: MessageState = MessageState.COMPLETE) => {
          const safeBody = String(body || "").trim() || lastBody || "…";
          if (String(body || "").trim()) lastBody = String(body).trim();
          await instance.messaging.upsertMessage(responseId, state, () =>
            timelineMessage(responseId, safeBody, [...events], {
              requestId: request.id,
            }),
          );
        };
        const action = (line: string) => {
          events.push(tl.action(line));
        };
        const toolCall = async (name: string, ok: boolean, detail: string, elapsedMs?: number) => {
          events.push(tl.tool(name, ok ? "ok" : "fallback", detail, elapsedMs));
          // Progressive Codex-like refresh so tool cards appear before the final answer.
          await reply(lastBody || "处理中…", MessageState.STREAMING);
        };

        if (!prompt) {
          await reply("随便说你的数据需求就行，也可以问我术语。");
          return;
        }

        let activeRequest: AgentTurnRequestContext | null = null;
        let expectedSnapshotKey = "";
        const requestIsCurrent = () => activeRequest != null
          && isRequestIdentityCurrent(activeRequest)
          && expectedSnapshotKey === intentSnapshotKey(intentRef.current);

        try {
          // Next-step navigation after done
          if (phaseRef.current === "done" || phaseRef.current === "failed") {
            const step = detectNextStepCommand(prompt);
            if (step) {
              const tab = step === "single" ? 1 : step === "batch" ? 2 : 3;
              onNavigate?.(tab);
              await reply(
                step === "single"
                  ? "已切到「单文件处理」。请在该页填写 accession 后手动开始。"
                  : step === "batch"
                    ? "已切到「批量处理」。请上传/粘贴清单后手动开始。"
                    : "已切到「AI-ready 构建」。请确认输入后手动开始。",
              );
              return;
            }
          }

          if (phaseRef.current === "running") {
            await reply("当前已有数据发现任务在跑。可点停止取消，或等它完成。");
            return;
          }

          // D1 normal online path: one grill-turn owns the reply and action.
          // Natural-language confirmation also goes through this Agent boundary.
          const fallbackPhase = phaseRef.current;
          // Completion is a checkpoint in the same scientific conversation,
          // not a new questionnaire. Re-enter dialogue with the accepted card,
          // SDK session, history, and decision memory intact. Only Restart
          // performs a full reset.
          const turnPhase: GrillPhase = fallbackPhase === "done" || fallbackPhase === "failed"
            ? "grilling"
            : fallbackPhase;
          const capturedSnapshot = captureIntentSnapshot(intentRef.current);
          activeRequest = {
            sessionId: dialogueSessionIdRef.current,
            generation: dialogueGenerationRef.current,
            ...capturedSnapshot,
          };
          expectedSnapshotKey = capturedSnapshot.snapshotKey;
          setPhase("grilling");
          await reply("正在结合当前策略思考…", MessageState.STREAMING);
          if (!requestIsCurrent()) return;
          const onlineStarted = performance.now();
          const agentTurn = await callGrillTurn(prompt, {
            phase: turnPhase,
            turnKind: "agent_turn",
            request: activeRequest,
            signal: requestOptions.signal,
          });
          if (!requestIsCurrent()) return;
          if (agentTurn) {
            await toolCall(
              "agent-turn",
              true,
              `${agentTurn.action} · ${agentTurn.tool_calls.length} tool call(s)`,
              Math.round(performance.now() - onlineStarted),
            );
            if (!requestIsCurrent()) return;
            const verificationEvent = semanticVerificationTimelineEvent(
              agentTurn.semantic_verification,
            );
            if (verificationEvent) {
              events.push(verificationEvent);
              await reply(lastBody || "处理中…", MessageState.STREAMING);
              if (!requestIsCurrent()) return;
            }
            pendingDecisionRef.current = agentTurn.next_decision;
            decisionMemoryRef.current = agentTurn.decision_memory;
            const reduction = reduceAgentTurn(intentRef.current, agentTurn, { phase: turnPhase });
            if (reduction.strategyUpdated || reduction.confirmationAccepted) {
              setIntent(reduction.spec);
              expectedSnapshotKey = intentSnapshotKey(reduction.spec);
            }
            if (reduction.strategyUpdated && agentTurn.strategy_patch) {
              await toolCall(
                "update_strategy",
                true,
                `更新字段：${Object.keys(agentTurn.strategy_patch).join("、")}`,
              );
              if (!requestIsCurrent()) return;
              action("Agent 已更新策略");
            }
            if (reduction.confirmationRequested) {
              await toolCall(
                "confirm_strategy",
                reduction.confirmationAccepted,
                reduction.confirmationAccepted
                  ? "已在待确认状态接受 Agent 的确认决策"
                  : reduction.confirmationFingerprint == null
                    ? "确认指纹缺失或不匹配；未启动搜索"
                    : "当前并非可接受确认的状态；未启动搜索",
              );
              if (!requestIsCurrent()) return;
            }

            const nextDecisionText = formatAgentNextDecision(agentTurn.next_decision);
            const confirmationGuardText = reduction.confirmationRequested && !reduction.confirmationAccepted
              ? "本轮没有启动搜索。我先保留或展示策略预览，确认只能在策略进入待确认状态后生效。"
              : "";
            const responseBody = joinAssistantParts(
              agentTurn.assistant_message || "我在听，你可以继续用自然语言说明判断。",
              confirmationGuardText,
              nextDecisionText,
              reduction.showConfirmation ? formatConfirmMessage(reduction.spec) : "",
            );
            dialogueHistoryRef.current = appendAgentDialogue(
              dialogueHistoryRef.current,
              prompt,
              responseBody,
            );

            if (reduction.confirmationAccepted) {
              setPhase("awaiting_confirm");
              await reply(responseBody, MessageState.COMPLETE);
              if (!requestIsCurrent() || !reduction.confirmationFingerprint || !activeRequest) return;
              await runDiscovery({
                request: activeRequest,
                agentStrategyFingerprint: reduction.confirmationFingerprint,
              }, request.id, requestOptions.signal);
              return;
            }

            setPhase(reduction.awaitingConfirmation ? "awaiting_confirm" : "grilling");
            await reply(responseBody, MessageState.COMPLETE);
            return;
          }

          const unavailable = reduceAgentUnavailable(intentRef.current, turnPhase);
          setPhase(unavailable.phase);
          await toolCall(
            "agent-turn",
            false,
            lastGrillErrorRef.current
              ? `Agent \u8bf7\u6c42\u5931\u8d25\uff1a${lastGrillErrorRef.current}`
              : "Agent \u672a\u8fd4\u56de\u53ef\u9a8c\u8bc1\u7684\u5bf9\u8bdd\u52a8\u4f5c",
            Math.round(performance.now() - onlineStarted),
          );
          if (!requestIsCurrent()) return;

          const failureReply = unavailable.assistantMessage;
          dialogueHistoryRef.current = appendAgentDialogue(
            dialogueHistoryRef.current,
            prompt,
            failureReply,
          );
          await reply(failureReply, MessageState.COMPLETE);
          return;
        } catch (reason) {
          if (activeRequest && !isRequestIdentityCurrent(activeRequest)) return;
          const msg = reason instanceof Error ? reason.message : String(reason);
          try {
            await reply(`处理时出了点问题：${msg}。本轮没有修改策略，也没有启动搜索；请稍后重试。`);
          } catch {
            // swallow — never bubble to Carbon catastrophic panel
          }
        }
      },
    }),
    [
      callGrillTurn,
      isRequestIdentityCurrent,
      onNavigate,
      runDiscovery,
      setIntent,
      setPhase,
    ],
  );

  return (
    <ChatCustomElement
      className="carbon-chat-host"
      namespace="PRIDE Agent"
      assistantName="蛋白质组学数据 Agent"
      onAfterRender={addWelcomeMessage}
      openChatByDefault
      shouldTakeFocusIfOpensAutomatically={false}
      shouldSanitizeHTML
      aiEnabled
      launcher={{ isOn: false }}
      layout={{ showFrame: false, hasContentMaxWidth: false }}
      header={{
        title: "数据搜集 Agent",
        name: "意图澄清 → 数据发现",
        hideMinimizeButton: true,
        showRestartButton: true,
      }}
      messaging={messaging}
      renderUserDefinedResponse={(state) =>
        renderDiscoveryProgressUserDefined(state) ?? renderCodexUserDefined(state)
      }
      strings={{
        window_title: "蛋白质组学数据 Agent",
        input_placeholder: "用自然语言聊需求，或问术语 / 确认…",
        input_ariaLabel: "描述数据需求",
      }}
    />
  );
}
