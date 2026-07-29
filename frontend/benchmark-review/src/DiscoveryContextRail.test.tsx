/* @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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
  it("restores detailed progress for a running discovery opened from history", () => {
    render(
      <DiscoveryContextRail
        spec={createEmptyIntent()}
        phase="idle"
        job={{
          job_id: "discovery_job_running",
          status: "running",
          execution_state: {
            schema_version: "discovery-execution/v1",
            phase: "reviewing",
            active_term_index: 1,
            candidate_count: 612,
            reviewed_project_count: 436,
            pending_review_count: 83,
            active_review_batch_size: 30,
            review_workers: 4,
            terms: [
              {
                term: "immunopeptidomics",
                term_index: 1,
                term_count: 34,
                status: "running",
                new_candidate_count: 305,
                exhausted: true,
              },
            ],
          },
          logs: [
            {
              type: "candidate_inspection_started",
              message: "Inspecting 2 candidate project(s): PXD004233, PXD027408",
              payload: {
                action: { accessions: ["PXD004233", "PXD027408"] },
              },
            },
            {
              type: "job_message",
              message: "Inspecting project PXD004233.",
            },
          ],
        }}
        onConfirm={vi.fn()}
        onApplyDefaults={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("当前任务与流程")).toBeTruthy();
    expect(screen.getByLabelText("仓库检索过程")).toBeTruthy();
    expect(screen.getByLabelText("项目审查过程")).toBeTruthy();
    expect(screen.getByText("已审项目")).toBeTruthy();
    expect(screen.getByText("PXD004233")).toBeTruthy();
    const progressWorkspace = screen.getByLabelText("运行进度主面板");
    expect(within(progressWorkspace).getByLabelText("当前任务与流程")).toBeTruthy();
    expect(
      within(screen.getByLabelText("策略与运行上下文")).queryByLabelText("数据发现进度"),
    ).toBeNull();
  });

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
            business_completion: {
              schema_version: "business-completion/v2",
              authority_source: "publication_contract_registry",
              succeeded: true,
              status: "build_ready_succeeded",
              package_kind: "build_ready",
              progress_visible: true,
              progress: {
                candidate_projects: 20,
                candidate_files: 3218,
                reviewed_projects: 20,
                judgment_qualified_projects: 1,
                build_ready_projects: 1,
                build_ready_files: 1,
                blocker_counts: {},
              },
              build_ready_package: {
                schema_version: "discovery-build-ready-package/v1",
                package_id: "package-example",
                authority: "publication_contract_registry",
                authority_run_id: "run-example",
                audit_ref: "audit-example",
                manifest_ref: "manifest-example",
                evidence_store_ref: "evidence-example",
                builder_entrypoint: "dataset-builder/preflight",
                builder_preflight_ref: "preflight-example",
                validated: true,
                builder_compatible: true,
                project_ids: ["PXD000001"],
                files: [
                  {
                    file_id: "file-1",
                    project_id: "PXD000001",
                    download_url: "https://example.invalid/file-1.raw",
                    expected_size_bytes: 1024,
                    file_role: "raw_acquisition",
                    validity_status: "valid",
                    needs_review: false,
                    evidence_observation_refs: ["observation-1"],
                    membership_ref: "membership-1",
                  },
                ],
                constraint_evidence: [],
                unresolved: [],
                excluded: [],
              },
              issuance_token: "issued-token",
              limitations: [],
              success_ui_allowed: true,
            },
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
    expect(screen.getAllByRole("button", { name: /查看结果/ })).toHaveLength(1);
    expect(screen.queryByText(/原始运行日志/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /查看结果/ }));
    expect(screen.getByRole("heading", { name: "发现结果" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: /质量说明|当前不足/ })).toBeTruthy();
    expect(screen.getByText("部分文件为 weak-keep；可交付但不是严格 valid。")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Agent 审查报告/ }).getAttribute("href")).toContain("agents_discovery_report_md");
    expect(screen.getByRole("link", { name: /可用批量输入 L1/ }).getAttribute("href")).toContain("batch_inputs_usable");
    expect(screen.getByRole("link", { name: /可用.*数据清单 CSV|可用文件清单 CSV/ }).getAttribute("href")).toContain("dataset_manifest_usable_csv");
    expect(screen.getByRole("link", { name: /完整运行包/ }).getAttribute("href")).toContain("discovery_run_bundle_zip");
    expect(screen.getByRole("button", { name: /送入批量参数规划/ })).toBeTruthy();
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

  it("presents L1 usable list for a non-build-ready run and allows batch handoff", () => {
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
              usable_files: 378,
              needs_review_files: 1441,
            },
            latest_discovery_audit: {
              counts: { usable_files: 378, strict_valid_files: 0 },
              issues: [
                { code: "qualified_project_has_no_delivery_assets", summary: "候选文件仍缺少可交付证据。" },
              ],
            },
          },
        }}
        onConfirm={vi.fn()}
        onApplyDefaults={vi.fn()}
        onNavigate={vi.fn()}
        onSeedBatchInputs={vi.fn()}
      />,
    );

    expect(screen.getByText("已交付候选清单")).toBeTruthy();
    expect(screen.getByText("可用文件清单已就绪")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "查看结果与送入批量" }));
    expect(screen.getByRole("heading", { name: "可用文件清单与说明" })).toBeTruthy();
    expect(screen.getByText("候选文件仍缺少可交付证据。")).toBeTruthy();
    expect(screen.getByRole("button", { name: "送入批量参数规划" })).toBeTruthy();
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
