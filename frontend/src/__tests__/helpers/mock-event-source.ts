/**
 * Shared MockEventSource test utility.
 *
 * Provides a test double for the browser EventSource API.
 * Previously copy-pasted between useSSE.test.ts and Dashboard.test.tsx.
 * Extracted here per ADV-060 to eliminate duplication and ensure all
 * consumers share the same implementation.
 *
 * Usage:
 *   import { MockEventSource, getLastInstance } from "./helpers/mock-event-source";
 *
 *   // In beforeEach:
 *   MockEventSource.reset();
 *   vi.stubGlobal("EventSource", MockEventSource);
 *
 *   // In tests:
 *   const es = getLastInstance();
 *   es.simulateEvent("progress", { status: "TRAINING", percent: 50, ... });
 *   es.simulateError();
 */

import { vi } from "vitest";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Minimal EventSource event shape consumed by useSSE and other hooks. */
export type SSEEventListener = (event: { data: string }) => void;

// ---------------------------------------------------------------------------
// MockEventSource class
// ---------------------------------------------------------------------------

/**
 * Test double for the browser EventSource API.
 *
 * Tracks all created instances via the static `instances` array so tests can
 * inspect how many EventSources were created and interact with each one.
 *
 * Mirrors the EventSource interface:
 *   - `addEventListener(type, listener)` / `removeEventListener(type, listener)`
 *   - `onerror` handler property
 *   - `close()` spy (vi.fn() — assert it was called)
 *
 * Additional test helpers:
 *   - `simulateEvent(type, data)` — trigger all registered listeners for `type`
 *   - `simulateRawEvent(type, rawData)` — trigger listeners with a raw string payload
 *   - `simulateError()` — trigger the `onerror` handler
 */
export class MockEventSource {
  /** All instances created since the last `reset()` call. */
  static instances: MockEventSource[] = [];

  /** The URL passed to the constructor. */
  url: string;

  /** EventSource ready state (0 = CONNECTING). Not actively managed by the mock. */
  readyState: number = 0;

  /** Registered event listeners, keyed by event type. */
  private listeners: Record<string, SSEEventListener[]> = {};

  /** Error handler — mirrors the EventSource.onerror property. */
  onerror: ((event: Event) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  /**
   * Register a listener for a named SSE event type.
   *
   * @param type - The SSE event type (e.g. "progress", "complete", "error").
   * @param listener - Callback invoked when the event fires.
   */
  addEventListener(type: string, listener: SSEEventListener): void {
    if (!this.listeners[type]) {
      this.listeners[type] = [];
    }
    this.listeners[type].push(listener);
  }

  /**
   * Unregister a previously registered listener.
   *
   * @param type - The SSE event type.
   * @param listener - The exact listener reference to remove.
   */
  removeEventListener(type: string, listener: SSEEventListener): void {
    if (this.listeners[type]) {
      this.listeners[type] = this.listeners[type].filter((l) => l !== listener);
    }
  }

  /**
   * Simulate receiving a named SSE event.
   *
   * Serialises `data` to JSON and invokes all registered listeners for `type`.
   *
   * @param type - The SSE event type (e.g. "progress", "complete", "error").
   * @param data - The event payload (will be JSON-serialised).
   */
  simulateEvent(type: string, data: unknown): void {
    const event = { data: JSON.stringify(data) } as { data: string };
    const handlers = this.listeners[type] ?? [];
    handlers.forEach((h) => h(event));
  }

  /**
   * Simulate receiving a named SSE event with a raw string payload.
   *
   * Unlike `simulateEvent`, this method does NOT JSON-serialise the data.
   * Use this to test malformed / non-JSON payloads (e.g., `"NOT_JSON"`).
   *
   * @param type - The SSE event type (e.g. "progress", "complete", "error").
   * @param rawData - The raw string to pass as `event.data`.
   */
  simulateRawEvent(type: string, rawData: string): void {
    const event = { data: rawData } as { data: string };
    const handlers = this.listeners[type] ?? [];
    handlers.forEach((h) => h(event));
  }

  /**
   * Simulate a connection error (triggers the `onerror` handler).
   */
  simulateError(): void {
    if (this.onerror) {
      this.onerror(new Event("error"));
    }
  }

  /** Spy for the close() call — use `expect(es.close).toHaveBeenCalledOnce()`. */
  close = vi.fn();

  // ---------------------------------------------------------------------------
  // Static helpers
  // ---------------------------------------------------------------------------

  /**
   * Clear the instances array.
   *
   * Call in `beforeEach` to ensure test isolation.
   */
  static reset(): void {
    MockEventSource.instances = [];
  }
}

// ---------------------------------------------------------------------------
// Convenience helper
// ---------------------------------------------------------------------------

/**
 * Return the most recently created MockEventSource instance.
 *
 * Throws if no instance has been created yet.
 *
 * @returns The last element of `MockEventSource.instances`.
 * @throws Error if `MockEventSource.instances` is empty.
 */
export function getLastInstance(): MockEventSource {
  const instances = MockEventSource.instances;
  const inst = instances[instances.length - 1];
  if (!inst) throw new Error("No MockEventSource instance created");
  return inst;
}
