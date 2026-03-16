/**
 * Vitest unit tests for the useSSE custom hook.
 *
 * Tests EventSource lifecycle, event parsing, cleanup, and error handling.
 *
 * Guards against:
 * - ES module mocking: vi.mock() at module level
 * - Timer cleanup on unmount: verify EventSource.close() is called
 * - Fake timers deadlock: NOT used here — no timer advancement needed
 *
 * ADV-060: MockEventSource extracted to helpers/mock-event-source.ts
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useSSE, type SSEState } from "../hooks/useSSE";
import { MockEventSource, getLastInstance } from "./helpers/mock-event-source";

// Stub the global EventSource with our shared mock
vi.stubGlobal("EventSource", MockEventSource);

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useSSE hook", () => {
  beforeEach(() => {
    MockEventSource.reset();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("opens an EventSource to the correct SSE URL on mount", () => {
    renderHook(() => useSSE(42));

    expect(MockEventSource.instances).toHaveLength(1);
    expect(getLastInstance().url).toBe("/jobs/42/stream");
  });

  it("returns initial null state before any events arrive", () => {
    const { result } = renderHook(() => useSSE(42));

    expect(result.current).toEqual<SSEState>({
      status: null,
      percent: null,
      currentEpoch: null,
      totalEpochs: null,
      error: null,
    });
  });

  it("parses a progress event and updates state", () => {
    const { result } = renderHook(() => useSSE(7));

    act(() => {
      getLastInstance().simulateEvent("progress", {
        status: "TRAINING",
        current_epoch: 5,
        total_epochs: 100,
        percent: 5,
      });
    });

    expect(result.current.status).toBe("TRAINING");
    expect(result.current.percent).toBe(5);
    expect(result.current.currentEpoch).toBe(5);
    expect(result.current.totalEpochs).toBe(100);
    expect(result.current.error).toBeNull();
  });

  it("parses a complete event and closes the EventSource", () => {
    const { result } = renderHook(() => useSSE(7));

    act(() => {
      getLastInstance().simulateEvent("complete", {
        status: "COMPLETE",
        current_epoch: 100,
        total_epochs: 100,
        percent: 100,
      });
    });

    expect(result.current.status).toBe("COMPLETE");
    expect(result.current.percent).toBe(100);
    expect(getLastInstance().close).toHaveBeenCalledOnce();
  });

  it("parses an error event, sets error state, and closes the EventSource", () => {
    const { result } = renderHook(() => useSSE(7));

    act(() => {
      getLastInstance().simulateEvent("error", {
        detail: "GPU out of memory",
      });
    });

    expect(result.current.status).toBe("FAILED");
    expect(result.current.error).toBe("GPU out of memory");
    expect(getLastInstance().close).toHaveBeenCalledOnce();
  });

  it("closes the EventSource on unmount (cleanup)", () => {
    const { unmount } = renderHook(() => useSSE(7));

    const es = getLastInstance();
    unmount();

    expect(es.close).toHaveBeenCalledOnce();
  });

  it("does nothing when jobId is null", () => {
    renderHook(() => useSSE(null));

    // No EventSource should be created
    expect(MockEventSource.instances).toHaveLength(0);
  });

  it("returns null state when jobId is null", () => {
    const { result } = renderHook(() => useSSE(null));

    expect(result.current).toEqual<SSEState>({
      status: null,
      percent: null,
      currentEpoch: null,
      totalEpochs: null,
      error: null,
    });
  });

  it("closes previous EventSource and opens a new one when jobId changes", () => {
    const { rerender } = renderHook(({ id }: { id: number | null }) => useSSE(id), {
      initialProps: { id: 1 as number | null },
    });

    const firstEs = getLastInstance();

    rerender({ id: 2 });

    expect(firstEs.close).toHaveBeenCalledOnce();
    expect(MockEventSource.instances).toHaveLength(2);
    expect(getLastInstance().url).toBe("/jobs/2/stream");
  });

  it("handles malformed progress payload (invalid JSON)", () => {
    const { result } = renderHook(() => useSSE(7));
    const es = getLastInstance();

    act(() => {
      es.simulateRawEvent("progress", "NOT_JSON");
    });

    expect(result.current.status).toBe("FAILED");
    expect(result.current.error).toBe("Received malformed progress data from server.");
    expect(es.close).toHaveBeenCalledOnce();
  });

  it("handles malformed complete payload (invalid JSON)", () => {
    const { result } = renderHook(() => useSSE(7));
    const es = getLastInstance();

    act(() => {
      es.simulateRawEvent("complete", "NOT_JSON");
    });

    expect(result.current.status).toBe("FAILED");
    expect(result.current.error).toBe("Received malformed completion data from server.");
    expect(es.close).toHaveBeenCalledOnce();
  });

  it("handles malformed error payload (invalid JSON)", () => {
    const { result } = renderHook(() => useSSE(7));
    const es = getLastInstance();

    act(() => {
      es.simulateRawEvent("error", "NOT_JSON");
    });

    expect(result.current.status).toBe("FAILED");
    expect(result.current.error).toBe("Received malformed error data from server.");
    expect(es.close).toHaveBeenCalledOnce();
  });
});
