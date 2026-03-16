/**
 * AriaLive — reusable ARIA live region component.
 *
 * Provides screen-reader-friendly announcement containers that follow
 * WCAG 2.1 Success Criterion 4.1.3 (Status Messages).
 *
 * CONSTITUTION: WCAG 2.1 AA compliance — aria-live attributes ensure
 * assistive technologies announce updates without requiring user focus.
 *
 * IMPORTANT: Do NOT nest `role="alert"` inside `aria-live="polite"`.
 * Use separate containers. The assertive variant IS effectively an alert
 * region but does not use role="alert" to avoid duplicate announcements.
 *
 * P20-T20.3 AC3: Inline styles extracted to .aria-live-region CSS class
 * in global.css.
 */

import type { ReactNode } from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface AriaLiveProps {
  /** Content to be announced by screen readers. */
  children: ReactNode;
  /** Additional CSS className. */
  className?: string;
}

// ---------------------------------------------------------------------------
// Components
// ---------------------------------------------------------------------------

/**
 * PoliteAnnouncement — wraps content in an `aria-live="polite"` region.
 *
 * Screen readers will announce content updates when idle. Use for
 * progress updates (e.g., "Job Synthesis reached 50%").
 *
 * @param children - Content to announce.
 * @param className - Optional CSS class.
 */
export function PoliteAnnouncement({
  children,
  className,
}: AriaLiveProps): JSX.Element {
  return (
    <div
      aria-live="polite"
      aria-atomic="true"
      className={`aria-live-region${className ? ` ${className}` : ""}`}
    >
      {children}
    </div>
  );
}

/**
 * AssertiveAnnouncement — wraps content in an `aria-live="assertive"` region.
 *
 * Screen readers will interrupt current speech to announce errors.
 * Use for critical error messages only (e.g., job failure).
 *
 * @param children - Content to announce.
 * @param className - Optional CSS class.
 */
export function AssertiveAnnouncement({
  children,
  className,
}: AriaLiveProps): JSX.Element {
  return (
    <div
      aria-live="assertive"
      aria-atomic="true"
      className={`aria-live-region${className ? ` ${className}` : ""}`}
    >
      {children}
    </div>
  );
}
