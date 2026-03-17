/**
 * Vitest unit tests for the API client module.
 *
 * Tests postUnseal, getHealth, getJobs, getJob, createJob, startJob, and
 * downloadJob discriminated union return values using the global fetch mock.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  createJob,
  downloadJob,
  getHealth,
  getJob,
  getJobs,
  postUnseal,
  startJob,
} from "../api/client";

// Mock the global fetch
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

/** Create a mock Response object. */
function mockResponse(
  status: number,
  body: unknown,
  ok: boolean = status >= 200 && status < 300,
): Response {
  return {
    ok,
    status,
    json: () => Promise.resolve(body),
    headers: new Headers(),
  } as unknown as Response;
}

/**
 * Create a mock blob Response for download endpoints.
 *
 * @param status - HTTP status code.
 * @param blobContent - The blob to return.
 * @param headers - Optional response headers.
 * @param ok - Override for the `ok` flag (default: status 200-299).
 * @param jsonBody - Optional JSON body for error responses.
 */
function mockBlobResponse(
  status: number,
  blobContent: Blob,
  headers: Record<string, string> = {},
  ok: boolean = status >= 200 && status < 300,
  jsonBody: unknown = {},
): Response {
  return {
    ok,
    status,
    blob: () => Promise.resolve(blobContent),
    json: () => Promise.resolve(jsonBody),
    headers: new Headers(headers),
  } as unknown as Response;
}

/** RFC 7807 problem detail fixture. */
const problemDetail = {
  type: "about:blank",
  title: "Not Found",
  status: 404,
  detail: "Resource not found.",
};

describe("postUnseal", () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("returns { ok: true, data } on HTTP 200 response", async () => {
    mockFetch.mockResolvedValue(
      mockResponse(200, { status: "unsealed" }),
    );

    const result = await postUnseal("correct-passphrase");

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data.status).toBe("unsealed");
    }
  });

  it("returns { ok: false, error } with EMPTY_PASSPHRASE on 400", async () => {
    mockFetch.mockResolvedValue(
      mockResponse(400, { error_code: "EMPTY_PASSPHRASE", detail: "Empty." }, false),
    );

    const result = await postUnseal("");

    expect(result.ok).toBe(false);
    if (!result.ok && result.error) {
      expect(result.error.error_code).toBe("EMPTY_PASSPHRASE");
      expect(result.error.detail).toBe("Empty.");
    }
  });

  it("returns { ok: false, error } with CONFIG_ERROR on 400", async () => {
    mockFetch.mockResolvedValue(
      mockResponse(400, { error_code: "CONFIG_ERROR", detail: "Salt not set." }, false),
    );

    const result = await postUnseal("passphrase");

    expect(result.ok).toBe(false);
    if (!result.ok && result.error) {
      expect(result.error.error_code).toBe("CONFIG_ERROR");
    }
  });

  it("returns { ok: false, networkError: true } when fetch throws", async () => {
    mockFetch.mockRejectedValue(new Error("Network failure"));

    const result = await postUnseal("passphrase");

    expect(result.ok).toBe(false);
    if (!result.ok) {
      // 'networkError' only exists on the null-error variant
      if (result.error === null) {
        expect(result.networkError).toBe(true);
      } else {
        expect.fail('Expected networkError variant');
      }
    }
  });

  it("returns CONFIG_ERROR for unexpected non-400 HTTP error status", async () => {
    mockFetch.mockResolvedValue(mockResponse(503, {}, false));

    const result = await postUnseal("passphrase");

    expect(result.ok).toBe(false);
    if (!result.ok && result.error) {
      expect(result.error.error_code).toBe("CONFIG_ERROR");
      expect(result.error.detail).toContain("503");
    }
  });

  it("sends passphrase in request body as JSON", async () => {
    mockFetch.mockResolvedValue(
      mockResponse(200, { status: "unsealed" }),
    );

    await postUnseal("my-secret-passphrase");

    expect(mockFetch).toHaveBeenCalledWith("/unseal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ passphrase: "my-secret-passphrase" }),
    });
  });
});

describe("getHealth", () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  it("returns { status: 'ok' } on HTTP 200", async () => {
    mockFetch.mockResolvedValue(mockResponse(200, { status: "ok" }));

    const result = await getHealth();

    expect(result).toEqual({ status: "ok" });
  });

  it("returns { status: 'locked' } on HTTP 423", async () => {
    mockFetch.mockResolvedValue(
      mockResponse(423, { detail: "Service sealed." }, false),
    );

    const result = await getHealth();

    expect(result).toEqual({ status: "locked" });
  });

  it("returns null when fetch throws (network error)", async () => {
    mockFetch.mockRejectedValue(new Error("Network failure"));

    const result = await getHealth();

    expect(result).toBeNull();
  });

  it("returns null for unexpected non-200/423 status codes", async () => {
    mockFetch.mockResolvedValue(mockResponse(500, {}, false));

    const result = await getHealth();

    expect(result).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// getJobs
// ---------------------------------------------------------------------------

describe("getJobs", () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  it("fetches /jobs?limit=20 when no cursor provided", async () => {
    mockFetch.mockResolvedValue(
      mockResponse(200, { items: [], next_cursor: null }),
    );

    await getJobs();

    expect(mockFetch).toHaveBeenCalledWith("/jobs?limit=20");
  });

  it("fetches /jobs?after=42&limit=20 when cursor provided", async () => {
    mockFetch.mockResolvedValue(
      mockResponse(200, { items: [], next_cursor: null }),
    );

    await getJobs(42);

    expect(mockFetch).toHaveBeenCalledWith("/jobs?after=42&limit=20");
  });

  it("returns { ok: true, data } on HTTP 200", async () => {
    const payload = { items: [{ id: 1 }], next_cursor: null };
    mockFetch.mockResolvedValue(mockResponse(200, payload));

    const result = await getJobs();

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data.items).toHaveLength(1);
    }
  });

  it("returns { ok: false, error } on non-2xx response — parses RFC 7807 body", async () => {
    // HTTP status 500 but the response body contains the problemDetail fixture
    // (status: 404). The assertion checks the parsed body, not the HTTP status.
    mockFetch.mockResolvedValue(mockResponse(500, problemDetail, false));

    const result = await getJobs();

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.status).toBe(404); // from problemDetail fixture
    }
  });

  it("returns network error when fetch throws", async () => {
    mockFetch.mockRejectedValue(new Error("Network failure"));

    const result = await getJobs();

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.title).toBe("Network Error");
    }
  });
});

// ---------------------------------------------------------------------------
// getJob
// ---------------------------------------------------------------------------

describe("getJob", () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  it("fetches /jobs/{id}", async () => {
    mockFetch.mockResolvedValue(mockResponse(200, { id: 7 }));

    await getJob(7);

    expect(mockFetch).toHaveBeenCalledWith("/jobs/7");
  });

  it("returns { ok: true, data } on HTTP 200", async () => {
    mockFetch.mockResolvedValue(mockResponse(200, { id: 7, status: "QUEUED" }));

    const result = await getJob(7);

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data.id).toBe(7);
    }
  });

  it("returns { ok: false, error } on HTTP 404", async () => {
    mockFetch.mockResolvedValue(mockResponse(404, problemDetail, false));

    const result = await getJob(999);

    expect(result.ok).toBe(false);
  });

  it("returns network error when fetch throws", async () => {
    mockFetch.mockRejectedValue(new Error("Network failure"));

    const result = await getJob(1);

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.title).toBe("Network Error");
    }
  });
});

// ---------------------------------------------------------------------------
// createJob
// ---------------------------------------------------------------------------

describe("createJob", () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  it("POSTs to /jobs with JSON body", async () => {
    const params = {
      table_name: "orders",
      parquet_path: "/data/orders.parquet",
      total_epochs: 50,
      checkpoint_every_n: 5,
    };
    mockFetch.mockResolvedValue(mockResponse(201, { id: 1, ...params }));

    await createJob(params);

    expect(mockFetch).toHaveBeenCalledWith("/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
  });

  it("returns { ok: true, data } on HTTP 201", async () => {
    const jobData = { id: 1, status: "QUEUED" };
    mockFetch.mockResolvedValue(mockResponse(201, jobData));

    const result = await createJob({
      table_name: "t",
      parquet_path: "/p",
      total_epochs: 1,
      checkpoint_every_n: 1,
    });

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data.id).toBe(1);
    }
  });

  it("returns { ok: false, error } on HTTP 422", async () => {
    mockFetch.mockResolvedValue(mockResponse(422, problemDetail, false));

    const result = await createJob({
      table_name: "",
      parquet_path: "",
      total_epochs: 0,
      checkpoint_every_n: 0,
    });

    expect(result.ok).toBe(false);
  });

  it("returns network error when fetch throws", async () => {
    mockFetch.mockRejectedValue(new Error("Network failure"));

    const result = await createJob({
      table_name: "t",
      parquet_path: "/p",
      total_epochs: 1,
      checkpoint_every_n: 1,
    });

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.title).toBe("Network Error");
    }
  });
});

// ---------------------------------------------------------------------------
// startJob
// ---------------------------------------------------------------------------

describe("startJob", () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  it("POSTs to /jobs/{id}/start", async () => {
    mockFetch.mockResolvedValue(
      mockResponse(202, { status: "accepted", job_id: 5 }),
    );

    await startJob(5);

    expect(mockFetch).toHaveBeenCalledWith("/jobs/5/start", { method: "POST" });
  });

  it("returns { ok: true, data } on HTTP 202", async () => {
    mockFetch.mockResolvedValue(
      mockResponse(202, { status: "accepted", job_id: 5 }),
    );

    const result = await startJob(5);

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data.job_id).toBe(5);
    }
  });

  it("returns { ok: false, error } on HTTP 409", async () => {
    mockFetch.mockResolvedValue(mockResponse(409, problemDetail, false));

    const result = await startJob(5);

    expect(result.ok).toBe(false);
  });

  it("returns network error when fetch throws", async () => {
    mockFetch.mockRejectedValue(new Error("Network failure"));

    const result = await startJob(1);

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.title).toBe("Network Error");
    }
  });
});

// ---------------------------------------------------------------------------
// downloadJob
// ---------------------------------------------------------------------------

describe("downloadJob", () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  it("GETs /jobs/{id}/download", async () => {
    const blob = new Blob(["data"], { type: "application/octet-stream" });
    mockFetch.mockResolvedValue(
      mockBlobResponse(200, blob, {
        "content-disposition": 'attachment; filename="products.parquet"',
      }),
    );

    await downloadJob(3);

    expect(mockFetch).toHaveBeenCalledWith("/jobs/3/download");
  });

  it("returns { ok: true, blob, filename } on HTTP 200 with Content-Disposition header", async () => {
    const blob = new Blob(["binary"], { type: "application/octet-stream" });
    mockFetch.mockResolvedValue(
      mockBlobResponse(200, blob, {
        "content-disposition": 'attachment; filename="customers.parquet"',
      }),
    );

    const result = await downloadJob(1);

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.blob).toBe(blob);
      expect(result.filename).toBe("customers.parquet");
    }
  });

  it("falls back to 'download.parquet' filename when Content-Disposition header is absent", async () => {
    const blob = new Blob(["binary"], { type: "application/octet-stream" });
    mockFetch.mockResolvedValue(mockBlobResponse(200, blob));

    const result = await downloadJob(2);

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.filename).toBe("download.parquet");
    }
  });

  it("falls back to 'download.parquet' when Content-Disposition has no filename token", async () => {
    const blob = new Blob(["binary"], { type: "application/octet-stream" });
    mockFetch.mockResolvedValue(
      mockBlobResponse(200, blob, { "content-disposition": "attachment" }),
    );

    const result = await downloadJob(4);

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.filename).toBe("download.parquet");
    }
  });

  it("returns { ok: false, error } on HTTP 404 with RFC 7807 body", async () => {
    // FINDING 6 (QA): Strengthen 404 assertion to verify error content,
    // not just ok === false.
    const error404 = {
      type: "about:blank",
      title: "Not Found",
      status: 404,
      detail: "Job not found.",
    };
    mockFetch.mockResolvedValue(
      mockBlobResponse(404, new Blob(), {}, false, error404),
    );

    const result = await downloadJob(999);

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.status).toBe(404);
      expect(typeof result.error.title).toBe("string");
      expect(result.error.title).toBe("Not Found");
      expect(result.error.detail).toBe("Job not found.");
    }
  });

  it("returns Download Error when blob() rejects after HTTP 200", async () => {
    // FINDING 4 (QA): blob() can reject if the connection drops mid-stream
    // after HTTP 200 is received. The button must not stay permanently disabled.
    mockFetch.mockResolvedValue({
      ok: true,
      status: 200,
      blob: () => Promise.reject(new Error("Connection reset")),
      headers: new Headers(),
    } as unknown as Response);

    const result = await downloadJob(7);

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.title).toBe("Download Error");
      expect(result.error.detail).toContain("Connection lost");
    }
  });

  it("parses filename from RFC 5987 filename* header", async () => {
    // ADVISORY 1 (DevOps): Happy-path test for RFC 5987 encoded filenames.
    const blob = new Blob(["binary"], { type: "application/octet-stream" });
    mockFetch.mockResolvedValue(
      mockBlobResponse(200, blob, {
        "content-disposition": "attachment; filename*=UTF-8''reports%20synth.parquet",
      }),
    );

    const result = await downloadJob(5);

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.filename).toBe("reports synth.parquet");
    }
  });

  it("strips path separators from server-supplied filename (FINDING 1)", async () => {
    // FINDING 1 (DevOps): Path separators must be stripped to prevent
    // path traversal when assigning to anchor.download.
    const blob = new Blob(["binary"], { type: "application/octet-stream" });
    mockFetch.mockResolvedValue(
      mockBlobResponse(200, blob, {
        "content-disposition": 'attachment; filename="../../etc/passwd"',
      }),
    );

    const result = await downloadJob(6);

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.filename).not.toContain("/");
      expect(result.filename).not.toContain("\\");
      expect(result.filename).toBe(".._.._etc_passwd");
    }
  });

  it("returns network error when fetch throws", async () => {
    mockFetch.mockRejectedValue(new Error("Network failure"));

    const result = await downloadJob(1);

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.title).toBe("Network Error");
    }
  });
});
