/* @vitest-environment jsdom */
import { createElement } from "react";
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  chatProps: null as unknown,
  restartHandler: null as (() => void) | null,
  grillTurn: vi.fn(),
  startDiscoveryJob: vi.fn(),
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
}));

vi.mock("./strategy-fingerprint", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  canonicalDiscoveryPayloadFingerprint: mocks.executionFingerprint,
}));

import { CarbonAgentChat, type GrillControls } from "./CarbonAgentChat";
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

async function mountChat() {
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
  mocks.executionFingerprint.mockReset();
  mocks.executionFingerprint.mockResolvedValue("d".repeat(64));
  window.sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("dialogue session lifecycle", () => {
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
    const firstPayload = mocks.grillTurn.mock.calls[0][0] as { session_id: string };
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

    expect(chat.instance.messaging.upsertMessage).not.toHaveBeenCalled();
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
      pending_decision: typeof horizonDecision;
      decision_memory: typeof decisionMemory;
    };
    expect(continuationPayload.session_id).toBe(firstPayload.session_id);
    expect(continuationPayload.intent_snapshot).toMatchObject({
      task_type: "denovo",
      run_horizon: "plan_only",
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
    expect(continuationPayload.pending_decision.options[1].strategy_patch).toEqual({
      runHorizon: "candidates_reviewed",
    });
    expect(continuationPayload.decision_memory).toEqual(decisionMemory);

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
    expect(mocks.startDiscoveryJob).not.toHaveBeenCalled();
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
      }),
    );
    expect(mocks.startDiscoveryJob.mock.calls[0][0]).toMatchObject({
      grill_confirmed: true,
      strategy_fingerprint: "d".repeat(64),
    });
  });

  it("records a plan-only confirmation without starting repository discovery", async () => {
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
    const chat = await mountChat();

    await sendMessage(chat.props, chat.instance, "Only make a plan.", "request-plan");
    await sendMessage(chat.props, chat.instance, "Confirm the plan.", "request-plan-confirm");

    expect(mocks.startDiscoveryJob).not.toHaveBeenCalled();
    expect(chat.onJob).not.toHaveBeenCalled();
    expect(chat.onPhaseChange.mock.calls.map(([phase]) => phase)).toContain("done");
    expect(
      (chat.instance.messaging.addMessage.mock.calls as unknown as Array<[unknown]>).some((call) =>
        JSON.stringify(call[0]).includes("没有访问 PRIDE"),
      ),
    ).toBe(true);
  });

  it.each(["ai_ready_table", "pre_release", "full_release"] as const)(
    "does not silently downgrade %s to plain repository discovery",
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
      const chat = await mountChat();

      await sendMessage(chat.props, chat.instance, "Prepare the staged result.");
      await sendMessage(chat.props, chat.instance, "Confirm.");

      expect(mocks.startDiscoveryJob).not.toHaveBeenCalled();
      expect(chat.onPhaseChange.mock.calls.map(([phase]) => phase)).toContain("grilling");
      expect(
        (chat.instance.messaging.addMessage.mock.calls as unknown as Array<[unknown]>).some((call) =>
          JSON.stringify(call[0]).includes("不会偷偷降级"),
        ),
      ).toBe(true);
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
  ] as const)("surfaces semantic verifier verdict %s honestly", async (
    verdict,
    verified,
    expectedDetail,
    expectedStatus,
  ) => {
    mocks.grillTurn.mockResolvedValueOnce(decodeAgentTurnResponse({
      status: "completed",
      action: "advise",
      assistant_message: "Verification outcome recorded.",
      tool_calls: [],
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
