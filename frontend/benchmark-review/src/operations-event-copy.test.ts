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
});
