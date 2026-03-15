/**
 * RFC7807Toast — renders an RFC 7807 Problem Detail as a human-readable card.
 *
 * Displays the `title` as the heading and `detail` as the description.
 * Supports manual close via the dismiss button. Auto-dismiss behaviour
 * is the caller's responsibility (e.g. ErrorBoundary schedules a timer
 * and sets `visible` to false; Dashboard uses its own useEffect).
 *
 * CONSTITUTION: WCAG 2.1 AA — error region uses role="alert" for assertive
 * announcement. No domain-specific logic lives here; all OOM / hint rendering
 * is the caller's responsibility via the `problem.detail` field.
 */

import type { ProblemDetail } from "../api/client";

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
}: RFC7807ToastProps): JSX.Element | null {
  if (!visible || problem === null) {
    return null;
  }

  return (
    <div
      role="alert"
      style={{
        position: "fixed",
        top: "var(--spacing-lg)",
        right: "var(--spacing-lg)",
        zIndex: 1000,
        minWidth: "20rem",
        maxWidth: "32rem",
        backgroundColor: "var(--color-surface)",
        border: "1px solid var(--color-error)",
        borderRadius: "var(--radius-md)",
        padding: "var(--spacing-md)",
        boxShadow: "0 4px 16px rgba(0,0,0,0.4)",
        fontFamily: "var(--font-family)",
        color: "var(--color-text-primary)",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: "var(--spacing-sm)",
        }}
      >
        <div style={{ flex: 1 }}>
          <p
            style={{
              color: "var(--color-error)",
              fontWeight: 600,
              marginBottom: "var(--spacing-xs)",
            }}
          >
            {problem.title}
            {problem.status > 0 && (
              <span
                style={{
                  marginLeft: "var(--spacing-xs)",
                  fontSize: "0.875rem",
                  fontWeight: 400,
                  color: "var(--color-text-secondary)",
                }}
              >
                (HTTP {problem.status})
              </span>
            )}
          </p>
          <p
            style={{
              fontSize: "0.875rem",
              color: "var(--color-text-secondary)",
              marginBottom: 0,
            }}
          >
            {problem.detail}
          </p>
        </div>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss"
          style={{
            background: "none",
            border: "none",
            cursor: "pointer",
            color: "var(--color-text-secondary)",
            fontSize: "1.25rem",
            lineHeight: 1,
            padding: "0 var(--spacing-xs)",
            flexShrink: 0,
          }}
        >
          ×
        </button>
      </div>
    </div>
  );
}
