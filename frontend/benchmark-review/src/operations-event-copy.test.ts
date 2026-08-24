import { describe, expect, it } from "vitest";

import { describeOperationsEvent } from "./operations-event-copy";

describe("operations event copy", () => {
  it("describes repository pagination in plain Chinese", () => {
    const event = describeOperationsEvent({
      id: 1,
      sequence: 1,
      job_id: "job",
      type: "repository_query_page_completed",
      level: "info",
      actor: "Repository Search",
      phase: "searching",
      message: "",
      payload: {
        query: "immunopeptidomics",
        page_number: 3,
        page_result_count: 80,
        pages_completed: 3,
        cumulative_count: 280,
      },
      created_at: "2026-07-31T10:00:00+08:00",
    });

    expect(event.title).toContain("第 3 页");
    expect(event.detail).toContain("本页返回 80");
    expect(event.detail).toContain("累计读取 280");
    expect(event.technical).toBe(false);
  });

  it("hides SDK lifecycle telemetry behind technical details", () => {
    const event = describeOperationsEvent({
      id: 2,
      sequence: 2,
      job_id: "job",
      type: "sdk_run_item",
      level: "info",
      actor: "OpenAI Agents SDK",
      phase: "searching",
      message: "sdk run item",
      payload: {},
      created_at: "2026-07-31T10:00:00+08:00",
    });

    expect(event.technical).toBe(true);
  });

  it("explains a repair-required quality audit in plain Chinese", () => {
    const event = describeOperationsEvent({
      id: 3,
      sequence: 3,
      job_id: "job",
      type: "discovery_quality_audited",
      level: "warning",
      actor: "quality-audit",
      phase: "finalizing",
      message: "Quality audit repair_required: 18 delivery-eligible project(s).",
      payload: {
        status: "repair_required",
        counts: { delivery_eligible_projects: 18, usable_files: 646 },
        issues: [
          { code: "inspected_projects_missing_judgments" },
          { code: "qualified_projects_have_unresolved_constraints" },
          { code: "quality_target_not_reached" },
        ],
      },
      created_at: "2026-08-24T16:00:00+08:00",
    });

    expect(event.title).toBe("科学质量检查发现 3 项需要继续处理");
    expect(event.detail).toContain("18 个可交付项目、646 个可用文件");
    expect(event.detail).toContain("部分已审项目还没有模型判断");
    expect(event.detail).not.toContain("repair_required");
  });
});
