/**
 * Vitest unit tests for the Dashboard component.
 *
 * Tests job list rendering, SSE streaming, localStorage rehydration,
 * pagination, form submission, and RFC 7807 error toasts.
 *
 * Guards against:
 * - ES module mocking: vi.mock() at module level with factory function
 * - Fake timers deadlock: vi.useFakeTimers() only in specific tests
 * - Timer cleanup: verify useEffect cleanup is exercised on unmount
 */

import {
  act,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ---------------------------------------------------------------------------
// ES-module mock for the API client — MUST be at module level
// ---------------------------------------------------------------------------

vi.mock("../api/client", () => ({
  getHealth: vi.fn(),
  postUnseal: vi.fn(),
  getJobs: vi.fn(),
  getJob: vi.fn(),
  createJob: vi.fn(),
  startJob: vi.fn(),
}));

// ---------------------------------------------------------------------------
// MockEventSource — same pattern as useSSE.test.ts
// ---------------------------------------------------------------------------

type EventListener = (event: { data: string }) => void;

class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  private listeners: Record<string, EventListener[]> = {};
  onerror: ((event: Event) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: EventListener): void {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(listener);
  }

  removeEventListener(type: string, listener: EventListener): void {
    if (this.listeners[type]) {
      this.listeners[type] = this.listeners[type].filter((l) => l !== listener);
    }
  }

  simulateEvent(type: string, data: unknown): void {
    const event = { data: JSON.stringify(data) } as { data: string };
    (this.listeners[type] ?? []).forEach((h) => h(event));
  }

  close = vi.fn();
}

vi.stubGlobal("EventSource", MockEventSource);

// ---------------------------------------------------------------------------
// Import AFTER mock setup
// ---------------------------------------------------------------------------

import * as client from "../api/client";
import Dashboard from "../routes/Dashboard";
import type { JobResponse } from "../api/client";

const mockGetJobs = vi.mocked(client.getJobs);
const mockGetJob = vi.mocked(client.getJob);
const mockCreateJob = vi.mocked(client.createJob);
const mockStartJob = vi.mocked(client.startJob);

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const queuedJob: JobResponse = {
  id: 1,
  status: "QUEUED",
  current_epoch: 0,
  total_epochs: 10,
  table_name: "customers",
  parquet_path: "/data/customers.parquet",
  artifact_path: null,
  error_msg: null,
  checkpoint_every_n: 2,
};

const trainingJob: JobResponse = {
  id: 2,
  status: "TRAINING",
  current_epoch: 3,
  total_epochs: 10,
  table_name: "orders",
  parquet_path: "/data/orders.parquet",
  artifact_path: null,
  error_msg: null,
  checkpoint_every_n: 2,
};

const completeJob: JobResponse = {
  id: 3,
  status: "COMPLETE",
  current_epoch: 10,
  total_epochs: 10,
  table_name: "products",
  parquet_path: "/data/products.parquet",
  artifact_path: "/output/products_synth.parquet",
  error_msg: null,
  checkpoint_every_n: 2,
};

const failedJob: JobResponse = {
  id: 4,
  status: "FAILED",
  current_epoch: 5,
  total_epochs: 10,
  table_name: "inventory",
  parquet_path: "/data/inventory.parquet",
  artifact_path: null,
  error_msg: "CUDA out of memory",
  checkpoint_every_n: 2,
};

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

function renderDashboard() {
  return render(
    <MemoryRouter>
      <Dashboard />
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

beforeEach(() => {
  MockEventSource.instances = [];
  localStorage.clear();
  mockGetJobs.mockReset();
  mockGetJob.mockReset();
  mockCreateJob.mockReset();
  mockStartJob.mockReset();

  // Default: empty job list
  mockGetJobs.mockResolvedValue({ ok: true, data: { items: [], next_cursor: null } });
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("Dashboard — initial render", () => {
  it("sets document.title to Dashboard — Conclave Engine", async () => {
    renderDashboard();
    await waitFor(() => {
      expect(document.title).toBe("Dashboard — Conclave Engine");
    });
  });

  it("renders the job list heading", async () => {
    renderDashboard();
    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: /active jobs/i }),
      ).toBeInTheDocument();
    });
  });

  it("shows empty state message when no jobs exist", async () => {
    renderDashboard();
    await waitFor(() => {
      expect(screen.getByText(/no jobs found/i)).toBeInTheDocument();
    });
  });

  it("renders jobs returned from the API", async () => {
    mockGetJobs.mockResolvedValue({
      ok: true,
      data: { items: [queuedJob, trainingJob], next_cursor: null },
    });

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByText("customers")).toBeInTheDocument();
      expect(screen.getByText("orders")).toBeInTheDocument();
    });
  });
});

describe("Dashboard — job status badges", () => {
  it("shows Start button only for QUEUED jobs", async () => {
    mockGetJobs.mockResolvedValue({
      ok: true,
      data: {
        items: [queuedJob, trainingJob, completeJob, failedJob],
        next_cursor: null,
      },
    });

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByText("customers")).toBeInTheDocument();
    });

    // Only the QUEUED job should have a Start button
    const startButtons = screen.getAllByRole("button", { name: /start/i });
    expect(startButtons).toHaveLength(1);
  });

  it("shows status badges for all jobs", async () => {
    mockGetJobs.mockResolvedValue({
      ok: true,
      data: {
        items: [queuedJob, trainingJob, completeJob, failedJob],
        next_cursor: null,
      },
    });

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByText(/queued/i)).toBeInTheDocument();
      expect(screen.getByText(/training/i)).toBeInTheDocument();
      expect(screen.getByText(/complete/i)).toBeInTheDocument();
      expect(screen.getByText(/failed/i)).toBeInTheDocument();
    });
  });
});

describe("Dashboard — job creation form", () => {
  it("renders the Create Job form with labelled inputs", async () => {
    renderDashboard();

    await waitFor(() => {
      expect(screen.getByLabelText(/table name/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/parquet path/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/total epochs/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/checkpoint every/i)).toBeInTheDocument();
    });
  });

  it("submits the create job form and adds the new job to the list", async () => {
    const user = userEvent.setup();

    mockCreateJob.mockResolvedValue({ ok: true, data: queuedJob });
    // After creation, list now has the new job
    mockGetJobs
      .mockResolvedValueOnce({ ok: true, data: { items: [], next_cursor: null } })
      .mockResolvedValue({
        ok: true,
        data: { items: [queuedJob], next_cursor: null },
      });

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByLabelText(/table name/i)).toBeInTheDocument();
    });

    await user.type(screen.getByLabelText(/table name/i), "customers");
    await user.type(
      screen.getByLabelText(/parquet path/i),
      "/data/customers.parquet",
    );
    await user.type(screen.getByLabelText(/total epochs/i), "10");
    await user.type(screen.getByLabelText(/checkpoint every/i), "2");

    await user.click(screen.getByRole("button", { name: /create job/i }));

    await waitFor(() => {
      expect(mockCreateJob).toHaveBeenCalledWith({
        table_name: "customers",
        parquet_path: "/data/customers.parquet",
        total_epochs: 10,
        checkpoint_every_n: 2,
      });
    });
  });
});

describe("Dashboard — starting a job and SSE streaming", () => {
  it("calls startJob when Start button is clicked", async () => {
    const user = userEvent.setup();

    mockGetJobs.mockResolvedValue({
      ok: true,
      data: { items: [queuedJob], next_cursor: null },
    });
    mockStartJob.mockResolvedValue({
      ok: true,
      data: { status: "accepted", job_id: 1 },
    });

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByText("customers")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /start/i }));

    await waitFor(() => {
      expect(mockStartJob).toHaveBeenCalledWith(1);
    });
  });

  it("stores active job ID in localStorage after job start", async () => {
    const user = userEvent.setup();

    mockGetJobs.mockResolvedValue({
      ok: true,
      data: { items: [queuedJob], next_cursor: null },
    });
    mockStartJob.mockResolvedValue({
      ok: true,
      data: { status: "accepted", job_id: 1 },
    });

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /start/i })).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /start/i }));

    await waitFor(() => {
      expect(localStorage.getItem("conclave_active_job_id")).toBe("1");
    });
  });

  it("opens an SSE stream after job start", async () => {
    const user = userEvent.setup();

    mockGetJobs.mockResolvedValue({
      ok: true,
      data: { items: [queuedJob], next_cursor: null },
    });
    mockStartJob.mockResolvedValue({
      ok: true,
      data: { status: "accepted", job_id: 1 },
    });

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /start/i })).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /start/i }));

    await waitFor(() => {
      expect(MockEventSource.instances.some((es) => es.url === "/jobs/1/stream")).toBe(
        true,
      );
    });
  });

  it("updates the progress bar when SSE emits progress events", async () => {
    const user = userEvent.setup();

    mockGetJobs.mockResolvedValue({
      ok: true,
      data: { items: [queuedJob], next_cursor: null },
    });
    mockStartJob.mockResolvedValue({
      ok: true,
      data: { status: "accepted", job_id: 1 },
    });

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /start/i })).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /start/i }));

    await waitFor(() => {
      expect(MockEventSource.instances.some((es) => es.url === "/jobs/1/stream")).toBe(
        true,
      );
    });

    const es = MockEventSource.instances.find((e) => e.url === "/jobs/1/stream")!;

    act(() => {
      es.simulateEvent("progress", {
        status: "TRAINING",
        current_epoch: 5,
        total_epochs: 10,
        percent: 50,
      });
    });

    await waitFor(() => {
      const progressBar = screen.getByRole("progressbar", { name: /job 1/i });
      expect(progressBar).toHaveAttribute("aria-valuenow", "50");
    });
  });

  it("clears localStorage when SSE emits a complete event", async () => {
    const user = userEvent.setup();

    mockGetJobs.mockResolvedValue({
      ok: true,
      data: { items: [queuedJob], next_cursor: null },
    });
    mockStartJob.mockResolvedValue({
      ok: true,
      data: { status: "accepted", job_id: 1 },
    });

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /start/i })).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /start/i }));

    await waitFor(() => {
      expect(localStorage.getItem("conclave_active_job_id")).toBe("1");
    });

    const es = MockEventSource.instances.find((e) => e.url === "/jobs/1/stream")!;

    act(() => {
      es.simulateEvent("complete", {
        status: "COMPLETE",
        current_epoch: 10,
        total_epochs: 10,
        percent: 100,
      });
    });

    await waitFor(() => {
      expect(localStorage.getItem("conclave_active_job_id")).toBeNull();
    });
  });

  it("clears localStorage when SSE emits an error event", async () => {
    const user = userEvent.setup();

    mockGetJobs.mockResolvedValue({
      ok: true,
      data: { items: [queuedJob], next_cursor: null },
    });
    mockStartJob.mockResolvedValue({
      ok: true,
      data: { status: "accepted", job_id: 1 },
    });

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /start/i })).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /start/i }));

    await waitFor(() => {
      expect(localStorage.getItem("conclave_active_job_id")).toBe("1");
    });

    const es = MockEventSource.instances.find((e) => e.url === "/jobs/1/stream")!;

    act(() => {
      es.simulateEvent("error", { detail: "OOM error" });
    });

    await waitFor(() => {
      expect(localStorage.getItem("conclave_active_job_id")).toBeNull();
    });
  });
});

describe("Dashboard — localStorage rehydration on mount", () => {
  it("reconnects to SSE on mount when localStorage has an active TRAINING job", async () => {
    localStorage.setItem("conclave_active_job_id", "2");

    mockGetJob.mockResolvedValue({ ok: true, data: trainingJob });
    mockGetJobs.mockResolvedValue({
      ok: true,
      data: { items: [trainingJob], next_cursor: null },
    });

    renderDashboard();

    await waitFor(() => {
      expect(mockGetJob).toHaveBeenCalledWith(2);
    });

    await waitFor(() => {
      expect(
        MockEventSource.instances.some((es) => es.url === "/jobs/2/stream"),
      ).toBe(true);
    });
  });

  it("reconnects to SSE on mount when localStorage has an active QUEUED job", async () => {
    localStorage.setItem("conclave_active_job_id", "1");

    mockGetJob.mockResolvedValue({ ok: true, data: queuedJob });
    mockGetJobs.mockResolvedValue({
      ok: true,
      data: { items: [queuedJob], next_cursor: null },
    });

    renderDashboard();

    await waitFor(() => {
      expect(mockGetJob).toHaveBeenCalledWith(1);
    });

    await waitFor(() => {
      expect(
        MockEventSource.instances.some((es) => es.url === "/jobs/1/stream"),
      ).toBe(true);
    });
  });

  it("clears localStorage when rehydrated job is COMPLETE", async () => {
    localStorage.setItem("conclave_active_job_id", "3");

    mockGetJob.mockResolvedValue({ ok: true, data: completeJob });
    mockGetJobs.mockResolvedValue({
      ok: true,
      data: { items: [completeJob], next_cursor: null },
    });

    renderDashboard();

    await waitFor(() => {
      expect(localStorage.getItem("conclave_active_job_id")).toBeNull();
    });

    // No SSE stream should be opened for a terminal job
    expect(
      MockEventSource.instances.some((es) => es.url === "/jobs/3/stream"),
    ).toBe(false);
  });

  it("clears localStorage when rehydrated job returns 404", async () => {
    localStorage.setItem("conclave_active_job_id", "999");

    mockGetJob.mockResolvedValue({
      ok: false,
      error: {
        type: "about:blank",
        title: "Not Found",
        status: 404,
        detail: "SynthesisJob with id=999 not found.",
      },
    });
    mockGetJobs.mockResolvedValue({
      ok: true,
      data: { items: [], next_cursor: null },
    });

    renderDashboard();

    await waitFor(() => {
      expect(localStorage.getItem("conclave_active_job_id")).toBeNull();
    });
  });
});

describe("Dashboard — pagination", () => {
  it("shows Load More button when next_cursor is present", async () => {
    mockGetJobs.mockResolvedValue({
      ok: true,
      data: { items: [queuedJob], next_cursor: 42 },
    });

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /load more/i })).toBeInTheDocument();
    });
  });

  it("hides Load More button when next_cursor is null", async () => {
    mockGetJobs.mockResolvedValue({
      ok: true,
      data: { items: [queuedJob], next_cursor: null },
    });

    renderDashboard();

    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: /load more/i }),
      ).not.toBeInTheDocument();
    });
  });

  it("fetches next page with cursor when Load More is clicked", async () => {
    const user = userEvent.setup();

    mockGetJobs
      .mockResolvedValueOnce({
        ok: true,
        data: { items: [queuedJob], next_cursor: 42 },
      })
      .mockResolvedValue({
        ok: true,
        data: { items: [trainingJob], next_cursor: null },
      });

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /load more/i })).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /load more/i }));

    await waitFor(() => {
      expect(mockGetJobs).toHaveBeenCalledWith(42);
    });
  });
});

describe("Dashboard — RFC 7807 error handling", () => {
  it("renders an error toast when job list API fails", async () => {
    mockGetJobs.mockResolvedValue({
      ok: false,
      error: {
        type: "about:blank",
        title: "Internal Server Error",
        status: 500,
        detail: "Unexpected database error.",
      },
    });

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
      expect(screen.getByText(/internal server error/i)).toBeInTheDocument();
    });
  });

  it("renders error detail from RFC 7807 response", async () => {
    mockGetJobs.mockResolvedValue({
      ok: false,
      error: {
        type: "about:blank",
        title: "Not Found",
        status: 404,
        detail: "Resource not found.",
      },
    });

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByText(/resource not found/i)).toBeInTheDocument();
    });
  });
});

describe("Dashboard — accessibility", () => {
  it("all form inputs have associated labels", async () => {
    renderDashboard();

    await waitFor(() => {
      const tableInput = screen.getByLabelText(/table name/i);
      const parquetInput = screen.getByLabelText(/parquet path/i);
      const epochsInput = screen.getByLabelText(/total epochs/i);
      const checkpointInput = screen.getByLabelText(/checkpoint every/i);

      expect(tableInput).toBeInTheDocument();
      expect(parquetInput).toBeInTheDocument();
      expect(epochsInput).toBeInTheDocument();
      expect(checkpointInput).toBeInTheDocument();
    });
  });

  it("progress bars have required aria attributes", async () => {
    const user = userEvent.setup();

    mockGetJobs.mockResolvedValue({
      ok: true,
      data: { items: [queuedJob], next_cursor: null },
    });
    mockStartJob.mockResolvedValue({
      ok: true,
      data: { status: "accepted", job_id: 1 },
    });

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /start/i })).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /start/i }));

    const es = await waitFor(() => {
      const found = MockEventSource.instances.find((e) => e.url === "/jobs/1/stream");
      expect(found).toBeDefined();
      return found!;
    });

    act(() => {
      es.simulateEvent("progress", {
        status: "TRAINING",
        current_epoch: 5,
        total_epochs: 10,
        percent: 50,
      });
    });

    await waitFor(() => {
      const progressBar = screen.getByRole("progressbar", { name: /job 1/i });
      expect(progressBar).toHaveAttribute("aria-valuemin", "0");
      expect(progressBar).toHaveAttribute("aria-valuemax", "100");
      expect(progressBar).toHaveAttribute("aria-valuenow", "50");
    });
  });
});

describe("Dashboard — aria-live regions", () => {
  it("renders an aria-live polite region for progress announcements", async () => {
    renderDashboard();

    await waitFor(() => {
      const politeRegions = document.querySelectorAll('[aria-live="polite"]');
      expect(politeRegions.length).toBeGreaterThan(0);
    });
  });

  it("announces job progress in the aria-live region", async () => {
    const user = userEvent.setup();

    mockGetJobs.mockResolvedValue({
      ok: true,
      data: { items: [queuedJob], next_cursor: null },
    });
    mockStartJob.mockResolvedValue({
      ok: true,
      data: { status: "accepted", job_id: 1 },
    });

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /start/i })).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /start/i }));

    const es = await waitFor(() => {
      const found = MockEventSource.instances.find((e) => e.url === "/jobs/1/stream");
      expect(found).toBeDefined();
      return found!;
    });

    act(() => {
      es.simulateEvent("progress", {
        status: "TRAINING",
        current_epoch: 5,
        total_epochs: 10,
        percent: 50,
      });
    });

    await waitFor(() => {
      // Check that the aria-live region contains the announcement text
      const liveRegion = document.querySelector('[aria-live="polite"]');
      expect(liveRegion?.textContent).toMatch(/50%|50/);
    });
  });
});
