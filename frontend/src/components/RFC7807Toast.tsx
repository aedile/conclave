/**
 * RFC7807Toast — renders an RFC 7807 Problem Detail as a human-readable card.
 *
 * Displays the `title` as the heading and `detail` as the description.
 * Supports manual close via the dismiss button. Auto-dismiss behaviour
 * is the caller's responsibility (e.g. ErrorBoundary schedules a timer
 * and sets `visible` to false; Dashboard uses its own useEffect).
 *
 * CONSTITUTION: WCAG 2.1 AA (P20-T20.3):
 *   - role="alertdialog" + aria-modal="true" announces the toast as a
 *     modal dialog to screen readers (AC4).
 *   - Always-present container pattern: the outer div is always in the DOM
 *     but hidden via the HTML `hidden` attribute when not visible. This
 *     prevents NVDA+Firefox from swallowing repeat announcements when the
 *     same container is destroyed and recreated (T17.2 retro finding).
 *   - aria-labelledby associates the dialog with its visible title heading.
 *   - aria-describedby associates the dialog with its detail paragraph.
 *   - NOTE: aria-live="assertive" is intentionally omitted. role="alertdialog"
 *     carries implicit aria-live="assertive" semantics per ARIA spec. Adding
 *     the explicit attribute causes double-announcement in NVDA+Firefox
 *     (documented in T17.2 retro — the exact issue this component guards against).
 *   - Focus is transferred to the container when the toast becomes visible,
 *     then trapped within via useFocusTrap (AC5). tabIndex={-1} allows the
 *     container to receive programmatic focus without entering the tab order.
 *   - No inline style= attributes on layout — all via CSS classes (AC3).
 */

import { useEffect, useRef } from "react";
import type { ProblemDetail } from "../api/client";
import { useFocusTrap } from "../hooks/useFocusTrap";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface RFC7807ToastProps {
  /** The RFC 7807 problem detail to display. */
  problem: ProblemDetail | null;
  /** Callback when the user manually dismisses the toast. */
  onDismiss: () => void;
  /** Whether the toast is visible. */
  visible: boolean;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * RFC7807Toast — renders an RFC 7807 Problem Detail as a dismissible card.
 *
 * Does NOT auto-dismiss on its own. The caller is responsible for setting
 * `visible` to false after a timeout (e.g. via `setTimeout` in a `useEffect`).
 *
 * @param problem - The RFC 7807 error payload.
 * @param onDismiss - Callback to clear the error state.
 * @param visible - Controls visibility; set to false to hide the toast.
 */
export function RFC7807Toast({
  problem,
  onDismiss,
  visible,
}: RFC7807ToastProps): JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);

  const isShown = visible && problem !== null;

  // Trap focus within the toast when it is visible (AC5)
  useFocusTrap(containerRef, isShown);

  // Transfer focus to the container when the toast becomes visible so that
  // keyboard and screen-reader users are immediately aware of the alert (AC5).
  // tabIndex={-1} on the container div allows this programmatic focus without
  // placing the container in the natural tab order.
  useEffect(() => {
    if (isShown) {
      containerRef.current?.focus();
    }
  }, [isShown]);

  return (
    <div
      ref={containerRef}
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="rfc7807-toast-title"
      aria-describedby="rfc7807-toast-detail"
      // tabIndex={-1} allows programmatic focus transfer without adding the
      // container to the natural tab order.
      tabIndex={-1}
      // Always-present container: use hidden attribute to hide semantically
      // rather than returning null, preventing NVDA+Firefox repeat swallowing.
      hidden={!isShown}
      className="rfc7807-toast"
    >
      <div className="rfc7807-toast__inner">
        <div className="rfc7807-toast__body">
          <p id="rfc7807-toast-title" className="rfc7807-toast__title">
            {problem?.title}
            {problem !== null && problem.status > 0 && (
              <span className="rfc7807-toast__status">
                (HTTP {problem.status})
              </span>
            )}
          </p>
          <p id="rfc7807-toast-detail" className="rfc7807-toast__detail">
            {problem?.detail}
          </p>
        </div>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss"
          className="rfc7807-toast__dismiss"
        >
          ×
        </button>
      </div>
    </div>
  );
}
