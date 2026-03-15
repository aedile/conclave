/**
 * useSSE — custom hook for consuming Server-Sent Events from the job stream.
 *
 * Wraps the browser EventSource API to subscribe to `/jobs/{jobId}/stream`.
 * Parses `progress`, `complete`, and `error` event types from the backend.
 * Closes the EventSource on unmount to prevent memory and connection leaks.
 *
 * CONSTITUTION: No external network calls — targets same-origin backend only.
 *
 * @example
 * ```tsx
 * const { status, percent, currentEpoch, totalEpochs, error } = useSSE(jobId);
 * ```
 */

import { useEffect, useState } from "react";
import type { JobStatus } from "../api/client";

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/** State shape returned by useSSE. All fields are null until the first event. */
export interface SSEState {
  /** Current job status derived from the most recent SSE event. */
  status: JobStatus | null;
  /** Completion percentage (0–100). */
  percent: number | null;
  /** Current training epoch number. */
  currentEpoch: number | null;
  /** Total training epochs for this job. */
  totalEpochs: number | null;
  /** Sanitized error detail string, set on "error" event. */
  error: string | null;
}

// ---------------------------------------------------------------------------
// Internal event payload shapes (from backend SSE stream)
// ---------------------------------------------------------------------------

interface ProgressPayload {
  status: "TRAINING";
  current_epoch: number;
  total_epochs: number;
  percent: number;
}

interface CompletePayload {
  status: "COMPLETE";
  current_epoch: number;
  total_epochs: number;
  percent: number;
}

interface ErrorPayload {
  detail: string;
}

const INITIAL_STATE: SSEState = {
  status: null,
  percent: null,
  currentEpoch: null,
  totalEpochs: null,
  error: null,
};

// ---------------------------------------------------------------------------
// Hook implementation
// ---------------------------------------------------------------------------

/**
 * Subscribe to the SSE stream for a synthesis job.
 *
 * Opens an `EventSource` connection to `/jobs/{jobId}/stream` and
 * listens for `progress`, `complete`, and `error` events. The connection
 * is automatically closed when the component unmounts or when `jobId`
 * changes to a different value.
 *
 * When `jobId` is `null`, no connection is opened and the returned state
 * contains all-null fields.
 *
 * Malformed SSE payloads (invalid JSON) are caught and result in a FAILED
 * status with an error message; the EventSource is closed in that case.
 *
 * @param jobId - The numeric job ID to stream, or `null` to skip.
 * @returns Current SSE state with status, progress, epoch counters, and error.
 */
export function useSSE(jobId: number | null): SSEState {
  const [state, setState] = useState<SSEState>(INITIAL_STATE);

  useEffect(() => {
    if (jobId === null) {
      setState(INITIAL_STATE);
      return;
    }

    const es = new EventSource(`/jobs/${jobId}/stream`);

    const handleProgress = (event: MessageEvent<string>): void => {
      let payload: ProgressPayload;
      try {
        payload = JSON.parse(event.data) as ProgressPayload;
      } catch (e) {
        console.warn("[useSSE] Malformed progress payload:", e);
        setState((prev) => ({
          ...prev,
          status: "FAILED",
          error: "Received malformed progress data from server.",
        }));
        es.close();
        return;
      }
      setState({
        status: payload.status,
        percent: payload.percent,
        currentEpoch: payload.current_epoch,
        totalEpochs: payload.total_epochs,
        error: null,
      });
    };

    const handleComplete = (event: MessageEvent<string>): void => {
      let payload: CompletePayload;
      try {
        payload = JSON.parse(event.data) as CompletePayload;
      } catch (e) {
        console.warn("[useSSE] Malformed complete payload:", e);
        setState((prev) => ({
          ...prev,
          status: "FAILED",
          error: "Received malformed completion data from server.",
        }));
        es.close();
        return;
      }
      setState({
        status: payload.status,
        percent: payload.percent,
        currentEpoch: payload.current_epoch,
        totalEpochs: payload.total_epochs,
        error: null,
      });
      es.close();
    };

    const handleError = (event: MessageEvent<string>): void => {
      let payload: ErrorPayload;
      try {
        payload = JSON.parse(event.data) as ErrorPayload;
      } catch (e) {
        console.warn("[useSSE] Malformed error payload:", e);
        setState((prev) => ({
          ...prev,
          status: "FAILED",
          error: "Received malformed error data from server.",
        }));
        es.close();
        return;
      }
      setState((prev) => ({
        ...prev,
        status: "FAILED",
        error: payload.detail,
      }));
      es.close();
    };

    // EventSource.addEventListener accepts EventListener (Event) but SSE events
    // are MessageEvent. We cast via unknown to satisfy TypeScript while keeping
    // the handler typed correctly.
    es.addEventListener("progress", handleProgress as unknown as EventListener);
    es.addEventListener("complete", handleComplete as unknown as EventListener);
    es.addEventListener("error", handleError as unknown as EventListener);

    return () => {
      es.removeEventListener("progress", handleProgress as unknown as EventListener);
      es.removeEventListener("complete", handleComplete as unknown as EventListener);
      es.removeEventListener("error", handleError as unknown as EventListener);
      es.close();
    };
  }, [jobId]);

  return state;
}
