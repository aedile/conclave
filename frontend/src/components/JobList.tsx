/**
 * JobList — job list sub-component.
 *
 * Extracted from Dashboard.tsx (P27-T27.2). Renders the list of synthesis
 * job cards, the empty state message, and the pagination "Load More" button.
 * All state is received as props; Dashboard owns the job array, cursor, and
 * SSE state.
 *
 * CONSTITUTION:
 *   - WCAG 2.1 AA: section heading ("Active Jobs") is preserved for landmark
 *     navigation. The "Load More" button uses type="button" to prevent
 *     accidental form submission and is keyboard accessible.
 *   - No inline style= attributes — all layout via CSS classes (P20-T20.3 AC3).
 *   - WCAG fix note: "Load More" button uses --color-accent-text (#818cf8,
 *     ~6.3:1 on --color-bg) via dashboard-pagination__btn class — the WCAG
 *     colour token is in global.css, not here.
 *
 * P27-T27.3: "Load More" pagination button replaced with AsyncButton component
 * for standardized loading/disabled pattern. Layout class
 * (.dashboard-pagination__btn) passed via className to preserve existing styling.
 */

import type { JobResponse } from "../api/client";
import type { SSEState } from "../hooks/useSSE";
import AsyncButton from "./AsyncButton";
import JobCard from "./JobCard";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Props for the JobList component. */
interface JobListProps {
  /** The ordered list of synthesis jobs to display. */
  jobs: JobResponse[];
  /** The job ID currently being streamed via SSE, or null. */
  activeJobId: number | null;
  /** SSE streaming state from the active job, or null. */
  sseState: SSEState | null;
  /** The job ID currently starting, for disabling the Start button. */
  startingJobId: number | null;
  /** Set of job IDs currently downloading, for disabling Download buttons. */
  downloadingJobIds: Set<number>;
  /** Cursor for the next page, or null when there are no more pages. */
  nextCursor: number | null;
  /** Whether a "load more" fetch is currently in-flight. */
  isLoadingMore: boolean;
  /** Callback invoked when the Start button on a job card is clicked. */
  onStart: (jobId: number) => void;
  /** Callback invoked when the Download button on a job card is clicked. */
  onDownload: (jobId: number) => void;
  /** Callback invoked when the Load More button is clicked. */
  onLoadMore: () => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Render the Active Jobs section including the job card list, empty state,
 * and pagination controls.
 *
 * @param jobs - Jobs to render. Empty array triggers the empty state message.
 * @param activeJobId - Job receiving the SSE stream, for forwarding sseState.
 * @param sseState - Live SSE state forwarded only to the matching active job card.
 * @param startingJobId - Job whose Start button should show "Starting…" and be disabled.
 * @param downloadingJobIds - Set of job IDs whose Download buttons are disabled.
 * @param nextCursor - Non-null cursor reveals the Load More button.
 * @param isLoadingMore - Disables and relabels the Load More button while fetching.
 * @param onStart - Forwarded to each JobCard's onStart prop.
 * @param onDownload - Forwarded to each JobCard's onDownload prop.
 * @param onLoadMore - Called when the Load More button is clicked.
 */
export default function JobList({
  jobs,
  activeJobId,
  sseState,
  startingJobId,
  downloadingJobIds,
  nextCursor,
  isLoadingMore,
  onStart,
  onDownload,
  onLoadMore,
}: JobListProps): JSX.Element {
  return (
    <section aria-labelledby="active-jobs-heading">
      <h2
        id="active-jobs-heading"
        className="dashboard-section__heading"
      >
        Active Jobs
      </h2>

      {jobs.length === 0 ? (
        <p className="dashboard-jobs__empty">
          No jobs found. Create a job above to get started.
        </p>
      ) : (
        <div className="dashboard-jobs__list">
          {jobs.map((job) => (
            <JobCard
              key={job.id}
              job={job}
              sseState={activeJobId === job.id ? sseState : null}
              onStart={onStart}
              isStarting={startingJobId === job.id}
              onDownload={onDownload}
              isDownloading={downloadingJobIds.has(job.id)}
            />
          ))}
        </div>
      )}

      {/* Pagination — load more.
          WCAG fix: uses --color-accent-text (#818cf8, ~6.3:1 on --color-bg)
          instead of --color-accent (#4f46e5, ~3:1 on --color-bg which fails
          WCAG 1.4.3 for text on a transparent/dark background).
          P27-T27.3: "Load More" replaced with AsyncButton. */}
      {nextCursor !== null && (
        <div className="dashboard-pagination">
          <AsyncButton
            type="button"
            isLoading={isLoadingMore}
            loadingText="Loading…"
            onClick={onLoadMore}
            className="dashboard-pagination__btn"
          >
            Load More
          </AsyncButton>
        </div>
      )}
    </section>
  );
}
