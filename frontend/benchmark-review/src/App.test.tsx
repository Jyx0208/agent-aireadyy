/* @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const { getDiscoveryJobMock } = vi.hoisted(() => ({
  getDiscoveryJobMock: vi.fn(),
}));

vi.mock("./CarbonAgentChat", () => ({
  CarbonAgentChat: () => <div>Agent chat</div>,
}));
vi.mock("./HistoryPanel", () => ({
  HistoryPanel: ({ onOpenDiscovery }: { onOpenDiscovery: (item: Record<string, unknown>) => void }) => (
    <>
      <button
        type="button"
        onClick={() => onOpenDiscovery({
          kind: "discovery",
          job_id: "discovery_job_running",
          status: "running",
        })}
      >
        Open running discovery
      </button>
      <button
        type="button"
        onClick={() => onOpenDiscovery({
          kind: "discovery",
          job_id: "discovery_job_interrupted",
          status: "interrupted",
        })}
      >
        Open interrupted discovery
      </button>
    </>
  ),
}));
vi.mock("./workflow-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./workflow-api")>();
  return {
    ...original,
    getDiscoveryJob: getDiscoveryJobMock,
  };
});
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

afterEach(() => {
  cleanup();
  getDiscoveryJobMock.mockReset();
});

describe("proteomics operational workbench", () => {
  it("keeps polling a running discovery restored from history", async () => {
    getDiscoveryJobMock.mockResolvedValue({
      job_id: "discovery_job_running",
      status: "running",
      execution_state: {
        schema_version: "discovery-execution/v1",
        phase: "reviewing",
        active_term_index: 1,
        candidate_count: 612,
        reviewed_project_count: 466,
        pending_review_count: 83,
        review_workers: 4,
        terms: [{
          term: "immunopeptidomics",
          term_index: 1,
          term_count: 34,
          status: "running",
          exhausted: true,
        }],
      },
      logs: [{
        type: "candidate_inspection_started",
        payload: { action: { accessions: ["PXD004233"] } },
      }],
    });

    render(<App />);
    fireEvent.click(screen.getByRole("tab", { name: "运行历史" }));
    fireEvent.click(screen.getByRole("button", { name: "Open running discovery" }));

    await waitFor(() => {
      expect(screen.getAllByLabelText("当前任务与流程").length).toBeGreaterThan(0);
      expect(getDiscoveryJobMock.mock.calls.length).toBeGreaterThan(1);
    }, { timeout: 3000 });
    expect(getDiscoveryJobMock).toHaveBeenCalledWith(
      "discovery_job_running",
      false,
      expect.any(AbortSignal),
    );
  });

  it("loads an interrupted discovery once without polling it as active", async () => {
    getDiscoveryJobMock.mockResolvedValue({
      job_id: "discovery_job_interrupted",
      status: "interrupted",
      resumable: true,
      logs: [],
    });

    render(<App />);
    fireEvent.click(screen.getByRole("tab", { name: "运行历史" }));
    fireEvent.click(screen.getByRole("button", { name: "Open interrupted discovery" }));

    await waitFor(() => {
      expect(getDiscoveryJobMock).toHaveBeenCalledWith(
        "discovery_job_interrupted",
        true,
      );
    });
    expect(getDiscoveryJobMock).toHaveBeenCalledTimes(1);
  });

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
