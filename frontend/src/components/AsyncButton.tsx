/**
 * AsyncButton — reusable button with standardized loading/disabled pattern.
 *
 * Unifies the async button pattern across Unseal, Dashboard, and JobCard.
 * The reference implementation is the Unseal submit button (spinner + aria).
 *
 * WCAG 2.1 AA:
 *   - aria-disabled reflects combined loading + external disabled state.
 *   - aria-live="polite" role="status" region announces loading start/end to
 *     screen readers without interrupting ongoing speech.
 *   - Spinner span is aria-hidden="true" so only loadingText is announced.
 *   - Visible :focus-visible ring is provided by global.css.
 *   - forwardRef allows parent to restore focus after error toast dismiss
 *     (Phase 23 retro — focus restoration pattern).
 *
 * BEM classes:
 *   .async-button          — block (applied by default; callers may supply className too)
 *   .async-button__spinner — spinner span (shared with all async button instances)
 *   .async-button--loading — modifier (applied when isLoading=true)
 *
 * P27-T27.3: Do not change click-handler, loading-state management, or error
 * handling — those remain in parent components.
 */

import { forwardRef } from "react";
import type { ButtonHTMLAttributes, ReactNode } from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface AsyncButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Controls the loading state. When true, shows spinner + loadingText. */
  isLoading: boolean;
  /** Text announced to screen readers and displayed during loading. */
  loadingText: string;
  /** Default button content (shown when not loading). */
  children: ReactNode;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * A button with a standardized loading state.
 *
 * When `isLoading` is true the button:
 *   1. Shows a decorative spinner (`aria-hidden`) and `loadingText`.
 *   2. Is disabled (native + aria-disabled).
 *   3. Announces the loading state via an `aria-live="polite"` region.
 *
 * Supports `ref` forwarding for focus restoration after async error dismissal.
 *
 * @param isLoading - Whether the button is in a loading state.
 * @param loadingText - Text shown (and announced) while loading.
 * @param children - Default button label content.
 * @param disabled - Optional external disabled state.
 * @param className - Additional CSS classes for layout-specific styling.
 * @param ref - Forwarded ref to the underlying `<button>` element.
 */
const AsyncButton = forwardRef<HTMLButtonElement, AsyncButtonProps>(
  function AsyncButton(
    {
      isLoading,
      loadingText,
      children,
      disabled = false,
      className,
      type = "button",
      ...rest
    },
    ref,
  ) {
    const isDisabled = isLoading || disabled;

    const buttonClass = [
      "async-button",
      isLoading ? "async-button--loading" : "",
      className ?? "",
    ]
      .filter(Boolean)
      .join(" ");

    return (
      <>
        {/*
         * aria-live region — outside the button so it persists across
         * button re-renders. "polite" does not interrupt ongoing speech.
         * role="status" is equivalent to aria-live="polite" + aria-atomic="true"
         * per ARIA spec. The region announces both the start (loadingText) and
         * end (empty string) of the async operation, satisfying the Phase 23
         * retro requirement for dual-state announcement.
         */}
        <span
          role="status"
          aria-live="polite"
          aria-atomic="true"
          className="async-button__live-region"
        >
          {isLoading ? loadingText : ""}
        </span>

        <button
          ref={ref}
          type={type}
          disabled={isDisabled}
          aria-disabled={isDisabled}
          className={buttonClass}
          {...rest}
        >
          {isLoading ? (
            <>
              {/*
               * Spinner is decorative — aria-hidden so the live region text
               * (above) is the sole screen reader announcement for loading state.
               * This prevents double-announcement (Phase 23 UI/UX finding).
               */}
              <span
                aria-hidden="true"
                className="async-button__spinner"
              />
              <span>{loadingText}</span>
            </>
          ) : (
            children
          )}
        </button>
      </>
    );
  },
);

export default AsyncButton;
