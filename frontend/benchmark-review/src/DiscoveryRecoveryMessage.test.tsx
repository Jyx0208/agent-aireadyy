/* @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, fireEvent } from "@testing-library/react";
import {
  DiscoveryRecoveryMessage,
  formatRecoveryFailureDetail,
  isDiscoveryRecoveryPayload,
  type DiscoveryRecoveryPayload,
} from "./DiscoveryRecoveryMessage";

const basePayload: DiscoveryRecoveryPayload = {
  kind: "discovery_recovery",
  jobId: "job_test_1",
  discoveryId: "agents_test",
  cardGeneration: "gen1",
  outcome: "failed",
  hasResults: true,
  summary: "本轮失败但已有 L1 进度",
};

afterEach(cleanup);

describe("DiscoveryRecoveryMessage", () => {
  it("validates recovery payload shape", () => {
    expect(isDiscoveryRecoveryPayload(basePayload)).toBe(true);
    expect(isDiscoveryRecoveryPayload({ kind: "other" })).toBe(false);
  });

  it("renders four recovery chips and fires actions", () => {
    const onAction = vi.fn();
    render(<DiscoveryRecoveryMessage payload={basePayload} onAction={onAction} />);
    expect(screen.getByTestId("discovery-recovery")).toBeTruthy();
    fireEvent.click(screen.getByText("查看本轮结果"));
    fireEvent.click(screen.getByText("先改策略再搜"));
    fireEvent.click(screen.getByText("按当前卡重新搜索"));
    fireEvent.click(screen.getByText("重置对话"));
    expect(onAction).toHaveBeenCalledTimes(4);
    expect(onAction.mock.calls[2][0]).toBe("research_current_card");
  });

  it("disables view_results when no results", () => {
    render(
      <DiscoveryRecoveryMessage
        payload={{ ...basePayload, hasResults: false }}
        onAction={vi.fn()}
      />,
    );
    const btn = screen.getByText("查看本轮结果");
    expect((btn as HTMLButtonElement).disabled || btn.getAttribute("aria-disabled") === "true" || (btn as HTMLButtonElement).hasAttribute("disabled")).toBe(true);
  });

  it("truncates huge failure detail", () => {
    const long = "x".repeat(500);
    const short = formatRecoveryFailureDetail(long, 50);
    expect(short.length).toBeLessThanOrEqual(50);
    expect(short.endsWith("…")).toBe(true);
  });
});
