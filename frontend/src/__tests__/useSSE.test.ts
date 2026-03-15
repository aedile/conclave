/**
 * Vitest unit tests for the useSSE custom hook.
 *
 * Tests EventSource lifecycle, event parsing, cleanup, and error handling.
 *
 * Guards against:
 * - ES module mocking: vi.mock() at module level
 * - Timer cleanup on unmount: verify EventSource.close() is called
 * - Fake timers deadlock: NOT used here — no timer advancement needed
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useSSE, type SSEState } from "../hooks/useSSE";

// ---------------------------------------------------------------------------
// MockEventSource — simulates browser EventSource API
// ---------------------------------------------------------------------------

type EventListener = (event: { data: string }) => void;

class MockEventSource {
  static instances: MockEventSource[] = [];

  url: string;
  readyState: number = 0; // CONNECTING
  private listeners: Record<string, EventListener[]> = {};
  private genericListeners: Array<(event: MessageEvent) => void> = [];
  onerror: ((event: Event) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: EventListener): void {
    if (!this.listeners[type]) {
      this.listeners[type] = [];
    }
    this.listeners[type].push(listener);
  }

  removeEventListener(type: string, listener: EventListener): void {
    if (this.listeners[type]) {
      this.listeners[type] = this.listeners[type].filter((l) => l !== listener);
    }
  }

  /** Simulate receiving a named event. */
  simulateEvent(type: string, data: unknown): void {
    const event = { data: JSON.stringify(data) } as { data: string };
    const handlers = this.listeners[type] ?? [];
    handlers.forEach((h) => h(event));
  }

  /** Simulate an error event (connection dropped). */
  simulateError(): void {
    if (this.onerror) {
      this.onerror(new Event("error"));
    }
  }

  close = vi.fn();
}

// Stub the global EventSource with our mock
vi.stubGlobal("EventSource", MockEventSource);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function lastInstance(): MockEventSource {
  const inst = MockEventSource.instances.at(-1);
  if (!inst) throw new Error("No MockEventSource instance created");
  return inst;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useSSE hook", () => {
  beforeEach(() => {
    MockEventSource.instances = [];
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("opens an EventSource to the correct SSE URL on mount", () => {
    renderHook(() => useSSE(42));

    expect(MockEventSource.instances).toHaveLength(1);
    expect(lastInstance().url).toBe("/jobs/42/stream");
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
      lastInstance().simulateEvent("progress", {
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
      lastInstance().simulateEvent("complete", {
        status: "COMPLETE",
        current_epoch: 100,
        total_epochs: 100,
        percent: 100,
      });
    });

    expect(result.current.status).toBe("COMPLETE");
    expect(result.current.percent).toBe(100);
    expect(lastInstance().close).toHaveBeenCalledOnce();
  });

  it("parses an error event, sets error state, and closes the EventSource", () => {
    const { result } = renderHook(() => useSSE(7));

    act(() => {
      lastInstance().simulateEvent("error", {
        detail: "GPU out of memory",
      });
    });

    expect(result.current.status).toBe("FAILED");
    expect(result.current.error).toBe("GPU out of memory");
    expect(lastInstance().close).toHaveBeenCalledOnce();
  });

  it("closes the EventSource on unmount (cleanup)", () => {
    const { unmount } = renderHook(() => useSSE(7));

    const es = lastInstance();
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

    const firstEs = lastInstance();

    rerender({ id: 2 });

    expect(firstEs.close).toHaveBeenCalledOnce();
    expect(MockEventSource.instances).toHaveLength(2);
    expect(lastInstance().url).toBe("/jobs/2/stream");
  });
});
