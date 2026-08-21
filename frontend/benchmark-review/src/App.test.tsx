/* @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const { chatEffectCleanupMock, chatHarness } = vi.hoisted(() => ({
  chatEffectCleanupMock: vi.fn(),
  chatHarness: {
    onJob: null as null | ((job: { job_id: string }) => void),
    initialOnJob: null as null | ((job: { job_id: string }) => void),
  },
}));

vi.mock("./CarbonAgentChat", async () => {
  const { useEffect } = await import("react");
  return {
    CarbonAgentChat: ({
      onJob,
    }: {
      onJob: (job: { job_id: string }) => void;
    }) => {
      chatHarness.onJob = onJob;
      chatHarness.initialOnJob ||= onJob;
      useEffect(() => () => chatEffectCleanupMock(), []);
      return <div>Agent chat</div>;
    },
  };
});

vi.mock("./OperationsConsole", () => ({
  OperationsConsole: ({
    jobId,
    activeTab,
    onTabChange,
  }: {
    jobId: string;
    activeTab: string;
    onTabChange: (tab: "events") => void;
  }) => (
    <section aria-label="运行进度主面板">
      Operations job {jobId} · tab {activeTab}
      <button type="button" onClick={() => onTabChange("events")}>
        查看运行事件
      </button>
    </section>
  ),
}));

vi.mock("./OperationsHistory", () => ({
  OperationsHistory: ({
    onOpenJob,
  }: {
    onOpenJob: (jobId: string) => void;
  }) => (
    <section aria-label="历史任务">
      <h1>历史任务与磁盘空间</h1>
      <button type="button" onClick={() => onOpenJob("history-job")}>
        打开历史发现任务
      </button>
    </section>
  ),
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

const renderApp = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  );
};

afterEach(() => {
  cleanup();
  chatEffectCleanupMock.mockReset();
  chatHarness.onJob = null;
  chatHarness.initialOnJob = null;
  window.localStorage.clear();
  window.history.replaceState(null, "", "#current");
});

describe("industrial operations shell", () => {
  it("shows the task-first Carbon product navigation and strategy workspace", async () => {
    renderApp();

    expect(
      screen.getByRole("banner", {
        name: "PRIDE 蛋白质组学数据运行平台",
      }),
    ).toBeTruthy();
    for (const name of [
      "当前任务",
      "历史任务",
      "批量处理",
      "单文件处理",
      "AI-ready 构建",
      "系统设置",
    ]) {
      expect(screen.getByRole("link", { name })).toBeTruthy();
    }
    expect(
      screen.getByRole("heading", { name: "定义数据发现目标" }),
    ).toBeTruthy();
    expect(screen.getByText("策略预览")).toBeTruthy();
    expect(await screen.findByText("Agent chat")).toBeTruthy();
  });

  it("opens database-backed history without mounting the heavy chat workspace", async () => {
    window.history.replaceState(null, "", "#history");
    renderApp();

    expect(
      await screen.findByRole("heading", { name: "历史任务与磁盘空间" }),
    ).toBeTruthy();
    expect(screen.queryByText("Agent chat")).toBeNull();

    fireEvent.click(
      screen.getByRole("button", { name: "打开历史发现任务" }),
    );

    expect(
      await screen.findByText(/Operations job history-job/),
    ).toBeTruthy();
    expect(
      window.localStorage.getItem("pride.discovery.activeJobId"),
    ).toBe("history-job");
  });

  it("keeps the live chat instance mounted while navigating between product sections", async () => {
    renderApp();
    expect(await screen.findByText("Agent chat")).toBeTruthy();

    fireEvent.click(screen.getByRole("link", { name: "历史任务" }));
    fireEvent.click(screen.getByRole("link", { name: "历史任务" }));
    fireEvent.click(screen.getByRole("link", { name: "批量处理" }));
    fireEvent.click(screen.getByRole("link", { name: "当前任务" }));

    expect(await screen.findByText("Agent chat")).toBeTruthy();
    expect(chatEffectCleanupMock).not.toHaveBeenCalled();
  });

  it("does not reset the selected operations tab when the same job publishes another snapshot", async () => {
    window.localStorage.setItem("pride.discovery.activeJobId", "job-live");
    renderApp();

    fireEvent.click(
      await screen.findByRole("button", { name: "查看运行事件" }),
    );
    expect(screen.getByText(/tab events/)).toBeTruthy();

    act(() => chatHarness.onJob?.({ job_id: "job-live" }));

    expect(screen.getByText(/tab events/)).toBeTruthy();
  });

  it("does not let a stale chat callback reset the selected operations tab", async () => {
    renderApp();
    expect(await screen.findByText("Agent chat")).toBeTruthy();
    const staleOnJob = chatHarness.initialOnJob;
    expect(staleOnJob).toBeTruthy();

    act(() => staleOnJob?.({ job_id: "job-live" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "查看运行事件" }),
    );
    expect(screen.getByText(/tab events/)).toBeTruthy();

    act(() => staleOnJob?.({ job_id: "job-live" }));

    expect(screen.getByText(/tab events/)).toBeTruthy();
  });

  it("restores the durable current job directly after refresh", async () => {
    window.localStorage.setItem(
      "pride.discovery.activeJobId",
      "durable-job",
    );
    renderApp();

    expect(
      await screen.findByText(/Operations job durable-job/),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "新建发现任务" })).toBeTruthy();
  });
});
