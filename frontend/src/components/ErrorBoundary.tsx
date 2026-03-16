/**
 * ErrorBoundary — global React error boundary with RFC 7807 toast rendering.
 *
 * Catches unhandled React render errors and displays a human-readable
 * remediation card. When the toast auto-dismisses, a persistent fallback
 * UI is shown so the screen is never blank.
 *
 * CONSTITUTION: WCAG 2.1 AA — error region uses role="alertdialog" for
 * modal notification (via RFC7807Toast). Auto-dismiss timer is cleared on
 * unmount.
 *
 * P20-T20.3 AC3: No inline style= attributes — all layout via CSS classes.
 *
 * Guards against:
 * - Timer cleanup: dismiss timer stored in instance variable, cleared on reset
 * - Fake timer deadlock: no timer interaction at module level
 * - Conflicting live-region nesting: role="alertdialog" is its own container,
 *   NOT nested inside aria-live="polite"
 * - Blank screen after toast dismiss: persistent fallback UI shown when
 *   hasError is true but toastVisible is false
 */

import React, { Component, type ReactNode } from "react";
import type { ProblemDetail } from "../api/client";
import { RFC7807Toast } from "./RFC7807Toast";

// ---------------------------------------------------------------------------
// Re-export for consumers that previously imported from here
// ---------------------------------------------------------------------------

export { RFC7807Toast } from "./RFC7807Toast";
export type { RFC7807ToastProps } from "./RFC7807Toast";

// ---------------------------------------------------------------------------
// Auto-dismiss timeout (ms)
// ---------------------------------------------------------------------------

const TOAST_DISMISS_MS = 5000;

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
 * the thrown value and renders an RFC7807Toast for TOAST_DISMISS_MS, then
 * transitions to a persistent fallback UI so the screen is never blank.
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

    if (hasError && toastVisible) {
      return (
        <RFC7807Toast
          problem={problem}
          onDismiss={this.handleDismiss}
          visible={toastVisible}
        />
      );
    }

    // Persistent fallback — shown after toast auto-dismisses or after manual
    // dismiss. Prevents a blank screen when hasError is true but toast is gone.
    if (hasError && !toastVisible) {
      return (
        <main className="error-boundary-fallback">
          <h1 className="error-boundary-fallback__title">
            Something went wrong
          </h1>
          <p className="error-boundary-fallback__detail">
            {problem?.detail}
          </p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="error-boundary-fallback__reload"
          >
            Reload page
          </button>
        </main>
      );
    }

    return this.props.children;
  }
}
