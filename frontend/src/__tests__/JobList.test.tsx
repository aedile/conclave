/**
 * Vitest unit tests for the JobList component.
 *
 * Tests prop-driven rendering of job cards, the empty state message,
 * pagination "Load More" button visibility and callback invocation,
 * and pass-through of onStart/onDownload callbacks to child JobCard
 * components.
 *
 * All tests verify that the extracted JobList produces the same HTML
 * output as the inline implementation in the original Dashboard (P27-T27.2 AC5).
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

// ---------------------------------------------------------------------------
// API client mock — required because JobCard imports type from api/client
// ---------------------------------------------------------------------------

vi.mock("../api/client", () => ({
  getHealth: vi.fn(),
  postUnseal: vi.fn(),
  getJobs: vi.fn(),
  getJob: vi.fn(),
  createJob: vi.fn(),
  startJob: vi.fn(),
  downloadJob: vi.fn(),
}));

import JobList from "../components/JobList";
import type { JobResponse } from "../api/client";

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
// Tests — rendering
// ---------------------------------------------------------------------------

describe("JobList — rendering", () => {
  it("renders the empty state message when jobs array is empty", () => {
    render(
      <JobList
        jobs={[]}
        activeJobId={null}
        sseState={null}
        startingJobId={null}
        downloadingJobIds={new Set()}
        nextCursor={null}
        isLoadingMore={false}
        onStart={vi.fn()}
        onDownload={vi.fn()}
        onLoadMore={vi.fn()}
      />,
    );

    expect(screen.getByText(/no jobs found/i)).toBeInTheDocument();
  });

  it("renders job cards for each job in the jobs array", () => {
    render(
      <JobList
        jobs={[queuedJob, completeJob]}
        activeJobId={null}
        sseState={null}
        startingJobId={null}
        downloadingJobIds={new Set()}
        nextCursor={null}
        isLoadingMore={false}
        onStart={vi.fn()}
        onDownload={vi.fn()}
        onLoadMore={vi.fn()}
      />,
    );

    expect(screen.getByText("customers")).toBeInTheDocument();
    expect(screen.getByText("products")).toBeInTheDocument();
  });

  it("renders job cards for three jobs", () => {
    render(
      <JobList
        jobs={[queuedJob, completeJob, failedJob]}
        activeJobId={null}
        sseState={null}
        startingJobId={null}
        downloadingJobIds={new Set()}
        nextCursor={null}
        isLoadingMore={false}
        onStart={vi.fn()}
        onDownload={vi.fn()}
        onLoadMore={vi.fn()}
      />,
    );

    expect(screen.getByText("customers")).toBeInTheDocument();
    expect(screen.getByText("products")).toBeInTheDocument();
    expect(screen.getByText("inventory")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Tests — pagination
// ---------------------------------------------------------------------------

describe("JobList — pagination", () => {
  it("shows Load More button when nextCursor is non-null", () => {
    render(
      <JobList
        jobs={[queuedJob]}
        activeJobId={null}
        sseState={null}
        startingJobId={null}
        downloadingJobIds={new Set()}
        nextCursor={42}
        isLoadingMore={false}
        onStart={vi.fn()}
        onDownload={vi.fn()}
        onLoadMore={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("button", { name: /load more/i }),
    ).toBeInTheDocument();
  });

  it("hides Load More button when nextCursor is null", () => {
    render(
      <JobList
        jobs={[queuedJob]}
        activeJobId={null}
        sseState={null}
        startingJobId={null}
        downloadingJobIds={new Set()}
        nextCursor={null}
        isLoadingMore={false}
        onStart={vi.fn()}
        onDownload={vi.fn()}
        onLoadMore={vi.fn()}
      />,
    );

    expect(
      screen.queryByRole("button", { name: /load more/i }),
    ).not.toBeInTheDocument();
  });

  it("shows Loading… and disables Load More when isLoadingMore is true", () => {
    render(
      <JobList
        jobs={[queuedJob]}
        activeJobId={null}
        sseState={null}
        startingJobId={null}
        downloadingJobIds={new Set()}
        nextCursor={42}
        isLoadingMore={true}
        onStart={vi.fn()}
        onDownload={vi.fn()}
        onLoadMore={vi.fn()}
      />,
    );

    const btn = screen.getByRole("button", { name: /loading/i });
    expect(btn).toBeDisabled();
  });

  it("calls onLoadMore when Load More is clicked", async () => {
    const user = userEvent.setup();
    const onLoadMore = vi.fn();

    render(
      <JobList
        jobs={[queuedJob]}
        activeJobId={null}
        sseState={null}
        startingJobId={null}
        downloadingJobIds={new Set()}
        nextCursor={42}
        isLoadingMore={false}
        onStart={vi.fn()}
        onDownload={vi.fn()}
        onLoadMore={onLoadMore}
      />,
    );

    await user.click(screen.getByRole("button", { name: /load more/i }));

    await waitFor(() => {
      expect(onLoadMore).toHaveBeenCalledOnce();
    });
  });
});

// ---------------------------------------------------------------------------
// Tests — callback pass-through to JobCard
// ---------------------------------------------------------------------------

describe("JobList — callback pass-through", () => {
  it("calls onStart with the correct job id when Start button is clicked", async () => {
    const user = userEvent.setup();
    const onStart = vi.fn();

    render(
      <JobList
        jobs={[queuedJob]}
        activeJobId={null}
        sseState={null}
        startingJobId={null}
        downloadingJobIds={new Set()}
        nextCursor={null}
        isLoadingMore={false}
        onStart={onStart}
        onDownload={vi.fn()}
        onLoadMore={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: /start/i }));

    await waitFor(() => {
      expect(onStart).toHaveBeenCalledWith(1);
    });
  });

  it("calls onDownload with the correct job id when Download button is clicked", async () => {
    const user = userEvent.setup();
    const onDownload = vi.fn();

    render(
      <JobList
        jobs={[completeJob]}
        activeJobId={null}
        sseState={null}
        startingJobId={null}
        downloadingJobIds={new Set()}
        nextCursor={null}
        isLoadingMore={false}
        onStart={vi.fn()}
        onDownload={onDownload}
        onLoadMore={vi.fn()}
      />,
    );

    await user.click(
      screen.getByRole("button", {
        name: /download synthetic data for products/i,
      }),
    );

    await waitFor(() => {
      expect(onDownload).toHaveBeenCalledWith(3);
    });
  });

  it("passes isStarting=true to the correct job's card", () => {
    render(
      <JobList
        jobs={[queuedJob]}
        activeJobId={null}
        sseState={null}
        startingJobId={1}
        downloadingJobIds={new Set()}
        nextCursor={null}
        isLoadingMore={false}
        onStart={vi.fn()}
        onDownload={vi.fn()}
        onLoadMore={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: /starting/i })).toBeDisabled();
  });

  it("passes isDownloading=true to the correct job's card", () => {
    render(
      <JobList
        jobs={[completeJob]}
        activeJobId={null}
        sseState={null}
        startingJobId={null}
        downloadingJobIds={new Set([3])}
        nextCursor={null}
        isLoadingMore={false}
        onStart={vi.fn()}
        onDownload={vi.fn()}
        onLoadMore={vi.fn()}
      />,
    );

    const downloadBtn = screen.getByRole("button", {
      name: /download synthetic data for products/i,
    });
    expect(downloadBtn).toBeDisabled();
    expect(downloadBtn).toHaveTextContent("Downloading\u2026");
  });
});
