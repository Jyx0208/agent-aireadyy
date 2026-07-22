/* @vitest-environment jsdom */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  CodexTimeline,
  isCodexTimelinePayload,
  isTimelineEvent,
  tl,
} from "./CodexTimeline";

describe("CodexTimeline", () => {
  it("keeps technical detail collapsed and exposes an accessible scroll region", () => {
    const { rerender } = render(
      <CodexTimeline
        events={[tl.tool("仓库检索", "running", "正在检索 PRIDE")]}
        streaming
      />,
    );

    const toggle = screen.getByRole("button", { name: /技术轨迹/ });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.getAttribute("aria-controls")).toBeTruthy();
    expect(screen.queryByRole("region")).toBeNull();

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("region", { name: /技术轨迹.*详情/ })).toBeTruthy();
    expect(screen.getByText("正在检索 PRIDE")).toBeTruthy();

    rerender(
      <CodexTimeline
        events={[
          tl.tool("仓库检索", "ok", "返回 40 个项目"),
          tl.tool("候选审查", "running", "正在审查 PXD000001"),
        ]}
        streaming
      />,
    );
    expect(screen.getByRole("button", { name: /技术轨迹/ }).getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("正在审查 PXD000001")).toBeTruthy();
  });

  it("validates every event instead of accepting arbitrary user-defined payloads", () => {
    expect(isTimelineEvent(tl.tool("候选审查", "ok", "完成"))).toBe(true);
    expect(isTimelineEvent({ type: "tool", name: "候选审查", status: "completed" })).toBe(false);
    expect(isCodexTimelinePayload({
      kind: "codex_timeline",
      events: [tl.action("开始筛选")],
      defaultOpen: false,
    })).toBe(true);
    expect(isCodexTimelinePayload({
      kind: "codex_timeline",
      events: [{ type: "think" }],
    })).toBe(false);
    expect(isCodexTimelinePayload({
      kind: "codex_timeline",
      events: [tl.action("开始筛选")],
      defaultOpen: "yes",
    })).toBe(false);
  });
});
