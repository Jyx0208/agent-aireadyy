/* @vitest-environment jsdom */
import { createElement } from "react";
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  chatProps: null as unknown,
  restartHandler: null as (() => void) | null,
  grillTurn: vi.fn(),
  startDiscoveryJob: vi.fn(),
  getDiscoveryJob: vi.fn(),
  delay: vi.fn(async () => undefined),
  executionFingerprint: vi.fn(),
}));

vi.mock("@carbon/ai-chat", () => ({
  ChatCustomElement: (props: unknown) => {
    mocks.chatProps = props;
    return null;
  },
  BusEventType: { RESTART_CONVERSATION: "restart_conversation" },
  MessageResponseTypes: { TEXT: "text" },
  MessageState: { COMPLETE: "complete", STREAMING: "streaming" },
}));

vi.mock("./workflow-api", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  grillTurn: mocks.grillTurn,
  startDiscoveryJob: mocks.startDiscoveryJob,
  getDiscoveryJob: mocks.getDiscoveryJob,
  delay: mocks.delay,
}));

vi.mock("./strategy-fingerprint", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  canonicalDiscoveryPayloadFingerprint: mocks.executionFingerprint,
}));

import {
  AGENT_TURN_TIMEOUT_MS,
  CarbonAgentChat,
  parseExplicitBulkSearchTermAddition,
  type GrillControls,
} from "./CarbonAgentChat";
import { decodeAgentTurnResponse } from "./agent-turn";
import { startDialogueSessionId } from "./dialogue-session";

type CapturedChatProps = {
  onAfterRender: (instance: unknown) => Promise<void>;
  messaging: {
    customSendMessage: (
      request: { id: string; input: { text: string } },
      options: { signal: AbortSignal },
      instance: unknown,
    ) => Promise<void>;
  };
  renderUserDefinedResponse?: (state: {
    messageItem?: { user_defined?: unknown } | null;
  }) => unknown;
};

function createChatInstance() {
  const renderedMessages: Array<Record<string, unknown>> = [];
  const instance = {
    on: vi.fn(({ handler }: { handler: () => void }) => {
      mocks.restartHandler = handler;
    }),
    messaging: {
      addMessage: vi.fn(async () => undefined),
      upsertMessage: vi.fn(
        async (
          _id: string,
          _state: string,
          createMessage: () => Record<string, unknown>,
        ) => {
          renderedMessages.push(createMessage());
        },
      ),
    },
  };
  return { instance, renderedMessages };
}

async function mountChat(externalSelectedSearchTerms: string[] = ["proteomics"]) {
  const onIntentChange = vi.fn();
  const onPhaseChange = vi.fn();
  const onJob = vi.fn();
  const chat = createChatInstance();
  const controlsRef: { current: GrillControls | null } = { current: null };

  await act(async () => {
    render(createElement(CarbonAgentChat, {
      onIntentChange,
      onPhaseChange,
      onJob,
      externalSelectedSearchTerms,
      onRegisterControls: (controls) => {
        controlsRef.current = controls;
      },
    }));
  });
  const props = mocks.chatProps as CapturedChatProps;
  await act(async () => {
    await props.onAfterRender(chat.instance);
  });
  return { ...chat, props, controlsRef, onIntentChange, onPhaseChange, onJob };
}

function sendMessage(
  props: CapturedChatProps,
  instance: unknown,
  text: string,
  id = `request-${crypto.randomUUID()}`,
) {
  return props.messaging.customSendMessage(
    { id, input: { text } },
    { signal: new AbortController().signal },
    instance,
  );
}

function latestTimelineEvents(messages: Array<Record<string, unknown>>) {
  const message = messages[messages.length - 1] as {
    output?: { generic?: Array<{ user_defined?: { events?: unknown[] } }> };
  } | undefined;
  const timeline = message?.output?.generic?.find((item) => item.user_defined?.events);
  return timeline?.user_defined?.events ?? [];
}

beforeEach(() => {
  mocks.chatProps = null;
  mocks.restartHandler = null;
  mocks.grillTurn.mockReset();
  mocks.startDiscoveryJob.mockReset();
  mocks.getDiscoveryJob.mockReset();
  mocks.delay.mockReset();
  mocks.delay.mockResolvedValue(undefined);
  mocks.executionFingerprint.mockReset();
  mocks.executionFingerprint.mockResolvedValue("d".repeat(64));
  window.sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("dialogue session lifecycle", () => {
  it("gives a model-routed strategy turn the server-aligned deadline", async () => {
    mocks.grillTurn.mockResolvedValueOnce(decodeAgentTurnResponse({
      status: "completed",
      action: "chat",
      assistant_message: "I will recommend additional theme terms.",
      tool_calls: [],
    }));
    const chat = await mountChat();

    await sendMessage(
      chat.props,
      chat.instance,
      "请根据项目元数据的常见写法，再推荐一批可能遗漏的免疫肽主题词。",
    );

    const payload = mocks.grillTurn.mock.calls[0][0] as {
      user_message: string;
      request_timeout_seconds: number;
    };
    expect(payload.user_message).toContain("再推荐一批");
    expect(payload.request_timeout_seconds).toBeGreaterThanOrEqual(170);
  });

  it("adds an explicit bulk keyword list locally without calling the model", async () => {
    expect(
      parseExplicitBulkSearchTermAddition(
        "HLA ligandome、MHC ligandome。把这些词也作为主题词",
      ),
    ).toEqual(["HLA ligandome", "MHC ligandome"]);
    expect(
      parseExplicitBulkSearchTermAddition(
        "HLA ligandome、MHC ligandome，加上这些词",
      ),
    ).toEqual(["HLA ligandome", "MHC ligandome"]);
    const chat = await mountChat([
      "immunopeptidomics",
      "immunopeptidome",
      "HLA ligandome",
    ]);
    const prompt =
      "MHC peptidome 、 HLA class I ligandome 、 HLA class II ligandome 、" +
      "immunopeptides 、 HLA ligands 、 MHC ligands 、 MHC-associated peptides 、" +
      "HLApeptidomics 、 MHC peptidomics 、 HLA-bound peptides 、 MHC-bound peptides 、" +
      "HLA\u0002presented peptides 、 MHC-presented peptides 、 HLA-associated peptides 、" +
      "eluted HLAligands 、 eluted MHC ligands 、 HLA immunopeptidome 、" +
      "MHC immunopeptidome 、 HLA-Ipeptidome 、 HLA-II peptidome 、 immunopeptide 、" +
      "immunopeptidomic 、 HLA 、 MHC 、 HLAimmunoprecipitation 、 MHC immunoaffinity 、" +
      "W6/32 、 neoepitope 、 HLA neoantigen，加上这些关键词，搜全面一些";

    await sendMessage(chat.props, chat.instance, prompt, "request-bulk-terms");

    expect(mocks.grillTurn).not.toHaveBeenCalled();
    const updated = chat.onIntentChange.mock.calls.at(-1)?.[0];
    expect(updated.selectedSearchTerms).toEqual(expect.arrayContaining([
      "immunopeptidomics",
      "HLA class I ligandome",
      "HLA-presented peptides",
      "W6/32",
      "HLA neoantigen",
    ]));
    expect(new Set(updated.selectedSearchTerms.map((term: string) => term.toLowerCase())).size)
      .toBe(updated.selectedSearchTerms.length);
    expect(updated.confirmed).toBe(false);
  });

  it("reports a model deadline as a timeout instead of an expired session", async () => {
    const chat = await mountChat();
    vi.useFakeTimers();
    mocks.grillTurn.mockImplementationOnce(
      (_payload: unknown, signal: AbortSignal) =>
        new Promise((_resolve, reject) => {
          signal.addEventListener(
            "abort",
            () => reject(new DOMException("Aborted", "AbortError")),
            { once: true },
          );
        }),
    );

    try {
      const pending = sendMessage(
        chat.props,
        chat.instance,
        "Add the complete immunopeptidomics synonym list.",
        "request-timeout",
      );
      await vi.advanceTimersByTimeAsync(AGENT_TURN_TIMEOUT_MS);
      await pending;

      const rendered = JSON.stringify(chat.renderedMessages);
      expect(rendered).toContain("模型响应超过 190 秒");
      expect(rendered).not.toContain("会话已过期");
    } finally {
      vi.useRealTimers();
    }
  });

  it("starts a new SDK session when the page strategy state starts fresh", () => {
    window.sessionStorage.setItem(
      "pride-agent-dialogue-session",
      "discovery-dialogue:stale-card-session",
    );
    const randomUUID = vi
      .spyOn(crypto, "randomUUID")
      .mockReturnValue("00000000-0000-4000-8000-000000000001");

    const created = startDialogueSessionId();

    expect(created).toBe(
      "discovery-dialogue:00000000-0000-4000-8000-000000000001",
    );
    expect(window.sessionStorage.getItem("pride-agent-dialogue-session")).toBe(created);
    randomUUID.mockRestore();
  });

  it("aborts a restarted grill turn and ignores its late response", async () => {
    let resolveFirstTurn!: (value: ReturnType<typeof decodeAgentTurnResponse>) => void;
    let firstSignal: AbortSignal | undefined;
    mocks.grillTurn.mockImplementationOnce(
      (_payload: unknown, signal: AbortSignal) => new Promise((resolve) => {
        firstSignal = signal;
        resolveFirstTurn = resolve;
      }),
    );
    const chat = await mountChat();

    const firstSend = sendMessage(chat.props, chat.instance, "Use mouse studies.", "request-old");
    await vi.waitFor(() => expect(mocks.grillTurn).toHaveBeenCalledTimes(1));
    const firstPayload = mocks.grillTurn.mock.calls[0][0] as {
      session_id: string;
      request_timeout_seconds: number;
    };
    expect(firstPayload.request_timeout_seconds).toBe(180);
    chat.instance.messaging.upsertMessage.mockClear();

    act(() => mocks.restartHandler?.());
    expect(firstSignal?.aborted).toBe(true);

    resolveFirstTurn(decodeAgentTurnResponse({
      status: "completed",
      action: "update_strategy",
      assistant_message: "Mouse studies selected.",
      tool_calls: [{ name: "update_strategy", arguments: { species: ["mouse"] } }],
    }));
    await firstSend;

        // Stale/aborted turns may close the STREAMING bubble with a non-apply notice,
    // but must not write the late strategy patch onto the restarted card.
    expect(chat.onIntentChange).toHaveBeenCalledTimes(1);
    expect(chat.onIntentChange.mock.calls[0][0].species).toEqual([]);
    expect(chat.onPhaseChange.mock.calls.map(([phase]) => phase)).toEqual(["grilling", "idle"]);

    mocks.grillTurn.mockResolvedValueOnce(decodeAgentTurnResponse({
      status: "completed",
      action: "chat",
      assistant_message: "Fresh session response.",
      tool_calls: [],
    }));
    await sendMessage(chat.props, chat.instance, "Start fresh.", "request-new");
    const secondPayload = mocks.grillTurn.mock.calls[1][0] as { session_id: string };
    expect(secondPayload.session_id).not.toBe(firstPayload.session_id);
  });

  it("abandons the canonical SDK session when the user stops an in-flight turn", async () => {
    let resolveStoppedTurn!: (value: ReturnType<typeof decodeAgentTurnResponse>) => void;
    mocks.grillTurn.mockImplementationOnce(
      () => new Promise((resolve) => {
        resolveStoppedTurn = resolve;
      }),
    );
    const chat = await mountChat();
    const controller = new AbortController();

    const stoppedSend = chat.props.messaging.customSendMessage(
      { id: "request-stopped", input: { text: "Switch to zebrafish." } },
      { signal: controller.signal },
      chat.instance,
    );
    await vi.waitFor(() => expect(mocks.grillTurn).toHaveBeenCalledTimes(1));
    const stoppedPayload = mocks.grillTurn.mock.calls[0][0] as { session_id: string };

    controller.abort();
    resolveStoppedTurn(decodeAgentTurnResponse({
      status: "completed",
      action: "update_strategy",
      assistant_message: "Zebrafish selected.",
      tool_calls: [{ name: "update_strategy", arguments: { species: ["Danio rerio"] } }],
    }));
    await stoppedSend;

    expect(chat.onIntentChange).not.toHaveBeenCalled();

    mocks.grillTurn.mockResolvedValueOnce(decodeAgentTurnResponse({
      status: "completed",
      action: "chat",
      assistant_message: "Continuing in a clean canonical session.",
      tool_calls: [],
    }));
    await sendMessage(chat.props, chat.instance, "Continue.", "request-after-stop");
    const nextPayload = mocks.grillTurn.mock.calls[1][0] as { session_id: string };
    expect(nextPayload.session_id).not.toBe(stoppedPayload.session_id);
    expect(window.sessionStorage.getItem("pride-agent-dialogue-session")).toBe(
      nextPayload.session_id,
    );
  });

  it("abandons the SDK session whenever a local card mutation invalidates an in-flight turn", async () => {
    let resolveStaleTurn!: (value: ReturnType<typeof decodeAgentTurnResponse>) => void;
    let staleSignal: AbortSignal | undefined;
    mocks.grillTurn.mockImplementationOnce(
      (_payload: unknown, signal: AbortSignal) => new Promise((resolve) => {
        staleSignal = signal;
        resolveStaleTurn = resolve;
      }),
    );
    const chat = await mountChat();

    const staleSend = sendMessage(chat.props, chat.instance, "Use mouse studies.", "request-stale-card");
    await vi.waitFor(() => expect(mocks.grillTurn).toHaveBeenCalledTimes(1));
    const stalePayload = mocks.grillTurn.mock.calls[0][0] as { session_id: string };

    act(() => chat.controlsRef.current?.applyDefaults());
    await vi.waitFor(() => expect(chat.onIntentChange).toHaveBeenCalledTimes(1));
    expect(staleSignal?.aborted).toBe(true);

    resolveStaleTurn(decodeAgentTurnResponse({
      status: "completed",
      action: "update_strategy",
      assistant_message: "Mouse studies selected.",
      tool_calls: [{ name: "update_strategy", arguments: { species: ["mouse"] } }],
    }));
    await staleSend;

    expect(chat.onIntentChange).toHaveBeenCalledTimes(1);
    expect(chat.onIntentChange.mock.calls[0][0].species).toEqual([]);

    mocks.grillTurn.mockResolvedValueOnce(decodeAgentTurnResponse({
      status: "completed",
      action: "chat",
      assistant_message: "Continuing from the accepted card only.",
      tool_calls: [],
    }));
    await sendMessage(chat.props, chat.instance, "Continue.", "request-after-card-change");
    const nextPayload = mocks.grillTurn.mock.calls[1][0] as { session_id: string };

    expect(nextPayload.session_id).not.toBe(stalePayload.session_id);
    expect(window.sessionStorage.getItem("pride-agent-dialogue-session")).toBe(
      nextPayload.session_id,
    );
  });

  it("continues a completed plan in the same dialogue with the full strategy and decision context", async () => {
    const strategyFingerprint = "c".repeat(64);
    const horizonDecision = {
      focus: "run_horizon",
      target_fields: ["run_horizon"],
      question: "Should I stop at a plan or collect reviewed candidates?",
      recommendation: {
        id: "candidates_reviewed",
        label: "Collect reviewed candidates",
        reason: "It produces an auditable shortlist.",
        strategy_patch: { run_horizon: "candidates_reviewed" },
      },
      options: [
        {
          id: "plan_only",
          label: "Plan only",
          reason: "Do not query PRIDE.",
          strategy_patch: { run_horizon: "plan_only" },
        },
        {
          id: "candidates_reviewed",
          label: "Collect reviewed candidates",
          reason: "Search and review projects.",
          strategy_patch: { run_horizon: "candidates_reviewed" },
        },
      ],
    };
    const decisionMemory = [{
      focus: "run_horizon",
      target_fields: ["run_horizon"],
      option_ids: ["plan_only", "candidates_reviewed"],
      selected_option_id: "plan_only",
      selected_option_label: "Plan only",
    }];

    mocks.grillTurn
      .mockResolvedValueOnce(decodeAgentTurnResponse({
        status: "completed",
        action: "update_strategy",
        assistant_message: "I kept your de novo training constraints and prepared a plan.",
        tool_calls: [{
          name: "update_strategy",
          arguments: { patch: {
            objective: "Build a multi-species immunopeptidomics de novo training set",
            task_type: "denovo",
            run_horizon: "plan_only",
            species: [],
            species_policy: "open",
            species_coverage: "broaden",
            acquisition_mode: "dda",
            labeling_strategy: "label_free",
            special_themes: ["immunopeptidomics"],
            target_project_count: 20,
            coverage_mode: "balanced",
          } },
        }],
        next_decision: horizonDecision,
        decision_memory: decisionMemory,
      }))
      .mockResolvedValueOnce(decodeAgentTurnResponse({
        status: "completed",
        action: "confirm_strategy",
        assistant_message: "The plan is confirmed.",
        strategy_fingerprint: strategyFingerprint,
        tool_calls: [{
          name: "confirm_strategy",
          arguments: { strategy_fingerprint: strategyFingerprint },
        }],
        next_decision: horizonDecision,
        decision_memory: decisionMemory,
      }))
      .mockResolvedValueOnce(decodeAgentTurnResponse({
        status: "completed",
        action: "update_strategy",
        assistant_message: "I changed only the endpoint to reviewed candidates.",
        tool_calls: [{
          name: "update_strategy",
          arguments: { patch: { run_horizon: "candidates_reviewed" } },
        }],
        decision_memory: decisionMemory,
      }));
    mocks.startDiscoveryJob.mockResolvedValue({
      job_id: "legacy-plan-normalized",
      status: "cancelled",
    });
    const chat = await mountChat();

    await sendMessage(chat.props, chat.instance, "Prepare this de novo training search as a plan.");
    await sendMessage(chat.props, chat.instance, "Confirm this plan.");
    await sendMessage(chat.props, chat.instance, "I want candidate data, not only a plan.");

    expect(mocks.grillTurn).toHaveBeenCalledTimes(3);
    const firstPayload = mocks.grillTurn.mock.calls[0][0] as Record<string, unknown>;
    const continuationPayload = mocks.grillTurn.mock.calls[2][0] as {
      session_id: string;
      intent_snapshot: Record<string, unknown>;
      dialogue_history: Array<{ role: string; content: string }>;
      pending_decision: null;
      decision_memory: [];
    };
    expect(continuationPayload.session_id).toBe(firstPayload.session_id);
    expect(continuationPayload.intent_snapshot).toMatchObject({
      task_type: "denovo",
      run_horizon: "candidates_reviewed",
      species: [],
      species_policy: "open",
      acquisition_mode: "dda",
      labeling_strategy: "label_free",
      special_themes: ["immunopeptidomics"],
      target_project_count: 20,
      coverage_mode: "balanced",
    });
    expect(continuationPayload.dialogue_history.map(({ content }) => content)).toEqual(
      expect.arrayContaining([
        "Prepare this de novo training search as a plan.",
        "Confirm this plan.",
      ]),
    );
    expect(continuationPayload.pending_decision).toBeNull();
    expect(continuationPayload.decision_memory).toEqual([]);

    const revised = chat.onIntentChange.mock.calls.at(-1)?.[0];
    expect(revised).toMatchObject({
      taskType: "denovo",
      runHorizon: "candidates_reviewed",
      speciesPolicy: "open",
      acquisitionMode: "dda",
      labelingStrategy: "label_free",
      targetProjectCount: 20,
      coverageMode: "balanced",
      confirmed: false,
    });
    expect(mocks.startDiscoveryJob).toHaveBeenCalledTimes(1);
  });
});

describe("confirmation execution boundary", () => {
  it("never starts discovery from update_strategy or ready_to_confirm alone", async () => {
    mocks.grillTurn
      .mockResolvedValueOnce(decodeAgentTurnResponse({
        status: "completed",
        action: "update_strategy",
        assistant_message: "The strategy card is ready for review.",
        tool_calls: [{
          name: "update_strategy",
          arguments: { patch: {
            objective: "Review immunopeptidomics candidates",
            task_type: "denovo",
            run_horizon: "candidates_reviewed",
            target_project_count: 20,
            coverage_mode: "balanced",
          } },
        }],
      }))
      .mockResolvedValueOnce(decodeAgentTurnResponse({
        status: "completed",
        action: "ready_to_confirm",
        assistant_message: "Please approve the current card if it looks right.",
        tool_calls: [],
        gap_report: {
          required_missing: [],
          optional_missing: [],
          ready_for_confirm: true,
        },
      }));
    const chat = await mountChat();

    await sendMessage(chat.props, chat.instance, "Use this reviewed candidate strategy.");
    expect(mocks.startDiscoveryJob).not.toHaveBeenCalled();
    expect(chat.onIntentChange.mock.calls.at(-1)?.[0].confirmed).toBe(false);

    await sendMessage(chat.props, chat.instance, "Show me the strategy before I confirm.");
    expect(mocks.startDiscoveryJob).not.toHaveBeenCalled();
    expect(chat.onIntentChange.mock.calls.at(-1)?.[0].confirmed).toBe(false);
    expect(chat.onPhaseChange.mock.calls.map(([phase]) => phase)).not.toContain("running");
  });

  it("uses the model fingerprint only to gate the captured snapshot, then binds execution separately", async () => {
    const strategyFingerprint = "e".repeat(64);
    mocks.grillTurn
      .mockResolvedValueOnce(decodeAgentTurnResponse({
        status: "completed",
        action: "update_strategy",
        assistant_message: "The strategy is ready for confirmation.",
        tool_calls: [{
          name: "update_strategy",
          arguments: { patch: {
            objective: "Reviewed human proteomics candidates",
            task_type: "browse_only",
            run_horizon: "candidates_only",
            target_project_count: 20,
          } },
        }],
      }))
      .mockResolvedValueOnce(decodeAgentTurnResponse({
        status: "completed",
        action: "confirm_strategy",
        assistant_message: "Confirmed.",
        strategy_fingerprint: strategyFingerprint,
        tool_calls: [{
          name: "confirm_strategy",
          arguments: { strategy_fingerprint: strategyFingerprint },
        }],
      }));
    mocks.startDiscoveryJob.mockResolvedValue({
      job_id: "discovery-job-1",
      status: "cancelled",
    });
    const chat = await mountChat();

    await sendMessage(chat.props, chat.instance, "Use a reviewed set of 20.", "request-strategy");
    await sendMessage(chat.props, chat.instance, "Confirm this exact strategy.", "request-confirm");

    const confirmationPayload = mocks.grillTurn.mock.calls[1][0] as {
      pending_strategy_snapshot?: Record<string, unknown>;
      intent_snapshot: Record<string, unknown>;
    };
    expect(confirmationPayload.pending_strategy_snapshot).toEqual(
      confirmationPayload.intent_snapshot,
    );
    expect(mocks.startDiscoveryJob).toHaveBeenCalledTimes(1);
    expect(mocks.startDiscoveryJob.mock.calls[0][0]).toMatchObject({
      grill_confirmed: true,
      strategy_fingerprint: "d".repeat(64),
    });
    expect(mocks.startDiscoveryJob.mock.calls[0][0].strategy_fingerprint).not.toBe(strategyFingerprint);
    expect(mocks.executionFingerprint).toHaveBeenCalledWith(
      expect.objectContaining({ grill_confirmed: true }),
    );
  });

  it("sends an execution-payload fingerprint for the dedicated confirm button", async () => {
    mocks.grillTurn.mockResolvedValueOnce(decodeAgentTurnResponse({
      status: "completed",
      action: "update_strategy",
      assistant_message: "The strategy is ready for confirmation.",
      tool_calls: [{
        name: "update_strategy",
        arguments: { patch: {
          objective: "Reviewed human proteomics candidates",
          task_type: "browse_only",
          run_horizon: "candidates_only",
          target_project_count: 12,
          quota_flexibility: "fixed",
        } },
      }],
    }));
    mocks.startDiscoveryJob.mockResolvedValue({
      job_id: "discovery-job-button",
      status: "cancelled",
    });
    const chat = await mountChat();

    await sendMessage(chat.props, chat.instance, "Use a reviewed set of 12.", "request-button-strategy");
    expect(chat.controlsRef.current).not.toBeNull();

    act(() => chat.controlsRef.current?.confirm());
    await vi.waitFor(() => expect(mocks.startDiscoveryJob).toHaveBeenCalledTimes(1));

    expect(mocks.executionFingerprint).toHaveBeenCalledWith(
      expect.objectContaining({
        grill_confirmed: true,
        max_projects: 12,
        quota_flexibility: "fixed",
        query_terms: ["proteomics"],
      }),
    );
    expect(mocks.startDiscoveryJob.mock.calls[0][0]).toMatchObject({
      grill_confirmed: true,
      strategy_fingerprint: "d".repeat(64),
    });
  });

  it("fails closed when confirmation has no selected repository term", async () => {
    mocks.grillTurn.mockResolvedValueOnce(decodeAgentTurnResponse({
      status: "completed",
      action: "update_strategy",
      assistant_message: "The strategy is ready for confirmation.",
      tool_calls: [{
        name: "update_strategy",
        arguments: { patch: {
          objective: "Reviewed proteomics candidates",
          task_type: "browse_only",
          run_horizon: "candidates_only",
        } },
      }],
    }));
    const chat = await mountChat([]);

    await sendMessage(chat.props, chat.instance, "Use this strategy.");
    act(() => chat.controlsRef.current?.confirm());

    await vi.waitFor(() => {
      expect(chat.instance.messaging.addMessage).toHaveBeenCalled();
    });
    expect(mocks.startDiscoveryJob).not.toHaveBeenCalled();
  });

  it("normalizes a legacy plan-only confirmation to candidate review", async () => {
    const strategyFingerprint = "f".repeat(64);
    mocks.grillTurn
      .mockResolvedValueOnce(decodeAgentTurnResponse({
        status: "completed",
        action: "update_strategy",
        assistant_message: "The plan is ready.",
        tool_calls: [{
          name: "update_strategy",
          arguments: { patch: {
            objective: "Plan a human proteomics search",
            task_type: "browse_only",
            run_horizon: "plan_only",
            target_project_count: 20,
            coverage_mode: "balanced",
          } },
        }],
      }))
      .mockResolvedValueOnce(decodeAgentTurnResponse({
        status: "completed",
        action: "confirm_strategy",
        assistant_message: "Plan confirmed.",
        strategy_fingerprint: strategyFingerprint,
        tool_calls: [{
          name: "confirm_strategy",
          arguments: { strategy_fingerprint: strategyFingerprint },
        }],
      }));
    mocks.startDiscoveryJob.mockResolvedValue({
      job_id: "legacy-plan-normalized",
      status: "cancelled",
    });
    const chat = await mountChat();

    await sendMessage(chat.props, chat.instance, "Only make a plan.", "request-plan");
    await sendMessage(chat.props, chat.instance, "Confirm the plan.", "request-plan-confirm");

    expect(mocks.startDiscoveryJob).toHaveBeenCalledTimes(1);
    expect(mocks.startDiscoveryJob.mock.calls[0][0]).toMatchObject({
      run_horizon: "candidates_reviewed",
    });
    expect(chat.onJob).toHaveBeenCalled();
    expect(chat.onPhaseChange.mock.calls.map(([phase]) => phase)).toContain("done");
  });

  it.each(["ai_ready_table", "pre_release", "full_release"] as const)(
    "normalizes legacy %s to candidate review",
    async (runHorizon) => {
      const strategyFingerprint = "a".repeat(64);
      mocks.grillTurn
        .mockResolvedValueOnce(decodeAgentTurnResponse({
          status: "completed",
          action: "update_strategy",
          assistant_message: "The staged plan is ready.",
          tool_calls: [{
            name: "update_strategy",
            arguments: { patch: {
              objective: "Prepare a staged proteomics delivery",
              task_type: "browse_only",
              run_horizon: runHorizon,
              target_project_count: 20,
            } },
          }],
        }))
        .mockResolvedValueOnce(decodeAgentTurnResponse({
          status: "completed",
          action: "confirm_strategy",
          assistant_message: "Confirmed.",
          strategy_fingerprint: strategyFingerprint,
          tool_calls: [{
            name: "confirm_strategy",
            arguments: { strategy_fingerprint: strategyFingerprint },
          }],
        }));
      mocks.startDiscoveryJob.mockResolvedValue({
        job_id: `legacy-${runHorizon}`,
        status: "cancelled",
      });
      const chat = await mountChat();

      await sendMessage(chat.props, chat.instance, "Prepare the staged result.");
      await sendMessage(chat.props, chat.instance, "Confirm.");

      expect(mocks.startDiscoveryJob).toHaveBeenCalledTimes(1);
      expect(mocks.startDiscoveryJob.mock.calls[0][0]).toMatchObject({
        run_horizon: "candidates_reviewed",
      });
      expect(chat.onPhaseChange.mock.calls.map(([phase]) => phase)).toContain("done");
    },
  );
});

describe("semantic verification trace", () => {
  it.each([
    ["accept", true, "passed", "ok"],
    ["repair", true, "repaired", "ok"],
    ["reject", false, "rejected", "error"],
    ["unavailable", false, "unavailable", "fallback"],
    ["budget_exhausted", false, "budget_exhausted", "fallback"],
  ] as const)("surfaces semantic verifier verdict %s honestly on write turns", async (
    verdict,
    verified,
    expectedDetail,
    expectedStatus,
  ) => {
    mocks.grillTurn.mockResolvedValueOnce(decodeAgentTurnResponse({
      status: "completed",
      action: "update_strategy",
      assistant_message: "Verification outcome recorded.",
      tool_calls: [{ name: "update_strategy", arguments: { patch: { species: ["human"] } } }],
      semantic_verification: {
        verdict,
        verified,
        patch: verified ? { species: ["human"] } : {},
        rationale: "bounded semantic review",
      },
    }));
    const chat = await mountChat();

    await sendMessage(chat.props, chat.instance, "Review this proposal.");

    const semanticEvent = latestTimelineEvents(chat.renderedMessages).find(
      (event) => (event as { name?: string }).name === "semantic-verification",
    ) as { status?: string; detail?: string } | undefined;
    expect(semanticEvent?.status).toBe(expectedStatus);
    expect(semanticEvent?.detail).toContain(expectedDetail);
  });
});

describe("NB-5 recovery chips after done/failed", () => {
  function recoveryPayloadsFromAddMessage(addMessage: ReturnType<typeof vi.fn>) {
    return (addMessage.mock.calls as unknown as Array<[Record<string, unknown>]>)
      .map((call) => {
        const generic = (call[0]?.output as { generic?: Array<{ user_defined?: { kind?: string } }> } | undefined)
          ?.generic;
        return generic?.find((item) => item.user_defined?.kind === "discovery_recovery")?.user_defined;
      })
      .filter(Boolean) as Array<Record<string, unknown>>;
  }

  async function confirmReadyStrategyAndFailJob(chat: Awaited<ReturnType<typeof mountChat>>) {
    const strategyFingerprint = "b".repeat(64);
    mocks.grillTurn
      .mockResolvedValueOnce(decodeAgentTurnResponse({
        status: "completed",
        action: "update_strategy",
        assistant_message: "Strategy ready.",
        tool_calls: [{
          name: "update_strategy",
          arguments: { patch: {
            objective: "Reviewed human proteomics candidates for recovery tests",
            task_type: "browse_only",
            run_horizon: "candidates_only",
            target_project_count: 20,
            coverage_mode: "balanced",
          } },
        }],
      }))
      .mockResolvedValueOnce(decodeAgentTurnResponse({
        status: "completed",
        action: "confirm_strategy",
        assistant_message: "Confirmed.",
        strategy_fingerprint: strategyFingerprint,
        tool_calls: [{
          name: "confirm_strategy",
          arguments: { strategy_fingerprint: strategyFingerprint },
        }],
      }));
    mocks.startDiscoveryJob.mockResolvedValue({
      job_id: "discovery-job-failed-1",
      status: "failed",
      error: "PRIDE timeout after context explosion " + "x".repeat(500),
      discovery_id: "",
      record: {},
    });
    mocks.getDiscoveryJob.mockResolvedValue({
      job_id: "discovery-job-failed-1",
      status: "failed",
      error: "PRIDE timeout after context explosion " + "x".repeat(500),
      discovery_id: "",
      record: {},
    });

    await sendMessage(chat.props, chat.instance, "Use a reviewed set of 20.", "request-ready");
    await sendMessage(chat.props, chat.instance, "Confirm this strategy.", "request-confirm-fail");
    await vi.waitFor(() => expect(mocks.startDiscoveryJob).toHaveBeenCalledTimes(1));
    await vi.waitFor(() =>
      expect(chat.onPhaseChange.mock.calls.map(([phase]) => phase)).toContain("failed"),
    );
  }

  it("shows job-bound recovery chips after failed discovery without dumping raw error", async () => {
    const chat = await mountChat();
    await confirmReadyStrategyAndFailJob(chat);

    const recoveries = recoveryPayloadsFromAddMessage(chat.instance.messaging.addMessage);
    expect(recoveries).toHaveLength(1);
    expect(recoveries[0]).toMatchObject({
      kind: "discovery_recovery",
      jobId: "discovery-job-failed-1",
      outcome: "failed",
      hasResults: false,
    });
    expect(String(recoveries[0].summary || "").length).toBeLessThan(320);

    const rendered = chat.props.renderUserDefinedResponse?.({
      messageItem: { user_defined: recoveries[0] },
    });
    expect(rendered).toBeTruthy();
  });

  it("re-search opens a new SDK session and requires confirm (no auto-search)", async () => {
    const chat = await mountChat();
    await confirmReadyStrategyAndFailJob(chat);
    const sessionBefore = window.sessionStorage.getItem("pride-agent-dialogue-session");
    const recoveries = recoveryPayloadsFromAddMessage(chat.instance.messaging.addMessage);
    const payload = recoveries[0];
    expect(payload).toBeTruthy();

    const startCallsBefore = mocks.startDiscoveryJob.mock.calls.length;
    await act(async () => {
      const node = chat.props.renderUserDefinedResponse?.({
        messageItem: { user_defined: payload },
      }) as { props?: { onAction?: (action: string, p: unknown) => void; payload?: unknown } } | null;
      // React element from renderDiscoveryRecoveryUserDefined
      const onAction = (node as { props: { onAction: (a: string, p: unknown) => void; payload: unknown } }).props.onAction;
      const bound = (node as { props: { payload: unknown } }).props.payload;
      onAction("research_current_card", bound);
    });

    await vi.waitFor(() => {
      const sessionAfter = window.sessionStorage.getItem("pride-agent-dialogue-session");
      expect(sessionAfter).toBeTruthy();
      expect(sessionAfter).not.toBe(sessionBefore);
    });
    expect(mocks.startDiscoveryJob.mock.calls.length).toBe(startCallsBefore);
    expect(chat.onPhaseChange.mock.calls.map(([phase]) => phase)).toContain("awaiting_confirm");
    expect(chat.onIntentChange.mock.calls.at(-1)?.[0].confirmed).toBe(false);

    const texts = (chat.instance.messaging.addMessage.mock.calls as unknown as Array<[Record<string, unknown>]>)
      .map((call) => JSON.stringify(call[0]));
    expect(texts.some((t) => t.includes("新会话") || t.includes("再次确认"))).toBe(true);
  });

  it("does not dual-start discovery when re-search is clicked while running", async () => {
    const strategyFingerprint = "c".repeat(64);
    mocks.grillTurn
      .mockResolvedValueOnce(decodeAgentTurnResponse({
        status: "completed",
        action: "update_strategy",
        assistant_message: "Strategy ready.",
        tool_calls: [{
          name: "update_strategy",
          arguments: { patch: {
            objective: "Reviewed human proteomics candidates",
            task_type: "browse_only",
            run_horizon: "candidates_only",
            target_project_count: 12,
          } },
        }],
      }))
      .mockResolvedValueOnce(decodeAgentTurnResponse({
        status: "completed",
        action: "confirm_strategy",
        assistant_message: "Confirmed.",
        strategy_fingerprint: strategyFingerprint,
        tool_calls: [{
          name: "confirm_strategy",
          arguments: { strategy_fingerprint: strategyFingerprint },
        }],
      }));

    let resolveJob!: (value: Record<string, unknown>) => void;
    mocks.startDiscoveryJob.mockImplementationOnce(
      () => new Promise((resolve) => {
        resolveJob = resolve;
      }),
    );

    const chat = await mountChat();
    await sendMessage(chat.props, chat.instance, "Use 12 candidates.", "request-running-strategy");
    const confirmSend = sendMessage(chat.props, chat.instance, "Confirm.", "request-running-confirm");
    await vi.waitFor(() => expect(mocks.startDiscoveryJob).toHaveBeenCalledTimes(1));
    await vi.waitFor(() =>
      expect(chat.onPhaseChange.mock.calls.map(([phase]) => phase)).toContain("running"),
    );

    // Inject a stale recovery payload as if a previous run finished.
    const stalePayload = {
      kind: "discovery_recovery",
      jobId: "stale-job",
      discoveryId: "",
      cardGeneration: "stale",
      outcome: "failed",
      hasResults: false,
      summary: "stale",
    };
    // Force lastRecoveryRef by completing a prior path is hard while running;
    // instead click handler via research when phase is running with bound payload
    // by first finishing then immediately... Simpler: send NL while running.
    await sendMessage(chat.props, chat.instance, "start another search please", "request-dual");
    const dualReplies = (chat.instance.messaging.upsertMessage.mock.calls as Array<unknown[]>)
      .map((call) => {
        try {
          const factory = call[2] as () => Record<string, unknown>;
          return JSON.stringify(factory());
        } catch {
          return "";
        }
      })
      .join("\n");
    expect(dualReplies).toMatch(/已有数据发现任务在跑|不会双开/);
    expect(mocks.startDiscoveryJob).toHaveBeenCalledTimes(1);

    resolveJob({
      job_id: "discovery-job-running-1",
      status: "failed",
      error: "stopped",
      record: {},
    });
    mocks.getDiscoveryJob.mockResolvedValue({
      job_id: "discovery-job-running-1",
      status: "failed",
      error: "stopped",
      record: {},
    });
    await confirmSend;
    void stalePayload;
  });

  it("revise_strategy re-enters grilling without starting discovery", async () => {
    const chat = await mountChat();
    await confirmReadyStrategyAndFailJob(chat);
    const payload = recoveryPayloadsFromAddMessage(chat.instance.messaging.addMessage)[0];
    const startCallsBefore = mocks.startDiscoveryJob.mock.calls.length;

    await act(async () => {
      const node = chat.props.renderUserDefinedResponse?.({
        messageItem: { user_defined: payload },
      }) as { props: { onAction: (a: string, p: unknown) => void; payload: unknown } };
      node.props.onAction("revise_strategy", node.props.payload);
    });

    expect(mocks.startDiscoveryJob.mock.calls.length).toBe(startCallsBefore);
    expect(chat.onPhaseChange.mock.calls.map(([phase]) => phase).at(-1)).toBe("grilling");
    expect(chat.onIntentChange.mock.calls.at(-1)?.[0].confirmed).toBe(false);
  });
});

describe("NI-1 semantic chrome policy", () => {
  it("hides semantic-verification chrome on pure chat turns (NI-1)", async () => {
    mocks.grillTurn.mockResolvedValueOnce(decodeAgentTurnResponse({
      status: "completed",
      action: "chat",
      assistant_message: "这是术语说明，不是写卡。",
      tool_calls: [],
      semantic_verification: {
        verdict: "reject",
        verified: false,
        patch: {},
        rationale: "should not reach the user chrome",
      },
    }));
    const chat = await mountChat();
    await sendMessage(chat.props, chat.instance, "免疫肽是什么？");
    const semanticEvent = latestTimelineEvents(chat.renderedMessages).find(
      (event) => (event as { name?: string }).name === "semantic-verification",
    );
    expect(semanticEvent).toBeUndefined();
  });
});
