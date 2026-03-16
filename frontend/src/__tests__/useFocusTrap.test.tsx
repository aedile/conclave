/**
 * Vitest unit tests for the useFocusTrap hook.
 *
 * Verifies that:
 *   - Tab cycles focus forward within the trap container
 *   - Shift+Tab cycles focus backward within the trap container
 *   - Focus does not escape beyond the last/first focusable element
 *   - The hook is a no-op when isActive=false
 *   - The hook does nothing when the container has zero focusable elements
 *
 * WCAG 2.1 AA — SC 2.1.2 (No Keyboard Trap) requires that keyboard
 * focus can be moved to and from the modal using standard keys. The
 * hook satisfies this by cycling within the modal when active, and
 * doing nothing when inactive.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef } from "react";
import { describe, expect, it } from "vitest";
import { useFocusTrap } from "../hooks/useFocusTrap";

// ---------------------------------------------------------------------------
// Test component helpers
// ---------------------------------------------------------------------------

/**
 * A minimal component that renders three focusable buttons inside a container
 * and applies the useFocusTrap hook.
 */
function TrapContainer({ isActive }: { isActive: boolean }) {
  const containerRef = useRef<HTMLDivElement>(null);
  useFocusTrap(containerRef, isActive);

  return (
    <div ref={containerRef}>
      <button type="button">First</button>
      <button type="button">Second</button>
      <button type="button">Third</button>
    </div>
  );
}

/**
 * Renders the trap container with a button outside to verify focus does
 * not escape.
 */
function WithOutsideButton({ isActive }: { isActive: boolean }) {
  const containerRef = useRef<HTMLDivElement>(null);
  useFocusTrap(containerRef, isActive);

  return (
    <>
      <div ref={containerRef}>
        <button type="button">First</button>
        <button type="button">Last</button>
      </div>
      <button type="button">Outside</button>
    </>
  );
}

/**
 * Renders the trap container with NO focusable children inside the trap
 * boundary. Exercises the `if (focusable.length === 0) return;` guard.
 * A button outside the container is present to confirm normal tab order
 * is unaffected by the (no-op) trap.
 */
function EmptyTrapContainer({ isActive }: { isActive: boolean }) {
  const containerRef = useRef<HTMLDivElement>(null);
  useFocusTrap(containerRef, isActive);

  return (
    <>
      <div ref={containerRef}>
        {/* Intentionally no focusable elements inside the trap boundary */}
        <p>No interactive content here</p>
      </div>
      <button type="button">Outside</button>
    </>
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useFocusTrap", () => {
  it("cycles Tab from last focusable element back to first", async () => {
    const user = userEvent.setup();
    render(<TrapContainer isActive={true} />);

    const first = screen.getByRole("button", { name: "First" });
    const last = screen.getByRole("button", { name: "Third" });

    // Focus the last button, then Tab — should wrap to first
    last.focus();
    expect(document.activeElement).toBe(last);

    await user.tab();
    expect(document.activeElement).toBe(first);
  });

  it("cycles Shift+Tab from first focusable element back to last", async () => {
    const user = userEvent.setup();
    render(<TrapContainer isActive={true} />);

    const first = screen.getByRole("button", { name: "First" });
    const last = screen.getByRole("button", { name: "Third" });

    // Focus the first button, then Shift+Tab — should wrap to last
    first.focus();
    expect(document.activeElement).toBe(first);

    await user.tab({ shift: true });
    expect(document.activeElement).toBe(last);
  });

  it("does NOT trap focus when isActive=false", async () => {
    const user = userEvent.setup();
    render(<WithOutsideButton isActive={false} />);

    const last = screen.getByRole("button", { name: "Last" });
    const outside = screen.getByRole("button", { name: "Outside" });

    // Focus the last button, Tab — with trap inactive, focus goes to outside
    last.focus();
    await user.tab();
    expect(document.activeElement).toBe(outside);
  });

  it("moves Tab from second to third button normally (mid-trap navigation)", async () => {
    const user = userEvent.setup();
    render(<TrapContainer isActive={true} />);

    const second = screen.getByRole("button", { name: "Second" });
    const third = screen.getByRole("button", { name: "Third" });

    second.focus();
    await user.tab();
    expect(document.activeElement).toBe(third);
  });

  it("moves Shift+Tab from second to first button normally (mid-trap navigation)", async () => {
    const user = userEvent.setup();
    render(<TrapContainer isActive={true} />);

    const first = screen.getByRole("button", { name: "First" });
    const second = screen.getByRole("button", { name: "Second" });

    second.focus();
    await user.tab({ shift: true });
    expect(document.activeElement).toBe(first);
  });

  it("does nothing and does not throw when container has zero focusable elements", async () => {
    const user = userEvent.setup();
    render(<EmptyTrapContainer isActive={true} />);

    const outside = screen.getByRole("button", { name: "Outside" });

    // Focus the outside button and Tab — the trap container has no focusable
    // children so the hook's `if (focusable.length === 0) return;` guard fires.
    // Focus moves normally (no crash, no infinite loop).
    outside.focus();
    expect(document.activeElement).toBe(outside);

    // Tab from the only focusable element — browser wraps to body.
    // The key assertion is that the hook's zero-guard fires without error.
    await user.tab();
    // No exception thrown — the guard executed safely.
    expect(document.activeElement).not.toBe(outside);
  });
});
