/**
 * Dashboard — Job Synthesis monitoring interface.
 *
 * Displays active synthesis jobs, supports job creation, and streams live
 * progress via SSE. Persists the active job ID in localStorage so a page
 * refresh reconnects to the running stream automatically.
 *
 * CONSTITUTION:
 *   - WCAG 2.1 AA: aria-live regions, progressbar roles, labelled forms,
 *     required-field indicators (aria-required="true"), error identification
 *     (aria-invalid on failing inputs), assertive announcement for errors.
 *   - No inline style= attributes — all layout via CSS classes (P20-T20.3 AC3).
 *   - document.title set on mount.
 *   - prefers-reduced-motion respected via global.css @media rule.
 *
 * localStorage key: "conclave_active_job_id"
 *   Set when a job is started. Cleared when the job reaches a terminal state
 *   (COMPLETE or FAILED) or when a rehydrated job cannot be found (404).
 *
 * WCAG fix: "Load More" button uses --color-accent-text (#818cf8, ~6.3:1 on
 * --color-bg) instead of --color-accent (#4f46e5, ~3:1 on --color-bg which
 * fails WCAG 1.4.3 for text on transparent/dark background).
 *
 * Accessibility fix: form validation error div carries id="form-error" and the
 * field that triggered the error receives aria-describedby="form-error" so
 * assistive technologies can programmatically associate the message with its
 * input.
 *
 * WCAG 1.3.1 / 3.3.1 parity with Unseal.tsx (T17.2):
 *   - All required inputs carry aria-required="true".
 *   - Integer fields (total_epochs, checkpoint_every_n) set aria-invalid="true"
 *     when client-side validation fails, matching the Unseal pattern.
 *   - Visible asterisks in labels are wrapped with aria-hidden="true" so screen
 *     readers rely on aria-required instead of reading the literal "*".
 *
 * P23-T23.3: Download button wired via handleDownload. A browser anchor trick
 * (create <a>, set href to an object URL, programmatically click, revoke) is
 * used so the file download works without navigating away from the page.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  createJob,
  downloadJob,
  getJob,
  getJobs,
  startJob,
  type CreateJobParams,
  type JobResponse,
  type ProblemDetail,
} from "../api/client";
import { RFC7807Toast } from "../components/RFC7807Toast";
import JobCard from "../components/JobCard";
import { useSSE } from "../hooks/useSSE";
import { AssertiveAnnouncement, PoliteAnnouncement } from "../components/AriaLive";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const LOCAL_STORAGE_KEY = "conclave_active_job_id";
const TERMINAL_STATUSES = new Set(["COMPLETE", "FAILED"]);
/** Auto-dismiss duration for the Dashboard's standalone error toast (ms). */
const DASHBOARD_TOAST_DISMISS_MS = 8000;

// ---------------------------------------------------------------------------
// Create Job form state
// ---------------------------------------------------------------------------

interface CreateJobFormState {
  table_name: string;
  parquet_path: string;
  total_epochs: string;
  checkpoint_every_n: string;
}

const EMPTY_FORM: CreateJobFormState = {
  table_name: "",
  parquet_path: "",
  total_epochs: "",
  checkpoint_every_n: "",
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Dashboard — main post-unseal UI for monitoring synthesis jobs.
 *
 * On mount:
 *  1. Loads the job list from GET /jobs.
 *  2. Reads localStorage for a persisted active job ID.
 *  3. If found, verifies the job still exists and is non-terminal; if so,
 *     opens an SSE stream for rehydration.
 */
export default function Dashboard(): JSX.Element {
  // Job list state
  const [jobs, setJobs] = useState<JobResponse[]>([]);
  const [nextCursor, setNextCursor] = useState<number | null>(null);
  const [isLoadingMore, setIsLoadingMore] = useState(false);

  // Active SSE job (the one being streamed)
  const [activeJobId, setActiveJobId] = useState<number | null>(null);

  // Starting state per-job (only one can start at a time in practice)
  const [startingJobId, setStartingJobId] = useState<number | null>(null);

  // Downloading state per-job
  const [downloadingJobId, setDownloadingJobId] = useState<number | null>(null);

  // RFC 7807 toast for API errors
  const [apiError, setApiError] = useState<ProblemDetail | null>(null);
  const [errorVisible, setErrorVisible] = useState(false);

  // Create Job form
  const [form, setForm] = useState<CreateJobFormState>(EMPTY_FORM);
  const [isCreating, setIsCreating] = useState(false);

  // Form validation error (for NaN guards on integer fields).
  // errorField tracks which input triggered the error so aria-describedby and
  // aria-invalid can be applied to that specific input element (WCAG 3.3.1).
  const [formValidationError, setFormValidationError] = useState<string | null>(null);
  const [formErrorField, setFormErrorField] = useState<keyof CreateJobFormState | null>(null);

  // Announcement text for screen readers (aria-live polite region)
  const [announcement, setAnnouncement] = useState("");
  const announcementRef = useRef("");

  // SSE streaming state
  const sseState = useSSE(activeJobId);

  // -------------------------------------------------------------------------
  // Auto-dismiss for the standalone error toast
  // -------------------------------------------------------------------------

  useEffect(() => {
    if (!errorVisible) return;

    const timer = setTimeout(() => {
      setErrorVisible(false);
    }, DASHBOARD_TOAST_DISMISS_MS);

    return () => {
      clearTimeout(timer);
    };
  }, [errorVisible]);

  // -------------------------------------------------------------------------
  // Side effects from SSE state
  // -------------------------------------------------------------------------

  const prevSsePercent = useRef<number | null>(null);

  useEffect(() => {
    if (sseState.percent !== null && sseState.percent !== prevSsePercent.current) {
      prevSsePercent.current = sseState.percent;
      const text = `Job Synthesis reached ${sseState.percent}%`;
      if (announcementRef.current !== text) {
        announcementRef.current = text;
        setAnnouncement(text);
      }
    }

    if (sseState.status === "COMPLETE" || sseState.status === "FAILED") {
      localStorage.removeItem(LOCAL_STORAGE_KEY);
      setActiveJobId(null);
    }
  }, [sseState]);

  // -------------------------------------------------------------------------
  // Load jobs on mount
  // -------------------------------------------------------------------------

  const loadJobs = useCallback(async (cursor?: number): Promise<void> => {
    const result = await getJobs(cursor);
    if (result.ok) {
      if (cursor !== undefined) {
        setJobs((prev) => [...prev, ...result.data.items]);
      } else {
        setJobs(result.data.items);
      }
      setNextCursor(result.data.next_cursor);
    } else {
      setApiError(result.error);
      setErrorVisible(true);
    }
  }, []);

  // Rehydrate from localStorage on mount
  useEffect(() => {
    void (async () => {
      const stored = localStorage.getItem(LOCAL_STORAGE_KEY);
      if (stored !== null) {
        const jobId = parseInt(stored, 10);
        if (!isNaN(jobId)) {
          const result = await getJob(jobId);
          if (result.ok) {
            const job = result.data;
            if (!TERMINAL_STATUSES.has(job.status)) {
              setActiveJobId(jobId);
            } else {
              localStorage.removeItem(LOCAL_STORAGE_KEY);
            }
          } else {
            // Job not found or errored — clear stale entry
            localStorage.removeItem(LOCAL_STORAGE_KEY);
          }
        }
      }

      await loadJobs();
    })();
  }, [loadJobs]);

  // Set page title
  useEffect(() => {
    document.title = "Dashboard — Conclave Engine";
  }, []);

  // -------------------------------------------------------------------------
  // Event handlers
  // -------------------------------------------------------------------------

  const handleLoadMore = async (): Promise<void> => {
    if (nextCursor === null) return;
    setIsLoadingMore(true);
    await loadJobs(nextCursor);
    setIsLoadingMore(false);
  };

  const handleStart = async (jobId: number): Promise<void> => {
    setStartingJobId(jobId);
    const result = await startJob(jobId);
    setStartingJobId(null);
    if (result.ok) {
      localStorage.setItem(LOCAL_STORAGE_KEY, String(jobId));
      setActiveJobId(jobId);
    } else {
      setApiError(result.error);
      setErrorVisible(true);
    }
  };

  /**
   * Trigger a browser file download for a COMPLETE job's synthesised artefact.
   *
   * Uses the anchor-click pattern to initiate a file download without leaving
   * the page:
   *   1. Fetch the blob from GET /jobs/{id}/download.
   *   2. Create an object URL for the blob.
   *   3. Create a temporary <a> element, set its href and download attributes,
   *      click it programmatically, then revoke the object URL to release memory.
   *
   * On failure the RFC 7807 error toast is shown (AC4).
   *
   * @param jobId - The numeric ID of the COMPLETE job to download.
   */
  const handleDownload = async (jobId: number): Promise<void> => {
    setDownloadingJobId(jobId);
    const result = await downloadJob(jobId);
    setDownloadingJobId(null);

    if (result.ok) {
      const objectUrl = URL.createObjectURL(result.blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = result.filename;
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      URL.revokeObjectURL(objectUrl);
    } else {
      setApiError(result.error);
      setErrorVisible(true);
    }
  };

  const handleFormChange = (
    field: keyof CreateJobFormState,
    value: string,
  ): void => {
    setForm((prev) => ({ ...prev, [field]: value }));
  };

  const handleCreateJob = async (
    e: React.FormEvent<HTMLFormElement>,
  ): Promise<void> => {
    e.preventDefault();
    setFormValidationError(null);
    setFormErrorField(null);

    const totalEpochs = parseInt(form.total_epochs, 10);
    if (isNaN(totalEpochs)) {
      setFormValidationError("Total Epochs must be a valid integer.");
      setFormErrorField("total_epochs");
      return;
    }

    const checkpointEveryN = parseInt(form.checkpoint_every_n, 10);
    if (isNaN(checkpointEveryN)) {
      setFormValidationError("Checkpoint Every must be a valid integer.");
      setFormErrorField("checkpoint_every_n");
      return;
    }

    setIsCreating(true);

    const params: CreateJobParams = {
      table_name: form.table_name,
      parquet_path: form.parquet_path,
      total_epochs: totalEpochs,
      checkpoint_every_n: checkpointEveryN,
    };

    const result = await createJob(params);
    setIsCreating(false);

    if (result.ok) {
      setForm(EMPTY_FORM);
      await loadJobs();
    } else {
      setApiError(result.error);
      setErrorVisible(true);
    }
  };

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  return (
    <main
      id="main-content"
      tabIndex={-1}
      className="dashboard-main"
    >
      {/* RFC 7807 error toast */}
      <RFC7807Toast
        problem={apiError}
        visible={errorVisible}
        onDismiss={() => setErrorVisible(false)}
      />

      {/* Assertive announcement for API errors — interrupts screen readers for
          critical failures. Separate container from role="alertdialog" toast. */}
      <AssertiveAnnouncement>
        {errorVisible && apiError !== null ? apiError.title : ""}
      </AssertiveAnnouncement>

      {/* Hidden aria-live region for progress announcements.
          IMPORTANT: This is a separate container from role="alertdialog" — no nesting. */}
      <PoliteAnnouncement>
        {announcement}
      </PoliteAnnouncement>

      <div className="dashboard-content">
        {/* Page heading */}
        <header>
          <h1 className="dashboard-header__title">
            Conclave Engine
          </h1>
          <p className="dashboard-header__subtitle">
            Monitor and manage data synthesis jobs.
          </p>
        </header>

        {/* Create Job form */}
        <section aria-labelledby="create-job-heading">
          <h2
            id="create-job-heading"
            className="dashboard-section__heading"
          >
            Create Job
          </h2>

          <form
            onSubmit={(e) => void handleCreateJob(e)}
            className="dashboard-form"
          >
            {/* Form validation error — id="form-error" enables aria-describedby
                association from the triggering input field (WCAG 1.3.1).
                WCAG FIX: Container is always in the DOM so NVDA+Firefox does not
                swallow repeat announcements when the same error fires twice.
                Only the text content is conditional; padding collapses to 0 when
                empty so the layout is unaffected. */}
            <div
              id="form-error"
              role="alert"
              className={`dashboard-form__error${formValidationError !== null ? " dashboard-form__error--active" : ""}`}
            >
              {formValidationError}
            </div>

            <div className="dashboard-form__field">
              <label
                htmlFor="table-name"
                className="dashboard-form__label"
              >
                Table Name{" "}
                {/* Visible required indicator — aria-hidden so SR reads aria-required */}
                <span aria-hidden="true" className="dashboard-form__required-indicator">
                  *
                </span>
              </label>
              <input
                id="table-name"
                type="text"
                required
                aria-required="true"
                value={form.table_name}
                onChange={(e) => handleFormChange("table_name", e.target.value)}
                placeholder="e.g. customers"
                className="dashboard-form__input"
              />
            </div>

            <div className="dashboard-form__field">
              <label
                htmlFor="parquet-path"
                className="dashboard-form__label"
              >
                Parquet Path{" "}
                {/* Visible required indicator — aria-hidden so SR reads aria-required */}
                <span aria-hidden="true" className="dashboard-form__required-indicator">
                  *
                </span>
              </label>
              <input
                id="parquet-path"
                type="text"
                required
                aria-required="true"
                value={form.parquet_path}
                onChange={(e) =>
                  handleFormChange("parquet_path", e.target.value)
                }
                placeholder="e.g. /data/customers.parquet"
                className="dashboard-form__input"
              />
            </div>

            <div className="dashboard-form__field">
              <label
                htmlFor="total-epochs"
                className="dashboard-form__label"
              >
                Total Epochs{" "}
                {/* Visible required indicator — aria-hidden so SR reads aria-required */}
                <span aria-hidden="true" className="dashboard-form__required-indicator">
                  *
                </span>
              </label>
              <input
                id="total-epochs"
                type="number"
                required
                min={1}
                aria-required="true"
                aria-invalid={formErrorField === "total_epochs"}
                value={form.total_epochs}
                onChange={(e) =>
                  handleFormChange("total_epochs", e.target.value)
                }
                placeholder="e.g. 100"
                aria-describedby={formErrorField === "total_epochs" ? "form-error" : undefined}
                className="dashboard-form__input"
              />
            </div>

            <div className="dashboard-form__field">
              <label
                htmlFor="checkpoint-every"
                className="dashboard-form__label"
              >
                Checkpoint Every (epochs){" "}
                {/* Visible required indicator — aria-hidden so SR reads aria-required */}
                <span aria-hidden="true" className="dashboard-form__required-indicator">
                  *
                </span>
              </label>
              <input
                id="checkpoint-every"
                type="number"
                required
                min={1}
                aria-required="true"
                aria-invalid={formErrorField === "checkpoint_every_n"}
                value={form.checkpoint_every_n}
                onChange={(e) =>
                  handleFormChange("checkpoint_every_n", e.target.value)
                }
                placeholder="e.g. 10"
                aria-describedby={formErrorField === "checkpoint_every_n" ? "form-error" : undefined}
                className="dashboard-form__input"
              />
            </div>

            <div className="dashboard-form__actions">
              <button
                type="submit"
                disabled={isCreating}
                className="dashboard-form__submit"
              >
                {isCreating ? "Creating…" : "Create Job"}
              </button>
            </div>
          </form>
        </section>

        {/* Job list */}
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
                  onStart={(id) => void handleStart(id)}
                  isStarting={startingJobId === job.id}
                  onDownload={(id) => void handleDownload(id)}
                  isDownloading={downloadingJobId === job.id}
                />
              ))}
            </div>
          )}

          {/* Pagination — load more.
              WCAG fix: uses --color-accent-text (#818cf8, ~6.3:1 on --color-bg)
              instead of --color-accent (#4f46e5, ~3:1 on --color-bg which fails
              WCAG 1.4.3 for text on a transparent/dark background). */}
          {nextCursor !== null && (
            <div className="dashboard-pagination">
              <button
                type="button"
                disabled={isLoadingMore}
                onClick={() => void handleLoadMore()}
                className="dashboard-pagination__btn"
              >
                {isLoadingMore ? "Loading…" : "Load More"}
              </button>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
