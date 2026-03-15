/**
 * JobCard — displays a single synthesis job with status, progress, and actions.
 *
 * Shows the job's table name, status badge, epoch counter, and progress bar.
 * Renders a "Start" button only for QUEUED jobs. The progress bar is fully
 * accessible with role="progressbar" and aria-value* attributes.
 *
 * CONSTITUTION: WCAG 2.1 AA — all interactive elements are keyboard accessible,
 * progress bar has required ARIA attributes, status badges use semantic colours.
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
// ---------------------------------------------------------------------------

const STATUS_COLORS: Record<string, string> = {
  QUEUED: "var(--color-text-secondary)",
  TRAINING: "var(--color-accent)",
  COMPLETE: "var(--color-success)",
  FAILED: "var(--color-error)",
};

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
  // Use SSE state if available and training, otherwise fall back to job snapshot
  const isStreaming = sseState !== null && sseState.status !== null;
  const displayStatus = isStreaming ? (sseState.status ?? job.status) : job.status;
  const displayEpoch = isStreaming
    ? (sseState.currentEpoch ?? job.current_epoch)
    : job.current_epoch;
  const displayTotal = isStreaming
    ? (sseState.totalEpochs ?? job.total_epochs)
    : job.total_epochs;
  const displayPercent = isStreaming
    ? (sseState.percent ?? Math.round((job.current_epoch / job.total_epochs) * 100))
    : Math.round((job.current_epoch / job.total_epochs) * 100);

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
              transition: "width 0.3s ease",
              // Respect prefers-reduced-motion
              // (handled via global.css @media block)
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
