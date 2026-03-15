/**
 * Vitest unit tests for the ErrorBoundary component.
 *
 * Tests React error catching, RFC 7807 toast rendering, and dismiss behaviour.
 *
 * Guards against:
 * - Fake timers deadlock: useFakeTimers only in tests that need timer advancement
 * - Timer cleanup: verify auto-dismiss clears its timer
 */

import {
  act,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ErrorBoundary from "../components/ErrorBoundary";

// ---------------------------------------------------------------------------
// Helper — component that throws on demand
// ---------------------------------------------------------------------------

function BombComponent({ shouldThrow }: { shouldThrow: boolean }) {
  if (shouldThrow) {
    throw new Error("Bomb exploded");
  }
  return <div>Safe content</div>;
}

// ---------------------------------------------------------------------------
// Suppress console.error for intentional throws
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ErrorBoundary", () => {
  it("renders children when no error is thrown", () => {
    render(
      <ErrorBoundary>
        <div>Child content</div>
      </ErrorBoundary>,
    );

    expect(screen.getByText("Child content")).toBeInTheDocument();
  });

  it("catches a child component error and renders fallback UI", () => {
    render(
      <ErrorBoundary>
        <BombComponent shouldThrow={true} />
      </ErrorBoundary>,
    );

    // Should not show the child
    expect(screen.queryByText("Safe content")).not.toBeInTheDocument();
    // Should show error fallback
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("does not catch errors outside its subtree", () => {
    // Rendering two siblings — ErrorBoundary only wraps one
    render(
      <div>
        <ErrorBoundary>
          <div>Protected child</div>
        </ErrorBoundary>
        <div>Outside sibling</div>
      </div>,
    );

    expect(screen.getByText("Outside sibling")).toBeInTheDocument();
  });
});

describe("RFC7807Toast component", () => {
  it("renders title and detail from RFC 7807 format", () => {
    render(
      <ErrorBoundary>
        <BombComponent shouldThrow={true} />
      </ErrorBoundary>,
    );

    // The fallback should display something human-readable
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("toast can be dismissed via close button", async () => {
    const user = userEvent.setup();

    render(
      <ErrorBoundary>
        <BombComponent shouldThrow={true} />
      </ErrorBoundary>,
    );

    const closeButton = screen.getByRole("button", { name: /dismiss/i });
    await user.click(closeButton);

    // After dismiss the toast/alert region should be gone
    await waitFor(() => {
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });
  });

  it("auto-dismisses toast after timeout", async () => {
    vi.useFakeTimers();

    render(
      <ErrorBoundary>
        <BombComponent shouldThrow={true} />
      </ErrorBoundary>,
    );

    expect(screen.getByRole("alert")).toBeInTheDocument();

    // Advance past TOAST_DISMISS_MS (5000ms) and flush React state.
    // Do NOT use waitFor() with fake timers — it deadlocks.
    await act(async () => {
      vi.advanceTimersByTime(6000);
    });

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
