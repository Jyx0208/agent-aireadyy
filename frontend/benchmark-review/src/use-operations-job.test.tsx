/* @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { OperationsEvent, OperationsJob } from "./operations-api";
import { useOperationsJob } from "./use-operations-job";

const job: OperationsJob = {
  job_id: "job-live",
  job_type: "discovery",
  status: "reviewing",
  phase: "reviewing",
  objective: "live operations",
  repository: "pride",
  species: "Homo sapiens",
  version: 1,
  last_event_sequence: 3,
  cancel_requested: false,
  resumable: false,
  progress: {
    current_term: "",
    term_total: 2,
    term_completed: 2,
    raw_hit_count: 10,
    candidate_count: 4,
    reviewed_count: 0,
    pending_review_count: 4,
    qualified_count: 0,
    file_clue_count: 0,
    usable_file_count: 0,
    batch_count: 0,
    worker_count: 4,
  },
  heartbeat_at: "2026-07-30T08:00:00Z",
  created_at: "2026-07-30T08:00:00Z",
  updated_at: "2026-07-30T08:00:00Z",
};

const event: OperationsEvent = {
  id: 4,
  sequence: 4,
  job_id: "job-live",
  type: "project_review_step",
  level: "info",
  actor: "Candidate Inspector",
  phase: "reviewing",
  message: "PXD000001 metadata scored",
  payload: {
    project_accession: "PXD000001",
    worker_slot: 1,
    step: "metadata_score",
  },
  created_at: "2026-07-30T08:00:01Z",
};

class FakeEventSource {
  static latest: FakeEventSource | null = null;
  listeners = new Map<string, (event: MessageEvent) => void>();
  onerror: (() => void) | null = null;

  constructor(public url: string) {
    FakeEventSource.latest = this;
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    this.listeners.set(type, listener as (event: MessageEvent) => void);
  }

  emit(type: string, value: unknown) {
    this.listeners.get(type)?.(
      new MessageEvent(type, { data: JSON.stringify(value) }),
    );
  }

  close() {}
}

vi.mock("./operations-api", async (loadOriginal) => {
  const original = await loadOriginal<typeof import("./operations-api")>();
  return {
    ...original,
    getOperationsJob: vi.fn(async () => job),
    getOperationsEvents: vi.fn(async () => ({
      items: [],
      last_event_sequence: 3,
    })),
  };
});

afterEach(() => {
  vi.restoreAllMocks();
  FakeEventSource.latest = null;
});

describe("operations live state", () => {
  it("refreshes SQL-backed worker and detail projections immediately for each SSE event", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const wrapper = ({ children }: PropsWithChildren) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useOperationsJob("job-live"), {
      wrapper,
    });

    await waitFor(() => expect(FakeEventSource.latest).not.toBeNull());
    act(() => FakeEventSource.latest?.emit("job-event", event));

    await waitFor(() =>
      expect(result.current.events.map((item) => item.sequence)).toContain(4),
    );
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["operations-job-detail", "job-live"],
    });
  });
});
