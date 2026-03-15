/**
 * ErrorBoundary — global React error boundary with RFC 7807 toast rendering.
 *
 * Catches unhandled React render errors and displays a human-readable
 * remediation card. Also exports a standalone RFC7807Toast component for
 * use in non-error-boundary contexts (e.g., API call failures).
 *
 * CONSTITUTION: WCAG 2.1 AA — error region uses role="alert" for
 * assertive announcement. Auto-dismiss timer is cleared on unmount.
 *
 * Guards against:
 * - Timer cleanup: dismiss timer stored in instance variable, cleared on reset
 * - Fake timer deadlock: no timer interaction at module level
 * - Conflicting live-region nesting: role="alert" is its own container,
 *   NOT nested inside aria-live="polite"
 */

import React, { Component, type ReactNode } from "react";
import type { ProblemDetail } from "../api/client";

// ---------------------------------------------------------------------------
// Auto-dismiss timeout (ms)
// ---------------------------------------------------------------------------

const TOAST_DISMISS_MS = 5000;

// ---------------------------------------------------------------------------
// RFC 7807 Toast component
// ---------------------------------------------------------------------------

interface RFC7807ToastProps {
  /** The RFC 7807 problem detail to display. */
  problem: ProblemDetail | null;
  /** Callback when the user manually dismisses the toast. */
  onDismiss: () => void;
  /** Whether the toast is visible. */
  visible: boolean;
}

/**
 * RFC7807Toast — renders an RFC 7807 Problem Detail as a human-readable card.
 *
 * Displays the `title` as the heading and `detail` as the description.
 * Auto-dismisses after TOAST_DISMISS_MS. Supports manual close via button.
 *
 * @param problem - The RFC 7807 error payload.
 * @param onDismiss - Callback to clear the error state.
 * @param visible - Controls visibility.
 */
export function RFC7807Toast({
  problem,
  onDismiss,
  visible,
}: RFC7807ToastProps): JSX.Element | null {
  if (!visible || problem === null) {
    return null;
  }

  const isOomError =
    problem.detail.toLowerCase().includes("reduction_factor") ||
    problem.detail.toLowerCase().includes("out of memory");

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
              marginBottom: isOomError ? "var(--spacing-sm)" : 0,
            }}
          >
            {problem.detail}
          </p>
          {isOomError && (
            <p
              style={{
                fontSize: "0.875rem",
                color: "var(--color-warning)",
                backgroundColor: "rgba(251,191,36,0.1)",
                padding: "var(--spacing-xs) var(--spacing-sm)",
                borderRadius: "var(--radius-sm)",
              }}
            >
              Remediation: reduce the number of epochs or the batch size to
              lower memory usage.
            </p>
          )}
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

// ---------------------------------------------------------------------------
// ErrorBoundary state
// ---------------------------------------------------------------------------

interface ErrorBoundaryState {
  hasError: boolean;
  problem: ProblemDetail | null;
  toastVisible: boolean;
}

interface ErrorBoundaryProps {
  children: ReactNode;
}

// ---------------------------------------------------------------------------
// ErrorBoundary class component
// ---------------------------------------------------------------------------

/**
 * ErrorBoundary — React class component that catches unhandled render errors.
 *
 * Renders children normally when no error has occurred. On error, captures
 * the thrown value and renders an RFC7807Toast.
 *
 * Must be a class component — React's `componentDidCatch` API is only
 * available on class components.
 */
export default class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  private dismissTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, problem: null, toastVisible: false };
  }

  static getDerivedStateFromError(error: unknown): Partial<ErrorBoundaryState> {
    // Build a ProblemDetail from the caught error.
    // The error may or may not be an RFC 7807 object — normalise it.
    const detail =
      error instanceof Error ? error.message : String(error);

    const problem: ProblemDetail = {
      type: "about:blank",
      title: "Unexpected Error",
      status: 0,
      detail,
    };

    return { hasError: true, problem, toastVisible: true };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo): void {
    // Log to console for debugging in development.
    // Production deployments should send to an error reporting service here.
    console.error("[ErrorBoundary] Caught render error:", error, info);

    // Schedule auto-dismiss
    this.scheduleDismiss();
  }

  componentWillUnmount(): void {
    this.clearDismissTimer();
  }

  private scheduleDismiss(): void {
    this.clearDismissTimer();
    this.dismissTimer = setTimeout(() => {
      this.setState({ toastVisible: false });
    }, TOAST_DISMISS_MS);
  }

  private clearDismissTimer(): void {
    if (this.dismissTimer !== null) {
      clearTimeout(this.dismissTimer);
      this.dismissTimer = null;
    }
  }

  private handleDismiss = (): void => {
    this.clearDismissTimer();
    this.setState({ toastVisible: false });
  };

  override render(): ReactNode {
    const { hasError, problem, toastVisible } = this.state;

    if (hasError) {
      return (
        <RFC7807Toast
          problem={problem}
          onDismiss={this.handleDismiss}
          visible={toastVisible}
        />
      );
    }

    return this.props.children;
  }
}
