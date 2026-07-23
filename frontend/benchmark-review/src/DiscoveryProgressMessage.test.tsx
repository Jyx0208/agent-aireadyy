/* @vitest-environment jsdom */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  buildDiscoveryRunView,
  DiscoveryProgressMessage,
  isDiscoveryProgressPayload,
  toDiscoveryProgressPayload,
} from "./DiscoveryProgressMessage";
import { normalizeDiscoveryJobForUi } from "./workflow-api";

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
    expect(screen.getByText("检索项目")).toBeTruthy();
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

  it("shows candidate and L1 usable counts when publication is not build-ready", () => {
    const view = buildDiscoveryRunView({
      status: "blocked",
      record: {
        project_count: 24,
        file_count: 1477,
        summary: {
          candidate_projects: 24,
          selected_projects: 0,
          usable_files: 378,
          needs_review_files: 1441,
        },
        latest_discovery_audit: {
          counts: { usable_files: 378, strict_valid_files: 0 },
        },
      },
    });

    expect(view.status).toBe("blocked");
    expect(view.metrics.selectedProjects).toBe(378);
    expect(view.metrics.usableFiles).toBe(378);
    expect(view.metrics.reviews).toBe(1441);
    expect(view.summary).toContain("24");

    render(<DiscoveryProgressMessage payload={toDiscoveryProgressPayload(view)} />);
    expect(screen.getByText("已交付候选清单")).toBeTruthy();
    expect(screen.getAllByText("可用文件 L1").length).toBeGreaterThan(0);
    expect(screen.getByText("1441")).toBeTruthy();
  });

  it("does not render reviewed 32/0 progress as business completion", () => {
    const job = normalizeDiscoveryJobForUi({
      status: "completed",
      logs: [
        {
          sequence: 1,
          type: "discovery_quality_repair_completed",
          message: "Repair runner returned after reviewing candidates.",
        },
      ],
      record: {
        project_count: 32,
        file_count: 2409,
        review_count: 20,
        business_completion: {
          succeeded: false,
          status: "blocked_with_progress",
          package_kind: "progress",
          progress_visible: true,
          success_ui_allowed: false,
          progress: {
            candidate_projects: 32,
            candidate_files: 2409,
            reviewed_projects: 20,
            judgment_qualified_projects: 20,
            build_ready_projects: 0,
            build_ready_files: 0,
            blocker_counts: {
              missing_file_evidence: 2408,
              missing_labeling_evidence: 2181,
            },
          },
        },
        summary: {
          candidate_projects: 32,
          judgment_qualified_projects: 20,
          build_ready_projects: 0,
          selected_projects: 0,
        },
      },
    });
    const view = buildDiscoveryRunView(job);

    expect(job.status).toBe("blocked");
    expect(view.status).toBe("blocked");
    expect(view.statusLabel).not.toBe("已完成");
    expect(view.metrics.projects).toBe(32);
    expect(view.metrics.inspectedProjects).toBe(20);
    expect(view.metrics.judgmentQualifiedProjects).toBe(20);
    expect(view.metrics.buildReadyProjects).toBe(0);
    expect(view.metrics.selectedProjects).toBe(0);
    expect(view.technicalEvents.map((event) => "text" in event ? event.text : event.detail).join(" "))
      .toContain("结果待审计");

    render(<DiscoveryProgressMessage payload={toDiscoveryProgressPayload(view)} />);
    expect(screen.queryByText("已完成")).toBeNull();
    expect(screen.getByText("已交付候选清单")).toBeTruthy();
    expect(screen.getByText("已审项目")).toBeTruthy();
    expect(screen.getByText("判断合格")).toBeTruthy();
    expect(screen.getAllByText("可用文件 L1").length).toBeGreaterThan(0);
    expect(screen.getByText(/missing file evidence：2408/)).toBeTruthy();
  });

  it("shows completion only when every build-ready authority field is valid", () => {
    const view = buildDiscoveryRunView({
      status: "completed",
      record: {
        business_completion: {
          schema_version: "business-completion/v2",
          authority_source: "publication_contract_registry",
          succeeded: true,
          status: "build_ready_succeeded",
          package_kind: "build_ready",
          progress_visible: true,
          success_ui_allowed: true,
          build_ready_package: { package_id: "package-1" },
          issuance_token: "issued-token",
          progress: {
            candidate_projects: 9,
            candidate_files: 80,
            reviewed_projects: 7,
            judgment_qualified_projects: 3,
            build_ready_projects: 2,
            build_ready_files: 12,
            blocker_counts: {},
          },
        },
      },
    });

    expect(view.status).toBe("completed");
    expect(view.statusLabel).toBe("已完成");
    expect(view.metrics.buildReadyProjects).toBe(2);
    expect(view.metrics.buildReadyFiles).toBe(12);
  });

  it("shows Authority and builder preflight provenance without granting success", () => {
    const view = buildDiscoveryRunView({
      status: "completed",
      record: {
        publication_authority: {
          authority_mode: "production",
          key_id: "staging-ed25519-key",
        },
        publication_builder_preflight_status: "ready",
        publication_builder_preflight_ref: "preflight:sha256:example",
        business_completion: {
          schema_version: "business-completion/v2",
          authority_source: "publication_contract_registry",
          succeeded: false,
          status: "blocked_with_progress",
          package_kind: "progress",
          success_ui_allowed: false,
          progress: { build_ready_projects: 0, build_ready_files: 0 },
        },
      },
    });

    expect(view.status).toBe("blocked");
    expect(view.provenance).toEqual({
      authorityMode: "production",
      authorityKeyId: "staging-ed25519-key",
      builderPreflightStatus: "ready",
      builderPreflightRef: "preflight:sha256:example",
    });

    render(<DiscoveryProgressMessage payload={toDiscoveryProgressPayload(view)} />);
    expect(screen.queryByText("已完成")).toBeNull();
    expect(screen.getByText("Authority 模式")).toBeTruthy();
    expect(screen.getByText("production")).toBeTruthy();
    expect(screen.getByText("staging-ed25519-key")).toBeTruthy();
    expect(screen.getByText(/ready（兼容预检，不等于 dry-run 接受）/)).toBeTruthy();
  });

  it("fails closed when a claimed success has no build-ready files", () => {
    const view = buildDiscoveryRunView({
      status: "completed",
      logs: [{ sequence: 1, type: "repair_succeeded", message: "runner says success" }],
      record: {
        business_completion: {
          succeeded: true,
          status: "build_ready_succeeded",
          package_kind: "build_ready",
          success_ui_allowed: true,
          progress: { build_ready_projects: 1, build_ready_files: 0 },
        },
      },
    });

    expect(view.status).toBe("blocked");
    expect(view.statusLabel).toBe("已交付候选清单");
    expect(JSON.stringify(view.technicalEvents)).toMatch(/build-ready 仅作参考|可用文件清单为准交付/);
    expect(JSON.stringify(view.technicalEvents)).not.toMatch(/权威审计已确认/);
  });
});
