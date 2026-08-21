import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import {
  getOperationsEvents,
  getOperationsJob,
  operationsEventUrl,
  operationsTerminal,
  type OperationsEvent,
  type OperationsJob,
} from "./operations-api";

export type OperationsConnectionState =
  | "connecting"
  | "live"
  | "reconnecting"
  | "closed";

const mergeEvents = (
  history: OperationsEvent[] | undefined,
  live: OperationsEvent[],
) => {
  const bySequence = new Map<number, OperationsEvent>();
  [...(history || []), ...live].forEach((event) => {
    bySequence.set(event.sequence, event);
  });
  return [...bySequence.values()]
    .sort((left, right) => left.sequence - right.sequence)
    .slice(-1_000);
};

export function useOperationsJob(jobId: string) {
  const queryClient = useQueryClient();
  const [connection, setConnection] =
    useState<OperationsConnectionState>("connecting");
  const [liveEvents, setLiveEvents] = useState<OperationsEvent[]>([]);
  const jobQuery = useQuery({
    queryKey: ["operations-job", jobId],
    queryFn: ({ signal }) => getOperationsJob(jobId, signal),
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const value = query.state.data as OperationsJob | undefined;
      return value && !operationsTerminal(value.status) ? 15_000 : false;
    },
    staleTime: 2_000,
  });
  const lastSequence = Number(jobQuery.data?.last_event_sequence || 0);
  const eventQuery = useQuery({
    queryKey: ["operations-events", jobId, Math.max(0, lastSequence - 200)],
    queryFn: ({ signal }) =>
      getOperationsEvents(
        jobId,
        Math.max(0, lastSequence - 200),
        200,
        signal,
      ),
    enabled: Boolean(jobId) && jobQuery.isSuccess,
    staleTime: 10_000,
  });

  useEffect(() => {
    setLiveEvents([]);
    setConnection("connecting");
  }, [jobId]);

  useEffect(() => {
    if (!jobId || !jobQuery.data) return;
    if (operationsTerminal(jobQuery.data.status)) {
      setConnection("closed");
      return;
    }
    let source: EventSource | null = null;
    let stopped = false;
    const connect = () => {
      if (stopped) return;
      setConnection((current) =>
        current === "live" ? "reconnecting" : "connecting",
      );
      source = new EventSource(
        operationsEventUrl(jobId, Number(jobQuery.data?.last_event_sequence || 0)),
      );
      source.addEventListener("connected", (raw) => {
        setConnection("live");
        const data = JSON.parse((raw as MessageEvent).data) as {
          snapshot?: OperationsJob;
        };
        if (data.snapshot) {
          queryClient.setQueryData(["operations-job", jobId], data.snapshot);
        }
      });
      source.addEventListener("job-event", (raw) => {
        setConnection("live");
        const event = JSON.parse((raw as MessageEvent).data) as OperationsEvent;
        setLiveEvents((current) =>
          mergeEvents(current, [event]).slice(-1_000),
        );
        // The event and its SQL projections are committed in one transaction
        // before SSE publishes it. Refresh active detail views immediately so
        // worker slots, review steps, terms, files, and batches show the same
        // committed truth instead of waiting for their fallback poll.
        void queryClient.invalidateQueries({
          queryKey: ["operations-job-detail", jobId],
        });
      });
      source.addEventListener("snapshot", (raw) => {
        setConnection("live");
        const snapshot = JSON.parse(
          (raw as MessageEvent).data,
        ) as OperationsJob;
        queryClient.setQueryData(["operations-job", jobId], snapshot);
        void queryClient.invalidateQueries({
          queryKey: ["operations-job-detail", jobId],
        });
      });
      source.addEventListener("heartbeat", (raw) => {
        setConnection("live");
        const heartbeat = JSON.parse(
          (raw as MessageEvent).data,
        ) as Pick<
          OperationsJob,
          "job_id" | "status" | "phase" | "heartbeat_at" | "updated_at"
        >;
        queryClient.setQueryData<OperationsJob>(
          ["operations-job", jobId],
          (current) =>
            current
              ? {
                  ...current,
                  status: heartbeat.status,
                  phase: heartbeat.phase,
                  heartbeat_at: heartbeat.heartbeat_at,
                  updated_at: heartbeat.updated_at,
                }
              : current,
        );
      });
      source.addEventListener("complete", (raw) => {
        const snapshot = JSON.parse(
          (raw as MessageEvent).data,
        ) as OperationsJob;
        queryClient.setQueryData(["operations-job", jobId], snapshot);
        setConnection("closed");
        source?.close();
      });
      source.onerror = () => {
        if (stopped) return;
        setConnection("reconnecting");
      };
    };
    connect();
    return () => {
      stopped = true;
      source?.close();
    };
  }, [
    jobId,
    jobQuery.data?.job_id,
    operationsTerminal(jobQuery.data?.status),
    queryClient,
  ]);

  const events = useMemo(
    () => mergeEvents(eventQuery.data?.items, liveEvents),
    [eventQuery.data?.items, liveEvents],
  );

  return {
    job: jobQuery.data || null,
    events,
    connection,
    isLoading: jobQuery.isLoading,
    error: jobQuery.error,
    refetch: jobQuery.refetch,
  };
}
