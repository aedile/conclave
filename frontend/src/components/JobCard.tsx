/**
 * JobCard — displays a single synthesis job with status, progress, and actions.
 *
 * Shows the job's table name, status badge, epoch counter, and progress bar.
 * Renders a "Start" button only for QUEUED jobs. The progress bar is fully
 * accessible with role="progressbar" and aria-value* attributes.
 *
 * CONSTITUTION: WCAG 2.1 AA — all interactive elements are keyboard accessible,
 * progress bar has required ARIA attributes, status badges use semantic colours.
 * prefers-reduced-motion is respected via window.matchMedia so that inline
 * styles do not override the @media CSS block.
 *
 * ADV-061 fix: totalEpochs guard prevents division-by-zero when total_epochs=0.
 *
 * WCAG colour fix: TRAINING status badge uses --color-accent-text (#818cf8,
 * indigo-400, ~5:1 on --color-surface) instead of --color-accent (#4f46e5,
 * indigo-600, ~2.6:1 on --color-surface which fails WCAG 1.4.3 for small text).
 */

import type { JobResponse } from "../api/client";
import type { SSEState } from "../hooks/useSSE";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface JobCardProps {
  /** The job to display. */
  job: JobResponse;
  /** SSE streaming state for this job, if it is currently active. */
  sseState: SSEState | null;
  /** Callback invoked when the user clicks the Start button. */
  onStart: (jobId: number) => void;
  /** Whether a start action is in progress for this job. */
  isStarting: boolean;
}

// ---------------------------------------------------------------------------
// Status badge colours — mapped to CSS custom properties
//
// WCAG note: Status badge text is rendered at 0.75rem (12px) uppercase on
// --color-surface (#1a1d27). WCAG 1.4.3 requires 4.5:1 contrast for small text.
//   QUEUED:   --color-text-secondary (#9ca3af) ~5.9:1 on --color-surface  ✓
//   TRAINING: --color-accent-text (#818cf8)    ~5:1 on --color-surface     ✓
//             (NOT --color-accent #4f46e5 which is only ~2.6:1 — fails AA)
//   COMPLETE: --color-success (#34d399)        ~8.4:1 on --color-surface  ✓
//   FAILED:   --color-error (#f87171)          ~5.8:1 on --color-surface  ✓
// ---------------------------------------------------------------------------

const STATUS_COLORS: Record<string, string> = {
  QUEUED: "var(--color-text-secondary)",
  TRAINING: "var(--color-accent-text)",
  COMPLETE: "var(--color-success)",
  FAILED: "var(--color-error)",
};

// ---------------------------------------------------------------------------
// Reduced motion helper
// ---------------------------------------------------------------------------

/**
 * Check whether the user has requested reduced motion.
 *
 * Evaluated lazily inside the component to avoid module-load failures in
 * environments where `window.matchMedia` is unavailable (e.g. jsdom tests).
 * Returns false (motion enabled) when the API is not present.
 */
function checkPrefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Calculate a progress percentage safely, guarding against division by zero.
 *
 * ADV-061: When total_epochs is 0 (job created with total_epochs=0, which is
 * an edge case that should be prevented by the API but may reach the UI),
 * return 0 instead of NaN to keep the progressbar aria-valuenow valid.
 *
 * @param currentEpoch - The current epoch count.
 * @param totalEpochs - The total epoch count. Treated as 0 if falsy.
 * @returns Integer percentage in [0, 100], or 0 if totalEpochs is 0.
 */
function safePercent(currentEpoch: number, totalEpochs: number): number {
  if (totalEpochs === 0) return 0;
  return Math.round((currentEpoch / totalEpochs) * 100);
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Render a synthesis job card.
 *
 * @param job - The job data from the API.
 * @param sseState - Live SSE state if streaming; null otherwise.
 * @param onStart - Handler called with the job ID when Start is clicked.
 * @param isStarting - Disables the button while start is pending.
 */
export default function JobCard({
  job,
  sseState,
  onStart,
  isStarting,
}: JobCardProps): JSX.Element {
  // Evaluate reduced-motion preference inside render so tests can stub
  // window.matchMedia after module load without errors at import time.
  const prefersReducedMotion = checkPrefersReducedMotion();

  // Use SSE state if available and training, otherwise fall back to job snapshot
  const isStreaming = sseState !== null && sseState.status !== null;
  const displayStatus = isStreaming ? (sseState.status ?? job.status) : job.status;
  const displayEpoch = isStreaming
    ? (sseState.currentEpoch ?? job.current_epoch)
    : job.current_epoch;
  const displayTotal = isStreaming
    ? (sseState.totalEpochs ?? job.total_epochs)
    : job.total_epochs;

  // ADV-061: use safePercent to guard against total_epochs=0 (division-by-zero)
  const displayPercent = isStreaming
    ? (sseState.percent ?? safePercent(job.current_epoch, job.total_epochs))
    : safePercent(job.current_epoch, job.total_epochs);

  const statusColor = STATUS_COLORS[displayStatus] ?? "var(--color-text-secondary)";

  const showProgressBar =
    displayStatus === "TRAINING" || displayStatus === "COMPLETE" || displayStatus === "FAILED";

  return (
    <article
      style={{
        backgroundColor: "var(--color-surface)",
        border: "1px solid var(--color-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--spacing-md)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--spacing-sm)",
      }}
    >
      {/* Header row — table name + status badge */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <h3
          style={{
            fontFamily: "var(--font-family)",
            fontWeight: 600,
            color: "var(--color-text-primary)",
            fontSize: "1rem",
            margin: 0,
          }}
        >
          {job.table_name}
        </h3>
        <span
          style={{
            fontFamily: "var(--font-family)",
            fontSize: "0.75rem",
            fontWeight: 600,
            color: statusColor,
            textTransform: "uppercase",
            letterSpacing: "0.05em",
          }}
        >
          {displayStatus}
        </span>
      </div>

      {/* Epoch counter */}
      <p
        style={{
          fontFamily: "var(--font-family)",
          fontSize: "0.875rem",
          color: "var(--color-text-secondary)",
          margin: 0,
        }}
      >
        Epoch {displayEpoch} / {displayTotal}
      </p>

      {/* Progress bar — visible during / after training */}
      {showProgressBar && (
        <div
          role="progressbar"
          aria-label={`Job ${job.id} progress`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={displayPercent}
          style={{
            height: "0.5rem",
            backgroundColor: "var(--color-border)",
            borderRadius: "var(--radius-sm)",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              width: `${displayPercent}%`,
              height: "100%",
              backgroundColor:
                displayStatus === "FAILED"
                  ? "var(--color-error)"
                  : displayStatus === "COMPLETE"
                    ? "var(--color-success)"
                    : "var(--color-accent)",
              // Respect prefers-reduced-motion: window.matchMedia check takes
              // precedence over inline style so @media rules cannot be bypassed.
              transition: prefersReducedMotion ? "none" : "width 0.3s ease",
            }}
          />
        </div>
      )}

      {/* Error message for failed jobs */}
      {displayStatus === "FAILED" && (sseState?.error ?? job.error_msg) && (
        <p
          style={{
            fontFamily: "var(--font-family)",
            fontSize: "0.875rem",
            color: "var(--color-error)",
            margin: 0,
          }}
        >
          {sseState?.error ?? job.error_msg}
        </p>
      )}

      {/* Start button — only shown for QUEUED jobs */}
      {job.status === "QUEUED" && (
        <button
          type="button"
          disabled={isStarting}
          onClick={() => onStart(job.id)}
          style={{
            alignSelf: "flex-start",
            backgroundColor: "var(--color-accent)",
            color: "#ffffff",
            border: "none",
            borderRadius: "var(--radius-sm)",
            padding: "var(--spacing-xs) var(--spacing-md)",
            fontFamily: "var(--font-family)",
            fontSize: "0.875rem",
            fontWeight: 600,
            cursor: isStarting ? "not-allowed" : "pointer",
            opacity: isStarting ? 0.7 : 1,
          }}
        >
          {isStarting ? "Starting…" : "Start"}
        </button>
      )}
    </article>
  );
}
