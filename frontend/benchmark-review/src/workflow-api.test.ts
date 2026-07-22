import { afterEach, describe, expect, it, vi } from "vitest";

import {
  getDiscoveryJob,
  parseDiscoveryGoal,
  startBatch,
  startDiscoveryJob,
  startSingleTask,
  terminalWorkflowStatus,
  withoutBlankSecret,
  workflowJson,
} from "./workflow-api";

afterEach(() => vi.unstubAllGlobals());

describe("operational workflow API", () => {
  it("treats legacy JSON error payloads as failures", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: "blocked" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );
    await expect(workflowJson("/api/example")).rejects.toThrow("blocked");
  });

  it("submits single and batch payloads to their original API seams", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ task_id: "task-1", status: "queued" })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ batch_id: "batch-1", status: "queued" })));
    vi.stubGlobal("fetch", fetchMock);

    await startSingleTask({ input_value: "PXD000001", run_mode: "parameters" });
    await startBatch({ inputs: ["PXD000001/file.raw"], run_mode: "parameters" });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/tasks");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/batches/parameters");
  });

  it("recognizes every terminal workflow state", () => {
    expect(["completed", "failed", "blocked", "cancelled"].every(terminalWorkflowStatus)).toBe(true);
    expect(terminalWorkflowStatus("running")).toBe(false);
  });

  it("treats a quality-blocked Discovery job as a valid terminal domain record", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            status: "blocked",
            error: "selection_quality_gate_not_completed",
          }),
        ),
      ),
    );

    await expect(workflowJson("/api/discovery/jobs/job-blocked")).resolves.toMatchObject({
      status: "blocked",
    });
  });

  it("starts Discovery only with a grill-confirmed payload and without review-pool actions", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ job_id: "job-1", status: "queued" })));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", { randomUUID: () => "stable-id" });

    await startDiscoveryJob({
      prompt: "human phosphoproteomics",
      task_type: "rt_prediction",
      scale_mode: "balanced",
      max_projects: 80,
      grill_confirmed: true,
    });
    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(fetchMock.mock.calls[0][0]).toBe("/api/discovery/jobs");
    expect(body).toMatchObject({
      prompt: "human phosphoproteomics",
      runtime: "openai_agents",
      source: "remote",
      grill_confirmed: true,
      task_type: "rt_prediction",
    });
    expect(JSON.stringify(body)).not.toContain("review");
  });

  it("does not manufacture confirmation for an unconfirmed discovery payload", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(startDiscoveryJob({ prompt: "human phosphoproteomics" })).rejects.toThrow(
      "Explicit strategy confirmation",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("calls parse-goal for intent drafting", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "completed", fields: { task_type: "rt_prediction" } })),
    );
    vi.stubGlobal("fetch", fetchMock);
    await parseDiscoveryGoal("RT 预测 human DDA");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/discovery/parse-goal");
    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body.prompt).toContain("RT");
  });


  it("returns failed discovery job payloads without treating error as transport failure", async () => {
    const failed = {
      job_id: "job-failed-1",
      status: "failed",
      error: "Discovery request is required for OpenAI Agents mode.",
      logs: [{ message: "Discovery failed: Discovery request is required for OpenAI Agents mode." }],
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(failed), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const job = await getDiscoveryJob("job-failed-1");
    expect(job.status).toBe("failed");
    expect(job.error).toContain("Discovery request is required");
    expect(fetchMock.mock.calls[0][0]).toContain("/api/discovery/jobs/job-failed-1");
  });

  it("preserves a saved API key when the settings draft is blank", () => {
    expect(withoutBlankSecret({ api_key: "", model: "m" })).toEqual({ model: "m" });
    expect(withoutBlankSecret({ api_key: "new", model: "m" })).toEqual({ api_key: "new", model: "m" });
  });
});
