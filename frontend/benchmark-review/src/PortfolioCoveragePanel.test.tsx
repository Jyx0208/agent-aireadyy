// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PortfolioCoveragePanel } from "./PortfolioCoveragePanel";

describe("PortfolioCoveragePanel", () => {
  it("shows the compact summary for a covered portfolio", () => {
    render(
      <PortfolioCoveragePanel
        state={{
          status: "ready",
          spec: { target_projects: 8, target_files: 16 },
          coverage: { distinct_projects: 8, selected_files: 16 },
          gaps: [],
        }}
      />,
    );
    expect(screen.getByLabelText("Portfolio coverage")).toBeTruthy();
    expect(screen.getByText("8 / 8 个项目，16 / 16 个文件")).toBeTruthy();
    expect(screen.getByText("已覆盖")).toBeTruthy();
  });

  it("expands only when a deterministic gap exists", () => {
    render(
      <PortfolioCoveragePanel
        state={{
          status: "needs_recovery",
          spec: { target_projects: 8 },
          coverage: { distinct_projects: 5, selected_files: 10 },
          gaps: [{ dimension: "labs", required: 4, observed: 2, severity: "hard" }],
        }}
      />,
    );
    expect(screen.getByText("1 个缺口")).toBeTruthy();
    expect(screen.getByText("labs")).toBeTruthy();
    expect(screen.getByText(/需要 4，目前 2/)).toBeTruthy();
    expect(screen.getByText(/硬条件/)).toBeTruthy();
  });

  it("shows the bounded recovery plan without hiding approval gates", () => {
    render(
      <PortfolioCoveragePanel
        state={{
          status: "needs_recovery",
          spec: {},
          coverage: { distinct_projects: 1, selected_files: 1 },
          gaps: [{ dimension: "labs", required: 2, observed: 1, severity: "hard" }],
          recovery_actions: [
            { id: "inspect", kind: "inspect_metadata", expected_gain: "查 SDRF", requires_approval: false },
            { id: "relax", kind: "relax_hard_requirement", expected_gain: "放宽硬条件", requires_approval: true },
          ],
        }}
      />,
    );
    expect(screen.getByText("Agent 下一步")).toBeTruthy();
    expect(screen.getByText(/查 SDRF/)).toBeTruthy();
    expect(screen.getByText(/需要确认/)).toBeTruthy();
  });
});
