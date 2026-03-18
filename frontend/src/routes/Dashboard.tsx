/**
 * Dashboard — Job Synthesis monitoring interface (thin coordinator).
 *
 * Manages state (jobs, SSE, errors, form) and passes props to:
 *   - CreateJobForm — form rendering, accessibility, submit callback
 *   - JobList       — job cards, empty state, pagination Load More
 *
 * CONSTITUTION: WCAG 2.1 AA — aria-live regions stay here because they
 * announce cross-cutting events (SSE progress, download lifecycle).
 * localStorage key "conclave_active_job_id" owned here.
 *
 * P27-T27.2: Extracted from 618-line monolith.
 * Review fixes preserved:
 *   FINDING 2 — aria-live announcements on download start/complete/failure.
 *   FINDING 3 — focus restored to trigger element after error toast dismiss.
 *   FINDING 5 — downloadingJobIds uses Set<number> for concurrent downloads.
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
import { useSSE } from "../hooks/useSSE";
import { AssertiveAnnouncement, PoliteAnnouncement } from "../components/AriaLive";
import CreateJobForm, {
  type CreateJobFormState,
} from "../components/CreateJobForm";
import JobList from "../components/JobList";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const LOCAL_STORAGE_KEY = "conclave_active_job_id";
const TERMINAL_STATUSES = new Set(["COMPLETE", "FAILED"]);
/** Auto-dismiss duration for the Dashboard's standalone error toast (ms). */
const DASHBOARD_TOAST_DISMISS_MS = 8000;

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

  // Downloading state — Set<number> supports concurrent downloads (FINDING 5)
  const [downloadingJobIds, setDownloadingJobIds] = useState<Set<number>>(
    new Set(),
  );

  // RFC 7807 toast for API errors
  const [apiError, setApiError] = useState<ProblemDetail | null>(null);
  const [errorVisible, setErrorVisible] = useState(false);

  // Ref to trigger element for focus restoration after toast dismiss (FINDING 3)
  const errorTriggerRef = useRef<Element | null>(null);

  // Create Job form — controlled by Dashboard, rendered by CreateJobForm
  const [form, setForm] = useState<CreateJobFormState>(EMPTY_FORM);
  const [isCreating, setIsCreating] = useState(false);
  const [formValidationError, setFormValidationError] = useState<string | null>(null);
  const [formErrorField, setFormErrorField] =
    useState<keyof CreateJobFormState | null>(null);

  // Announcement text for aria-live polite region
  const [announcement, setAnnouncement] = useState("");
  const announcementRef = useRef("");

  const sseState = useSSE(activeJobId);

  // -------------------------------------------------------------------------
  // Auto-dismiss for the standalone error toast
  // -------------------------------------------------------------------------

  useEffect(() => {
    if (!errorVisible) return;
    const timer = setTimeout(() => setErrorVisible(false), DASHBOARD_TOAST_DISMISS_MS);
    return () => clearTimeout(timer);
  }, [errorVisible]);

  // -------------------------------------------------------------------------
  // SSE side effects — progress announcements and terminal state cleanup
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
  // Load jobs
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

  // Rehydrate localStorage on mount, then load jobs
  useEffect(() => {
    void (async () => {
      const stored = localStorage.getItem(LOCAL_STORAGE_KEY);
      if (stored !== null) {
        const jobId = parseInt(stored, 10);
        if (!isNaN(jobId)) {
          const result = await getJob(jobId);
          if (result.ok) {
            if (!TERMINAL_STATUSES.has(result.data.status)) {
              setActiveJobId(jobId);
            } else {
              localStorage.removeItem(LOCAL_STORAGE_KEY);
            }
          } else {
            localStorage.removeItem(LOCAL_STORAGE_KEY);
          }
        }
      }
      await loadJobs();
    })();
  }, [loadJobs]);

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
      errorTriggerRef.current = document.activeElement;
      setApiError(result.error);
      setErrorVisible(true);
    }
  };

  /**
   * Download a COMPLETE job's synthesised artefact via anchor-click pattern.
   * Announces download lifecycle to screen readers (FINDING 2).
   * Captures focus before call for restoration on toast dismiss (FINDING 3).
   *
   * @param jobId - The ID of the COMPLETE job to download.
   */
  const handleDownload = async (jobId: number): Promise<void> => {
    errorTriggerRef.current = document.activeElement;

    const job = jobs.find((j) => j.id === jobId);
    const jobTableName = job?.table_name ?? String(jobId);

    const startText = `Downloading synthetic data for ${jobTableName}...`;
    announcementRef.current = startText;
    setAnnouncement(startText);

    setDownloadingJobIds((prev) => new Set(prev).add(jobId));
    const result = await downloadJob(jobId);
    setDownloadingJobIds((prev) => {
      const next = new Set(prev);
      next.delete(jobId);
      return next;
    });

    if (result.ok) {
      const completeText = `Download complete for ${result.filename}`;
      announcementRef.current = completeText;
      setAnnouncement(completeText);

      const objectUrl = URL.createObjectURL(result.blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = result.filename;
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      URL.revokeObjectURL(objectUrl);
    } else {
      const failText = "Download failed";
      announcementRef.current = failText;
      setAnnouncement(failText);
      setApiError(result.error);
      setErrorVisible(true);
    }
  };

  /** Dismiss toast and restore focus to the element that triggered the error. */
  const handleErrorDismiss = (): void => {
    setErrorVisible(false);
    const target = errorTriggerRef.current;
    if (target instanceof HTMLElement) {
      setTimeout(() => target.focus(), 0);
    }
    errorTriggerRef.current = null;
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
      errorTriggerRef.current = document.activeElement;
      setApiError(result.error);
      setErrorVisible(true);
    }
  };

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  return (
    <main id="main-content" tabIndex={-1} className="dashboard-main">
      <RFC7807Toast
        problem={apiError}
        visible={errorVisible}
        onDismiss={handleErrorDismiss}
      />

      {/* Assertive region: interrupts SR for critical API errors */}
      <AssertiveAnnouncement>
        {errorVisible && apiError !== null ? apiError.title : ""}
      </AssertiveAnnouncement>

      {/* Polite region: SSE progress + download lifecycle announcements */}
      <PoliteAnnouncement>{announcement}</PoliteAnnouncement>

      <div className="dashboard-content">
        <header>
          <h1 className="dashboard-header__title">Conclave Engine</h1>
          <p className="dashboard-header__subtitle">
            Monitor and manage data synthesis jobs.
          </p>
        </header>

        <CreateJobForm
          form={form}
          isCreating={isCreating}
          formValidationError={formValidationError}
          formErrorField={formErrorField}
          onFormChange={handleFormChange}
          onSubmit={(e) => void handleCreateJob(e)}
        />

        <JobList
          jobs={jobs}
          activeJobId={activeJobId}
          sseState={sseState}
          startingJobId={startingJobId}
          downloadingJobIds={downloadingJobIds}
          nextCursor={nextCursor}
          isLoadingMore={isLoadingMore}
          onStart={(id) => void handleStart(id)}
          onDownload={(id) => void handleDownload(id)}
          onLoadMore={() => void handleLoadMore()}
        />
      </div>
    </main>
  );
}
