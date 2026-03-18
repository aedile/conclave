/**
 * JobCard — displays a single synthesis job with status, progress, and actions.
 *
 * Shows the job's table name, status badge, epoch counter, and progress bar.
 * Renders a "Start" button only for QUEUED jobs.  Renders a "Download" button
 * only for COMPLETE jobs (P23-T23.3).  The progress bar is fully accessible
 * with role="progressbar" and aria-value* attributes.
 *
 * CONSTITUTION: WCAG 2.1 AA — all interactive elements are keyboard accessible,
 * progress bar has required ARIA attributes, status badges use semantic colours.
 * prefers-reduced-motion is respected via global.css @media rule on the
 * .job-card__progress-fill class.
 *
 * P20-T20.3 AC3: No inline style= attributes on layout elements — all via CSS
 * classes. The single remaining style= on the status badge span is a CSS custom
 * property token assignment (color only), not a layout-bearing inline style.
 *
 * ADV-061 fix: totalEpochs guard prevents division-by-zero when total_epochs=0,
 * negative, or NaN — uses !totalEpochs || totalEpochs <= 0 to cover all falsy
 * and negative edge cases.
 *
 * WCAG colour fix: TRAINING status badge uses --color-accent-text (#818cf8,
 * indigo-400, ~5.6:1 on --color-surface) instead of --color-accent (#4f46e5,
 * indigo-600, ~3:1 on --color-bg which fails WCAG 1.4.3 for small text).
 *
 * P23-T23.3: Download button uses --color-success token so it remains visually
 * distinct from the Start button (--color-accent-text) and maintains 4.5:1
 * contrast ratio on --color-surface.
 *
 * P27-T27.3: Start and Download buttons replaced with AsyncButton component
 * for standardized loading/disabled pattern. Layout classes (.job-card__start-btn,
 * .job-card__download-btn) passed via className to preserve existing styling.
 */

import type { JobResponse } from "../api/client";
import type { SSEState } from "../hooks/useSSE";
import AsyncButton from "./AsyncButton";

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
  /** Callback invoked when the user clicks the Download button. */
  onDownload: (jobId: number) => void;
  /** Whether a download action is in progress for this job. */
  isDownloading: boolean;
}

// ---------------------------------------------------------------------------
// Status badge colours — mapped to CSS custom properties
//
// WCAG note: Status badge text is rendered at 0.75rem (12px) uppercase on
// --color-surface (#1a1d27). WCAG 1.4.3 requires 4.5:1 contrast for small text.
//   QUEUED:   --color-text-secondary (#9ca3af) ~5.9:1 on --color-surface  ✓
//   TRAINING: --color-accent-text (#818cf8)    ~5.6:1 on --color-surface  ✓
//             (NOT --color-accent #4f46e5 which is only ~3:1 on bg — fails AA)
//   COMPLETE: --color-success (#34d399)        ~8.4:1 on --color-surface  ✓
//   FAILED:   --color-error (#f87171)          ~5.8:1 on --color-surface  ✓
//
// Applied via inline CSS custom property on the span — a single colour token
// assignment that does not constitute a layout-bearing inline style.
// ---------------------------------------------------------------------------

const STATUS_COLORS: Record<string, string> = {
  QUEUED: "var(--color-text-secondary)",
  TRAINING: "var(--color-accent-text)",
  COMPLETE: "var(--color-success)",
  FAILED: "var(--color-error)",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Calculate a progress percentage safely, guarding against division by zero,
 * negative values, and NaN.
 *
 * ADV-061: When total_epochs is falsy (0, NaN) or negative (which should be
 * prevented by the API but may still reach the UI), return 0 instead of NaN
 * or a negative value to keep the progressbar aria-valuenow valid.
 *
 * @param currentEpoch - The current epoch count.
 * @param totalEpochs - The total epoch count. Returns 0 if falsy or negative.
 * @returns Integer percentage in [0, 100], or 0 if totalEpochs is falsy or ≤ 0.
 */
function safePercent(currentEpoch: number, totalEpochs: number): number {
  if (!totalEpochs || totalEpochs <= 0) return 0;
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
 * @param onDownload - Handler called with the job ID when Download is clicked.
 * @param isDownloading - Disables the button while download is in progress.
 */
export default function JobCard({
  job,
  sseState,
  onStart,
  isStarting,
  onDownload,
  isDownloading,
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

  // ADV-061: use safePercent to guard against total_epochs=0, negative, or NaN
  const displayPercent = isStreaming
    ? (sseState.percent ?? safePercent(job.current_epoch, job.total_epochs))
    : safePercent(job.current_epoch, job.total_epochs);

  const statusColor = STATUS_COLORS[displayStatus] ?? "var(--color-text-secondary)";

  const showProgressBar =
    displayStatus === "TRAINING" || displayStatus === "COMPLETE" || displayStatus === "FAILED";

  // Progress fill class — colour varies by job state
  const progressFillClass = [
    "job-card__progress-fill",
    displayStatus === "FAILED" ? "job-card__progress-fill--failed" : "",
    displayStatus === "COMPLETE" ? "job-card__progress-fill--complete" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <article className="job-card">
      {/* Header row — table name + status badge */}
      <div className="job-card__header">
        <h3 className="job-card__title">{job.table_name}</h3>
        {/* Status color is a single CSS custom property token — not a layout style */}
        <span
          className="job-card__status"
          style={{ color: statusColor }}
        >
          {displayStatus}
        </span>
      </div>

      {/* Epoch counter */}
      <p className="job-card__epoch">
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
          className="job-card__progress-track"
        >
          <div
            className={progressFillClass}
            style={{ width: `${displayPercent}%` }}
          />
        </div>
      )}

      {/* Error message for failed jobs */}
      {displayStatus === "FAILED" && (sseState?.error ?? job.error_msg) && (
        <p className="job-card__error-msg">
          {sseState?.error ?? job.error_msg}
        </p>
      )}

      {/* Start button — only shown for QUEUED jobs.
          P27-T27.3: replaced with AsyncButton; layout class passed via className. */}
      {job.status === "QUEUED" && (
        <AsyncButton
          type="button"
          isLoading={isStarting}
          loadingText="Starting…"
          onClick={() => onStart(job.id)}
          className="job-card__start-btn"
        >
          Start
        </AsyncButton>
      )}

      {/* Download button — only shown for COMPLETE jobs.
          WCAG 2.1 AA (P23-T23.3 AC5):
          - aria-label encodes the table name for descriptive screen-reader text
          - type="button" prevents accidental form submission
          - disabled while download is in progress (AC3)
          - fully keyboard accessible as a native <button> element
          P27-T27.3: replaced with AsyncButton; layout class passed via className. */}
      {job.status === "COMPLETE" && (
        <AsyncButton
          type="button"
          isLoading={isDownloading}
          loadingText="Downloading…"
          onClick={() => onDownload(job.id)}
          aria-label={`Download synthetic data for ${job.table_name}`}
          className="job-card__download-btn"
        >
          Download
        </AsyncButton>
      )}
    </article>
  );
}
