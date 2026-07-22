/* @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DiscoveryContextRail } from "./DiscoveryContextRail";
import { createEmptyIntent } from "./intent-spec";

afterEach(cleanup);

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

describe("DiscoveryContextRail", () => {
  it("keeps one compact result entry and puts downloads in its result dialog", () => {
    const spec = {
      ...createEmptyIntent("探索人类免疫肽项目"),
      objective: "探索人类免疫肽项目",
      taskType: "browse_only" as const,
      runHorizon: "candidates_reviewed" as const,
      species: ["Homo sapiens"],
      speciesPolicy: "prefer" as const,
      coverageMode: "curated" as const,
      targetProjectCount: 20,
      instrumentPreference: "newer" as const,
      resolvedFields: ["objective", "task_type", "run_horizon", "species", "coverage_mode", "target_project_count", "instrument_preference"],
    };
    render(
      <DiscoveryContextRail
        spec={spec}
        phase="done"
        job={{
          status: "completed",
          record: {
            discovery_id: "agents_example",
            project_count: 20,
            file_count: 3218,
            review_count: 2,
            latest_discovery_audit: {
              issues: [
                {
                  code: "delivery_relies_on_weak_keep_files",
                  severity: "warning",
                  summary: "部分文件为 weak-keep；可交付但不是严格 valid。",
                },
              ],
            },
          },
        }}
        onConfirm={vi.fn()}
        onApplyDefaults={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    expect(screen.getAllByText("较新仪器优先").length).toBeGreaterThan(0);
    expect(screen.getByText("发现结果已就绪")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "查看结果" })).toHaveLength(1);
    expect(screen.queryByText(/原始运行日志/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "查看结果" }));
    expect(screen.getByRole("heading", { name: "发现结果" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "质量说明" })).toBeTruthy();
    expect(screen.getByText("部分文件为 weak-keep；可交付但不是严格 valid。")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Agent 审查报告/ }).getAttribute("href")).toContain("agents_discovery_report_md");
    expect(screen.getByRole("link", { name: /数据清单 CSV/ }).getAttribute("href")).toContain("dataset_manifest_csv");
    expect(screen.getByRole("link", { name: /项目评分与理由/ }).getAttribute("href")).toContain("project_judgments_table_csv");
  });

  it("does not render unresolved dimensions as confirmed defaults", () => {
    render(
      <DiscoveryContextRail
        spec={createEmptyIntent()}
        phase="idle"
        job={null}
        onConfirm={vi.fn()}
        onApplyDefaults={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    expect(screen.getByText("还没有形成可执行搜索目标")).toBeTruthy();
    expect(screen.getAllByText("待补齐").length).toBeGreaterThan(0);
    expect(screen.queryByText(/均衡 · 约 80/)).toBeNull();
  });

  it("keeps a quality-blocked run auditable without presenting candidates as delivered", () => {
    render(
      <DiscoveryContextRail
        spec={createEmptyIntent("免疫肽候选")}
        phase="failed"
        job={{
          status: "blocked",
          record: {
            discovery_id: "agents_blocked",
            project_count: 24,
            file_count: 1477,
            summary: {
              selected_projects: 0,
              needs_review_files: 1441,
            },
            latest_discovery_audit: {
              issues: [
                { code: "qualified_project_has_no_delivery_assets", summary: "候选文件仍缺少可交付证据。" },
              ],
            },
          },
        }}
        onConfirm={vi.fn()}
        onApplyDefaults={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    expect(screen.getByText("质量未通过")).toBeTruthy();
    expect(screen.getByText("候选已保留，但没有结果通过交付质量闸门")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "查看审计" }));
    expect(screen.getByRole("heading", { name: "质量审计与候选证据" })).toBeTruthy();
    expect(screen.getByText("候选文件仍缺少可交付证据。")).toBeTruthy();
    expect(screen.getAllByText("0").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "单文件处理" })).toBeNull();
  });

  it("shows a resolved mixed-acquisition policy in the compact preview while mode is open", () => {
    render(
      <DiscoveryContextRail
        spec={{
          ...createEmptyIntent("open acquisition strategy"),
          acquisitionMode: "unknown",
          mixedAcquisitionPolicy: "reject_mixed",
          resolvedFields: ["acquisition_mode", "mixed_acquisition_policy"],
        }}
        phase="grilling"
        job={null}
        onConfirm={vi.fn()}
        onApplyDefaults={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    expect(screen.getByText("开放 · 混合项目整项排除")).toBeTruthy();
  });

  it("surfaces arbitrary verified hard constraints and preserved scientific notes", () => {
    const spec = {
      ...createEmptyIntent("海洋物种对照数据"),
      objective: "海洋物种对照数据",
      taskType: "other" as const,
      runHorizon: "candidates_reviewed" as const,
      coverageMode: "curated" as const,
      targetProjectCount: 15,
      species: ["Danio rerio"],
      speciesPolicy: "exclude" as const,
      labelingStrategy: "itraq" as const,
      labelingHard: true,
      notes: "仅保留有明确组织来源的研究。",
      openRisks: ["组织注释可能只存在于 SDRF"],
      resolvedFields: [
        "objective", "task_type", "run_horizon", "coverage_mode", "target_project_count",
        "species", "species_policy", "labeling_strategy", "labeling_hard", "notes", "open_risks",
      ],
    };
    render(
      <DiscoveryContextRail
        spec={spec}
        phase="awaiting_confirm"
        job={null}
        onConfirm={vi.fn()}
        onApplyDefaults={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByText("查看完整策略"));
    expect(screen.getByText("排除物种：Danio rerio")).toBeTruthy();
    expect(screen.getByText("标记方式必须为：iTRAQ")).toBeTruthy();
    expect(screen.getByText("仅保留有明确组织来源的研究。")).toBeTruthy();
    expect(screen.getByText("组织注释可能只存在于 SDRF")).toBeTruthy();
  });
});
