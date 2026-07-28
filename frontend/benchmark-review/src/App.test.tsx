/* @vitest-environment jsdom */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import App from "./App";

vi.mock("./CarbonAgentChat", () => ({
  CarbonAgentChat: () => <div>Agent chat</div>,
}));
vi.stubGlobal(
  "ResizeObserver",
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
);
Object.defineProperty(window, "matchMedia", {
  configurable: true,
  value: vi.fn().mockReturnValue({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }),
});
vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({}))));

describe("proteomics operational workbench", () => {
  it("uses one compact discovery workspace without duplicate progress surfaces", () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: "蛋白质组学数据搜集与处理 Agent" })).toBeTruthy();
    const buildStamp = screen.getByLabelText("构建身份");
    expect(buildStamp.textContent?.trim()).toMatch(/^Build v\S+/);
    expect(buildStamp.getAttribute("title")).toMatch(/构建版本 .+；修订 .+；构建时间 .+/);
    for (const name of ["数据发现", "单文件处理", "批量处理", "AI-ready 构建", "运行历史", "设置"]) {
      expect(screen.getAllByText(name).length).toBeGreaterThan(0);
    }
    expect(screen.getByText("自然语言对齐科学需求，确认策略后再检索、审查并交付可追溯结果。")).toBeTruthy();
    expect(screen.getByText("策略预览")).toBeTruthy();
    expect(screen.getByText("确认主题词，开始搜")).toBeTruthy();
    expect(screen.getByText("补齐稳妥默认")).toBeTruthy();
    expect(screen.getByText("查看完整策略")).toBeTruthy();
    expect(screen.getByText(/这里只显示真实运行状态、关键计数和结果入口/)).toBeTruthy();
    expect(screen.queryByText(/原始运行日志/)).toBeNull();
    expect(screen.queryByText("正在做什么")).toBeNull();
    expect(screen.queryByRole("heading", { name: "发现结果" })).toBeNull();
    expect(screen.queryByText("Evidence & Grading")).toBeNull();
    expect(screen.queryByText("Review API Token")).toBeNull();
    expect(screen.queryByText("把 Agent 的判断过程变成可检查的工作流")).toBeNull();
  });
});
