/* @vitest-environment jsdom */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  buildDiscoveryRunView,
  DiscoveryProgressMessage,
  isDiscoveryProgressPayload,
  toDiscoveryProgressPayload,
} from "./DiscoveryProgressMessage";

afterEach(cleanup);

describe("discovery run display projection", () => {
  it("shows human progress and uses an indeterminate bar without a real percentage", () => {
    const view = buildDiscoveryRunView({
      status: "running",
      logs: [
        { sequence: 1, type: "candidate_search_started", message: "正在检索 PRIDE 项目：immunopeptide" },
        { sequence: 2, type: "candidate_inspection_started", message: "正在检查项目 PXD000001。" },
      ],
      record: { project_count: 12, file_count: 48, review_count: 3 },
    });
    const payload = toDiscoveryProgressPayload(view);

    expect(view.progressPercent).toBeNull();
    expect(view.milestones.length).toBeGreaterThanOrEqual(2);
    expect(view.milestones.length).toBeLessThanOrEqual(3);
    render(<DiscoveryProgressMessage payload={payload} />);

    expect(screen.getByLabelText("数据发现进度")).toBeTruthy();
    expect(screen.getAllByText(/PXD000001|候选审查/).length).toBeGreaterThan(0);
    expect(screen.getByText("候选项目")).toBeTruthy();
    expect(screen.getByText("48")).toBeTruthy();
    const progress = screen.getByRole("progressbar");
    expect(progress.getAttribute("aria-valuenow")).toBeNull();
    expect(screen.getByRole("button", { name: /技术轨迹 · 2 条运行事件/ }).getAttribute("aria-expanded")).toBe("false");
  });

  it("passes through a real server progress percentage without inventing one", () => {
    const view = buildDiscoveryRunView({
      status: "running",
      record: { progress_percent: 37, project_count: 4 },
    });
    render(<DiscoveryProgressMessage payload={toDiscoveryProgressPayload(view)} />);

    expect(view.progressPercent).toBe(37);
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("37");
  });

  it("rejects malformed progress payloads at the user-defined boundary", () => {
    const valid = toDiscoveryProgressPayload(buildDiscoveryRunView({ status: "queued" }));
    expect(isDiscoveryProgressPayload(valid)).toBe(true);
    expect(isDiscoveryProgressPayload({ ...valid, progressPercent: 145 })).toBe(false);
    expect(isDiscoveryProgressPayload({ ...valid, metrics: { projects: -1, files: 0, reviews: 0 } })).toBe(false);
    expect(isDiscoveryProgressPayload({ ...valid, metrics: { projects: 1, files: 1, reviews: 0, selectedProjects: -1 } })).toBe(false);
    expect(isDiscoveryProgressPayload({ ...valid, technicalEvents: [{ type: "tool", name: "x", status: "done" }] })).toBe(false);
  });

  it("shows candidate, delivery, and review counts when the quality gate blocks publication", () => {
    const view = buildDiscoveryRunView({
      status: "blocked",
      record: {
        project_count: 24,
        file_count: 1477,
        summary: {
          candidate_projects: 24,
          selected_projects: 0,
          needs_review_files: 1441,
        },
      },
    });

    expect(view.status).toBe("blocked");
    expect(view.metrics.selectedProjects).toBe(0);
    expect(view.metrics.reviews).toBe(1441);
    expect(view.summary).toContain("24");
    expect(view.summary).toContain("0");

    render(<DiscoveryProgressMessage payload={toDiscoveryProgressPayload(view)} />);
    expect(screen.getByText("质量未通过")).toBeTruthy();
    expect(screen.getByText("通过交付")).toBeTruthy();
    expect(screen.getByText("1441")).toBeTruthy();
  });
});
