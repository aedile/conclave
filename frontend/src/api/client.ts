/**
 * API client for the Conclave Engine backend.
 *
 * Typed fetch wrappers that translate HTTP responses into discriminated
 * union results. The caller never sees raw Response objects — every call
 * resolves to either a success payload or a structured ApiError.
 *
 * CONSTITUTION: No external API calls. All requests target the same origin
 * (proxied via Vite to http://localhost:8000 in development).
 */

/** Structured error returned by the /unseal endpoint on ValueError. */
export interface UnsealErrorResponse {
  error_code: "EMPTY_PASSPHRASE" | "ALREADY_UNSEALED" | "CONFIG_ERROR";
  detail: string;
}

/** Successful response from the /unseal endpoint. */
export interface UnsealSuccessResponse {
  status: "unsealed";
}

/** Discriminated union result type for unseal calls. */
export type UnsealResult =
  | { ok: true; data: UnsealSuccessResponse }
  | { ok: false; error: UnsealErrorResponse }
  | { ok: false; error: null; networkError: true };

/**
 * POST /unseal — derive the KEK and transition the vault to unsealed state.
 *
 * Differentiates between three failure modes:
 *   1. Network error (no response) → { networkError: true }
 *   2. Structured 400 from backend → { error: UnsealErrorResponse }
 *   3. Unexpected HTTP error → generic error with CONFIG_ERROR code
 *
 * @param passphrase - The operator passphrase to derive the KEK from.
 * @returns A discriminated union result that the caller should switch on.
 */
export async function postUnseal(passphrase: string): Promise<UnsealResult> {
  let response: Response;

  try {
    response = await fetch("/unseal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ passphrase }),
    });
  } catch {
    // fetch() throws on network failure (DNS, CORS, offline, etc.)
    return { ok: false, error: null, networkError: true };
  }

  if (response.ok) {
    const data = (await response.json()) as UnsealSuccessResponse;
    return { ok: true, data };
  }

  if (response.status === 400) {
    const error = (await response.json()) as UnsealErrorResponse;
    return { ok: false, error };
  }

  // Unexpected HTTP status — treat as configuration error
  return {
    ok: false,
    error: {
      error_code: "CONFIG_ERROR",
      detail: `Unexpected server response: HTTP ${response.status}`,
    },
  };
}

/** Response from the /health endpoint. */
export interface HealthResponse {
  status: "ok";
}

/**
 * GET /health — check if the backend is reachable.
 *
 * Returns null on network error so callers can gracefully degrade.
 *
 * @returns The health response or null on network failure.
 */
export async function getHealth(): Promise<
  HealthResponse | { status: "locked" } | null
> {
  let response: Response;

  try {
    response = await fetch("/health");
  } catch {
    return null;
  }

  if (response.status === 423) {
    return { status: "locked" };
  }

  if (response.ok) {
    return (await response.json()) as HealthResponse;
  }

  return null;
}

// ---------------------------------------------------------------------------
// RFC 7807 Problem Detail — structured error format used by all job endpoints
// ---------------------------------------------------------------------------

/** RFC 7807 Problem Detail error object returned by job API endpoints. */
export interface ProblemDetail {
  type: string;
  title: string;
  status: number;
  detail: string;
}

// ---------------------------------------------------------------------------
// Job API types
// ---------------------------------------------------------------------------

/** Job status values as returned by the backend. */
export type JobStatus = "QUEUED" | "TRAINING" | "COMPLETE" | "FAILED";

/**
 * A single synthesis job as returned by the backend.
 *
 * Maps exactly to the backend's `JobResponse` schema.
 */
export interface JobResponse {
  id: number;
  status: JobStatus;
  current_epoch: number;
  total_epochs: number;
  table_name: string;
  parquet_path: string;
  artifact_path: string | null;
  error_msg: string | null;
  checkpoint_every_n: number;
}

/** Paginated job list response from GET /jobs. */
export interface JobListResponse {
  items: JobResponse[];
  next_cursor: number | null;
}

/** Payload for creating a new synthesis job. */
export interface CreateJobParams {
  table_name: string;
  parquet_path: string;
  total_epochs: number;
  checkpoint_every_n: number;
}

/** Accepted response from POST /jobs/{id}/start. */
export interface StartJobResponse {
  status: "accepted";
  job_id: number;
}

// ---------------------------------------------------------------------------
// Discriminated union result types for job endpoints
// ---------------------------------------------------------------------------

/** Result type for GET /jobs. */
export type GetJobsResult =
  | { ok: true; data: JobListResponse }
  | { ok: false; error: ProblemDetail };

/** Result type for GET /jobs/{id}. */
export type GetJobResult =
  | { ok: true; data: JobResponse }
  | { ok: false; error: ProblemDetail };

/** Result type for POST /jobs. */
export type CreateJobResult =
  | { ok: true; data: JobResponse }
  | { ok: false; error: ProblemDetail };

/** Result type for POST /jobs/{id}/start. */
export type StartJobResult =
  | { ok: true; data: StartJobResponse }
  | { ok: false; error: ProblemDetail };

/**
 * Result type for GET /jobs/{id}/download.
 *
 * On success, carries the raw Blob and the filename parsed from the
 * Content-Disposition header (or "download.parquet" as a fallback).
 */
export type DownloadJobResult =
  | { ok: true; blob: Blob; filename: string }
  | { ok: false; error: ProblemDetail };

// ---------------------------------------------------------------------------
// Shared helper
// ---------------------------------------------------------------------------

/**
 * Parse an RFC 7807 error body or create a generic one from the HTTP status.
 *
 * @param response - The failed HTTP response.
 * @returns A `ProblemDetail` describing the failure.
 */
async function parseProblemDetail(response: Response): Promise<ProblemDetail> {
  try {
    return (await response.json()) as ProblemDetail;
  } catch (e) {
    console.warn("[parseProblemDetail] Failed to parse error body:", e);
    return {
      type: "about:blank",
      title: "Error",
      status: response.status,
      detail: `Unexpected server response: HTTP ${response.status}`,
    };
  }
}

/**
 * Extract the filename from a Content-Disposition header value.
 *
 * Parses `filename=` and `filename*=UTF-8''` tokens. Falls back to
 * "download.parquet" if the header is absent or contains no filename token.
 *
 * @param header - The raw Content-Disposition header string, or null.
 * @returns The decoded filename, or "download.parquet" as a fallback.
 */
function extractFilename(header: string | null): string {
  if (!header) return "download.parquet";

  // Try filename*=UTF-8''<encoded> (RFC 5987)
  const rfc5987Match = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(header);
  if (rfc5987Match?.[1]) {
    try {
      return decodeURIComponent(rfc5987Match[1].trim());
    } catch {
      // Fall through to simple filename= match
    }
  }

  // Try filename="<name>" or filename=<name>
  const simpleMatch = /filename\s*=\s*"?([^";]+)"?/i.exec(header);
  if (simpleMatch?.[1]) {
    return simpleMatch[1].trim();
  }

  return "download.parquet";
}

// ---------------------------------------------------------------------------
// Job API functions
// ---------------------------------------------------------------------------

/**
 * GET /jobs — retrieve a paginated list of synthesis jobs.
 *
 * @param after - Optional cursor for forward pagination.
 * @returns Discriminated union of paginated job list or RFC 7807 error.
 */
export async function getJobs(after?: number): Promise<GetJobsResult> {
  const url = after !== undefined ? `/jobs?after=${after}&limit=20` : "/jobs?limit=20";
  let response: Response;

  try {
    response = await fetch(url);
  } catch {
    return {
      ok: false,
      error: {
        type: "about:blank",
        title: "Network Error",
        status: 0,
        detail: "Unable to connect to the server.",
      },
    };
  }

  if (response.ok) {
    const data = (await response.json()) as JobListResponse;
    return { ok: true, data };
  }

  return { ok: false, error: await parseProblemDetail(response) };
}

/**
 * GET /jobs/{jobId} — retrieve a single synthesis job.
 *
 * @param jobId - The numeric ID of the job.
 * @returns Discriminated union of job data or RFC 7807 error.
 */
export async function getJob(jobId: number): Promise<GetJobResult> {
  let response: Response;

  try {
    response = await fetch(`/jobs/${jobId}`);
  } catch {
    return {
      ok: false,
      error: {
        type: "about:blank",
        title: "Network Error",
        status: 0,
        detail: "Unable to connect to the server.",
      },
    };
  }

  if (response.ok) {
    const data = (await response.json()) as JobResponse;
    return { ok: true, data };
  }

  return { ok: false, error: await parseProblemDetail(response) };
}

/**
 * POST /jobs — create a new synthesis job.
 *
 * @param params - Job creation parameters.
 * @returns Discriminated union of created job or RFC 7807 error.
 */
export async function createJob(params: CreateJobParams): Promise<CreateJobResult> {
  let response: Response;

  try {
    response = await fetch("/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
  } catch {
    return {
      ok: false,
      error: {
        type: "about:blank",
        title: "Network Error",
        status: 0,
        detail: "Unable to connect to the server.",
      },
    };
  }

  if (response.status === 201 || response.ok) {
    const data = (await response.json()) as JobResponse;
    return { ok: true, data };
  }

  return { ok: false, error: await parseProblemDetail(response) };
}

/**
 * POST /jobs/{jobId}/start — transition a QUEUED job to TRAINING.
 *
 * @param jobId - The numeric ID of the job to start.
 * @returns Discriminated union of accepted response or RFC 7807 error.
 */
export async function startJob(jobId: number): Promise<StartJobResult> {
  let response: Response;

  try {
    response = await fetch(`/jobs/${jobId}/start`, { method: "POST" });
  } catch {
    return {
      ok: false,
      error: {
        type: "about:blank",
        title: "Network Error",
        status: 0,
        detail: "Unable to connect to the server.",
      },
    };
  }

  if (response.ok) {
    const data = (await response.json()) as StartJobResponse;
    return { ok: true, data };
  }

  return { ok: false, error: await parseProblemDetail(response) };
}

/**
 * GET /jobs/{jobId}/download — download the synthesised artefact for a
 * COMPLETE job.
 *
 * The backend returns `application/octet-stream` with a
 * `Content-Disposition: attachment; filename="<name>"` header.  This
 * function reads the response as a Blob and extracts the filename from the
 * header so the caller can trigger a browser download without ever touching
 * the raw Response object.
 *
 * CONSTITUTION: No PII is stored or logged.  The Blob is passed directly to
 * the caller for immediate browser-side download initiation.
 *
 * @param jobId - The numeric ID of the COMPLETE job to download.
 * @returns Discriminated union of `{ blob, filename }` or RFC 7807 error.
 */
export async function downloadJob(jobId: number): Promise<DownloadJobResult> {
  let response: Response;

  try {
    response = await fetch(`/jobs/${jobId}/download`);
  } catch {
    return {
      ok: false,
      error: {
        type: "about:blank",
        title: "Network Error",
        status: 0,
        detail: "Unable to connect to the server.",
      },
    };
  }

  if (response.ok) {
    const blob = await response.blob();
    const filename = extractFilename(
      response.headers.get("content-disposition"),
    );
    return { ok: true, blob, filename };
  }

  return { ok: false, error: await parseProblemDetail(response) };
}
