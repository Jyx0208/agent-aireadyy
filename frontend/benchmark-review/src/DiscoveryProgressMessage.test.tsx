/* @vitest-environment jsdom */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildDiscoveryRunView,
  DiscoveryProgressMessage,
  isDiscoveryProgressPayload,
  toDiscoveryProgressPayload,
} from "./DiscoveryProgressMessage";
import { normalizeDiscoveryJobForUi } from "./workflow-api";

afterEach(cleanup);

describe("discovery run display projection", () => {
  it("keeps recently reviewed projects ahead of a long queued candidate tail", () => {
    const previews = Array.from({ length: 130 }, (_, index) => ({
      project_accession: `PXD${String(index + 1).padStart(6, "0")}`,
      title: `Candidate ${index + 1}`,
      project_score: 130 - index,
    }));
    const view = buildDiscoveryRunView({
      status: "running",
      logs: [
        {
          sequence: 1,
          type: "candidate_search_completed",
          payload: {
            observation: {
              candidate_count: previews.length,
              previews,
            },
          },
        },
        {
          sequence: 2,
          type: "candidate_inspection_started",
          payload: {
            action: {
              accessions: ["PXD000001"],
            },
          },
        },
        {
          sequence: 3,
          type: "candidate_inspection_completed",
          payload: {
            observation: {
              project_assessments: [
                {
                  project_accession: "PXD000001",
                  project_title: "Reviewed first candidate",
                  selected_file_count: 4,
                },
              ],
            },
          },
        },
      ],
    });

    expect(view.reviewTrace).toHaveLength(130);
    expect(view.reviewTrace[0]).toMatchObject({
      projectAccession: "PXD000001",
      status: "inspected",
      selectedFileCount: 4,
    });

    render(<DiscoveryProgressMessage payload={toDiscoveryProgressPayload(view)} />);
    expect(screen.getByText("PXD000001")).toBeTruthy();
    expect(screen.getByText("Reviewed first candidate")).toBeTruthy();
  });

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
    expect(screen.getByText("候选项目（已去重）")).toBeTruthy();
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

  it("keeps every planned repository query and its live execution details visible", () => {
    const job = {
      status: "running",
      logs: [
        {
          sequence: 1,
          type: "candidate_search_started",
          payload: {
            action: {
              queries: [
                { query: "immunopeptidomics", depth: 200, budget_role: "primary_theme" },
                { query: "HLA ligandome", depth: 150, budget_role: "theme_synonym" },
              ],
            },
          },
        },
        {
          sequence: 2,
          type: "repository_query_started",
          payload: {
            query: "immunopeptidomics",
            executed_query: "immunopeptidomics",
            depth: 200,
            role: "primary_theme",
            max_pages: 10,
          },
        },
        {
          sequence: 3,
          type: "repository_query_page_completed",
          payload: {
            query: "immunopeptidomics",
            executed_query: "immunopeptidomics",
            depth: 200,
            role: "primary_theme",
            page: 2,
            max_pages: 10,
            cumulative_count: 140,
          },
        },
      ],
    };
    const view = buildDiscoveryRunView(job);

    expect(view.searchTrace).toHaveLength(2);
    expect(view.searchTrace[0]).toMatchObject({
      query: "immunopeptidomics",
      depth: 200,
      role: "primary_theme",
      status: "running",
      executedSeeds: ["immunopeptidomics"],
      pagesCompleted: 2,
      maxPages: 10,
      currentSeedResultCount: 140,
    });
    expect(view.searchTrace[1]).toMatchObject({
      query: "HLA ligandome",
      status: "planned",
    });

    render(<DiscoveryProgressMessage payload={toDiscoveryProgressPayload(view)} />);
    expect(screen.getByLabelText("仓库检索过程")).toBeTruthy();
    expect(screen.getByText("immunopeptidomics")).toBeTruthy();
    expect(screen.getByText("HLA ligandome")).toBeTruthy();
    expect(screen.getByText(/核心主题 · 检索中/)).toBeTruthy();
    expect(screen.getByText(/完成 2\/10 页，本轮已返回 140/)).toBeTruthy();
    expect(screen.getByText(/0\/2 个查询动作已结束 · 共 1 轮 · 2 个主题词/)).toBeTruthy();
  });

  it("shows the Agent request and deterministic theme-order correction transparently", () => {
    const view = buildDiscoveryRunView({
      status: "running",
      logs: [
        {
          type: "candidate_search_started",
          payload: {
            action: {
              queries: [
                { query: "HLA ligandome", depth: 150, budget_role: "theme_synonym" },
              ],
            },
          },
        },
        {
          type: "repository_theme_order_corrected",
          payload: {
            requested_queries: ["HLA ligandome"],
            executed_query: "immunopeptidomics",
            reason: "active_confirmed_theme_not_exhausted",
          },
        },
        {
          type: "repository_query_started",
          payload: {
            query: "immunopeptidomics",
            executed_query: "immunopeptidomics",
            depth: 200,
            role: "primary_theme",
            max_pages: 2,
            start_offset: 200,
          },
        },
        {
          type: "candidate_search_completed",
          payload: {
            observation: {
              query_yields: [
                {
                  query: "immunopeptidomics",
                  executed_query: "immunopeptidomics",
                  raw_result_count: 200,
                  new_candidate_count: 120,
                },
              ],
            },
          },
        },
      ],
    });

    expect(view.searchTrace).toHaveLength(1);
    expect(view.searchTrace[0]).toMatchObject({
      query: "HLA ligandome",
      status: "completed",
      depth: 200,
      startOffset: 200,
      role: "primary_theme",
      executedSeeds: ["immunopeptidomics"],
      rawResultCount: 200,
      newCandidateCount: 120,
    });

    render(<DiscoveryProgressMessage payload={toDiscoveryProgressPayload(view)} />);
    expect(screen.getByText("HLA ligandome")).toBeTruthy();
    expect(screen.getByText(/提交给 PRIDE：immunopeptidomics/)).toBeTruthy();
    expect(screen.getByText(/本轮原始返回 200 · 去重后新增 120/)).toBeTruthy();
  });

  it("groups repository work by round and describes depth as an incremental read", () => {
    const view = buildDiscoveryRunView({
      status: "running",
      logs: [
        {
          type: "candidate_search_started",
          payload: {
            action: {
              queries: [
                { query: "immunopeptidomics", depth: 200, budget_role: "primary_theme" },
              ],
            },
          },
        },
        {
          type: "repository_query_started",
          payload: {
            query: "immunopeptidomics",
            executed_query: "immunopeptidomics",
            depth: 200,
            role: "primary_theme",
            start_offset: 0,
            max_pages: 2,
          },
        },
        {
          type: "candidate_search_started",
          payload: {
            action: {
              queries: [
                { query: "immunopeptidomics", depth: 400, budget_role: "primary_theme" },
              ],
            },
          },
        },
        {
          type: "repository_query_started",
          payload: {
            query: "immunopeptidomics",
            executed_query: "immunopeptidomics",
            depth: 400,
            role: "primary_theme",
            start_offset: 200,
            max_pages: 4,
          },
        },
      ],
    });

    expect(view.searchTrace.map((item) => [item.round, item.startOffset])).toEqual([
      [1, 0],
      [2, 200],
    ]);
    const { container } = render(
      <DiscoveryProgressMessage payload={toDiscoveryProgressPayload(view)} />,
    );
    expect(screen.getByText("第 1 轮")).toBeTruthy();
    expect(screen.getByText("第 2 轮")).toBeTruthy();
    expect(screen.getByText(/0\/2 个查询动作已结束 · 共 2 轮 · 1 个主题词/)).toBeTruthy();
    expect(screen.getByText(/本轮最多追加 400 条/)).toBeTruthy();
    expect(screen.getByText(/从第 201 条继续/)).toBeTruthy();
    const rounds = container.querySelectorAll<HTMLDetailsElement>(
      ".discovery-progress__search-round",
    );
    expect(rounds).toHaveLength(2);
    expect(rounds[0].open).toBe(false);
    expect(rounds[1].open).toBe(true);
  });

  it("uses authoritative term tasks and hides transport pagination rounds", () => {
    const view = buildDiscoveryRunView({
      status: "running",
      execution_state: {
        schema_version: "discovery-execution/v1",
        phase: "searching",
        active_term_index: 2,
        candidate_count: 305,
        reviewed_project_count: 200,
        pending_review_count: 0,
        review_workers: 4,
        all_terms_exhausted: false,
        completion_ready: false,
        terms: [
          {
            term: "immunopeptidomics",
            term_index: 1,
            term_count: 2,
            role: "primary_theme",
            status: "completed",
            chunks_completed: 2,
            raw_result_count: 305,
            new_candidate_count: 305,
            exhausted: true,
            failure_reason: "",
            reviewed_project_count: 200,
          },
          {
            term: "HLA ligandome",
            term_index: 2,
            term_count: 2,
            role: "theme_synonym",
            status: "running",
            chunks_completed: 1,
            raw_result_count: 21,
            new_candidate_count: 14,
            exhausted: false,
            failure_reason: "",
            reviewed_project_count: 200,
          },
        ],
      },
      logs: [
        {
          type: "candidate_search_started",
          payload: {
            action: {
              queries: [
                { query: "HLA ligandome", depth: 2000, budget_role: "theme_synonym" },
              ],
            },
          },
        },
      ],
    });

    expect(view.metrics.projects).toBe(305);
    expect(view.metrics.inspectedProjects).toBe(200);
    render(<DiscoveryProgressMessage payload={toDiscoveryProgressPayload(view)} />);
    expect(screen.getByText(/正在完整检索主题词 2\/2/)).toBeTruthy();
    expect(screen.getByText(/1\/2 个主题词已结束/)).toBeTruthy();
    expect(screen.getByText("主题词 1/2")).toBeTruthy();
    expect(screen.getByText("主题词 2/2")).toBeTruthy();
    expect(screen.getByText(/内部分页段 2 个 · 原始返回 305/)).toBeTruthy();
    expect(screen.getByText(/已明确读到末尾/)).toBeTruthy();
    expect(screen.queryByText("第 1 轮")).toBeNull();
  });

  it("explains legacy open-ended order failures in user-facing language", () => {
    const view = buildDiscoveryRunView({
      status: "running",
      logs: [
        {
          type: "candidate_search_started",
          payload: {
            action: {
              queries: [
                { query: "immunopeptidomics", depth: 200, budget_role: "primary_theme" },
              ],
            },
          },
        },
        {
          type: "candidate_search_failed",
          payload: {
            error:
              "open_ended_theme_order_violation: expected immunopeptidome; search that confirmed theme alone until repository_seed_exhausted before using another synonym",
          },
        },
      ],
    });

    render(<DiscoveryProgressMessage payload={toDiscoveryProgressPayload(view)} />);
    expect(
      screen.getByText(/旧版本会把重复提交的已耗尽词也判为失败；新版本已改为安全跳过/),
    ).toBeTruthy();
  });

  it("updates live raw hits without mislabelling them as deduplicated candidates", () => {
    const view = buildDiscoveryRunView({
      status: "running",
      record: { project_count: 0 },
      logs: [
        {
          type: "candidate_search_started",
          payload: {
            action: {
              queries: [
                { query: "immunopeptidomics", depth: 200, budget_role: "primary_theme" },
              ],
            },
          },
        },
        {
          type: "repository_query_page_completed",
          payload: {
            query: "immunopeptidomics",
            executed_query: "immunopeptidomics",
            depth: 200,
            page: 1,
            max_pages: 2,
            cumulative_count: 25,
          },
        },
      ],
    });

    expect(view.metrics.repositoryHits).toBe(25);
    expect(view.metrics.projects).toBe(0);
    render(<DiscoveryProgressMessage payload={toDiscoveryProgressPayload(view)} />);
    expect(screen.getByText("实时检索命中（含重复）")).toBeTruthy();
    expect(screen.getByText("候选项目（已去重）")).toBeTruthy();
  });

  it("updates the deduplicated candidate count after each completed query", () => {
    const view = buildDiscoveryRunView({
      status: "running",
      record: { project_count: 0 },
      logs: [
        {
          type: "candidate_search_started",
          payload: {
            action: {
              queries: [
                { query: "immunopeptidomics", depth: 200, budget_role: "primary_theme" },
                { query: "HLA ligandome", depth: 200, budget_role: "synonym" },
              ],
            },
          },
        },
        {
          type: "repository_query_completed",
          payload: {
            query: "immunopeptidomics",
            executed_query: "immunopeptidomics",
            raw_result_count: 25,
            new_candidate_count: 20,
          },
        },
      ],
    });

    expect(view.metrics.projects).toBe(20);
    expect(view.metrics.repositoryHits).toBe(25);
  });

  it("shows project-level metadata reading, evidence, decision, and grade", () => {
    const view = buildDiscoveryRunView({
      status: "running",
      logs: [
        {
          type: "candidate_inspection_started",
          payload: {
            action: { accessions: ["PXD000123"] },
          },
        },
        {
          type: "job_message",
          message: "Inspecting project PXD000123.",
        },
        {
          type: "candidate_inspection_completed",
          payload: {
            observation: {
              project_assessments: [
                {
                  project_accession: "PXD000123",
                  project_title: "HLA class I immunopeptidomics",
                  matched_intent_terms: ["HLA class I", "immunopeptidomics"],
                  query_hits: ["HLA class I ligandome"],
                  species: ["Homo sapiens"],
                  acquisition_mode: "dda",
                  selected_file_count: 12,
                  project_description_excerpt: "HLA-I peptides enriched by immunoaffinity.",
                  selected_file_examples: ["sample1.raw", "sample2.raw"],
                  sdrf: { status: "available", row_count: 12 },
                  available_evidence_refs: ["project_title", "species", "selected_files"],
                },
              ],
            },
          },
        },
        {
          type: "project_judgments_recorded",
          payload: {
            judgments: [
              {
                project_accession: "PXD000123",
                grade: 3,
                confidence: 0.91,
                decision: "include",
                evidence_stage: "inspection",
                explanation: "主题、物种和可用原始文件均有项目级证据。",
                evidence_refs: ["project_title", "species", "selected_files"],
              },
            ],
          },
        },
      ],
    });

    render(<DiscoveryProgressMessage payload={toDiscoveryProgressPayload(view)} />);
    expect(screen.getByLabelText("项目审查过程")).toBeTruthy();
    expect(screen.getByText("PXD000123")).toBeTruthy();
    expect(screen.getByText(/HLA class I immunopeptidomics/)).toBeTruthy();
    expect(screen.getByText(/项目级证据评分 3\/3/)).toBeTruthy();
    expect(screen.getByText(/置信度 91%/)).toBeTruthy();
    expect(screen.getByText(/主题、物种和可用原始文件/)).toBeTruthy();
    expect(screen.getByText(/project_title、species、selected_files/)).toBeTruthy();
    expect(screen.getByText(/HLA-I peptides enriched/)).toBeTruthy();
    expect(screen.getByText(/sample1.raw、sample2.raw/)).toBeTruthy();
    expect(screen.getByText(/SDRF：available，12 行/)).toBeTruthy();
  });

  it("shows terminal project-level reasons for unusable and failed inspections", () => {
    const view = buildDiscoveryRunView({
      status: "running",
      logs: [
        {
          type: "candidate_inspection_started",
          payload: { action: { accessions: ["PXD000124", "PXD000125"] } },
        },
        {
          type: "candidate_inspection_completed",
          payload: {
            observation: {
              inspection_outcomes: [
                {
                  project_accession: "PXD000124",
                  category: "no_usable_files",
                  stage: "score_files",
                  reason: "no usable acquisition files after filtering",
                },
                {
                  project_accession: "PXD000125",
                  category: "inspection_failure",
                  stage: "parse_sdrf",
                  reason: "parse_failure",
                  error: "invalid SDRF header",
                },
              ],
            },
          },
        },
      ],
    });

    expect(view.reviewTrace[0]).toMatchObject({
      projectAccession: "PXD000124",
      status: "excluded",
      stage: "未发现可用文件",
    });
    expect(view.reviewTrace[1]).toMatchObject({
      projectAccession: "PXD000125",
      status: "failed",
      stage: "项目审查失败：parse_sdrf",
    });
    render(<DiscoveryProgressMessage payload={toDiscoveryProgressPayload(view)} />);
    expect(screen.getAllByText(/no usable acquisition files after filtering/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/parse_failure：invalid SDRF header/).length).toBeGreaterThan(0);
  });

  it("keeps the review total separate from the 100-project display window", () => {
    const previews = Array.from({ length: 120 }, (_, index) => ({
      project_accession: `PXD${String(index + 1).padStart(6, "0")}`,
      title: `Candidate ${index + 1}`,
    }));
    const judgments = previews.slice(0, 8).map((preview) => ({
      project_accession: preview.project_accession,
      grade: 2,
      confidence: 0.8,
      decision: "include",
      explanation: "项目级证据满足要求。",
    }));
    const view = buildDiscoveryRunView({
      status: "running",
      record: { project_count: 120 },
      logs: [
        {
          type: "candidate_search_completed",
          payload: { observation: { previews } },
        },
        {
          type: "project_judgments_recorded",
          payload: { judgments },
        },
      ],
    });

    expect(view.reviewTrace).toHaveLength(120);
    render(<DiscoveryProgressMessage payload={toDiscoveryProgressPayload(view)} />);
    expect(screen.getAllByText(/8 个已有结论/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/候选总数 120/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/当前仅展示最近 100 个/).length).toBeGreaterThan(0);
    expect(screen.queryByText("8/100 个项目已有结论")).toBeNull();
  });

  it("explains no-usable-file outcomes with structured filter counts", () => {
    const view = buildDiscoveryRunView({
      status: "running",
      logs: [
        {
          type: "candidate_inspection_completed",
          payload: {
            observation: {
              inspection_outcomes: [
                {
                  project_accession: "PXD043658",
                  category: "no_usable_files",
                  stage: "score_files",
                  reason: "no usable acquisition/peaklist file candidates after filtering",
                  raw_file_count: 53,
                  excluded_file_count: 38,
                  file_role_counts: { raw_acquisition: 38, report_table: 15 },
                  filter_reason_counts: {
                    mixed_acquisition_project: 38,
                    "unsupported_file_role:report_table": 15,
                  },
                },
              ],
            },
          },
        },
      ],
    });

    render(<DiscoveryProgressMessage payload={toDiscoveryProgressPayload(view)} />);
    expect(screen.getByText(/文件检查：原始记录 53/)).toBeTruthy();
    expect(screen.getByText(/原始采集文件 38/)).toBeTruthy();
    expect(screen.getByText(/混合采集项目与“仅 DDA”硬约束冲突 ×38/)).toBeTruthy();
    expect(screen.getByText(/结果表或报告文件，不是采集\/峰表 ×15/)).toBeTruthy();
  });

  it("shows repository queries skipped with their actual reason", () => {
    const view = buildDiscoveryRunView({
      status: "running",
      logs: [
        {
          type: "candidate_search_started",
          payload: { action: { queries: [{ query: "human", depth: 20, budget_role: "filter_only" }] } },
        },
        {
          type: "repository_query_skipped",
          payload: {
            query: "human",
            executed_query: "human",
            depth: 20,
            role: "filter_only",
            reason: "filter_only_not_deep_searched",
          },
        },
      ],
    });

    expect(view.searchTrace[0]).toMatchObject({
      status: "skipped",
      skipReason: "filter_only_not_deep_searched",
    });
  });

  it("shows downloadable verified batches while discovery is still running", () => {
    const view = buildDiscoveryRunView({
      status: "running",
      result_batches: [
        {
          batch_index: 1,
          project_count: 30,
          file_count: 84,
          download_url: "/api/discovery/jobs/job-1/batches/1/download",
        },
      ],
    });

    render(<DiscoveryProgressMessage payload={toDiscoveryProgressPayload(view)} />);

    const link = screen.getByRole("link", { name: /批次 1：84 个文件（来自 30 个项目）/ });
    expect(link.getAttribute("href")).toBe(
      "/api/discovery/jobs/job-1/batches/1/download",
    );
  });

  it("exposes a working stop action for a running discovery job", () => {
    const onCancel = vi.fn();
    const payload = toDiscoveryProgressPayload(buildDiscoveryRunView({
      job_id: "job-stop-1",
      status: "running",
    }));

    render(<DiscoveryProgressMessage payload={payload} onCancel={onCancel} />);
    screen.getByTestId("discovery-cancel").click();

    expect(onCancel).toHaveBeenCalledWith("job-stop-1");
  });

  it("exposes a resume action for an interrupted discovery job", () => {
    const onResume = vi.fn();
    const payload = toDiscoveryProgressPayload(buildDiscoveryRunView({
      job_id: "job-resume-1",
      status: "interrupted",
      resumable: true,
    }));

    render(<DiscoveryProgressMessage payload={payload} onResume={onResume} />);
    screen.getByTestId("discovery-resume").click();

    expect(onResume).toHaveBeenCalledWith("job-resume-1");
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
    expect(screen.getAllByText("可交付文件（总）").length).toBeGreaterThan(0);
    expect(screen.getAllByText("1441").length).toBeGreaterThan(0);
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
    expect(screen.getAllByText("可交付文件（总）").length).toBeGreaterThan(0);
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
